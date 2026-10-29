import GRDB
import SwiftUI

/// One explanation (`GET /v1/users/me/explain/{metric|status}/{key}[?date=]`, explain/ on the server): what the item
/// is, how to read it, advice from the user's own numbers (rules, no AI) and `ai_prompt` for 「问 AI」. The words live
/// only here, in the sheet (app.js explainHtml).
struct ExplainModel: Equatable {
  struct Read: Equatable { var range: String; var label: String; var tone: Tone }
  struct Advice: Equatable { var text: String; var tone: Tone }

  var title: String?
  var currentValue: String?
  var currentUnit: String?
  var currentBasis: String?
  var currentTone: Tone = .text
  var facts: [(String, String)] = []
  var missing: String?
  var advice: [Advice] = []
  var what: String?
  var read: [Read] = []
  var recovery: String?
  var recoveryHead: String
  var tips: [String] = []
  var avoid: [String] = []
  var caution: [String] = []
  var profile: [String] = []
  var aiPrompt: String?

  init(json: [String: Any]) {
    title = json["title"] as? String
    if let c = json["current"] as? [String: Any] {
      if let n = TodayModel.num(c["value"]) { currentValue = TodayModel.fmt(n, digits: 1) } else { currentValue = c["value"] as? String }
      currentUnit = c["unit"] as? String
      currentBasis = c["basis"] as? String
      currentTone = Self.tone(c["tone"] as? String)
    }
    facts = ((json["facts"] as? [[String: Any]]) ?? []).compactMap { f in
      guard let l = f["label"] as? String else { return nil }
      return (l, (f["value"] as? String) ?? TodayModel.num(f["value"]).map { TodayModel.fmt($0, digits: 1) } ?? "—")
    }
    missing = json["missing"] as? String
    advice = ((json["advice"] as? [[String: Any]]) ?? []).compactMap { a in
      (a["text"] as? String).map { Advice(text: $0, tone: Self.tone(a["tone"] as? String)) }
    }
    what = json["what"] as? String
    read = ((json["read"] as? [[String: Any]]) ?? []).compactMap { r in
      guard let range = r["range"] as? String, let label = r["label"] as? String else { return nil }
      return Read(range: range, label: label, tone: Self.tone(r["tone"] as? String))
    }
    recovery = json["recovery"] as? String
    recoveryHead = (json["key"] as? String) == "recovery" ? tr("怎么用", "How to use it") : tr("和恢复", "And recovery")
    tips = json["tips"] as? [String] ?? []
    let ex = json["exercises"] as? [String: Any]
    avoid = (json["avoid"] as? [String] ?? []) + ((ex?["banned"] as? [[String: Any]]) ?? []).compactMap { b in
      (b["name"] as? String).map { n in [n, b["reason"] as? String].compactMap { $0 }.joined(separator: " · ") }
    }
    caution = ((ex?["caution"] as? [[String: Any]]) ?? []).compactMap { b in
      (b["name"] as? String).map { n in [n, b["cues"] as? String].compactMap { $0 }.joined(separator: " · ") }
    }
    profile = json["profile"] as? [String] ?? []
    aiPrompt = json["ai_prompt"] as? String
  }

  static func tone(_ s: String?) -> Tone {
    switch s {
    case "good": return .good
    case "mid", "warn": return .mid
    case "bad": return .bad
    default: return .text
    }
  }

  static func == (a: ExplainModel, b: ExplainModel) -> Bool {
    a.title == b.title && a.currentValue == b.currentValue && a.what == b.what && a.advice == b.advice
      && a.facts.map(\.0) == b.facts.map(\.0) && a.facts.map(\.1) == b.facts.map(\.1) && a.aiPrompt == b.aiPrompt
  }
}

/// Loads an explanation: the cached copy at once, a fresh one from the server into `docs` (online-only content, kept
/// as a cache document so a second open works offline).
@MainActor
final class ExplainLoader: ObservableObject {
  @Published private(set) var model: ExplainModel?
  @Published private(set) var error: String?
  private var cancellable: AnyDatabaseCancellable?

