import SwiftUI

/// Someone who shares something with me (`GET /v1/web/me` → `viewable`). The list is the server's answer, in memory only.
struct ViewablePerson: Identifiable, Equatable {
  let id: Int
  var displayName: String
  var remark: String?
  var avatar: String?

  var shownName: String {
    if let remark, !remark.isEmpty { return remark }
    return displayName
  }

  /// Everyone but me.
  static func parse(_ v: Any?) -> [ViewablePerson] {
    ((v as? [[String: Any]]) ?? []).compactMap { p in
      guard (p["self"] as? Bool) != true, let id = (p["id"] as? NSNumber)?.intValue else { return nil }
      return ViewablePerson(id: id, displayName: p["display_name"] as? String ?? "", remark: p["remark"] as? String,
                            avatar: p["avatar"] as? String)
    }
  }
}

/// The pages of someone else's read-only view, in the order of the Today page's tabs.
enum MemberTab: String, CaseIterable, Identifiable {
  case today, trends, sleep, food, record, health
  var id: String { rawValue }

  var title: String {
    switch self {
    case .today: return tr("今天", "Today")
    case .trends: return tr("趋势", "Trends")
    case .sleep: return tr("睡眠", "Sleep")
    case .food: return tr("饮食", "Food")
    case .record: return tr("记录", "Log")
    case .health: return tr("健康", "Health")
    }
  }
}

private struct MemberViewKey: EnvironmentKey {
  static let defaultValue: MemberViewStore? = nil
}

extension EnvironmentValues {
  /// Set inside someone else's read-only view (`MemberScreen`). Views shared with my own pages check it: they read this
  /// store instead of the local database, and draw no write controls and no AI entries.
  var memberView: MemberViewStore? {
    get { self[MemberViewKey.self] }
    set { self[MemberViewKey.self] = newValue }
  }
}

/// Someone else's data while I am looking at it, and nothing else (docs/ios-native-app.md section 4.2, 5.5).
///
/// **It stays in this object's memory.** There is no database, no `docs`, no offline queue here, and the requests go
/// through `APIClient.uncached()`; the object is dropped when the view closes. What I may see is decided by the server
/// alone, block by block: a 403 / 404, or a `{"code": "forbidden"}` block in an answer, means "not shared" and the block is
/// not drawn; the phone has no list of scopes of its own and computes nothing for someone else (their numbers are the
/// server's copy).
@MainActor
final class MemberViewStore: ObservableObject {
  enum State: Equatable { case loading, loaded, denied, offline, failed }
  enum Phase: Equatable { case loading, ready, notShared, offline, failed }

  let person: ViewablePerson
  /// The path segment of `/v1/users/{user}/…`.
  let user: String
  @Published private(set) var phase: Phase = .loading
  @Published private(set) var tabs: [MemberTab] = []
  /// Their local today (the today document's `date`).
  @Published private(set) var today: String?
  /// Bumped by a refresh: the pages load their documents again.
  @Published private(set) var refreshes = 0

  private let client: APIClient
  private var docs: [String: Any] = [:]
  private var states: [String: State] = [:]
  private var closed = false

  init(person: ViewablePerson, client: APIClient) {
    self.person = person
    self.user = String(person.id)
    self.client = client
  }

  // MARK: - what may be asked

  /// What the read-only view reads, under `/v1/users/{id}/`: GET only, always the viewed person's path, never `me`. It
  /// is not a permission check (that is the server's), it keeps the view from ever drawing my own data under someone
  /// else's name, or asking for something it does not show: no ECG, no AI settings, no writes.
  static let readable = [
    "today", "trends", "vo2max", "sleep/details", "sleep/nights", "sleep/night", "days", "strain-coach", "health-monitor", "memory",
    "reports", "journal/analysis", "journal/items", "journal/frequent", "journal", "training/day", "training/calendar", "training/week",
    "training/muscle-load", "exercises", "water", "recipes", "supplements", "labs/summary", "labs", "events", "explain",
  ]

  nonisolated static func allows(_ r: APIRequest, user: String) -> Bool {
    guard r.method == .get, r.body == nil else { return false }
    let prefix = "/v1/users/\(user)/"
    guard r.path.hasPrefix(prefix) else { return false }
    let rest = String(r.path.dropFirst(prefix.count))
    return readable.contains { rest == $0 || rest.hasPrefix($0 + "/") }
  }

  /// A GET under this person's path.
  func request(_ path: String, _ query: [String: String] = [:]) -> APIRequest { .get("/v1/users/\(user)/\(path)", query) }

  // MARK: - documents

  private nonisolated static func key(_ r: APIRequest) -> String {
    r.path + "?" + r.query.map { "\($0.name)=\($0.value ?? "")" }.joined(separator: "&")
  }

  /// `Any` answers crossing the task group; read once on the main actor.
  private struct Box: @unchecked Sendable { let value: Any }

