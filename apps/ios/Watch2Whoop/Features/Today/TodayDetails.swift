import Foundation
import GRDB

// The details the Today tab opens (#340, the gaps against the page's Today): stress, the sleep coach and body age, plus
// the next step under an empty recovery ring. Models and where they read from; the views are in TodayDetailViews.swift.
//
// Every detail follows docs/ios-native-2.0.md section 3.1: the phone's own result when its package is primary and the day
// has one, else the server's document. In that first case nothing is pulled from the server.

/// A detail opened over the Today tab (inside `TodaySheet.detail`).
enum TodayDetail: Equatable, Identifiable {
  case stress, sleepCoach, bodyAge, bedtimeGoal, sleepHelp
  case lab(key: String, title: String)

  var id: String {
    switch self {
    case .stress: return "stress"
    case .sleepCoach: return "sleepCoach"
    case .bodyAge: return "bodyAge"
    case .bedtimeGoal: return "bedtimeGoal"
    case .sleepHelp: return "sleepHelp"
    case .lab(let k, _): return "lab:\(k)"
    }
  }

  var title: String {
    switch self {
    case .stress: return tr("压力", "Stress")
    case .sleepCoach: return tr("睡眠教练", "Sleep coach")
    case .bodyAge: return tr("身体年龄", "Body age")
    case .bedtimeGoal: return tr("入睡目标", "Bedtime goal")
    case .sleepHelp: return tr("在手表上开启睡眠追踪", "Turn on sleep tracking on the watch")
    case .lab(_, let t): return t
    }
  }

  /// A long list or a form: straight to the large detent.
  var large: Bool {
    switch self {
    case .bodyAge, .bedtimeGoal, .lab: return true
    default: return false
    }
  }
}

/// One read of what a detail draws: JSON bytes (compared before redrawing) and whether they are the phone's own.
struct DetailRead: Equatable {
  var body: Data?
  var local = false

  init(body: Data? = nil, local: Bool = false) {
    self.body = body
    self.local = local
  }

  init(json: [String: Any], local: Bool) {
    self.init(body: try? JSONSerialization.data(withJSONObject: json, options: [.sortedKeys]), local: local)
  }

  var json: [String: Any]? { body.flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } }
}

/// Observes one detail in the local database; pulls the server's document only when the phone has nothing of its own.
@MainActor
final class DetailStore<Model: Equatable>: ObservableObject {
  @Published private(set) var model: Model?
  @Published private(set) var loading = true
  @Published private(set) var offline = false
  private var cancellable: AnyDatabaseCancellable?

  func start(_ session: AccountSession, fetch: DocFetch, parse: @escaping ([String: Any]) -> Model?,
             read: @escaping @Sendable (Database) throws -> DetailRead) {
    guard cancellable == nil else { return }
    cancellable = ValueObservation.tracking(read)
      .removeDuplicates()
      .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { [weak self] r in
        let m = r.json.flatMap(parse)
        if self?.model != m { self?.model = m }
      })
    let db = session.db, api = session.api
    Task {
      let own = ((try? await db.writer.read(read))?.local) ?? false
      if !own { offline = await DocPuller(api: api, db: db).pull([fetch]).offline }
      loading = false
    }
  }
}

// MARK: - stress

/// One day of stress (`stress_daily` on the phone, `/stress?start=&end=&hours=true` on the server): the score and level, the
/// hourly curve, hours per level, the peak hour and the calm heart rate (app.js stressHtml).
struct StressDayModel: Equatable {
  struct Hour: Equatable, Identifiable {
    var id: Int { hour }
    var hour: Int
    var score: Double
  }

  var date: String
  var score: Double?
  var level: String?
  var hours: [Hour] = []
  var highHours: Double?
  var mediumHours: Double?
  var lowHours: Double?
  var peakHour: Int?
  var calmHr: Double?
  /// Short label of why there is no score.
  var reason: String?

  var levelLabel: String? {
    switch level {
    case "high": return tr("高", "High")
    case "medium": return tr("中", "Medium")
    case "low": return tr("低", "Low")
    default: return nil
    }
  }

  var tone: Tone {
    switch level {
    case "high": return .bad
    case "medium": return .mid
    case "low": return .good
    default: return .text
    }
  }

