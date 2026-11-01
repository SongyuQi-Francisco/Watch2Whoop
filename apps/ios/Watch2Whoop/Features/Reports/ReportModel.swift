import Foundation
import GRDB

/// Weekly / monthly report (`GET /v1/users/me/reports?period=week|month&end=`, insights/reports.py): this period's
/// summary against the previous one, as number tiles with the change (the page's REPORT_CARDS), the journal findings,
/// and the behaviour → next-day recovery card (`/journal/analysis?days=90`). The words are written by the user's AI.
struct ReportModel: Equatable {
  struct Tile: Equatable, Identifiable {
    var id: String
    var label: String
    var value: String
    var unit: String?
    var delta: String?
    var tone: Tone
  }
  struct Finding: Equatable, Identifiable {
    var id: String
    var label: String
    var diff: Double
  }

  var range: String
  var previous: String?
  var tiles: [Tile]
  var findings: [Finding]

  enum Period: String, CaseIterable, Identifiable {
    case week, month
    var id: String { rawValue }
    var label: String { self == .week ? tr("周报", "Weekly") : tr("月报", "Monthly") }
  }

  init?(json: [String: Any]) {
    guard let cur = json["current"] as? [String: Any] else { return nil }
    let start = json["start"] as? String ?? "", end = json["end"] as? String ?? ""
    range = "\(TodayModel.shortDay(start)) – \(TodayModel.shortDay(end))"
    if let p = json["previous"] as? [String: Any], let a = p["start"] as? String, let b = p["end"] as? String {
      previous = "\(TodayModel.shortDay(a)) – \(TodayModel.shortDay(b))"
    }
    let change = json["change"] as? [String: Any] ?? [:]
    let prev = json["previous_values"] as? [String: Any] ?? [:]
    func get(_ d: [String: Any], _ block: String, _ key: String) -> Any? { (d[block] as? [String: Any])?[key] }
    func n(_ d: [String: Any], _ block: String, _ key: String) -> Double? { TodayModel.num(get(d, block, key)) }

    var tiles: [Tile] = []
    /// One tile: value, change (shown with an arrow), better direction (1 higher is better, -1 lower, 0 neutral).
    func add(_ id: String, _ label: String, _ v: Double?, _ fmt: (Double) -> String, unit: String? = nil, change dv: Double?,
             _ fmtCh: (Double) -> String, better: Int) {
      guard let v else { return }
      var delta: String?
      var tone = Tone.text
      if let dv {
        delta = "\(dv > 0 ? "↑" : dv < 0 ? "↓" : "→") \(fmtCh(abs(dv)))"
        if dv != 0 && better != 0 { tone = dv * Double(better) > 0 ? .good : .bad }
      }
      tiles.append(Tile(id: id, label: label, value: fmt(v), unit: unit, delta: delta, tone: tone))
    }
    add("recovery", tr("恢复", "Recovery"), n(cur, "recovery", "avg"), { TodayModel.fmt($0) }, change: n(change, "recovery", "avg"),
        { TodayModel.fmt($0) }, better: 1)
    add("sleep", tr("睡眠", "Sleep"), n(cur, "sleep", "avg_actual_min"), { TrendsFormat.hm($0) ?? "—" },
        change: n(change, "sleep", "avg_actual_min"), { "\(TodayModel.fmt($0))′" }, better: 1)
    if let bed = get(cur, "sleep", "avg_bedtime") as? String {
      let pb = get(prev, "sleep", "avg_bedtime") as? String
      tiles.append(Tile(id: "bedtime", label: tr("入睡", "Bedtime"), value: bed, delta: pb.map { "\(tr("上期", "Before")) \($0)" }, tone: .text))
    }
    add("consistency", tr("规律性", "Regularity"), n(cur, "sleep", "consistency_pct"), { "\(TodayModel.fmt($0))%" },
        change: n(change, "sleep", "consistency_pct"), { TodayModel.fmt($0) }, better: 1)
    add("load", tr("负荷", "Strain"), n(cur, "load", "avg_strain"), { TodayModel.fmt($0, digits: 1) }, change: n(change, "load", "avg_strain"),
        { TodayModel.fmt($0, digits: 1) }, better: 0)
    add("stress", tr("压力", "Stress"), n(cur, "stress", "avg"), { TodayModel.fmt($0, digits: 2) }, change: n(change, "stress", "avg"),
        { TodayModel.fmt($0, digits: 2) }, better: -1)
    add("training", tr("训练", "Training"), n(cur, "training", "sessions"), { TodayModel.fmt($0) }, unit: tr("次", "sessions"),
        change: n(change, "training", "sessions"), { TodayModel.fmt($0) }, better: 0)
    add("weight", tr("体重", "Weight"), n(cur, "weight", "last_kg"), { TodayModel.fmt($0, digits: 1) }, unit: "kg",
        change: n(cur, "weight", "change_kg"), { "\(TodayModel.fmt($0, digits: 1))kg" }, better: -1)
    self.tiles = tiles
    findings = ((json["journal_findings"] as? [[String: Any]]) ?? []).prefix(3).compactMap { f in
      guard let key = f["key"] as? String, let d = TodayModel.num(f["diff_recovery"]) else { return nil }
      return Finding(id: key, label: f["label"] as? String ?? key, diff: d)
    }
  }
}

/// One journal item's link to next-day recovery (≥ 5 days with and without it in the last 90 days).
struct BehaviorRow: Equatable, Identifiable {
  var id: String
  var label: String
  var diff: Double
  var tone: Tone
  var reliable: Bool

