import Foundation
import GRDB

/// Document keys of the Trends tab (table `docs`). `end` nil = ending on the user's today, so today's keys stay the same
/// from day to day (the page's cache keys work the same way).
enum TrendsDocs {
  static func trends(_ days: Int, end: String?) -> String { "trends:\(days)" + (end.map { ":\($0)" } ?? "") }
  static func vo2max(_ end: String?) -> String { "trends.vo2max:\(end ?? "")" }
  static func sleepDetails(_ end: String?) -> String { "trends.sleepdetails:\(end ?? "")" }
  static func sleepNights(_ end: String?) -> String { "trends.nights:\(end ?? "")" }

  static func fetches(days: Int, end: String?) -> [DocFetch] {
    [DocFetch(trends(days, end: end), Endpoint.trends(days: days, end: end)),
     DocFetch(vo2max(end), Endpoint.vo2max(end: end)),
     DocFetch(sleepDetails(end), Endpoint.sleepDetails(end: end))]
  }
}

/// The ranges of the range control (the page also has 180 days; #340 N4 keeps four).
enum TrendRange: Int, CaseIterable, Identifiable {
  case week = 7, month = 30, quarter = 90, year = 365
  var id: Int { rawValue }
  var label: String {
    switch self {
    case .week: return tr("1周", "1W")
    case .month: return tr("1月", "1M")
    case .quarter: return tr("3月", "3M")
    case .year: return tr("1年", "1Y")
    }
  }
}

/// One read of the local database: the range's documents and the phone's own status cards for the end date.
struct TrendsSnapshot: Equatable {
  var trends: DocRecord?
  var local: DocRecord?
  var vo2: DocRecord?
  var sleep: DocRecord?

  /// The page this snapshot draws (nil until the range's document is cached).
  static func page(_ s: TrendsSnapshot) -> TrendsPage? {
    guard let json = s.trends?.json, let data = TrendsData(json: json) else { return nil }
    return TrendsPage(data: data, local: TrendsLocal.Output(json: s.local?.json), vo2: s.vo2?.json, sleep: s.sleep?.json)
  }

  static func read(_ db: Database, days: Int, end: String?, endDay: String?) throws -> TrendsSnapshot {
    var s = TrendsSnapshot()
    s.trends = try DocRecord.fetchOne(db, key: TrendsDocs.trends(days, end: end))
    let day = end ?? endDay ?? (s.trends?.json?["end"] as? String).map { String($0.prefix(10)) }
    if let day { s.local = try DocRecord.fetchOne(db, key: TrendsLocal.docKey(day)) }
    s.vo2 = try DocRecord.fetchOne(db, key: TrendsDocs.vo2max(end))
    s.sleep = try DocRecord.fetchOne(db, key: TrendsDocs.sleepDetails(end))
    return s
  }
}

/// What the metrics view draws.
struct TrendsPage: Equatable {
  var data: TrendsData
  var status: TrendStatus
  var localCount: Int
  /// Fitness level from the VO₂max endpoint (same-age, same-sex percentile).
  var fitness: String?
  var regularity: Regularity?

  struct Regularity: Equatable {
    var pct7: Double?
    var pct30: Double?
    /// Bedtime → wake per night, minutes after 18:00 (the page's regularity bars).
    var nights: [ClosedRange<Double>]
  }

  init(data: TrendsData, local: TrendsLocal.Output?, vo2: [String: Any]?, sleep: [String: Any]?) {
    self.data = data
    let merged = TrendsLocal.merge(server: data.status, local: local)
    status = TrendStatus(status: merged.status, local: merged.keys)
    localCount = merged.keys.count
    if let level = vo2?["level"] as? String {
      let names = ["excellent": tr("优秀", "Excellent"), "good": tr("良好", "Good"), "average": tr("中等", "Average"),
                   "fair": tr("一般", "Fair"), "poor": tr("较差", "Poor")]
      fitness = names[level].map { tr("体能 \($0)", "Fitness \($0)") }
    }
    if let s = sleep, s["consistency_pct_7d"] != nil || s["consistency_pct_30d"] != nil {
      let off = { (iso: String) -> Double? in
        let t = Array(iso)
        guard t.count >= 16, let h = Int(String(t[11...12])), let m = Int(String(t[14...15])) else { return nil }
        let x = Double(h * 60 + m)
        return x < 18 * 60 ? x + 6 * 60 : x - 18 * 60
      }
      let nights: [ClosedRange<Double>] = ((s["nights"] as? [[String: Any]]) ?? []).compactMap { n in
        guard let b = (n["bedtime"] as? String).flatMap(off), let w = (n["wake_time"] as? String).flatMap(off) else { return nil }
        return b...max(b + 10, w)
      }
      regularity = Regularity(pct7: TodayModel.num(s["consistency_pct_7d"]), pct30: TodayModel.num(s["consistency_pct_30d"]), nights: nights)
    }
  }
}