  var totalHours: Double { (highHours ?? 0) + (mediumHours ?? 0) + (lowHours ?? 0) }

  /// The curve as 24 slots (nil = no score that hour), as the chart takes it.
  var curve: [Double?] {
    var out = [Double?](repeating: nil, count: 24)
    for h in hours where (0..<24).contains(h.hour) { out[h.hour] = h.score }
    return out
  }

  init?(json: [String: Any]) {
    guard let d = json["date"] as? String else { return nil }
    let n = TodayModel.num
    date = d
    score = n(json["score"])
    level = json["level"] as? String
    hours = ((json["hours"] as? [[String: Any]]) ?? []).compactMap { h in
      guard let hour = n(h["hour"]), let s = n(h["score"]) else { return nil }
      return Hour(hour: Int(hour), score: s)
    }
    highHours = n(json["high_h"])
    mediumHours = n(json["medium_h"])
    lowHours = n(json["low_h"])
    peakHour = n(json["peak_hour"]).map { Int($0) }
    calmHr = n((json["baseline"] as? [String: Any])?["calm_hr"])
    if score == nil { reason = Self.reason(json["flags"] as? [String: Any]) }
  }

  /// The page's STRESS_SHORT: a short label for why a day has no score.
  static func reason(_ flags: [String: Any]?) -> String? {
    let names = ["no_raw_hr": tr("无心率", "No HR"), "baseline_insufficient": tr("基线不足", "No baseline"),
                 "no_calm_hours": tr("无安静时段", "No calm hours"), "no_motion_data": tr("无步数", "No steps")]
    return (flags ?? [:]).keys.sorted().compactMap { names[$0] }.first
  }
}

enum StressDetail {
  static func docKey(_ day: String) -> String { "today.stress:\(day)" }
  static func fetch(_ day: String) -> DocFetch {
    DocFetch(docKey(day), .get("/v1/users/me/stress", ["start": day, "end": day, "hours": "true"]))
  }

  /// The phone's row in the server's row shape, nil without one.
  static func localRow(_ db: Database, day: String) throws -> [String: Any]? {
    guard let r = try Row.fetchOne(db, sql: """
      SELECT date, score, level, low_h, medium_h, high_h, peak_hour, hours, baseline, flags FROM stress_daily WHERE date = ?
      """, arguments: [day]) else { return nil }
    let nz = SeriesLocal.nz
    let parsed = { (d: Data?) -> Any in d.flatMap { try? JSONSerialization.jsonObject(with: $0) } ?? NSNull() }
    return ["date": r["date"] as String, "score": nz(r["score"] as Double?), "level": nz(r["level"] as String?),
            "low_h": r["low_h"] as Int, "medium_h": r["medium_h"] as Int, "high_h": r["high_h"] as Int,
            "peak_hour": nz(r["peak_hour"] as Int?), "hours": parsed(r["hours"]), "baseline": parsed(r["baseline"]),
            "flags": parsed(r["flags"])]
  }

  static func read(_ db: Database, day: String) throws -> DetailRead {
    if StressStage.mode(db) == .primary, let row = try localRow(db, day: day) { return DetailRead(json: row, local: true) }
    guard let first = (try DocRecord.fetchOne(db, key: docKey(day)))?.json?["days"] as? [[String: Any]], let row = first.first else {
      return DetailRead()
    }
    return DetailRead(json: row, local: false)
  }
}

// MARK: - sleep coach

/// Tonight's sleep coach (`sleep_coach` of `coach.local:<day>` on the phone, `/sleep-coach` on the server; app.js goalCardHtml):
/// when to go to bed, how long to sleep, when to get up, how much the plan falls short.
struct SleepCoachModel: Equatable {
  var bedtime: String?
  var need: Double?
  var wake: String?
  /// Minutes the bedtime goal falls short of the need (only with a goal and a wake time).
  var shortfall: Double?
  var debt: Double?
  /// The bedtime comes from the user's goal (else from the need).
  var fromGoal = false

