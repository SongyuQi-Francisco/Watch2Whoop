import Combine
import Foundation
import GRDB

/// Document keys of the Today tab (table `docs`), next to N0's `today:<date>` / `today:current`.
enum TodayDocs {
  static func activity(_ day: String) -> String { "today.activity:\(day)" }
  static func coach(_ day: String) -> String { "today.coach:\(day)" }
  static func monitor(_ day: String) -> String { "today.monitor:\(day)" }
  static let memory = "memory:me"
  static let sync = "sync:overview"
  static let circles = "circles:overview"
  static func days(_ month: String) -> String { "days:\(month)" }
  static func sleepNight(_ day: String?) -> String { "sleep:night:\(day ?? "latest")" }
  static func explain(_ kind: String, _ key: String, _ day: String?) -> String { "explain:\(kind):\(key):\(day ?? "")" }
  static let weightRecent = "weight:recent"

  /// What a refresh of `day` pulls besides the today document. Undated views (status cards, sync, circles) only on today.
  static func fetches(day: String, isToday: Bool, circles: Bool) -> [DocFetch] {
    var f = [
      DocFetch(activity(day), Endpoint.dayActivity(day)),
      DocFetch(coach(day), Endpoint.strainCoach(day)),
      DocFetch(monitor(day), Endpoint.healthMonitor(day)),
    ]
    if isToday {
      f.append(DocFetch(memory, Endpoint.memory()))
      f.append(DocFetch(sync, Endpoint.syncOverview()))
      if circles { f.append(DocFetch(TodayDocs.circles, Endpoint.friendsOverview)) }
    }
    return f
  }
}

/// What one read of the local database gives the Today tab (compared before redrawing).
struct TodaySnapshot: Equatable {
  var docs: [String: DocRecord] = [:]
  var day: String?
  var local: DailyScore?
  var localPrev: DailyScore?

  /// The today document, then the day's other views. One read, so the page never draws half old, half new.
  static func read(_ db: Database, date: String?) throws -> TodaySnapshot {
    var s = TodaySnapshot()
    let mainKey = date.map(DocKey.today) ?? DocKey.todayCurrent
    if let main = try DocRecord.fetchOne(db, key: mainKey) { s.docs[mainKey] = main }
    s.day = date ?? (s.docs[mainKey].flatMap(Self.dateOf))
    var keys = [TodayDocs.memory, TodayDocs.sync, TodayDocs.circles]
    if let d = s.day { keys += [TodayDocs.activity(d), TodayDocs.coach(d), TodayDocs.monitor(d)] }
    for r in try DocRecord.fetchAll(db, keys: keys) { s.docs[r.key] = r }
    if let d = s.day {
      s.local = try DailyScore.filter(Column("date") == d && Column("origin") == "app").fetchOne(db)
      s.localPrev = try DailyScore.filter(Column("date") == LocalDay.add(d, -1) && Column("origin") == "app").fetchOne(db)
    }
    return s
  }

  /// `"date"` of a today document.
  static func dateOf(_ r: DocRecord) -> String? { r.json?["date"] as? String }
}

/// The Today tab's state: observes the local database (first value synchronously, so cached data is on screen in the
/// first frame), refreshes through the sync layer, and keeps the selected day (nil = the user's today).
@MainActor
final class TodayStore: ObservableObject {
  @Published private(set) var model: TodayModel = .empty
  @Published private(set) var hasData = false
  @Published private(set) var date: String?
  /// The user's own today (from the current today document).
  @Published private(set) var userToday: String?

  private var cancellable: AnyDatabaseCancellable?
  private var lastSnapshot: TodaySnapshot?
  private weak var session: AccountSession?
  private var scoring = false

  /// With the session the first read happens here, so the page's first body already has the cached numbers (one layout
  /// pass instead of an empty one followed by a full one).
  init(session: AccountSession? = nil) {
    if let session { observe(session) }
  }

  var isObserving: Bool { cancellable != nil }

