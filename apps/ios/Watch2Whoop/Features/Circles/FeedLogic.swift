import Foundation

/// Pure feed rules, kept apart from the store so they are unit-tested: merging a fresh page into the cached feed, and
/// laying the offline queue's likes and comments over what the server last said.
enum FeedLogic {
  /// Posts kept in the cache document (older ones are fetched again when someone scrolls that far).
  static let cacheLimit = 120

  /// Newest first: by day, then by id (the server's order, friends/feed.py).
  static func sorted(_ posts: [Post]) -> [Post] {
    posts.sorted { a, b in a.date == b.date ? a.id > b.id : a.date > b.date }
  }

  /// The first page again (pull to refresh, every return to the tab). Fresh posts replace their cached copies; cached
  /// posts inside the range the fresh page covers but missing from it are gone on the server (the author left the
  /// circle, the training was removed) and go here too; older cached posts stay, so the list does not jump back.
  static func mergeHead(_ cache: FeedCache, _ page: FeedPage) -> FeedCache {
    var out = cache
    out.previewExercises = page.previewExercises
    out.commentPreview = page.commentPreview
    out.commentMax = page.commentMax
    out.hasCircles = page.hasCircles
    guard let oldest = page.posts.last else {
      out.posts = []
      out.next = nil
      return out
    }
    let fresh = Set(page.posts.map(\.id))
    // with no next page the server's whole feed is this page: older cached posts are gone too
    let older = page.next == nil ? [] : cache.posts.filter { p in
      !fresh.contains(p.id) && (p.date < oldest.date || (p.date == oldest.date && p.id < oldest.id))
    }
    out.posts = Array(sorted(page.posts + older).prefix(cacheLimit))
    if older.isEmpty {
      out.next = page.next   // the cache had nothing beyond this page, or the server has nothing beyond it
    } else if out.posts.count < page.posts.count + older.count {
      out.next = page.next   // trimmed to the limit: continue from the server's cursor (duplicates are merged away)
    }
    return out
  }

  /// A page further down (infinite scroll).
  static func append(_ cache: FeedCache, _ page: FeedPage) -> FeedCache {
    var out = cache
    let ids = Set(page.posts.map(\.id))
    out.posts = sorted(cache.posts.filter { !ids.contains($0.id) } + page.posts)
    out.next = page.next
    return out
  }

  // MARK: - offline queue overlay

  /// Queue entries this page writes (outbox `kind`).
  static let likeKind = "circle.like"
  static let commentKind = "circle.comment"

  static func likeGroup(_ post: Int) -> String { "circle.like:\(post)" }
  static func commentGroup(_ post: Int) -> String { "circle.comment:\(post)" }

  /// The post an outbox path is about (`/v1/friends/feed/posts/12/like` → 12).
  static func postId(_ path: String) -> Int? {
    let parts = path.split(separator: "/")
    guard let i = parts.firstIndex(of: "posts"), i + 1 < parts.count else { return nil }
    return Int(parts[i + 1])
  }

  /// The comment text a queued comment carries.
  static func commentText(_ item: OutboxItem) -> String? {
    guard let b = item.body, let o = try? JSONSerialization.jsonObject(with: b) as? [String: Any] else { return nil }
    return o["body"] as? String
  }

  /// Comments still in the queue for a post, oldest first, as the list shows them (id = minus the outbox id).
  static func queuedComments(_ pending: [OutboxItem], post: Int, me: PostUser) -> [Comment] {
    pending.filter { $0.kind == commentKind && postId($0.path) == post }.compactMap { item in
      guard let text = commentText(item), let id = item.id else { return nil }
      let state: Comment.LocalState = item.state == .failed || item.state == .conflict ? .failed : .pending
      return Comment(id: -Int(id), user: me, body: text, local: state)
    }
  }

  /// What the user sees: the server's posts with their own queued likes and comments applied (likes: the last queued
  /// state wins; the count moves by one when it differs from the server's).
  static func overlay(_ posts: [Post], pending: [OutboxItem], me: PostUser, commentPreview: Int) -> [Post] {
    guard !pending.isEmpty else { return posts }
    var likes: [Int: Bool] = [:]
    for item in pending where item.kind == likeKind {
      if let id = postId(item.path) { likes[id] = item.method == APIRequest.Method.put.rawValue }
    }
    return posts.map { p in
      var p = p
      if let mine = likes[p.id], mine != p.likes.mine {
        p.likes = Post.Likes(count: max(0, p.likes.count + (mine ? 1 : -1)), mine: mine)
      }
      let queued = queuedComments(pending, post: p.id, me: me)
      if !queued.isEmpty {
        p.commentCount += queued.count
        p.previewComments = Array((p.previewComments + queued).suffix(max(1, commentPreview)))
      }
      return p
    }
  }
}