  init?(json: [String: Any]?) {
    guard let json else { return nil }
    let n = TodayModel.num
    bedtime = json["recommended_bedtime"] as? String
    need = n(json["need_min"])
    wake = json["wake_time"] as? String
    shortfall = n(json["shortfall_min"]).flatMap { $0 > 0 ? $0 : nil }
    debt = n(json["sleep_debt_min"])
    fromGoal = json["source"] as? String == "goal"
    guard bedtime != nil || need != nil else { return nil }
  }

  /// 「需要 8:10 · 08:30 起 · 差 0:20」
  var line: String? {
    var parts: [String] = []
    if let need { parts.append("\(tr("需要", "Need")) \(TodayModel.hm(need))") }
    if let wake { parts.append(tr("\(wake) 起", "up at \(wake)")) }
    if let shortfall {
      let t = TodayModel.hm(shortfall)
      parts.append(tr("差 \(t)", "short \(t)"))
    }
    return parts.isEmpty ? nil : parts.joined(separator: " · ")
  }
}

// MARK: - the next step under an empty recovery ring

/// What the button under an empty ring does (`recovery_empty.action`, web/recovery_empty.py) and its short label.
struct RecoveryNext: Equatable {
  enum Action: String { case consent, sync, sleepHelp = "sleep_help" }
  var action: Action
  var label: String

  init?(_ empty: [String: Any]?) {
    guard let a = (empty?["action"] as? String).flatMap(Action.init(rawValue:)), let l = empty?["cta"] as? String, !l.isEmpty else { return nil }
    action = a
    label = l
  }
}

// MARK: - body age

/// Body age: the current week, what it is made of by domain, the weekly history (app.js healthspanSheet).
/// Takes the server's `/healthspan` answer; `BodyAgeDetail.localView` gives the same shape from the phone's weeks.
struct BodyAgeModel: Equatable {
  struct Contributor: Equatable, Identifiable {
    var id: String { key }
    var key: String
    var label: String
    var value: String?
    var years: Double
    /// The record behind it may be incomplete (#210).
    var partial = false

    /// > 0 = ages you. Colour of the years and of the bar.
    var tone: Tone { years > 0 ? .bad : years < 0 ? .good : .text }
  }

  struct Group: Equatable, Identifiable {
    var id: String
    var title: String
    var items: [Contributor]
  }

  struct Week: Equatable {
    var date: String
    var age: Double
  }

  var bodyAge: Double?
  var chronoAge: Double?
  var delta: Double?
  var pace: Double?
  var groups: [Group] = []
  /// Items without enough data (their short labels).
  var insufficient: [String] = []
  var history: [Week] = []

  init(json: [String: Any]) {
    let n = TodayModel.num
    bodyAge = n(json["body_age"])
    chronoAge = n(json["chrono_age"])
    delta = n(json["delta_years"])
    pace = n(json["pace_of_aging"])
    history = ((json["history"] as? [[String: Any]]) ?? []).compactMap { h in
      guard let a = n(h["body_age"]), let d = (h["as_of"] as? String) ?? (h["week"] as? String) else { return nil }
      return Week(date: String(d.prefix(10)), age: a)
    }
    var order: [String] = [], byCat: [String: [Contributor]] = [:]
    for c in (json["contributors"] as? [[String: Any]]) ?? [] {
      guard let key = c["key"] as? String else { continue }
      let cat = c["category"] as? String ?? "other"
      if byCat[cat] == nil { order.append(cat) }
      byCat[cat, default: []].append(Contributor(
        key: key, label: Self.label(key, c["label"] as? String), value: Self.value(c), years: n(c["years"]) ?? 0,
        partial: c["coverage"] as? String == "possibly_incomplete"))
    }
    groups = order.map { Group(id: $0, title: Self.category($0), items: byCat[$0] ?? []) }
    insufficient = ((json["insufficient"] as? [[String: Any]]) ?? []).compactMap { i in
      (i["key"] as? String).map { Self.label($0, i["label"] as? String) }
    }
  }

  /// The largest |years|, floor 0.5: a bar's full width (app.js healthspanDetailHtml).
  var scale: Double { max(0.5, groups.flatMap(\.items).map { abs($0.years) }.max() ?? 0) }

  static func category(_ c: String) -> String {
    switch c {
    case "sleep": return tr("睡眠", "Sleep")
    case "activity": return tr("活动", "Activity")
    case "fitness": return tr("体能", "Fitness")
    default: return c
    }
  }