  func start(_ session: AccountSession, kind: String, key: String, day: String?) {
    let docKey = TodayDocs.explain(kind, key, day)
    let localKey = ExplainLocal.key(kind, key, day)
    // M6 primary: the phone's sheet (its personal part on the server's static texts) once computed, else the server's
    cancellable = ValueObservation.tracking { db -> DocRecord? in
      if kind == "metric", RulesStage.mode(db) == .primary, let l = try DocRecord.fetchOne(db, key: localKey) { return l }
      return try DocRecord.fetchOne(db, key: docKey)
    }
      .removeDuplicates()
      .start(in: session.db.writer, scheduling: .immediate, onError: { _ in }, onChange: { [weak self] rec in
        if let json = rec?.json { self?.model = ExplainModel(json: json) }
      })
    Task {
      let out = await DocPuller(api: session.api, db: session.db).pull([DocFetch(docKey, Endpoint.explain(kind, key, day: day))])
      if kind == "metric" { ExplainLocal.refresh(session: session, key: key, day: day) }
      if model == nil {
        if out.removed.contains(docKey) { error = tr("无权限", "No access") }
        else if out.offline && !ExplainLocal.supported(key) { error = tr("需要联网", "Needs a connection") }
      }
    }
  }
}

/// The explanation body; `head` shows the big current value (off when the sheet already shows the number).
struct ExplainBody: View {
  let kind: String
  let key: String
  var day: String?
  var head = true
  @EnvironmentObject private var session: AccountSession
  @StateObject private var loader = ExplainLoader()

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      if let m = loader.model { content(m) }
      else if let e = loader.error { ToneBadge(text: e).padding(.top, 12) }
      else { ProgressView().tint(Palette.text3).frame(maxWidth: .infinity).padding(.vertical, 24) }
    }
    .task { loader.start(session, kind: kind, key: key, day: day) }
  }

  @ViewBuilder private func content(_ m: ExplainModel) -> some View {
    if head, let v = m.currentValue {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text(v).font(.system(size: 34, weight: .bold).monospacedDigit()).foregroundColor(Palette.text)
        if let u = m.currentUnit { Text(u).font(Typo.caption).foregroundColor(Palette.text2) }
        Spacer()
        if let b = m.currentBasis, !b.isEmpty { ToneBadge(text: b, tone: m.currentTone) }
      }
      .padding(.top, 4)
    }
    if !m.facts.isEmpty {
      VStack(spacing: 0) { ForEach(Array(m.facts.enumerated()), id: \.offset) { _, f in KVRow(label: f.0, value: f.1) } }
        .padding(.top, 6)
    }
    if let miss = m.missing, m.currentValue == nil { ToneBadge(text: miss).padding(.top, 10) }
    if !m.advice.isEmpty {
      heading(tr("建议", "Advice"))
      VStack(alignment: .leading, spacing: 8) {
        ForEach(Array(m.advice.enumerated()), id: \.offset) { _, a in bullet(a.text, tone: a.tone) }
      }
    }
    if let w = m.what { heading(tr("是什么", "What it is")); para(w) }
    if !m.read.isEmpty {
      heading(tr("怎么看", "How to read it"))
      VStack(spacing: 0) {
        ForEach(Array(m.read.enumerated()), id: \.offset) { _, r in KVRow(label: r.range, badge: (r.label, r.tone)) }
      }
    }
    if let r = m.recovery { heading(m.recoveryHead); para(r) }
    list(tr("注意", "Notes"), m.tips, tone: .text)
    list(tr("避免", "Avoid"), m.avoid, tone: .bad)
    list(tr("小心做", "With care"), m.caution, tone: .mid)
    list(tr("你的档案", "Your profile"), m.profile, tone: .sleep)
    if let p = m.aiPrompt { AskAIButton(prompt: p).padding(.top, 20) }
  }

  private func heading(_ t: String) -> some View {
    Text(t).font(Typo.section).foregroundColor(Palette.text2).padding(.top, 18).padding(.bottom, 6)
  }

  private func para(_ t: String) -> some View {
    Text(t).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true).lineSpacing(3)
  }

  private func bullet(_ t: String, tone: Tone) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: 8) {
      Circle().fill(tone == .text ? Palette.text3 : tone.color).frame(width: 6, height: 6).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 1 }
      Text(t).font(Typo.body).foregroundColor(Palette.text).fixedSize(horizontal: false, vertical: true)
    }
  }

  @ViewBuilder private func list(_ title: String, _ items: [String], tone: Tone) -> some View {
    if !items.isEmpty {
      heading(title)
      VStack(alignment: .leading, spacing: 8) { ForEach(Array(items.enumerated()), id: \.offset) { _, t in bullet(t, tone: tone) } }
    }
  }
}

