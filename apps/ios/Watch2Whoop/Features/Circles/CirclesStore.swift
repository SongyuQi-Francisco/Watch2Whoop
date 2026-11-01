import Foundation
import GRDB

/// The circles of the signed-in account: what the Friends tab and the circle summary card show.
///
/// - **Read**: cache first, then the server. Every answer is kept in the local database's `docs` table
///   (`circles:list`, `circles:overview:<scope>`, `circles:feed:<scope>`, `circles:board:<board>:<scope>`,
///   `circles:comments:<post>`), so the tab opens on the last state, offline included, and a refresh only replaces what
///   came back (the feed merges its first page into the cached posts, `FeedLogic.mergeHead`).
/// - **Write**: likes and comments go through the offline queue (kinds `circle.like` / `circle.comment`, one group per
///   post): the screen changes at once, the queue sends when there is a connection, and what is still queued is laid
///   over the server's answer (`FeedLogic.overlay`). A like is last-write-wins (the queue keeps only the newest state).
///   Everything else (create, join, invite, leave, share settings, cheers, avatars) is online-only
///   (docs/ios-native-app.md section 5.5): offline it says so instead of pretending.
/// - **Privacy**: only what the server sends is shown; it decides at every read what each person shares with the viewer.
///
/// One store per account session (`CirclesStore.shared(for:)`), so the tab and the Today page's summary card agree.
@MainActor
final class CirclesStore: ObservableObject {
  enum Mode: String, CaseIterable { case feed, members, board }

  @Published private(set) var list: CirclesList?
  @Published private(set) var overview: MemberOverview?
  @Published private(set) var feed = FeedCache()
  @Published private(set) var board: Leaderboard?
  @Published private(set) var comments: [Int: [Comment]] = [:]
  /// Queued circle writes (likes, comments), kept up to date from the database.
  @Published private(set) var pending: [OutboxItem] = []
  /// ❤️ and comments on my posts since I last opened the feed (the red dot).
  @Published private(set) var unread = 0
  @Published private(set) var refreshing = false
  @Published private(set) var loadingMore = false
  @Published private(set) var offline = false
  /// The first load failed and there is no cache to show.
  @Published private(set) var loadError: String?
  /// A short message for the toast (an action that did not go through).
  @Published var toast: String?
  /// The circle the tab is narrowed to (flag circles_by_circle); nil = all circles.
  @Published private(set) var circle: Int?
  @Published var mode: Mode
  @Published private(set) var boardName: String
  /// A post a notification asked for: the feed scrolls there and opens its comments.
  @Published var focusPost: Int?
  /// The community rules sheet is up (flag circle_rules): accepting runs `afterRules`.
  @Published var showRules = false
  @Published private(set) var rulesHost: String?
  /// The 「已屏蔽」 list (loaded when that page opens).
  @Published private(set) var blocked: [BlockedPerson]?
  private var afterRules: (() -> Void)?

  let session: AccountSession
  private var observer: Task<Void, Never>?
  private var lastRefresh: Date?
  private var draining = false

  private static var stores: [ObjectIdentifier: CirclesStore] = [:]

  /// The account's store (made on first use, dropped with the session).
  static func shared(for session: AccountSession) -> CirclesStore {
    let key = ObjectIdentifier(session)
    if let s = stores[key] { return s }
    stores = stores.filter { $0.key == key }   // a new session (sign-out / another account): forget the old one
    let s = CirclesStore(session: session)
    stores[key] = s
    return s
  }

  init(session: AccountSession) {
    self.session = session
    let d = UserDefaults.standard
    circle = d.object(forKey: Self.circleKey) as? Int
    boardName = d.string(forKey: Self.boardKey) ?? "body"
    mode = session.flags.contains(Self.feedFlag) ? .feed : .members
    if let m = d.string(forKey: Self.modeKey).flatMap(Mode.init(rawValue:)) { mode = m }
    #if DEBUG
    // simulator screenshots: SIMCTL_CHILD_W2W_DEV_CIRCLES=members|board|feed[:circle id] (Debug builds only)
    if let dev = ProcessInfo.processInfo.environment["W2W_DEV_CIRCLES"] {
      let parts = dev.split(separator: ":")
      if let m = parts.first.flatMap({ Mode(rawValue: String($0)) }) { mode = m }
      circle = parts.count > 1 ? Int(parts[1]) : nil
    }
    #endif
    loadCache()
    watchQueue()
  }