  /// The phone's stored rows keep the bundle's Chinese label: English by key (insights/healthspan_store.LABELS_EN).
  static func label(_ key: String, _ stored: String?) -> String {
    guard !Lang.isZh, let en = english[key] else { return stored ?? key }
    return en.0
  }

  static let english: [String: (String, String)] = [
    "sleep_duration": ("Average sleep", "h"), "sleep_regularity": ("Sleep regularity (SRI)", ""), "steps": ("Daily steps", "steps"),
    "mvpa": ("Weekly moderate-to-vigorous activity", "min"), "strength": ("Weekly strength training", "min"),
    "vo2max": ("VO₂max", "mL/kg/min"), "rhr": ("Resting HR", "bpm"), "body_comp": ("Body composition", ""),
    "activity": ("Activity (steps + training)", "step equiv."),
  ]

  /// The reading behind an item (app.js contribValue).
  static func value(_ c: [String: Any]) -> String? {
    guard let v = TodayModel.num(c["value"]) else { return nil }
    let key = c["key"] as? String ?? ""
    if key == "body_comp" {
      let waist = c["measure"] as? String == "waist_to_height"
      return "\(waist ? tr("腰围身高比", "Waist-to-height") : "BMI") \(TodayModel.fmt(v, digits: waist ? 2 : 1))"
    }
    let digits = abs(v) >= 100 || v == v.rounded() ? 0 : 1
    let unit = Lang.isZh ? (c["unit"] as? String ?? "") : (english[key]?.1 ?? (c["unit"] as? String ?? ""))
    return TodayModel.fmt(v, digits: digits) + (unit.isEmpty ? "" : " \(unit)")
  }

  /// `+4.0`, `−1.0` (the page's signed).
  static func signed(_ v: Double, digits: Int = 1) -> String {
    let text = String(format: "%.\(digits)f", abs(v))
    return (v > 0 ? "+" : v < 0 ? "−" : "") + text
  }

  /// Faster than the calendar = red, slower = green (the page's paceClass).
  static func paceTone(_ p: Double?) -> Tone {
    guard let p else { return .text }
    return p < paceGood ? .good : p > paceBad ? .bad : .text
  }

  static let paceGood = 0.95
  static let paceBad = 1.05
}

enum BodyAgeDetail {
  /// The weeks the sheet shows (the page's `/healthspan?weeks=26`).
  static let weeks = 26
  static let docKey = "healthspan:me"
  static var fetch: DocFetch { DocFetch(docKey, .get("/v1/users/me/healthspan", ["weeks": String(weeks)])) }

  /// insights/healthspan_store.view on the phone's `healthspan_weekly`; nil without a week.
  static func localView(_ db: Database) throws -> [String: Any]? {
    let rows = try Row.fetchAll(db, sql: """
      SELECT week_start, as_of, chrono_age, body_age, delta_years, pace_of_aging, contributors, insufficient
      FROM healthspan_weekly ORDER BY week_start DESC LIMIT ?
      """, arguments: [weeks])
    guard let cur = rows.first else { return nil }
    let nz = SeriesLocal.nz
    let parsed = { (d: Data?) -> Any in d.flatMap { try? JSONSerialization.jsonObject(with: $0) } ?? [Any]() }
    return ["body_age": nz(cur["body_age"] as Double?), "chrono_age": nz(cur["chrono_age"] as Double?),
            "delta_years": nz(cur["delta_years"] as Double?), "pace_of_aging": nz(cur["pace_of_aging"] as Double?),
            "as_of": cur["as_of"] as String,
            "history": rows.reversed().map { r -> [String: Any] in
              ["week": r["week_start"] as String, "as_of": r["as_of"] as String, "body_age": nz(r["body_age"] as Double?)]
            },
            "contributors": parsed(cur["contributors"]), "insufficient": parsed(cur["insufficient"])]
  }

  static func read(_ db: Database) throws -> DetailRead {
    if HealthspanStage.mode(db) == .primary, let view = try localView(db) { return DetailRead(json: view, local: true) }
    return DetailRead(body: try DocRecord.fetchOne(db, key: docKey)?.body)
  }
}