  /// Starts (or restarts, after a date change) the observation. Synchronous first value.
  func observe(_ session: AccountSession) {
    self.session = session
    if userToday == nil, let cur = try? session.db.doc(DocKey.todayCurrent) { userToday = TodaySnapshot.dateOf(cur) }
    let date = self.date, circlesOn = session.circlesTab
    cancellable?.cancel()
    cancellable = ValueObservation.tracking { db in try TodaySnapshot.read(db, date: date) }
      .removeDuplicates()
      .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { [weak self] snap in
        self?.apply(snap, circlesOn: circlesOn)
      })
  }

  func stop() {
    cancellable?.cancel()
    cancellable = nil
  }

  private func apply(_ s: TodaySnapshot, circlesOn: Bool) {
    lastSnapshot = s
    let mainKey = date.map(DocKey.today) ?? DocKey.todayCurrent
    if date == nil, let d = s.day { userToday = d }
    let inp = TodayInputs(
      today: s.docs[mainKey]?.json, activity: s.day.flatMap { s.docs[TodayDocs.activity($0)]?.json },
      coach: s.day.flatMap { s.docs[TodayDocs.coach($0)]?.json }, monitor: s.day.flatMap { s.docs[TodayDocs.monitor($0)]?.json },
      memory: s.docs[TodayDocs.memory]?.json, sync: s.docs[TodayDocs.sync]?.json, circles: s.docs[TodayDocs.circles]?.json,
      localDay: s.local, localPrev: s.localPrev, userToday: userToday, circlesOn: circlesOn)
    let m = TodayModel(inp)
    if m != model { model = m }
    if !hasData && s.docs[mainKey] != nil {
      hasData = true
      PerfProbe.shared.firstData()
    }
  }

  /// Look back at `day` (nil or the user's today = today).
  func select(_ day: String?) {
    let d = (day == userToday) ? nil : day
    guard d != date, let session else { return }
    date = d
    hasData = false
    observe(session)
    Task { await refresh() }
  }

  /// Pull to refresh / on appear: the today document through the coordinator (status line, offline queue), the other
  /// views in parallel; then the phone's own scores when it has raw data.
  func refresh() async {
    guard let session else { return }
    let date = self.date
    let known = date ?? userToday
    let puller = DocPuller(api: session.api, db: session.db)
    let circles = session.circlesTab
    if let day = known {
      async let main: Void = session.sync.refresh(.today(date: date))
      async let rest = puller.pull(TodayDocs.fetches(day: day, isToday: date == nil, circles: circles))
      _ = await (main, rest)
    } else {
      await session.sync.refresh(.today(date: nil))
      if let cur = try? session.db.doc(DocKey.todayCurrent), let day = TodaySnapshot.dateOf(cur) {
        userToday = day
        _ = await puller.pull(TodayDocs.fetches(day: day, isToday: true, circles: circles))
      }
    }
    scoreLocally()
  }

  /// The reminder card's actions (已看 / 不再提醒 / 标为未读): the local copy of the today document first, then the
  /// offline queue (PATCH /reminders/{id}).
  func setReminder(_ id: Int, status: String) {
    guard let session else { return }
    let key = date.map(DocKey.today) ?? DocKey.todayCurrent
    for k in Set([key, DocKey.todayCurrent, userToday.map(DocKey.today) ?? key]) {
      guard let rec = try? session.db.doc(k), var json = rec.json, var list = json["reminders"] as? [[String: Any]] else { continue }
      if status == "dismissed" { list.removeAll { ($0["id"] as? NSNumber)?.intValue == id } }
      else { for i in list.indices where (list[i]["id"] as? NSNumber)?.intValue == id { list[i]["status"] = status } }
      json["reminders"] = list
      if let data = try? JSONSerialization.data(withJSONObject: json) { try? session.db.saveDoc(k, json: data, version: rec.version) }
    }
    let body = try? JSONSerialization.data(withJSONObject: ["status": status])
    session.sync.enqueue(kind: "reminder", group: "reminder:\(id)", method: .patch, path: Endpoint.reminder(id), body: body)
  }

  /// LocalScoring off the main thread, once at a time; a no-op until the phone has raw samples (N8 / N2).
  private func scoreLocally() {
    guard let session, !scoring, LocalScoring.hasRawData(session.db), let today = userToday else { return }
    scoring = true
    let db = session.db, tz = model.timezone ?? TimeZone.current.identifier, kind = model.hrvKind ?? "sdnn"
    Task.detached(priority: .utility) {
      defer { Task { @MainActor in self.scoring = false } }
      guard let rt = try? OpenBodyScoreRuntime.builtin() else { return }
      _ = try? LocalScoring.run(db: db, runtime: rt, tz: tz, hrvKind: kind, today: today)
    }
  }
}