  deinit { observer?.cancel() }

  // MARK: - flags (rollout flags from /v1/web/me; the server answers 404 while one is off)

  static let feedFlag = "circle_feed"
  var feedOn: Bool { session.flags.contains(Self.feedFlag) }
  var byCircle: Bool { session.flags.contains("circles_by_circle") }
  var avatarsOn: Bool { session.flags.contains("avatars") }
  var remarksOn: Bool { session.flags.contains("member_remarks") }
  var prefsOn: Bool { list?.prefsOn ?? false }
  /// All circles mixed: no leaderboard (people of different circles don't know each other).
  var boardOn: Bool { !byCircle || circle != nil }
  var modes: [Mode] { [feedOn ? .feed : nil, .members, boardOn ? .board : nil].compactMap { $0 } }
  var hasCircles: Bool { (list?.circles.isEmpty == false) || overview?.hasCircles == true }

  /// Me as a post / comment author (queued comments are drawn with it).
  var me: PostUser {
    PostUser(id: session.config.userId ?? 0, name: overview?.me?.displayName ?? session.displayName ?? tr("我", "Me"),
             isSelf: true, avatar: overview?.me?.avatar)
  }

  /// The feed as shown: the cached posts with the queued likes and comments on top.
  var posts: [Post] { FeedLogic.overlay(feed.posts, pending: pending, me: me, commentPreview: feed.commentPreview) }

  /// A post's comments as shown: the server's, then the queued ones.
  func commentList(_ post: Int) -> [Comment]? {
    let queued = FeedLogic.queuedComments(pending, post: post, me: me)
    guard let c = comments[post] else { return queued.isEmpty ? nil : queued }
    return c + queued
  }

  /// The switcher chips: every circle that is not archived.
  var chips: [CircleChip] { (overview?.circles ?? []).filter { !$0.archived } }

  // MARK: - cache

  private static let circleKey = "w2w.circles.circle.v1"
  private static let boardKey = "w2w.circles.board.v1"
  private static let modeKey = "w2w.circles.mode.v1"

  enum Key {
    static let list = "circles:list"
    static func overview(_ c: Int?) -> String { "circles:overview:\(c.map(String.init) ?? "all")" }
    static func feed(_ c: Int?) -> String { "circles:feed:\(c.map(String.init) ?? "all")" }
    static func board(_ b: String, _ c: Int?) -> String { "circles:board:\(b):\(c.map(String.init) ?? "all")" }
    static func comments(_ post: Int) -> String { "circles:comments:\(post)" }
    static let unread = "circles:unread"
  }

  private func cached<T: Decodable>(_ key: String, as: T.Type) -> T? {
    guard let d = try? session.db.doc(key) else { return nil }
    return try? JSONDecoder.api.decode(T.self, from: d.body)
  }

  private func store<T: Encodable>(_ key: String, _ value: T) {
    guard let data = try? JSONEncoder.api.encode(value) else { return }
    try? session.db.saveDoc(key, json: data)
  }

  private func loadCache() {
    list = cached(Key.list, as: CirclesList.self)
    overview = cached(Key.overview(circle), as: MemberOverview.self)
    feed = cached(Key.feed(circle), as: FeedCache.self) ?? FeedCache()
    board = cached(Key.board(boardName, circle), as: Leaderboard.self)
    unread = cached(Key.unread, as: Int.self) ?? 0
  }

  private func watchQueue() {
    let kinds = [FeedLogic.likeKind, FeedLogic.commentKind]
    let obs = ValueObservation.tracking { db in
      try OutboxItem.filter(kinds.contains(Column("kind"))).order(Column("id")).fetchAll(db)
    }.removeDuplicates()
    let writer = session.db.writer
    observer = Task { [weak self] in
      do {
        for try await items in obs.values(in: writer) {
          await self?.queueChanged(items)
        }
      } catch {}
    }
  }

