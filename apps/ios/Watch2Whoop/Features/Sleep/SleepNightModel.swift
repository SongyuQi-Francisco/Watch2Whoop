import Foundation

/// One night in full (`GET /v1/users/me/sleep/night[?date=]`, sleep_night/ on the server; the page's sleep-detail.js):
/// times, efficiency, latency, awakenings, the stage timeline and composition, the sleep score's parts, this night against
/// the user's 7 / 30-night averages and the overnight vitals. Numbers only; `ai_prompt` goes to 「问 AI」.
struct SleepNightModel: Equatable {
  struct Stage: Equatable {
    var stage: String
    var offset: Double
    var minutes: Double
    var start: String
    var end: String
  }

  struct Part: Equatable, Identifiable {
    var id: String { key }
    var key: String
    var score: Double?
    var weight: Double
  }

  struct Compare: Equatable, Identifiable {
    var id: String { label }
    var label: String
    var night: String
    var avg7: String
    var avg30: String
  }

  struct Vital: Equatable, Identifiable {
    var id: String { key }
    var key: String
    var label: String
    var value: String
    var unit: String
    var delta: String?
    var worse: Bool
  }

  var date: String
  var prev: String?
  var next: String?
  var hasMain = false
  var start = "", end = ""
  var asleep: Double?
  var inBed: Double?
  var efficiency: Double?
  var latency: Double?
  var awakenings: Double?
  var staged = false
  var stages: [Stage] = []
  var composition: [(String, Double, Double)] = []   // stage, minutes, percent
  var naps: [String] = []
  var score: Double?
  var parts: [Part] = []
  var need: Double?, actual: Double?, debt: Double?, performance: Double?
  var compare: [Compare] = []
  var regularity7: Double?, regularity30: Double?
  var vitals: [Vital] = []
  var aiPrompt: String?

  /// The stage timeline's lanes top to bottom (the Apple Watch sleep page's order); unspecified sleep is drawn as core.
  static let lanes = ["awake", "rem", "core", "deep"]