/// Small formatting helpers of the feed (the Today page's circle-feed.js wording).
enum FeedFormat {
  static func number(_ v: Double, digits: Int = 0) -> String {
    let f = NumberFormatter()
    f.numberStyle = .decimal
    f.maximumFractionDigits = digits
    f.minimumFractionDigits = 0
    f.locale = Locale(identifier: Lang.isZh ? "zh_CN" : "en_US")
    return f.string(from: NSNumber(value: v)) ?? "\(v)"
  }

  static func duration(_ min: Double) -> String {
    let m = Int(min.rounded())
    return m >= 60 ? tr("\(m / 60) 小时 \(m % 60) 分", "\(m / 60) h \(m % 60) min") : tr("\(m) 分钟", "\(m) min")
  }

  static func set(_ s: Post.Exercise.WorkSet) -> String {
    if let d = s.durationS, s.reps == nil { return "\(number(d)) s" }
    let reps = s.reps.map { number($0) } ?? "—"
    if let w = s.weightKg, w > 0 { return "\(number(w, digits: 1)) kg × \(reps)" }
    return "× \(reps)"
  }

  static func sleep(_ min: Double) -> String {
    let m = Int(min.rounded())
    return "\(m / 60):" + String(format: "%02d", m % 60)
  }

  /// 「3 小时前」 from when the workout ended, else 今天 / 昨天 / the date (circle-feed.js `ago`).
  static func ago(_ p: Post, now: Date = Date()) -> String {
    if let s = p.startedAt, let start = parseDate(s) {
      let end = start.addingTimeInterval((p.durationMin ?? 0) * 60)
      let secs = now.timeIntervalSince(end)
      if secs >= -60 && secs < 86_400 {
        if secs < 60 { return tr("刚刚", "Just now") }
        if secs < 3600 { return tr("\(Int(secs / 60)) 分钟前", "\(Int(secs / 60)) min ago") }
        return tr("\(Int(secs / 3600)) 小时前", "\(Int(secs / 3600)) h ago")
      }
    }
    return day(p.date, now: now)
  }

  static func day(_ iso: String, now: Date = Date()) -> String {
    let cal = Calendar.current
    guard let d = dayFormatter.date(from: String(iso.prefix(10))) else { return iso }
    let diff = cal.dateComponents([.day], from: cal.startOfDay(for: d), to: cal.startOfDay(for: now)).day ?? 0
    if diff == 0 { return tr("今天", "Today") }
    if diff == 1 { return tr("昨天", "Yesterday") }
    let f = DateFormatter()
    f.locale = Locale(identifier: Lang.isZh ? "zh_CN" : "en_US")
    f.setLocalizedDateFormatFromTemplate(cal.isDate(d, equalTo: now, toGranularity: .year) ? "MMMdEEE" : "yMMMd")
    return f.string(from: d)
  }

  static let dayFormatter: DateFormatter = {
    let f = DateFormatter()
    f.calendar = Calendar(identifier: .gregorian)
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyy-MM-dd"
    return f
  }()

  /// ISO 8601 with or without fractions (FastAPI's datetimes).
  static func parseDate(_ s: String) -> Date? {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = f.date(from: s) { return d }
    f.formatOptions = [.withInternetDateTime]
    return f.date(from: s)
  }

  /// Time left on an invite code (nil = never expires).
  static func timeLeft(_ expires: String?, now: Date = Date()) -> String {
    guard let e = expires, let d = parseDate(e) else { return tr("永久有效", "Never expires") }
    let h = d.timeIntervalSince(now) / 3600
    if h < 1 { return tr("剩不到 1 小时", "Less than 1 h left") }
    if h < 48 { return tr("剩 \(Int(h)) 小时", "\(Int(h)) h left") }
    let days = Int((h / 24).rounded(.up))
    return tr("剩 \(days) 天", "\(days) days left")
  }
}