  /// Queued writes changed: a comment that left the queue without failing is on the server now, so that post's comments
  /// and the feed are fetched again (whoever drained the queue: this store or the sync coordinator).
  private func queueChanged(_ items: [OutboxItem]) async {
    let gone = pending.filter { old in old.kind == FeedLogic.commentKind && !items.contains { $0.id == old.id } }
    pending = items
    let posts = Set(gone.compactMap { FeedLogic.postId($0.path) })
    guard !posts.isEmpty else { return }
    for p in posts where comments[p] != nil { await loadComments(p) }
    await refreshFeed()
  }

  // MARK: - reading

  /// Everything the tab shows, cached first; `force` = pull to refresh (else at most every 20 seconds).
  func refresh(force: Bool = false) async {
    if !force, let t = lastRefresh, Date().timeIntervalSince(t) < 20 { return }
    guard !refreshing else { return }
    refreshing = true
    defer { refreshing = false }
    lastRefresh = Date()
    await drain()
    async let a: Void = refreshList()
    async let b: Void = refreshOverview()
    async let c: Void = feedOn ? refreshFeed() : ()
    async let d: Void = feedOn ? refreshUnread() : ()
    _ = await (a, b, c, d)
    if mode == .board && boardOn { await refreshBoard() }
  }

  /// One GET kept as a document; nil when it failed (the cache stays on screen).
  private func fetch<T: Decodable>(_ r: APIRequest, key: String?, as: T.Type) async -> T? {
    do {
      let data = try await session.api.data(r)
      let value = try JSONDecoder.api.decode(T.self, from: data)
      if let key { try? session.db.saveDoc(key, json: data) }
      offline = false
      loadError = nil
      return value
    } catch let e as APIError {
      failed(e)
    } catch {
      loadError = tr("服务器的回答看不懂", "Unexpected answer from the server")
    }
    return nil
  }

  private func failed(_ e: APIError) {
    switch e {
    case .network: offline = true
    case .notFound: break   // a flag that is off for this user: that block just stays empty
    default: loadError = e.userMessage
    }
  }

  func refreshList() async {
    if let l = await fetch(.get(Endpoint.Circles.list), key: Key.list, as: CirclesList.self) { list = l }
  }

  func refreshOverview() async {
    let scope = circle
    let r = APIRequest.get(Endpoint.Circles.overview, scope.map { ["circle": String($0)] } ?? [:])
    guard let o = await fetch(r, key: Key.overview(scope), as: MemberOverview.self), scope == circle else { return }
    overview = o
    // the circle I was looking at is gone (left / dissolved / archived): back to all circles
    if let c = circle, let chips = o.circles, !chips.contains(where: { $0.id == c && !$0.archived }) { select(nil) }
  }

  func refreshFeed() async {
    guard feedOn else { return }
    let scope = circle
    var q: [String: String] = [:]
    if let scope { q["circle"] = String(scope) }
    guard let page = await fetch(.get(Endpoint.Circles.feed, q), key: nil, as: FeedPage.self), scope == circle else { return }
    feed = FeedLogic.mergeHead(feed, page)
    store(Key.feed(scope), feed)
  }

  /// The next page when the list is scrolled near its end.
  func loadMore() async {
    guard feedOn, let next = feed.next, !loadingMore else { return }
    loadingMore = true
    defer { loadingMore = false }
    let scope = circle
    var q = ["before": next]
    if let scope { q["circle"] = String(scope) }
    guard let page = await fetch(.get(Endpoint.Circles.feed, q), key: nil, as: FeedPage.self), scope == circle else { return }
    feed = FeedLogic.append(feed, page)
    store(Key.feed(scope), feed)
  }

  func refreshBoard() async {
    guard boardOn else { return }
    let scope = circle, name = boardName
    var q = ["board": name]
    if let scope { q["circle"] = String(scope) }
    if let b = await fetch(.get(Endpoint.Circles.leaderboard, q), key: Key.board(name, scope), as: Leaderboard.self),
       scope == circle, name == boardName {
      board = b
    }
  }

