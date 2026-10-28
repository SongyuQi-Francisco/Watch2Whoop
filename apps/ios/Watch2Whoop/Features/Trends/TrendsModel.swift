import Foundation

/// The Trends tab's data, parsed once from the `/trends` document (trends/series.py on the server) plus the status cards
/// the phone computed itself (`TrendsLocal`). Plain values only, so SwiftUI can compare and skip redraws.
struct TrendSeries: Equatable {
  struct Band: Equatable { var lo: [Double?]; var hi: [Double?]; var mean: [Double?] }
  struct Target: Equatable { var bands: [ClosedRange<Double>?]; var inRange: Int; var daysWithStrain: Int }
  struct Zones: Equatable { var green = 0, yellow = 0, red = 0 }

  var key: String
  var label: String
  var unit: String
  var group: String
  var values: [Double?]
  var avg7: [Double?]
  var n: Int
  var mean: Double?
  var prevMean: Double?
  var prevN: Int
  var baseline: Band?
  var zones: Zones?
  /// Recovery contributors per day (z-scores vs baseline: hrv, rhr, resp_rate, sleep_performance).
  var contributors: [[String: Double]?] = []
  var target: Target?

  init?(key: String, json: [String: Any]) {
    guard let values = json["values"] as? [Any] else { return nil }
    self.key = key
    label = json["label"] as? String ?? key
    unit = json["unit"] as? String ?? ""
    group = json["group"] as? String ?? ""
    self.values = values.map(TodayModel.num)
    avg7 = (json["avg7"] as? [Any])?.map(TodayModel.num) ?? Array(repeating: nil, count: values.count)
    n = (json["n"] as? NSNumber)?.intValue ?? self.values.compactMap { $0 }.count
    mean = TodayModel.num(json["mean"])
    prevMean = TodayModel.num(json["prev_mean"])
    prevN = (json["prev_n"] as? NSNumber)?.intValue ?? 0
    if let b = json["baseline"] as? [String: Any] {
      baseline = Band(lo: ((b["lo"] as? [Any]) ?? []).map(TodayModel.num), hi: ((b["hi"] as? [Any]) ?? []).map(TodayModel.num),
                      mean: ((b["mean"] as? [Any]) ?? []).map(TodayModel.num))
    }
    if let z = json["zones"] as? [String: Any] {
      zones = Zones(green: (z["green"] as? NSNumber)?.intValue ?? 0, yellow: (z["yellow"] as? NSNumber)?.intValue ?? 0,
                    red: (z["red"] as? NSNumber)?.intValue ?? 0)
    }
    contributors = ((json["contributors"] as? [Any]) ?? []).map { c in
      (c as? [String: Any]).map { d in d.compactMapValues(TodayModel.num) }
    }
    if let t = json["target"] as? [String: Any] {
      let bands: [ClosedRange<Double>?] = ((t["bands"] as? [Any]) ?? []).map { b in
        guard let a = b as? [Any], a.count == 2, let lo = TodayModel.num(a[0]), let hi = TodayModel.num(a[1]), lo <= hi else { return nil }
        return lo...hi
      }
      target = Target(bands: bands, inRange: (t["in_range_days"] as? NSNumber)?.intValue ?? 0,
                      daysWithStrain: (t["days_with_strain"] as? NSNumber)?.intValue ?? 0)
    }
  }

  /// The latest reading in the range.
  var last: Double? { values.last { $0 != nil } ?? nil }
}

struct TrendsData: Equatable {
  struct Week: Equatable { var week: String; var value: Double; var target: Double? }
  struct HRZones: Equatable { var labels: [String]; var totalMin: [Double]; var weeks: [(week: String, minutes: [Double])]
    static func == (a: HRZones, b: HRZones) -> Bool {
      a.labels == b.labels && a.totalMin == b.totalMin && a.weeks.map(\.week) == b.weeks.map(\.week) && a.weeks.map(\.minutes) == b.weeks.map(\.minutes)
    }
  }

  var start: String
  var end: String
  var days: Int
  var prevStart: String
  var dates: [String]
  var series: [String: TrendSeries]
  var unavailable: [String: String]
  var hrZones: HRZones?
  var aerobicWeeks: [Week]
  /// Status cards from the server (as of `end`), before the phone's own values are laid over them.
  var status: [String: Any]

