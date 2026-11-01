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

/// The ranges of the range control (the page also has 180 days; #340 N4 keeps four). 「今天」 (#357) is one day: its
/// metric list reads the 61-day document (the day plus its 60-day baseline window, `TodayParams.trendsDays`).
enum TrendRange: Int, CaseIterable, Identifiable {
  case today = 1, week = 7, month = 30, quarter = 90, year = 365
  var id: Int { rawValue }
  var label: String {
    switch self {
    case .today: return tr("今天", "Today")
    case .week: return tr("7 天", "7 d")
    case .month: return tr("30 天", "30 d")
    case .quarter: return tr("90 天", "90 d")
    case .year: return tr("365 天", "365 d")
    }
  }

  /// `days` of the `/trends` document this range draws.
  var docDays: Int { self == .today ? TodayParams.trendsDays : rawValue }
}

/// 「全部指标 →」 on the Today tab (#357): switches to Trends, mode 指标, range 今天, ending on the day Today shows.
@MainActor
final class TrendsLink: ObservableObject {
  static let shared = TrendsLink()

  struct Request: Equatable {
    /// nil = the user's today.
    var end: String?
    var range: TrendRange = .today
    var mode: TrendsView.Mode = .metrics
  }

  @Published private(set) var pending: Request?

  /// `day`: the day the Today tab shows (nil = today).
  func open(day: String?) { pending = Request(end: day) }

  /// The pending request, once.
  func take() -> Request? {
    defer { pending = nil }
    return pending
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

  /// The phone's own documents instead of the server's when package `series` is primary (M3, `SeriesLocal`).
  static func read(_ db: Database, days: Int, end: String?, endDay: String?) throws -> TrendsSnapshot {
    var s = TrendsSnapshot()
    s.trends = try SeriesLocal.shown(db, server: TrendsDocs.trends(days, end: end), local: SeriesLocal.trendsKey(days, end: end))
    let day = end ?? endDay ?? (s.trends?.json?["end"] as? String).map { String($0.prefix(10)) }
    if let day { s.local = try DocRecord.fetchOne(db, key: TrendsLocal.docKey(day)) }
    s.vo2 = try SeriesLocal.shown(db, server: TrendsDocs.vo2max(end), local: SeriesLocal.vo2Key(end))
    s.sleep = try SeriesLocal.shown(db, server: TrendsDocs.sleepDetails(end), local: SeriesLocal.detailsKey(end))
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
        let evening: Double = 1080   // 18:00 → 0
        return x < evening ? x + 360 : x - evening
      }
      let nights: [ClosedRange<Double>] = ((s["nights"] as? [[String: Any]]) ?? []).compactMap { n in
        guard let bs = n["bedtime"] as? String, let ws = n["wake_time"] as? String, let b = off(bs), let w = off(ws) else { return nil }
        let end: Double = Swift.max(b + 10, w)
        return b...end
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
  /// The range picked in this run of the app (#357: kept while the app runs, a fresh launch starts on 「今天」).
  static var sessionRange: TrendRange = .today

  init(session: AccountSession? = nil) {
    range = Self.sessionRange
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
    let days = range.docDays, end = self.end, endDay = end ?? userToday
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
    Self.sessionRange = r
    observe(session)
    Task { await refresh() }
  }

  /// A request from 「全部指标 →」: range and end date at once, one reload.
  func apply(_ r: TrendsLink.Request) {
    let d = (r.end == userToday) ? nil : r.end
    guard r.range != range || d != end else { return }
    range = r.range
    Self.sessionRange = r.range
    end = d
    guard let session else { return }
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

  /// The range's documents from the server, then the phone's own status cards and (package `series` on) its own series.
  func refresh() async {
    guard let session else { return }
    loading = true
    let days = range.docDays, end = self.end
    let out = await DocPuller(api: session.api, db: session.db).pull(TrendsDocs.fetches(days: days, end: end))
    offline = out.offline
    loading = false
    computeLocal()
    SeriesLocal.refresh(session: session, [.trends(days: days, end: end), .vo2(end: end), .details(end: end)], userToday: userToday)
  }

  /// The phone's own status cards for the end date (`TrendsStore.computeLocal(session:day:)`).
  private func computeLocal() {
    guard let session, let day = end ?? page?.data.end ?? userToday else { return }
    Self.computeLocal(session: session, day: day)
  }

  private static var computing: Set<String> = []

  /// TrendsLocal off the main thread, once at a time per day; the bundle is only loaded when the phone has score rows.
  /// Shared by Trends and the Today tab's 今日核心 / 需要注意 (#357).
  static func computeLocal(session: AccountSession, day: String) {
    guard !computing.contains(day) else { return }
    let hasRows = (try? session.db.writer.read { db in
      try Bool.fetchOne(db, sql: "SELECT EXISTS (SELECT 1 FROM daily_scores LIMIT 1) OR EXISTS (SELECT 1 FROM daily_metrics WHERE metric = 'heart_rate_variability_sdnn' LIMIT 1)")
    }) ?? false
    guard hasRows else { return }
    computing.insert(day)
    let db = session.db, tz = TrendsStore.timezone(session)
    Task.detached(priority: .utility) {
      defer { Task { @MainActor in TrendsStore.computing.remove(day) } }
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