  func refreshUnread() async {
    guard feedOn, let data = try? await session.api.data(.get(Endpoint.Circles.unread)),
          let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
    unread = (o["count"] as? NSNumber)?.intValue ?? 0
    store(Key.unread, unread)
  }

  /// The feed was looked at: the red dot goes (the server starts counting again from now).
  func markSeen() {
    guard feedOn else { return }
    unread = 0
    store(Key.unread, 0)
    let api = session.api
    Task { _ = try? await api.data(APIRequest(method: .post, path: Endpoint.Circles.seen, idempotent: true)) }
  }

  func loadComments(_ post: Int) async {
    if comments[post] == nil { comments[post] = cached(Key.comments(post), as: [Comment].self) }
    var q: [String: String] = [:]
    if let c = circle { q["circle"] = String(c) }
    struct Answer: Decodable { var comments: [Comment] }
    if let a = await fetch(.get(Endpoint.Circles.comments(post), q), key: nil, as: Answer.self) {
      comments[post] = a.comments
      store(Key.comments(post), a.comments)
    } else if comments[post] == nil {
      comments[post] = []
    }
  }

  // MARK: - narrowing

  func select(_ id: Int?) {
    guard id != circle else { return }
    circle = id
    UserDefaults.standard.set(id, forKey: Self.circleKey)
    overview = cached(Key.overview(id), as: MemberOverview.self) ?? overview
    feed = cached(Key.feed(id), as: FeedCache.self) ?? FeedCache()
    board = cached(Key.board(boardName, id), as: Leaderboard.self)
    comments = [:]
    if mode == .board && !boardOn { setMode(.members) }
    Task {
      await refreshOverview()
      await refreshFeed()
      if mode == .board { await refreshBoard() }
    }
  }

  func setMode(_ m: Mode) {
    mode = m
    UserDefaults.standard.set(m.rawValue, forKey: Self.modeKey)
    if m == .board { Task { await refreshBoard() } }
    if m == .feed { markSeen() }
  }

  func setBoard(_ name: String) {
    boardName = name
    UserDefaults.standard.set(name, forKey: Self.boardKey)
    board = cached(Key.board(name, circle), as: Leaderboard.self)
    Task { await refreshBoard() }
  }

  // MARK: - likes and comments (offline queue)

  private var queue: OfflineQueue { session.sync.queue }

  /// ❤️ / un-❤️: the heart changes now; the queue sends the newest state when it can.
  func setLike(_ post: Post, _ mine: Bool) {
    guard post.likes.mine != mine else { return }
    if let i = feed.posts.firstIndex(where: { $0.id == post.id }), feed.posts[i].likes.mine != mine {
      feed.posts[i].likes = Post.Likes(count: max(0, feed.posts[i].likes.count + (mine ? 1 : -1)), mine: mine)
      store(Key.feed(circle), feed)
    }
    let group = FeedLogic.likeGroup(post.id)
    do {
      try queue.supersede(group: group)
      try queue.enqueue(kind: FeedLogic.likeKind, group: group, method: mine ? .put : .delete,
                        path: Endpoint.Circles.like(post.id), body: nil)
    } catch {
      toast = tr("没存下来，再试一次", "Couldn't save that, try again")
    }
    Task { await drain() }
  }

  /// A comment: shown at once (marked as sending), sent by the queue.
  func addComment(_ post: Int, _ text: String) {
    let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !t.isEmpty else { return }
    // the server answers a resend with the same client_id (after a timeout) with the first comment, not a second one
    let clientId = UUID().uuidString.lowercased()
    guard let body = try? JSONEncoder.api.encode(["body": t, "client_id": clientId]) else { return }
    do {
      try queue.enqueue(kind: FeedLogic.commentKind, group: FeedLogic.commentGroup(post), method: .post,
                        path: Endpoint.Circles.comments(post), body: body, clientId: clientId)
    } catch {
      toast = tr("没存下来，再试一次", "Couldn't save that, try again")
      return
    }
    Task { await drain() }
  }