/// The Trends tab's state: reads the local database first (the cached `/trends` document and the phone's own status
/// cards), refreshes the documents through `DocPuller`, then recomputes the local status cards off the main thread.
@MainActor
final class TrendsStore: ObservableObject {
  @Published private(set) var page: TrendsPage?
  @Published private(set) var loading = false
  @Published private(set) var offline = false
  @Published private(set) var range: TrendRange
  /// Last day of the range; nil = the user's today.
  @Published private(set) var end: String?
  @Published private(set) var userToday: String?

  private var cancellable: AnyDatabaseCancellable?
  private weak var session: AccountSession?
  private var computing = false
  static let rangeKey = "w2w.trends.days"

  init(session: AccountSession? = nil) {
    range = TrendRange(rawValue: UserDefaults.standard.integer(forKey: Self.rangeKey)) ?? .month
    // frame-meter runs (PerfProbe): `W2W_PERF_TRENDS_DAYS=365` picks the range
    if PerfProbe.enabled, let n = ProcessInfo.processInfo.environment["W2W_PERF_TRENDS_DAYS"].flatMap(Int.init),
       let r = TrendRange(rawValue: n) {
      range = r
    }
    if let session { observe(session) }
  }

  var isObserving: Bool { cancellable != nil }

  func observe(_ session: AccountSession) {
    self.session = session
    if userToday == nil, let cur = try? session.db.doc(DocKey.todayCurrent) { userToday = TodaySnapshot.dateOf(cur) }
    let days = range.rawValue, end = self.end, endDay = end ?? userToday
    cancellable?.cancel()
    // Parsing a year of 37 series happens in `map`, off the main thread (except the first, synchronous value).
    cancellable = ValueObservation.tracking { db in try TrendsSnapshot.read(db, days: days, end: end, endDay: endDay) }
      .removeDuplicates()
      .map(TrendsSnapshot.page)
      .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { [weak self] p in
        guard let self, p != self.page else { return }
        self.page = p
      })
  }

  func setRange(_ r: TrendRange) {
    guard r != range, let session else { return }
    range = r
    UserDefaults.standard.set(r.rawValue, forKey: Self.rangeKey)
    observe(session)
    Task { await refresh() }
  }

  /// End the range on `day` (nil or the user's today = today).
  func select(_ day: String?) {
    let d = (day == userToday) ? nil : day
    guard d != end, let session else { return }
    end = d
    observe(session)
    Task { await refresh() }
  }

  /// The range's documents from the server, then the phone's own status cards.
  func refresh() async {
    guard let session else { return }
    loading = true
    let out = await DocPuller(api: session.api, db: session.db).pull(TrendsDocs.fetches(days: range.rawValue, end: end))
    offline = out.offline
    loading = false
    computeLocal()
  }

  /// TrendsLocal off the main thread, once at a time; the bundle is only loaded when the phone has score rows.
  private func computeLocal() {
    guard let session, !computing else { return }
    guard let day = end ?? page?.data.end ?? userToday else { return }
    let hasRows = (try? session.db.writer.read { db in
      try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM daily_scores LIMIT 1) OR EXISTS (SELECT 1 FROM daily_metrics WHERE metric = 'heart_rate_variability_sdnn' LIMIT 1)")
    }) ?? false
    guard hasRows else { return }
    computing = true
    let db = session.db, tz = TrendsStore.timezone(session)
    Task.detached(priority: .utility) {
      defer { Task { @MainActor in self.computing = false } }
      guard let rt = try? OpenBodyScoreRuntime.builtin() else { return }
      _ = try? TrendsLocal.run(db: db, runtime: rt, end: day, tz: tz)
    }
  }

  /// The user's time zone from the today document (the server's), else the phone's.
  static func timezone(_ session: AccountSession) -> String {
    if let j = (try? session.db.doc(DocKey.todayCurrent))??.json, let tz = (j["user"] as? [String: Any])?["timezone"] as? String {
      return tz
    }
    return TimeZone.current.identifier
  }
}