  init?(json: [String: Any]) {
    guard let dates = json["dates"] as? [String], let s = json["series"] as? [String: Any] else { return nil }
    self.dates = dates.map { String($0.prefix(10)) }
    start = String((json["start"] as? String ?? dates.first ?? "").prefix(10))
    end = String((json["end"] as? String ?? dates.last ?? "").prefix(10))
    days = (json["days"] as? NSNumber)?.intValue ?? dates.count
    prevStart = String((json["prev_start"] as? String ?? "").prefix(10))
    var out: [String: TrendSeries] = [:]
    for (k, v) in s { if let j = v as? [String: Any], let t = TrendSeries(key: k, json: j) { out[k] = t } }
    series = out
    unavailable = (json["unavailable"] as? [String: String]) ?? [:]
    if let z = json["hr_zones"] as? [String: Any] {
      hrZones = HRZones(labels: z["labels"] as? [String] ?? [],
                        totalMin: ((z["total_min"] as? [Any]) ?? []).map { TodayModel.num($0) ?? 0 },
                        weeks: ((z["weeks"] as? [[String: Any]]) ?? []).map { w in
                          (String((w["week"] as? String ?? "").prefix(10)), ((w["minutes"] as? [Any]) ?? []).map { TodayModel.num($0) ?? 0 })
                        })
    }
    aerobicWeeks = ((json["aerobic_weeks"] as? [[String: Any]]) ?? []).compactMap { w in
      guard let week = w["week"] as? String else { return nil }
      return Week(week: String(week.prefix(10)), value: TodayModel.num(w["value"]) ?? 0, target: TodayModel.num(w["target"]))
    }
    status = json["status"] as? [String: Any] ?? [:]
  }

  static func == (a: TrendsData, b: TrendsData) -> Bool {
    a.start == b.start && a.end == b.end && a.days == b.days && a.dates == b.dates && a.series == b.series
      && a.hrZones == b.hrZones && a.aerobicWeeks == b.aerobicWeeks && a.unavailable == b.unavailable
      && NSDictionary(dictionary: a.status).isEqual(to: b.status)
  }

  /// Weekly strength volume (sum of the daily muscle load per Monday-first week), as on the page (muscleWeeks).
  var muscleWeeks: [Week] {
    guard let s = series["muscle_load"], s.n > 0 else { return [] }
    var order: [String] = [], sums: [String: Double] = [:]
    for (i, d) in dates.enumerated() {
      let wk = TrendsFormat.mondayOf(d)
      if sums[wk] == nil { order.append(wk) }
      sums[wk, default: 0] += s.values[safe: i].flatMap { $0 } ?? 0
    }
    return order.map { Week(week: $0, value: sums[$0] ?? 0, target: nil) }
  }

  /// Long ranges (or ones crossing a new year) carry the year in their dates.
  var withYear: Bool { days >= 180 || prevStart.prefix(4) != end.prefix(4) }
}

/// Status cards as of the end date (short labels; the sheet has the details): the server's, with the phone's own
/// results laid over them where the algorithm bundle could compute them (`localKeys`).
struct TrendStatus: Equatable {
  struct Tile: Equatable, Identifiable {
    var id: String { key }
    var key: String
    var label: String
    var value: String
    var unit: String?
    var tone: Tone
    var sub: String?
    var rows: [KV]
    var local: Bool
    /// Body battery's points through the day (hour, level 0–100).
    var points: [BatteryPoint] = []
    /// Load focus (inside the training-status sheet): label and percent per group.
    var focus: [(label: String, pct: Double)] = []

    static func == (a: Tile, b: Tile) -> Bool {
      a.key == b.key && a.value == b.value && a.tone == b.tone && a.sub == b.sub && a.rows == b.rows && a.local == b.local
        && a.points == b.points && a.focus.map(\.label) == b.focus.map(\.label) && a.focus.map(\.pct) == b.focus.map(\.pct)
    }
  }
  struct KV: Equatable { var label: String; var value: String }
  struct BatteryPoint: Equatable { var hour: Double; var level: Double }

  var tiles: [Tile]

  static let toneMap: [String: [String: Tone]] = [
    "hrv_status": ["balanced": .good, "high": .text, "low": .mid, "poor": .bad],
    "training_status": ["productive": .good, "maintaining": .text, "recovery": .text, "strained": .mid, "unproductive": .mid,
                        "overreaching": .bad, "detraining": .mid],
    "resilience": ["exceptional": .good, "strong": .good, "solid": .text, "adequate": .mid, "limited": .bad],
    "illness": ["low": .good, "watch": .mid, "high": .bad],
    "recovery_index": ["good": .good, "fair": .mid, "late": .bad],
    "hr_recovery": ["good": .good, "fair": .text, "low": .bad],
  ]