  /// A queued comment the server refused (or one still waiting): send it again, or drop it.
  func retryQueued(_ comment: Comment) {
    guard comment.id < 0 else { return }
    try? queue.retry(Int64(-comment.id))
    Task { await drain() }
  }

  func discardQueued(_ comment: Comment) {
    guard comment.id < 0 else { return }
    try? queue.discard(Int64(-comment.id))
  }

  /// Sends what the queue has (one drain at a time from here; the queue itself never sends an entry twice).
  func drain() async {
    guard !draining else { return }
    draining = true
    defer { draining = false }
    _ = try? await queue.drain(send: OfflineQueue.apiSender(session.api))
  }

  func deleteComment(_ c: Comment, post: Int) async {
    guard c.id > 0 else { discardQueued(c); return }
    do {
      _ = try await session.api.data(APIRequest(method: .delete, path: Endpoint.Circles.comment(c.id)))
      comments[post]?.removeAll { $0.id == c.id }
      if let i = feed.posts.firstIndex(where: { $0.id == post }) {
        feed.posts[i].commentCount = max(0, feed.posts[i].commentCount - 1)
        feed.posts[i].previewComments.removeAll { $0.id == c.id }
        store(Key.feed(circle), feed)
      }
    } catch let e as APIError {
      toast = e.userMessage
    } catch {}
  }

  // MARK: - moderation (block, report, community rules; App Store guideline 1.2). Online only.

  /// Report a post, a comment or a person's profile with one of `ReportReason.all`. The server hides it for me at once;
  /// here it goes from the screen too (a profile report also blocks the person: everything is reloaded).
  func report(_ kind: ReportKind, _ id: Int, reason: String, post: Int? = nil) async {
    struct Body: Encodable { let kind: String; let id: Int; let reason: String }
    let ok = await write {
      try await self.session.api.data(try .json(.post, Endpoint.Circles.reports, Body(kind: kind.rawValue, id: id, reason: reason), idempotent: false))
    } != nil
    guard ok else { return }
    toast = tr("已举报，我们会尽快处理", "Reported. We'll look at it soon")
    switch kind {
    case .post:
      feed.posts.removeAll { $0.id == id }
      store(Key.feed(circle), feed)
    case .comment:
      if let post { comments[post]?.removeAll { $0.id == id } }
      for i in feed.posts.indices where feed.posts[i].previewComments.contains(where: { $0.id == id }) {
        feed.posts[i].previewComments.removeAll { $0.id == id }
        feed.posts[i].commentCount = max(0, feed.posts[i].commentCount - 1)
      }
      store(Key.feed(circle), feed)
    case .profile:
      await reloadAll()
    }
  }

  /// Block someone (across all circles): their posts, comments, ❤️, card and board row go, and theirs mine.
  func block(_ user: Int) async {
    let ok = await write { try await self.session.api.data(APIRequest(method: .put, path: Endpoint.Circles.block(user))) } != nil
    guard ok else { return }
    toast = tr("已屏蔽", "Blocked")
    feed.posts.removeAll { $0.user.id == user }
    for i in feed.posts.indices { feed.posts[i].previewComments.removeAll { $0.user.id == user } }
    for k in comments.keys { comments[k]?.removeAll { $0.user.id == user } }
    store(Key.feed(circle), feed)
    await reloadAll()
  }

  func unblock(_ user: Int) async {
    let ok = await write { try await self.session.api.data(APIRequest(method: .delete, path: Endpoint.Circles.block(user))) } != nil
    guard ok else { return }
    blocked?.removeAll { $0.id == user }
    await reloadAll()
  }

  func loadBlocked() async {
    struct Answer: Decodable { var blocks: [BlockedPerson] }
    if let a = await fetch(.get(Endpoint.Circles.blocks), key: nil, as: Answer.self) { blocked = a.blocks }
  }

  /// Runs `action` now, or after the community rules were accepted when the server asks for them first. `host`: the view
  /// that shows the rules sheet (each screen that can ask presents its own, `RulesGate`).
  func requireRules(host: String, _ action: @escaping () -> Void) {
    Task {
      if list == nil { await refreshList() }
      guard list?.rulesNeeded == true else { action(); return }
      afterRules = action
      rulesHost = host
      showRules = true
    }
  }

