import Foundation

// 我 → 消息 (#359; server: inbox/store.py, flag `inbox`). One list merged on the server: admin notices, the weekly what's new,
// circle invites as cards, circle activity, system messages. Kept as one document (`inbox:list`), so it opens offline.

extension Endpoint {
  enum Inbox {
    static let list = "/v1/users/me/inbox"                             // ?before=&limit=
    static let read = "/v1/users/me/inbox/read"                        // {ids} or {all: true}
    static func item(_ id: String) -> String { "/v1/users/me/inbox/items/\(id)" }   // DELETE: hide
    static func accept(_ invite: Int) -> String { "/v1/users/me/inbox/invites/\(invite)/accept" }
    static func ignore(_ invite: Int) -> String { "/v1/users/me/inbox/invites/\(invite)/ignore" }
    static func mute(_ invite: Int) -> String { "/v1/users/me/inbox/invites/\(invite)/mute" }
  }
}

struct InboxItem: Codable, Equatable, Identifiable {
  /// `notice:3`, `whatsnew:2026-10-25`, `invite:12`, `notification:9`.
  let id: String
  /// notice / whatsnew / circle_invite / circle / system / ai_card; a later type shows as a plain row.
  var type: String
  var kind: String?
  var at: String?
  var read = false
  var title = ""
  var body: String?
  var url: String?
  var linkLabel: String?
  var circleId: Int?
  var entries: [Entry] = []
  var invite: Card?

  struct Entry: Codable, Equatable, Hashable {
    var date: String
    var text: String
    var url: String?
  }

  /// What joining means, for someone who is not in the circle yet: nothing about its members.
  struct Card: Codable, Equatable {
    let inviteId: Int
    var circleName = ""
    var avatar: String?
    var ownerName: String?
    var members = 0
    var expiresAt: String?
    var defaultScopes: [String] = []
    var shares: [Share] = []
    var consentOk = true
    var rulesOk = true
    var canMute = false

    struct Share: Codable, Equatable, Hashable {
      let scope: String
      let label: String
    }

    enum CodingKeys: String, CodingKey {
      case avatar, members, shares
      case inviteId = "invite_id", circleName = "circle_name", ownerName = "owner_name", expiresAt = "expires_at"
      case defaultScopes = "default_scopes", consentOk = "consent_ok", rulesOk = "rules_ok", canMute = "can_mute"
    }

    init(from d: Decoder) throws {
      let c = try d.container(keyedBy: CodingKeys.self)
      inviteId = try c.decode(Int.self, forKey: .inviteId)
      circleName = c.opt(.circleName) ?? ""
      avatar = c.opt(.avatar)
      ownerName = c.opt(.ownerName)
      members = c.opt(.members) ?? 0
      expiresAt = c.opt(.expiresAt)
      defaultScopes = c.opt(.defaultScopes) ?? []
      shares = c.opt(.shares) ?? []
      consentOk = c.opt(.consentOk) ?? true
      rulesOk = c.opt(.rulesOk) ?? true
      canMute = c.opt(.canMute) ?? false
    }
  }

  enum CodingKeys: String, CodingKey {
    case id, type, kind, at, read, title, body, url, entries, invite
    case linkLabel = "link_label", circleId = "circle_id"
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    id = try c.decode(String.self, forKey: .id)
    type = c.opt(.type) ?? ""
    kind = c.opt(.kind)
    at = c.opt(.at)
    read = c.opt(.read) ?? false
    title = c.opt(.title) ?? ""
    body = c.opt(.body)
    url = c.opt(.url)
    linkLabel = c.opt(.linkLabel)
    circleId = c.opt(.circleId)
    entries = c.opt(.entries) ?? []
    invite = c.opt(.invite)
  }
}

/// One page of `GET /inbox`.
struct InboxPage: Decodable, Equatable {
  var items: [InboxItem] = []
  var unread = 0
  var more = false
  var next: String?

  enum CodingKeys: String, CodingKey { case items, unread, more, next }

  init(items: [InboxItem] = [], unread: Int = 0, more: Bool = false, next: String? = nil) {
    self.items = items
    self.unread = unread
    self.more = more
    self.next = next
  }

  init(from d: Decoder) throws {
    let c = try d.container(keyedBy: CodingKeys.self)
    items = c.opt(.items) ?? []
    unread = c.opt(.unread) ?? 0
    more = c.opt(.more) ?? false
    next = c.opt(.next)
  }
}

/// The document kept on the phone: what was last seen, and what was done offline and has not reached the server yet.
struct InboxCache: Codable, Equatable {
  var items: [InboxItem] = []
  var unread = 0
  var next: String?
  /// Marked read / hidden here, not on the server yet (sent before the next read; an item that left the list is no error).
  var pendingRead: [String] = []
  var pendingHidden: [String] = []
  var pendingAllRead = false
}

