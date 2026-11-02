import Combine
import Foundation
import UIKit

/// 我 → 消息 for the signed-in account (flag `inbox`; #359). Read: the cache first (`inbox:list`, so it opens offline), then the server.
/// Read marks and hiding change the screen at once and are sent when there is a connection (kept in the same document, not in
/// the offline queue: an item that left the list meanwhile is no failure to show anyone). Joining and ignoring are online-only
/// (docs/ios-native-app.md section 5.5). One store per account session, so the row in 我, the tab's badge and the page agree.
@MainActor
final class InboxStore: ObservableObject {
  static let flag = "inbox"
  static let cacheKey = "inbox:list"

  @Published private(set) var cache = InboxCache()
  @Published private(set) var refreshing = false
  @Published private(set) var offline = false
  @Published var toast: String?
  /// An item a push or a link asked for: the page scrolls to it.
  @Published var focus: String?

  let session: AccountSession
  private var lastRefresh: Date?
  private var loadingMore = false
  private var watchers: Set<AnyCancellable> = []
  private var poller: Task<Void, Never>?

  private static var stores: [ObjectIdentifier: InboxStore] = [:]

  /// The account's store (made on first use, dropped with the session). Starts keeping the badge up to date.
  static func shared(for session: AccountSession) -> InboxStore {
    let key = ObjectIdentifier(session)
    if let s = stores[key] { return s }
    stores = stores.filter { $0.key == key }   // a new session (sign-out / another account): forget the old one
    let s = InboxStore(session: session)
    stores[key] = s
    s.start()
    return s
  }

  init(session: AccountSession) {
    self.session = session
    if let d = try? session.db.doc(Self.cacheKey), let c = try? JSONDecoder.api.decode(InboxCache.self, from: d.body) { cache = c }
  }

  deinit { poller?.cancel() }

  var on: Bool { session.flags.contains(Self.flag) }
  var items: [InboxItem] { on ? cache.items : [] }
  /// The row in 我 and the red badge on the tab.
  var unread: Int { on ? cache.unread : 0 }