  func loadRules() async -> CircleRules? {
    await fetch(.get(Endpoint.Circles.rules), key: nil, as: CircleRules.self)
  }

  /// "Accept": recorded on the server, then the action that asked for the rules goes ahead.
  func acceptRules(_ version: String) async -> Bool {
    let ok = await write { try await self.session.api.data(try .json(.post, Endpoint.Circles.rulesAccept, ["version": version], idempotent: true)) } != nil
    guard ok else { return false }
    list?.rules.accepted = true
    if let l = list { store(Key.list, l) }
    showRules = false
    let next = afterRules
    afterRules = nil
    next?()
    return true
  }

  func declineRules() {
    afterRules = nil
    showRules = false
  }

  // MARK: - online-only actions (create, join, invite, manage, cheers, avatars)

  /// Runs an online write; on failure the toast says why and nil comes back.
  @discardableResult
  private func write<T>(_ op: () async throws -> T) async -> T? {
    do {
      let v = try await op()
      offline = false
      return v
    } catch let e as APIError {
      if case .network = e { offline = true }
      toast = e.userMessage
    } catch {
      toast = tr("出错了", "Something went wrong")
    }
    return nil
  }

  /// After a change to circles or members: everything the tab shows may differ.
  func reloadAll() async {
    lastRefresh = nil
    await refreshList()
    await refreshOverview()
    await refreshFeed()
    if mode == .board { await refreshBoard() }
  }

  func create(name: String, scopes: [String]) async -> CreatedCircle? {
    struct Body: Encodable { let name: String; let scopes: [String] }
    let r = await write { try await self.session.api.send(try .json(.post, Endpoint.Circles.create, Body(name: name, scopes: scopes), idempotent: false), as: CreatedCircle.self) }
    if r != nil { await reloadAll() }
    return r
  }

  /// A new invite code: `days` (one of limits.inviteDays) or nil = never expires; `reset` voids the live ones first.
  func mintInvite(_ circle: Int, days: Int?, reset: Bool = false) async -> MintedInvite? {
    var body: [String: AnyEncodable] = ["reset": AnyEncodable(reset)]
    if let days { body["days"] = AnyEncodable(days) } else { body["permanent"] = AnyEncodable(true) }
    let r = await write { try await self.session.api.send(try .json(.post, Endpoint.Circles.invite(circle), body, idempotent: false), as: MintedInvite.self) }
    if r != nil { await refreshList() }
    return r
  }

  func revokeInvite(_ circle: Int, _ invite: Int) async {
    if await write({ try await self.session.api.data(APIRequest(method: .delete, path: Endpoint.Circles.revokeInvite(circle, invite))) }) != nil {
      await refreshList()
    }
  }

  func preview(code: String) async -> Result<InvitePreview, APIError> {
    do {
      return .success(try await session.api.send(.get(Endpoint.Circles.preview, ["code": code]), as: InvitePreview.self))
    } catch let e as APIError {
      return .failure(e)
    } catch {
      return .failure(.decoding(""))
    }
  }

  /// Join with a code, or accept an invite addressed to me.
  func join(code: String?, inviteId: Int?, scopes: [String]) async -> Bool {
    var body: [String: AnyEncodable] = ["scopes": AnyEncodable(scopes)]
    if let code { body["code"] = AnyEncodable(code) }
    if let inviteId { body["invite_id"] = AnyEncodable(inviteId) }
    let ok = await write { try await self.session.api.data(try .json(.post, Endpoint.Circles.join, body, idempotent: false)) } != nil
    if ok { await reloadAll() }
    return ok
  }

  func decline(_ invite: Int) async {
    if await write({ try await self.session.api.data(APIRequest(method: .delete, path: Endpoint.Circles.decline(invite))) }) != nil {
      await refreshList()
    }
  }

  func declineAll() async {
    if await write({ try await self.session.api.data(APIRequest(method: .delete, path: Endpoint.Circles.invites)) }) != nil {
      await refreshList()
    }
  }