  init(json: [String: Any]) {
    date = json["date"] as? String ?? ""
    prev = json["prev"] as? String
    next = json["next"] as? String
    aiPrompt = json["ai_prompt"] as? String
    naps = ((json["naps"] as? [[String: Any]]) ?? []).compactMap { n in
      guard let a = n["start"] as? String, let b = n["end"] as? String else { return nil }
      return "\(a)–\(b)"
    }
    let n = TodayModel.num
    if let m = json["main"] as? [String: Any] {
      hasMain = true
      start = m["start"] as? String ?? ""
      end = m["end"] as? String ?? ""
      asleep = n(m["asleep_min"]); inBed = n(m["in_bed_min"]); efficiency = n(m["efficiency_pct"])
      latency = n(m["latency_min"]); awakenings = n(m["awakenings"])
      staged = m["staged"] as? Bool ?? false
      stages = ((m["stages"] as? [[String: Any]]) ?? []).compactMap { s in
        guard let st = s["stage"] as? String, let o = n(s["offset_min"]), let mins = n(s["minutes"]) else { return nil }
        return Stage(stage: st, offset: o, minutes: mins, start: s["start"] as? String ?? "", end: s["end"] as? String ?? "")
      }
      composition = ((m["composition"] as? [[String: Any]]) ?? []).compactMap { c in
        guard let st = c["stage"] as? String else { return nil }
        return (st, n(c["min"]) ?? 0, n(c["pct"]) ?? 0)
      }
    }
    if let s = json["score"] as? [String: Any] {
      score = n(s["value"])
      parts = ((s["parts"] as? [[String: Any]]) ?? []).compactMap { p in
        (p["key"] as? String).map { Part(key: $0, score: n(p["score"]), weight: n(p["weight"]) ?? 0) }
      }
      need = n(s["need_min"]); actual = n(s["actual_min"]); debt = n(s["debt_min"]); performance = n(s["performance_pct"])
    }
    let b = json["baseline"] as? [String: Any] ?? [:]
    let a7 = b["avg7"] as? [String: Any] ?? [:], a30 = b["avg30"] as? [String: Any] ?? [:]
    let hm = { (v: Any?) in n(v).map(TodayModel.hm) ?? "—" }
    let pct = { (v: Any?) in n(v).map { "\(TodayModel.fmt($0))%" } ?? "—" }
    let txt = { (v: Any?) in (v as? String) ?? "—" }
    if hasMain, let m = json["main"] as? [String: Any] {
      compare = [
        Compare(label: tr("睡着", "Asleep"), night: hm(m["asleep_min"]), avg7: hm(a7["asleep_min"]), avg30: hm(a30["asleep_min"])),
        Compare(label: tr("入睡", "Bedtime"), night: start, avg7: txt(a7["bedtime"]), avg30: txt(a30["bedtime"])),
        Compare(label: tr("醒来", "Wake"), night: end, avg7: txt(a7["wake"]), avg30: txt(a30["wake"])),
        Compare(label: tr("效率", "Efficiency"), night: pct(m["efficiency_pct"]), avg7: pct(a7["efficiency_pct"]), avg30: pct(a30["efficiency_pct"])),
      ]
      if staged {
        compare.append(Compare(label: tr("深睡", "Deep"), night: hm(m["deep_min"]), avg7: hm(a7["deep_min"]), avg30: hm(a30["deep_min"])))
        compare.append(Compare(label: "REM", night: hm(m["rem_min"]), avg7: hm(a7["rem_min"]), avg30: hm(a30["rem_min"])))
      }
      if let sc = score {
        compare.append(Compare(label: tr("睡眠分", "Sleep score"), night: TodayModel.fmt(sc),
                               avg7: n(a7["sleep_score"]).map { TodayModel.fmt($0) } ?? "—",
                               avg30: n(a30["sleep_score"]).map { TodayModel.fmt($0) } ?? "—"))
      }
    }
    regularity7 = n(b["regularity_pct_7d"]); regularity30 = n(b["regularity_pct_30d"])
    // label, digits, which direction is worse (−1 lower, 1 higher, 0 either way), threshold for colouring the delta
    let spec: [String: (String, Int, Double, Double)] = [
      "hrv": ("HRV", 0, -1, 5), "rhr": (tr("最低心率", "Lowest HR"), 0, 1, 3), "resp_rate": (tr("呼吸", "Breathing"), 1, 1, 1),
      "spo2": ("SpO₂", 1, -1, 1.5), "wrist_temp": (tr("腕温", "Wrist temp"), 2, 0, 0.4),
    ]
    vitals = ((json["vitals"] as? [[String: Any]]) ?? []).compactMap { v in
      guard let key = v["key"] as? String, let s = spec[key], let value = n(v["value"]) else { return nil }
      var delta: String?, worse = false
      if let d = n(v["delta"]) {
        worse = s.2 == 0 ? abs(d) >= s.3 : d * s.2 >= s.3
        delta = abs(d) < 0.5 / pow(10, Double(s.1)) ? "±0" : (d > 0 ? "+" : "") + TodayModel.fmt(d, digits: s.1)
      }
      let unit = v["unit"] as? String ?? ""
      return Vital(key: key, label: s.0, value: TodayModel.fmt(value, digits: s.1),
                   unit: unit == "rpm" ? tr("次/分", "/min") : unit, delta: delta, worse: worse)
    }
  }

  static func stageName(_ s: String) -> String {
    switch s {
    case "deep": return tr("深睡", "Deep")
    case "core": return tr("核心", "Core")
    case "rem": return "REM"
    case "awake": return tr("清醒", "Awake")
    default: return tr("未分阶段", "Unstaged")
    }
  }

  static func partName(_ k: String) -> String {
    switch k {
    case "duration": return tr("时长", "Duration")
    case "stages": return tr("睡眠阶段", "Stages")
    case "consistency": return tr("规律性", "Consistency")
    case "interruptions": return tr("中途醒来", "Interruptions")
    default: return k
    }
  }

  static func == (a: SleepNightModel, b: SleepNightModel) -> Bool {
    a.date == b.date && a.stages == b.stages && a.score == b.score && a.compare == b.compare && a.vitals == b.vitals
      && a.asleep == b.asleep && a.prev == b.prev && a.next == b.next && a.parts == b.parts
  }
}