  /// The page's behaviorModel (journal.js): items with a result, largest effect first; `lacking` = items without enough
  /// days. With the second version (`regression` in the outcome, flag `journal_regression`) the adjusted estimate and its
  /// Benjamini-Hochberg direction replace the raw difference.
  static func parse(_ list: [[String: Any]]) -> (rows: [BehaviorRow], lacking: Int) {
    let adj = adjusted(list)
    var rows: [BehaviorRow] = []
    for a in list {
      guard let o = (a["outcomes"] as? [String: Any])?["next_recovery"] as? [String: Any],
            let r = adj ? (o["regression"] as? [String: Any]) : Optional(o), r["status"] as? String == "ok" else { continue }
      let item = a["item"] as? [String: Any] ?? [:]
      let dir = r["direction"] as? String
      rows.append(BehaviorRow(id: item["key"] as? String ?? UUID().uuidString, label: shortLabel(item),
                              diff: TodayModel.num(adj ? r["estimate"] : r["diff_yes_minus_no"]) ?? 0,
                              tone: dir == "better" ? .good : dir == "worse" ? .bad : .text, reliable: r["reliable"] as? Bool ?? false))
    }
    rows.sort { abs($0.diff) > abs($1.diff) }
    return (rows, list.count - rows.count)
  }

  /// The list carries the second version (adjusted for strain, sleep and weekday).
  static func adjusted(_ list: [[String: Any]]) -> Bool {
    list.contains { (($0["outcomes"] as? [String: Any])?["next_recovery"] as? [String: Any])?["regression"] != nil }
  }

  /// The analysis document: the server's list, or the phone's `{"results": [...]}` (ReportsLocal).
  static func list(_ body: Data) -> [[String: Any]] {
    let o = try? JSONSerialization.jsonObject(with: body)
    return (o as? [[String: Any]]) ?? ((o as? [String: Any])?["results"] as? [[String: Any]]) ?? []
  }

  /// The item's name without bracketed notes and alternatives (journal labels are the user's data).
  static func shortLabel(_ item: [String: Any]) -> String {
    let full = item["label"] as? String ?? item["label_zh"] as? String ?? item["key"] as? String ?? ""
    var s = full.replacingOccurrences(of: "\u{FF08}[^\u{FF09}]*\u{FF09}", with: "", options: .regularExpression)
      .replacingOccurrences(of: "\\([^)]*\\)", with: "", options: .regularExpression)
    if s.hasPrefix("\u{5403}\u{4E86}") { s.removeFirst(2) }
    s = String(s.components(separatedBy: " / ").first ?? s).trimmingCharacters(in: .whitespaces)
    return s.isEmpty ? full : s
  }
}

enum ReportDocs {
  static func report(_ p: ReportModel.Period, end: String?) -> String { "report:\(p.rawValue):\(end ?? "")" }
  /// `regression` = the second version (flag `journal_regression`): its own document, as the page's own cache key.
  static func behavior(end: String?, regression: Bool = false) -> String { "journal.analysis\(regression ? ".r" : ""):\(end ?? "")" }
}

/// The report's state: the cached documents first, then a refresh through `DocPuller`. With package `reports` (M5,
/// `ReportsLocal`) primary the page draws the phone's documents.
@MainActor
final class ReportStore: ObservableObject {
  @Published private(set) var report: ReportModel?
  @Published private(set) var behavior: (rows: [BehaviorRow], lacking: Int) = ([], 0)
  /// The 行为影响 card shows the second version (adjusted for strain, sleep and weekday).
  @Published private(set) var adjusted = false
  @Published private(set) var loaded = false
  @Published var period: ReportModel.Period = .week
  private var cancellable: AnyDatabaseCancellable?
  private var end: String?

  private struct Snap: Equatable { var report: DocRecord?; var behavior: DocRecord? }

  static func regression(_ session: AccountSession) -> Bool { session.flags.contains("journal_regression") }

  func observe(_ session: AccountSession, end: String?) {
    self.end = end
    let reg = Self.regression(session)
    let rk = ReportDocs.report(period, end: end), bk = ReportDocs.behavior(end: end, regression: reg)
    let lrk = ReportsLocal.reportKey(period.rawValue, end: end), lbk = ReportsLocal.analysisKey(end: end, regression: reg)
    cancellable?.cancel()
    cancellable = ValueObservation.tracking { db in
      Snap(report: try ReportsLocal.shown(db, server: rk, local: lrk), behavior: try ReportsLocal.shown(db, server: bk, local: lbk))
    }
    .removeDuplicates()
    .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { [weak self] s in
      guard let self else { return }
      let r = s.report?.json.flatMap(ReportModel.init(json:))
      if r != self.report { self.report = r }
      let list = s.behavior.map { BehaviorRow.list($0.body) } ?? []
      let b = BehaviorRow.parse(list), adj = BehaviorRow.adjusted(list)
      if b.rows != self.behavior.rows || b.lacking != self.behavior.lacking { self.behavior = b }
      if adj != self.adjusted { self.adjusted = adj }
    })
  }

  func refresh(_ session: AccountSession) async {
    let p = period, e = end, reg = Self.regression(session)
    _ = await DocPuller(api: session.api, db: session.db).pull([
      DocFetch(ReportDocs.report(p, end: e), Endpoint.report(p.rawValue, end: e)),
      DocFetch(ReportDocs.behavior(end: e, regression: reg), Endpoint.journalAnalysis(end: e, regression: reg)),
    ])
    loaded = true
    ReportsLocal.refresh(session: session, [.report(period: p.rawValue, end: e), .analysis(end: e, regression: reg)],
                         userToday: session.db.userToday())
  }

  func setPeriod(_ p: ReportModel.Period, session: AccountSession) {
    guard p != period else { return }
    period = p
    observe(session, end: end)
    Task { await refresh(session) }
  }
}