  /// Fetches what is not loaded yet (`force`: everything, a refresh). A failed refresh keeps what is on screen.
  func load(_ requests: [APIRequest], force: Bool = false) async {
    guard !closed else { return }
    var todo: [APIRequest] = []
    for r in requests {
      let k = Self.key(r)
      guard Self.allows(r, user: user) else {
        states[k] = .denied
        continue
      }
      if force || (states[k] != .loaded && states[k] != .denied) { todo.append(r) }
    }
    if todo.isEmpty { return }
    let client = self.client
    let results = await withTaskGroup(of: (String, Result<Box, APIError>).self) { group in
      for r in todo {
        group.addTask {
          let k = Self.key(r)
          do {
            let d = try await client.data(r)
            guard let o = try? JSONSerialization.jsonObject(with: d, options: [.fragmentsAllowed]) else {
              return (k, .failure(.decoding("not JSON")))
            }
            return (k, .success(Box(value: o)))
          } catch let e as APIError {
            return (k, .failure(e))
          } catch {
            return (k, .failure(.network(-1)))
          }
        }
      }
      var out: [(String, Result<Box, APIError>)] = []
      for await r in group { out.append(r) }
      return out
    }
    guard !closed else { return }   // closed while the answers were on their way: they go nowhere
    for (k, result) in results {
      switch result {
      case .success(let box) where Self.isForbidden(box.value):
        docs[k] = nil
        states[k] = .denied
      case .success(let box):
        docs[k] = box.value
        states[k] = .loaded
      case .failure(.forbidden), .failure(.notFound):   // no share (any more), or a feature that is off
        docs[k] = nil
        states[k] = .denied
      case .failure(.network):
        states[k] = docs[k] == nil ? .offline : .loaded
      case .failure:
        states[k] = docs[k] == nil ? .failed : .loaded
      }
    }
    objectWillChange.send()
  }

  func state(_ r: APIRequest) -> State { states[Self.key(r)] ?? .loading }
  func json(_ r: APIRequest) -> [String: Any]? { docs[Self.key(r)] as? [String: Any] }
  func list(_ r: APIRequest) -> [[String: Any]]? { docs[Self.key(r)] as? [[String: Any]] }
  /// Answered and not refused (it may still be empty).
  func allowed(_ r: APIRequest) -> Bool { state(r) == .loaded }

  /// A block the server did not let through (`web/views.py` `forbidden`).
  nonisolated static func isForbidden(_ v: Any?) -> Bool { (v as? [String: Any])?["code"] as? String == "forbidden" }

  /// The document without its refused blocks, so a page sees "no such block".
  nonisolated static func withoutRefused(_ doc: [String: Any]?) -> [String: Any]? { doc?.filter { !isForbidden($0.value) } }

  /// Everything goes: the view closed.
  func close() {
    closed = true
    docs.removeAll()
    states.removeAll()
    tabs = []
    today = nil
    phase = .loading
  }

  // MARK: - opening: which pages have anything to show

  func todayRequest(_ day: String?) -> APIRequest { Endpoint.today(user, date: day) }
  var trendsRequest: APIRequest { Endpoint.trends(days: TodayParams.trendsDays, end: nil, user: user) }
  var nightsRequest: APIRequest { Endpoint.sleepNights(end: nil, user: user) }
  var memoryRequest: APIRequest { Endpoint.memory(user: user) }
  func waterRequest(_ day: String) -> APIRequest { request("water", ["date": day]) }
  func calendarRequest(_ month: String) -> APIRequest { request("training/calendar", ["month": month]) }
  var journalItemsRequest: APIRequest { request("journal/items", ["active_only": "true"]) }
  var supplementsRequest: APIRequest { request("supplements") }
  var labsRequest: APIRequest { request("labs/summary") }
  func eventsRequest(_ today: String) -> APIRequest { request("events", ["start": RecordDay.add(today, -364), "end": today]) }

  /// The first request of each page: the pages shown are the ones the server answered.
  func start(force: Bool = false) async {
    // a refresh asks for everything again; what is on screen stays until the answers are in (see `load`)
    if force { states.removeAll() }
    let t = todayRequest(nil)
    await load([t])
    guard !closed else { return }
    guard let doc = json(t), let day = (doc["date"] as? String).map({ String($0.prefix(10)) }) else {
      switch state(t) {
      case .offline: phase = .offline
      case .denied: phase = .notShared
      default: phase = .failed
      }
      if state(t) == .denied { tabs = [] }
      return
    }
    today = day
    await load([trendsRequest, nightsRequest, memoryRequest, waterRequest(day), calendarRequest(String(day.prefix(7))), journalItemsRequest,
                supplementsRequest, labsRequest, eventsRequest(day)])
    guard !closed else { return }
    tabs = visibleTabs(doc)
    phase = tabs.isEmpty ? .notShared : .ready
    if force { refreshes += 1 }
  }

  private func visibleTabs(_ doc: [String: Any]) -> [MemberTab] {
    let block = { (k: String) in doc[k] != nil && !Self.isForbidden(doc[k]) }
    var out: [MemberTab] = []
    if block("scores") || block("sleep") || block("reminders") || block("training") || allowed(memoryRequest) { out.append(.today) }
    if !((json(trendsRequest)?["series"] as? [String: Any]) ?? [:]).isEmpty { out.append(.trends) }
    if allowed(nightsRequest) { out.append(.sleep) }
    if block("nutrition") || (today.map { allowed(waterRequest($0)) } ?? false) { out.append(.food) }
    if (today.map { allowed(calendarRequest(String($0.prefix(7)))) } ?? false) || allowed(journalItemsRequest) { out.append(.record) }
    if allowed(supplementsRequest) || allowed(labsRequest) || (today.map { allowed(eventsRequest($0)) } ?? false) { out.append(.health) }
    return out
  }
}

extension TodayModel {
  /// A day of someone else, from the server's documents only (never the phone's own rows): the rings without `scores`,
  /// and a metric section with nothing in it, are not drawn. `theirToday`: their local today.
  static func member(today: [String: Any]?, activity: [String: Any]?, coach: [String: Any]?, monitor: [String: Any]?,
                     memory: [String: Any]?, trends: [String: Any]?, theirToday: String?) -> TodayModel {
    let doc = MemberViewStore.withoutRefused(today)
    var m = TodayModel(TodayInputs(today: doc, activity: activity, coach: coach, monitor: monitor, memory: memory, userToday: theirToday,
                                   trends: trends))
    if doc?["scores"] == nil { m.rings = [] }
    m.sections = m.sections.filter { s in s.metrics.contains { $0.value != nil } }
    return m
  }
}