  /// `status`: the merged dictionary (server's keys, the phone's results replacing them); `local`: keys the phone computed.
  init(status: [String: Any], local: Set<String>) {
    let f = { (v: Any?, d: Int) -> String in TodayModel.num(v).map { TodayModel.fmt($0, digits: d) } ?? "—" }
    let tone = { (k: String, s: Any?) -> Tone in Self.toneMap[k]?[s as? String ?? ""] ?? .text }
    var out: [Tile] = []
    if let h = status["hrv_status"] as? [String: Any] {
      let band = (h["band_ms"] as? [Any]) ?? []
      out.append(Tile(key: "hrv_status", label: tr("HRV 状态", "HRV status"), value: h["label"] as? String ?? "—", tone: tone("hrv_status", h["status"]),
                      sub: "\(f(h["avg7_ms"], 0)) ms",
                      rows: [KV(label: tr("7 天均", "7-day mean"), value: "\(f(h["avg7_ms"], 0)) ms"),
                             KV(label: tr("基线带", "Baseline band"), value: band.count == 2 ? "\(f(band[0], 0))–\(f(band[1], 0)) ms" : "—"),
                             KV(label: tr("夜数", "Nights"), value: f(h["nights"], 0))],
                      local: local.contains("hrv_status")))
    }
    if let t = status["training_status"] as? [String: Any] {
      let acwr = f(t["acwr"], 2)
      var tile = Tile(key: "training_status", label: tr("训练状态", "Training status"), value: t["label"] as? String ?? "—",
                      tone: tone("training_status", t["status"]), sub: tr("负荷比 \(acwr)", "ACWR \(acwr)"),
                      rows: [KV(label: tr("急慢性负荷比", "Acute:chronic"), value: f(t["acwr"], 2)),
                             KV(label: tr("7 天 / 28 天 TRIMP", "7 / 28-day TRIMP"), value: "\(f(t["acute"], 0)) / \(f(t["chronic"], 0))"),
                             KV(label: tr("VO₂max 60 天", "VO₂max 60 days"), value: TodayModel.num(t["vo2_change_60d"]).map(TrendsFormat.signed1) ?? "—")],
                      local: local.contains("training_status"))
      if let lf = status["load_focus"] as? [String: Any], let groups = lf["groups"] as? [[String: Any]] {
        tile.focus = groups.map { ($0["label"] as? String ?? "", TodayModel.num($0["pct"]) ?? 0) }
        tile.local = tile.local && local.contains("load_focus")
      }
      out.append(tile)
    }
    if let r = status["resilience"] as? [String: Any] {
      out.append(Tile(key: "resilience", label: tr("韧性", "Resilience"), value: r["label"] as? String ?? "—", tone: tone("resilience", r["level"]),
                      sub: tr("14 天", "14 days"),
                      rows: [KV(label: tr("恢复能力", "Capacity"), value: TodayModel.num(r["capacity"]).map { TodayModel.fmt($0 * 100) } ?? "—"),
                             KV(label: tr("压力负担", "Stress load"), value: TodayModel.num(r["stress_load"]).map { TodayModel.fmt($0 * 100) } ?? "—")],
                      local: local.contains("resilience")))
    }
    if let il = status["illness"] as? [String: Any] {
      out.append(Tile(key: "illness", label: tr("生病风险", "Illness risk"), value: il["label"] as? String ?? "—", tone: tone("illness", il["level"]),
                      sub: f(il["score"], 0), rows: [KV(label: tr("分数", "Score"), value: "\(f(il["score"], 0)) / 100")],
                      local: local.contains("illness")))
    }
    if let bb = status["body_battery"] as? [String: Any], let cur = TodayModel.num(bb["current"]) {
      out.append(Tile(key: "body_battery", label: tr("身体电量", "Body battery"), value: TodayModel.fmt(cur),
                      tone: cur >= 60 ? .good : cur >= 30 ? .mid : .bad, sub: tr("估算", "Estimate"),
                      rows: [KV(label: tr("醒来", "On waking"), value: f(bb["wake"], 0)), KV(label: tr("最低", "Lowest"), value: f(bb["low"], 0))],
                      local: local.contains("body_battery"),
                      points: ((bb["points"] as? [[String: Any]]) ?? []).compactMap { p in
                        guard let h = TodayModel.num(p["hour"]), let v = TodayModel.num(p["v"]) else { return nil }
                        return BatteryPoint(hour: h, level: v)
                      }))
    }
    if let rt = status["recovery_time"] as? [String: Any] {
      let h = TodayModel.num(rt["hours"]) ?? 0
      var rows = [KV(label: tr("还需", "Still needs"), value: tr("\(TodayModel.fmt(h)) 小时", "\(TodayModel.fmt(h)) h"))]
      if let d = rt["from_date"] as? String {
        rows.append(KV(label: tr("来自", "From"), value: "\(TodayModel.shortDay(d)) · \(f(rt["from_strain"], 1))"))
      }
      out.append(Tile(key: "recovery_time", label: tr("恢复时间", "Recovery time"), value: h > 0 ? TodayModel.fmt(h) : tr("已恢复", "Recovered"),
                      unit: h > 0 ? tr("小时", "h") : nil, tone: h > 24 ? .mid : h > 0 ? .text : .good, sub: tr("估算", "Estimate"),
                      rows: rows, local: local.contains("recovery_time")))
    }
    if let ri = status["recovery_index"] as? [String: Any] {
      out.append(Tile(key: "recovery_index", label: tr("最低心率", "Lowest HR"), value: ri["label"] as? String ?? "—",
                      tone: tone("recovery_index", ri["level"]), sub: "\(f(ri["pct"], 0))%",
                      rows: [KV(label: tr("出现在整晚的", "Reached at"), value: "\(f(ri["pct"], 0))%")], local: local.contains("recovery_index")))
    }
    if let hr = status["hr_recovery"] as? [String: Any] {
      out.append(Tile(key: "hr_recovery", label: tr("心率恢复", "HR recovery"), value: f(hr["bpm"], 0), unit: "bpm",
                      tone: tone("hr_recovery", hr["level"]), sub: TodayModel.shortDay(hr["date"] as? String),
                      rows: [KV(label: tr("1 分钟下降", "Drop in 1 min"), value: "\(f(hr["bpm"], 0)) bpm")], local: local.contains("hr_recovery")))
    }
    if let c = status["chronotype"] as? [String: Any] {
      let mid = c["mid_sleep"] as? String ?? "—"
      out.append(Tile(key: "chronotype", label: tr("生物钟", "Chronotype"), value: c["label"] as? String ?? "—", tone: .text,
                      sub: "\(tr("中点", "Mid")) \(mid)",
                      rows: [KV(label: tr("睡眠中点", "Mid-sleep"), value: mid), KV(label: tr("夜数", "Nights"), value: f(c["nights"], 0)),
                             KV(label: tr("按休息日", "Free days"), value: (c["free_days"] as? Bool ?? false) ? tr("是", "Yes") : tr("否", "No"))],
                      local: local.contains("chronotype")))
    }
    tiles = out
  }
}