  private func start() {
    session.$flags.map { $0.contains(Self.flag) }.removeDuplicates().sink { [weak self] isOn in
      if isOn { Task { await self?.refresh(force: true) } }
    }.store(in: &watchers)
    NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification).sink { [weak self] _ in
      Task { await self?.refresh() }
    }.store(in: &watchers)
    NotificationCenter.default.publisher(for: NotificationTaps.pushArrived).sink { [weak self] _ in
      Task { await self?.refresh(force: true) }
    }.store(in: &watchers)
    poller = Task { [weak self] in
      while !Task.isCancelled {
        try? await Task.sleep(nanoseconds: 300_000_000_000)   // the badge keeps up while the app stays open
        guard UIApplication.shared.applicationState == .active else { continue }
        await self?.refresh()
      }
    }
  }

  private func save() {
    guard let data = try? JSONEncoder.api.encode(cache) else { return }
    try? session.db.saveDoc(Self.cacheKey, json: data)
  }

  // MARK: - reading

  /// The first page, after sending what was done offline. At most every 20 seconds unless `force` (pull to refresh, a push).
  func refresh(force: Bool = false) async {
    guard on, !refreshing else { return }
    if !force, let t = lastRefresh, Date().timeIntervalSince(t) < 20 { return }
    refreshing = true
    defer { refreshing = false }
    lastRefresh = Date()
    await flush()
    do {
      let page = try await session.api.send(.get(Endpoint.Inbox.list), as: InboxPage.self)
      cache = InboxLogic.mergeHead(cache, page)
      offline = false
      save()
    } catch APIError.network {
      offline = true
    } catch APIError.notFound {      // the flag went off for this person
      cache = InboxCache()
      save()
    } catch {}
  }

  /// The next page when the list is scrolled to its end.
  func loadMore() async {
    guard on, let next = cache.next, !loadingMore else { return }
    loadingMore = true
    defer { loadingMore = false }
    guard let page = try? await session.api.send(.get(Endpoint.Inbox.list, ["before": next]), as: InboxPage.self) else { return }
    cache = InboxLogic.append(cache, page)
    save()
  }

  // MARK: - read and hidden: here first, the server when it can be reached

  func markRead(_ item: InboxItem) {
    guard InboxLogic.markRead(&cache, item.id) else { return }
    cache.pendingRead.append(item.id)
    save()
    Task { await flush() }
  }

  func markRead(id: String) {
    guard let item = cache.items.first(where: { $0.id == id }) else { return }
    markRead(item)
  }

  func markAllRead() {
    InboxLogic.markAllRead(&cache)
    cache.pendingAllRead = true
    cache.pendingRead = []
    save()
    Task { await flush() }
  }

  /// Swiped away. Hiding an invite answers nothing: it stays open and on the Friends page.
  func hide(_ item: InboxItem) {
    InboxLogic.remove(&cache, item.id)
    cache.pendingRead.removeAll { $0 == item.id }
    cache.pendingHidden.append(item.id)
    save()
    Task { await flush() }
  }

  /// Sends what is pending; what could not be sent stays for next time.
  private func flush() async {
    let api = session.api
    if cache.pendingAllRead {
      guard (try? await api.data(try .json(.post, Endpoint.Inbox.read, ["all": true], idempotent: true))) != nil else { return }
      cache.pendingAllRead = false
      save()
    }
    let ids = cache.pendingRead
    if !ids.isEmpty {
      guard (try? await api.data(try .json(.post, Endpoint.Inbox.read, ["ids": ids], idempotent: true))) != nil else { return }
      cache.pendingRead.removeAll { ids.contains($0) }
      save()
    }
    for id in cache.pendingHidden {
      do {
        _ = try await api.data(APIRequest(method: .delete, path: Endpoint.Inbox.item(id)))
      } catch APIError.notFound {     // it left the list by itself
      } catch {
        return
      }
      cache.pendingHidden.removeAll { $0 == id }
      save()
    }
  }

  // MARK: - invites (online-only)

  enum Joining: Equatable {
    case joined
    case needsConsent
    case needsRules
    case gone
    case refused(String)
  }

  /// One step: joins with what the card listed. A 409 is either the consent or a refusal (the circle is full): the list knows
  /// which, so it is read again rather than guessing from the text.
  func accept(_ item: InboxItem) async -> Joining {
    guard let card = item.invite else { return .gone }
    if !card.consentOk { return .needsConsent }
    do {
      _ = try await session.api.data(APIRequest(method: .post, path: Endpoint.Inbox.accept(card.inviteId), idempotent: true))
      offline = false
      drop(item)
      return .joined
    } catch APIError.notFound {
      drop(item)
      return .gone
    } catch APIError.conflict(let message) {
      await refresh(force: true)
      if cache.items.first(where: { $0.id == item.id })?.invite?.consentOk == false { return .needsConsent }
      return .refused(message ?? tr("没能加入", "Couldn't join"))
    } catch APIError.http(428, _) {
      return .needsRules
    } catch let e as APIError {
      if case .network = e { offline = true }
      return .refused(e.userMessage)
    } catch {
      return .refused(tr("出错了", "Something went wrong"))
    }
  }

  /// After the consent sheet: the card no longer asks for it.
  func consentGiven() {
    for i in cache.items.indices where cache.items[i].invite != nil { cache.items[i].invite?.consentOk = true }
    save()
  }

  func ignore(_ item: InboxItem) async {
    guard let card = item.invite else { return }
    await answer(item, Endpoint.Inbox.ignore(card.inviteId))
  }

  /// 「不再接收此人的邀请」: ignores this one and everything else from the same person; they are not told.
  func mute(_ item: InboxItem) async {
    guard let card = item.invite else { return }
    await answer(item, Endpoint.Inbox.mute(card.inviteId))
    await refresh(force: true)
  }

  private func answer(_ item: InboxItem, _ path: String) async {
    do {
      _ = try await session.api.data(APIRequest(method: .post, path: path, idempotent: true))
      drop(item)
    } catch APIError.notFound {
      drop(item)
    } catch let e as APIError {
      if case .network = e { offline = true }
      toast = e.userMessage
    } catch {}
  }

  private func drop(_ item: InboxItem) {
    InboxLogic.remove(&cache, item.id)
    save()
  }
}