/// 2.0.0 package M6 (docs/ios-native-2.0.md section 4): the personal part of a metric's explanation sheet computed on the
/// phone with the bundle's `explainMetric` (src/37_explain.js, the twin of explain/personal.py: rules, never an AI) from the
/// local tables — the rows explain/personal.metric_data reads on the server. The static texts (what it is, how to read
/// it, tips) are the glossary's: they come from the server's cached sheet of the same metric (without one: the phone's
/// facts and advice, the key as the title). Lab and status sheets stay the server's (their data lives there).
///
/// Package `rules` (flag `native_rules`): shadow = computed and compared with the server's sheet (meta `explain.compare`,
/// field paths only), the sheet shows the server's; primary = the sheet shows the phone's once computed.
enum ExplainLocal {
  static let compareMeta = "explain.compare"
  static let scoreKeys: Set<String> = ["sleep_hours", "sleep_score", "sleep_debt", "sleep_consistency", "recovery"]
  static let dailyMetric = ["hrv": "heart_rate_variability_sdnn", "resting_hr": "resting_heart_rate"]
  /// explain/personal.py: the 60-day baseline before the 7-day window (scores params BASELINE_DAYS)
  static let baselineDays = 66
  static let trendDays = 30

  static func key(_ kind: String, _ key: String, _ day: String?) -> String { "explain.local:\(kind):\(key):\(day ?? "")" }

  /// The metrics the phone explains from its own tables (Trends metrics too, from the phone's 30-day series document).
  static func supported(_ key: String) -> Bool {
    scoreKeys.contains(key) || dailyMetric[key] != nil || ["strain", "sleep_efficiency", "bedtime", "weight", "vo2max"].contains(key)
  }

  @MainActor private static var running: Set<String> = []

  @MainActor
  static func refresh(session: AccountSession, key: String, day: String?) {
    let job = Self.key("metric", key, day)
    guard !running.contains(job) else { return }
    running.insert(job)
    let db = session.db, api = session.api, tz = TrendsStore.timezone(session)
    Task.detached(priority: .utility) {
      defer { Task { @MainActor in running.remove(job) } }
      guard await currentMode(db: db, api: api) != .off else { return }
      let zone = TimeZone(identifier: tz) ?? .current
      guard let rt = try? AlgoBundles(db: db).runtime() else { return }
      _ = try? compute(key: key, day: day, db: db, runtime: rt, tz: tz, today: LocalDay.string(Date(), zone))
    }
  }

  static func currentMode(db: AppDatabase, api: APIClient) async -> ComputeState.Mode {
    if let d = try? db.doc(LocalCompute.stateDoc), Date().timeIntervalSince1970 - d.fetchedAt < SeriesLocal.stateMaxAge {
      return ComputeState.decode(d.body).mode(RulesStage.package)
    }
    return await LocalCompute(db: db, api: api).fetchState().mode(RulesStage.package)
  }

  /// `explain.local:metric:<key>:<day>`; compared with the server's sheet when cached. nil when the phone has no input.
  @discardableResult
  static func compute(key: String, day: String?, db: AppDatabase, runtime rt: OpenBodyScoreRuntime, tz: String,
                      today: String) throws -> [String: Any]? {
    let d = day ?? today
    let server = try db.doc(TodayDocs.explain("metric", key, day))?.json
    guard let data = try db.writer.read({ try Self.data($0, key: key, day: d, tz: tz, today: today) }) else { return nil }
    _ = try? rt.invoke("setLanguage", [Lang.code])
    guard var out = try rt.invoke("explainMetric", [key, entry(server, key: key), data, d]) as? [String: Any] else { return nil }
    out["computed_at"] = Date().timeIntervalSince1970
    try db.saveDoc(Self.key("metric", key, day), json: try JSONSerialization.data(withJSONObject: out))
    if let server {
      let personal: Set<String> = ["current", "facts", "advice", "missing", "ai_prompt"]
      let c = SeriesLocal.compare(local: out.filter { personal.contains($0.key) }, server: server.filter { personal.contains($0.key) })
      record(key, c, db: db)
    }
    return out
  }