/// Small formatting helpers of the Trends tab (the page's shortDay / yearDay / signed).
enum TrendsFormat {
  static func signed1(_ v: Double) -> String { (v > 0 ? "+" : "") + TodayModel.fmt(v, digits: 1) }
  static func signed(_ v: Double, digits: Int) -> String { (v > 0 ? "+" : v < 0 ? "−" : "") + TodayModel.fmt(abs(v), digits: digits) }

  /// `2025/11/3` / `11/3/25`.
  static func yearDay(_ iso: String) -> String {
    let p = iso.prefix(10).split(separator: "-").compactMap { Int($0) }
    guard p.count == 3 else { return "" }
    return Lang.isZh ? "\(p[0])/\(p[1])/\(p[2])" : "\(p[1])/\(p[2])/\(String(format: "%02d", p[0] % 100))"
  }

  static func day(_ iso: String, year: Bool) -> String { year ? yearDay(iso) : TodayModel.shortDay(iso) }

  /// Half a year or more apart, or in different years: axis labels carry the year (the page's spansYear).
  static func spansYear(_ a: String, _ b: String) -> Bool {
    if a.prefix(4) != b.prefix(4) { return true }
    return daysBetween(a, b) >= 179
  }

  static func daysBetween(_ a: String, _ b: String) -> Int {
    let utc = TimeZone(identifier: "UTC")!
    guard let x = LocalDay.parse(a, utc), let y = LocalDay.parse(b, utc) else { return 0 }
    return Int((y.timeIntervalSince(x) / 86_400).rounded())
  }

  static func mondayOf(_ iso: String) -> String {
    let utc = TimeZone(identifier: "UTC")!
    guard let d = LocalDay.parse(iso, utc) else { return iso }
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = utc
    let back = (cal.component(.weekday, from: d) + 5) % 7
    return LocalDay.add(iso, -back)
  }

  /// "7:30" from minutes.
  static func hm(_ minutes: Double?) -> String? {
    guard let m = minutes else { return nil }
    let r = Int(m.rounded())
    return "\(r / 60):\(String(format: "%02d", r % 60))"
  }
}

extension Array {
  subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}