/// The pure part of the inbox, so it can be tested without a server.
enum InboxLogic {
  /// A first page over what is kept: the page is the truth for its range (an invite that was withdrawn goes), what was loaded
  /// further down stays when there is more than this page. What is still pending stays applied.
  static func mergeHead(_ cache: InboxCache, _ page: InboxPage) -> InboxCache {
    var out = cache
    var tail: [InboxItem] = []
    if page.more, let last = page.items.last {
      let shown = Set(page.items.map(\.id))
      tail = cache.items.filter { !shown.contains($0.id) && isOlder($0, than: last) }
    }
    out.items = page.items + tail
    out.next = tail.isEmpty ? page.next : cache.next
    out.unread = page.unread
    return applyPending(out)
  }

  /// A further page goes below what is kept.
  static func append(_ cache: InboxCache, _ page: InboxPage) -> InboxCache {
    var out = cache
    let known = Set(cache.items.map(\.id))
    out.items += page.items.filter { !known.contains($0.id) }
    out.next = page.next
    out.unread = page.unread
    return applyPending(out)
  }

  /// `read` and the count as they will be once the pending changes arrive.
  static func applyPending(_ cache: InboxCache) -> InboxCache {
    var out = cache
    let hidden = Set(cache.pendingHidden)
    let read = Set(cache.pendingRead)
    for item in out.items where !item.read && (cache.pendingAllRead || hidden.contains(item.id) || read.contains(item.id)) {
      out.unread = max(0, out.unread - 1)
    }
    out.items = out.items.filter { !hidden.contains($0.id) }
    for i in out.items.indices where cache.pendingAllRead || read.contains(out.items[i].id) { out.items[i].read = true }
    if cache.pendingAllRead { out.unread = 0 }
    return out
  }

  /// Mark one read here (nothing happens when it is read already). True when it changed.
  @discardableResult
  static func markRead(_ cache: inout InboxCache, _ id: String) -> Bool {
    guard let i = cache.items.firstIndex(where: { $0.id == id }), !cache.items[i].read else { return false }
    cache.items[i].read = true
    cache.unread = max(0, cache.unread - 1)
    return true
  }

  static func markAllRead(_ cache: inout InboxCache) {
    for i in cache.items.indices { cache.items[i].read = true }
    cache.unread = 0
  }

  /// Take one out of the list here; an unread one also leaves the count (the server counts a hidden item as read).
  static func remove(_ cache: inout InboxCache, _ id: String) {
    guard let i = cache.items.firstIndex(where: { $0.id == id }) else { return }
    if !cache.items[i].read { cache.unread = max(0, cache.unread - 1) }
    cache.items.remove(at: i)
  }

  private static func isOlder(_ a: InboxItem, than b: InboxItem) -> Bool {
    guard let x = a.at.flatMap(FeedFormat.parseDate), let y = b.at.flatMap(FeedFormat.parseDate) else { return false }
    return x < y
  }

  /// 「3 小时前」 within a day, then 昨天 / the date.
  static func when(_ iso: String?, now: Date = Date()) -> String {
    guard let iso, let d = FeedFormat.parseDate(iso) else { return "" }
    let secs = now.timeIntervalSince(d)
    if secs < 60 { return tr("刚刚", "Just now") }
    if secs < 3600 { return tr("\(Int(secs / 60)) 分钟前", "\(Int(secs / 60)) min ago") }
    if secs < 86_400 { return tr("\(Int(secs / 3600)) 小时前", "\(Int(secs / 3600)) h ago") }
    return FeedFormat.day(FeedFormat.dayFormatter.string(from: d), now: now)
  }
}

/// Where a link or a tapped push points in the inbox.
struct InboxLink: Equatable {
  /// The item to scroll to; nil = just the list.
  var item: String?

  /// `/app/?tab=me&inbox=invite:3` (an invite's push), `/app/#notifications` (a merged push), `/app/?whatsnew=1`.
  static func parse(_ raw: String) -> InboxLink? {
    let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let c = URLComponents(string: s.hasPrefix("/") ? "https://x\(s)" : s) else { return nil }
    if c.scheme?.lowercased() == "watch2whoop" { return nil }
    if c.fragment == "notifications" { return InboxLink(item: nil) }
    let q = Dictionary((c.queryItems ?? []).map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { a, _ in a })
    if let id = q["inbox"], !id.isEmpty { return InboxLink(item: id) }
    if q["whatsnew"] != nil { return InboxLink(item: nil) }
    return nil
  }

  /// What a tapped push opens (payload of notify/core.py): a post keeps opening the post; a message that lives in the inbox
  /// opens at its row.
  static func pushTarget(url: String?, kind: String?, nid: Int?) -> String? {
    if let kind, let nid, inInbox(kind) { return "/app/?tab=me&inbox=notification:\(nid)" }
    return url
  }

  private static func inInbox(_ kind: String) -> Bool {
    kind == "circle.joined" || kind == "circle.invite_accepted" || kind.hasPrefix("system") || kind.hasPrefix("ai_card")
  }
}