  /// The static texts of the sheet (the glossary entry `explainMetric` builds around) from the server's sheet.
  static func entry(_ server: [String: Any]?, key: String) -> [String: Any] {
    guard let s = server else { return ["title": key, "what": NSNull(), "read": [Any](), "tips": [Any](), "avoid": [Any]()] }
    let read: [[Any]] = ((s["read"] as? [[String: Any]]) ?? []).map { [$0["range"] ?? "", $0["label"] ?? "", $0["tone"] ?? NSNull()] }
    return ["title": s["title"] ?? key, "what": s["what"] ?? NSNull(), "read": read, "recovery": s["recovery"] ?? NSNull(),
            "tips": s["tips"] ?? [Any](), "avoid": s["avoid"] ?? [Any]()]
  }

  /// explain/personal.metric_data on the local tables (JSON, ISO dates); nil when the phone has nothing for that metric.
  static func data(_ db: Database, key: String, day: String, tz: String, today: String) throws -> [String: Any]? {
    let nz = SeriesLocal.nz
    if let metric = dailyMetric[key] {
      var values: [String: Double] = [:]
      for r in try Row.fetchAll(db, sql: """
        SELECT date, value FROM daily_metrics WHERE metric = ? AND value IS NOT NULL AND date BETWEEN ? AND ?
        """, arguments: [metric, LocalDay.add(day, -baselineDays), day]) {
        values[r["date"] as String] = r["value"] as Double
      }
      return ["values": values]
    }
    if scoreKeys.contains(key) || key == "strain" {
      let n = key == "strain" ? 8 : 7
      let rows = try SeriesLocal.scoreRows(db, from: LocalDay.add(day, -(n - 1)), to: day).map { r -> [String: Any] in
        var row: [String: Any] = [:]
        for k in ["date", "recovery", "sleep_score", "sleep_actual_min", "sleep_need_min", "sleep_debt_min", "strain", "tsb", "details"] {
          row[k] = nz(r[k] is NSNull ? nil : r[k])
        }
        return row
      }
      return ["scores": rows]
    }
    switch key {
    case "sleep_efficiency":
      let nights = try SeriesLocal.mainSleeps(db, from: LocalDay.add(day, -6), to: day).map { s -> [String: Any] in
        let inBed: Any = { if let a = TodayModel.num(s["start_at"]), let b = TodayModel.num(s["end_at"]) { return (b - a) / 60000 }; return NSNull() }()
        return ["wake_date": s["wake_date"] ?? NSNull(), "asleep_min": s["asleep_min"] ?? NSNull(), "in_bed": inBed]
      }
      return ["nights": nights]
    case "bedtime":
      let mains = try SeriesLocal.mainSleeps(db, from: LocalDay.add(day, -13), to: day).compactMap { s -> [String: Any]? in
        guard let a = TodayModel.num(s["start_at"]) else { return nil }
        return ["wake_date": s["wake_date"] ?? NSNull(), "start_at": a]
      }
      return ["tz": tz, "mains": mains]
    case "weight":
      let goal = TodayModel.num((try DocRecord.fetchOne(db, key: SeriesLocal.profileDoc))?.json?["goal_weight_kg"])
      return ["weights": try SeriesLocal.weights(db, from: LocalDay.add(day, -20), to: day), "goal": nz(goal)]
    case "vo2max":
      guard let v = try DocRecord.fetchOne(db, key: SeriesLocal.vo2Key(day == today ? nil : day))?.json else { return nil }
      return ["vo2max": v]
    default:
      guard let s = try DocRecord.fetchOne(db, key: SeriesLocal.trendsKey(trendDays, end: day == today ? nil : day))?.json,
            s["series"] != nil else { return nil }
      return ["series": s]
    }
  }

  static func record(_ key: String, _ c: SeriesLocal.Comparison, db: AppDatabase) {
    var all = (try? db.meta(compareMeta)).flatMap { $0.data(using: .utf8) }
      .flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] } ?? [:]
    all[key] = ["ok": c.ok, "fields_over": c.fieldsOver, "compared": c.compared, "at": Date().timeIntervalSince1970]
    if let data = try? JSONSerialization.data(withJSONObject: all, options: [.sortedKeys]) {
      try? db.setMeta(compareMeta, String(decoding: data, as: UTF8.self))
    }
  }
}