  func setScopes(_ circle: Int, _ scopes: [String]) async -> Bool {
    let ok = await write { try await self.session.api.data(try .json(.patch, Endpoint.Circles.me(circle), ["scopes": scopes])) } != nil
    if ok { await reloadAll() }
    return ok
  }

  func rename(_ circle: Int, _ name: String) async -> Bool {
    let ok = await write { try await self.session.api.data(try .json(.patch, Endpoint.Circles.circle(circle), ["name": name])) } != nil
    if ok { await reloadAll() }
    return ok
  }

  func leave(_ circle: Int) async -> Bool {
    let ok = await write { try await self.session.api.data(APIRequest(method: .delete, path: Endpoint.Circles.me(circle))) } != nil
    if ok { if self.circle == circle { select(nil) }; await reloadAll() }
    return ok
  }

  func remove(_ member: Int, from circle: Int) async {
    if await write({ try await self.session.api.data(APIRequest(method: .delete, path: Endpoint.Circles.member(circle, member))) }) != nil {
      await reloadAll()
    }
  }

  func dissolve(_ circle: Int) async -> Bool {
    let ok = await write { try await self.session.api.data(APIRequest(method: .delete, path: Endpoint.Circles.circle(circle))) } != nil
    if ok { if self.circle == circle { select(nil) }; await reloadAll() }
    return ok
  }

  /// My own pin / mute / archive of a circle (flag circle_prefs): nothing changes for anyone else, sharing included.
  func setPrefs(_ circle: Int, pinned: Bool? = nil, muted: Bool? = nil, archived: Bool? = nil) async {
    var body: [String: Bool] = [:]
    if let pinned { body["pinned"] = pinned }
    if let muted { body["muted"] = muted }
    if let archived { body["archived"] = archived }
    if await write({ try await self.session.api.data(try .json(.put, Endpoint.Circles.prefs(circle), body)) }) != nil {
      await reloadAll()
    }
  }

  /// 👏 / 💤 / 💪 to a member, once a day each.
  func nudge(_ to: Int, kind: String) async {
    struct Body: Encodable { let to: Int; let kind: String }
    do {
      _ = try await session.api.data(try .json(.post, Endpoint.Circles.nudges, Body(to: to, kind: kind), idempotent: false))
      toast = tr("已发送", "Sent")
    } catch APIError.conflict {
      toast = tr("今天已经发过了", "Already sent today")
    } catch let e as APIError {
      toast = e.userMessage
      return
    } catch { return }
    markSent(to, kind)
  }

  private func markSent(_ to: Int, _ kind: String) {
    guard var o = overview, let i = o.members.firstIndex(where: { $0.id == to }) else { return }
    if !o.members[i].sent.contains(kind) { o.members[i].sent.append(kind) }
    overview = o
    store(Key.overview(circle), o)
  }

  /// What I call a co-member (only I see it); empty clears it (flag member_remarks).
  func setRemark(_ user: Int, _ remark: String) async {
    let r = remark.trimmingCharacters(in: .whitespacesAndNewlines)
    let path = Endpoint.Circles.remark(user)
    if await write({
      let req = r.isEmpty ? APIRequest(method: .delete, path: path) : try APIRequest.json(.put, path, ["remark": r])
      return try await self.session.api.data(req)
    }) != nil { await reloadAll() }
  }

  /// A JPEG for my picture (circle nil) or a circle's logo (owner); the server checks, crops and shrinks it again.
  func uploadAvatar(_ jpeg: Data, circle: Int?) async -> Bool {
    var r = APIRequest(method: .put, path: circle.map(Endpoint.Circles.circleAvatar) ?? Endpoint.Circles.myAvatar, body: jpeg)
    r.contentType = "image/jpeg"
    r.timeout = 60
    let ok = await write { try await self.session.api.data(r) } != nil
    if ok { await reloadAll() }
    return ok
  }

  func deleteAvatar(circle: Int?) async {
    let path = circle.map(Endpoint.Circles.circleAvatar) ?? Endpoint.Circles.myAvatar
    if await write({ try await self.session.api.data(APIRequest(method: .delete, path: path)) }) != nil { await reloadAll() }
  }
}
