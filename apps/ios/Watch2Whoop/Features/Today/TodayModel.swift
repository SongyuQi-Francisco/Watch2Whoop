import Foundation

/// What the Today tab draws, built from the today document (`GET /v1/users/me/today`, stored as `today:current`).
/// Same numbers and rules as the page (app.js scoresHtml) and the 1.0.9 native Today (native_ui.dart todayModel):
/// sleep score 0–100 | recovery 0–100 in its zone colour | yesterday's strain on 0–21. N3 extends this (sleep, health
/// monitor, strain target, reminders, explain sheets).
struct TodayModel: Equatable {
  struct Ring: Equatable {
    var key: String
    var label: String
    var value: String?
    var fraction: Double
    var tone: Tone
  }

  var date: String?
  var rings: [Ring]
  var word: String?
  var sub: String?
  var chips: [String]
  var hrv: String?
  var rhr: String?
  var sleep: String?

  static let empty = TodayModel(json: [:])

  init(json: [String: Any]) {
    let scores = json["scores"] as? [String: Any] ?? [:]
    let today = scores["today"] as? [String: Any] ?? [:]
    let yesterday = scores["yesterday"] as? [String: Any] ?? [:]
    let rec = Self.num(today["recovery"]), sleepScore = Self.num(today["sleep_score"]), strain = Self.num(yesterday["strain"])
    date = json["date"] as? String
    rings = [
      Ring(key: "sleep", label: tr("睡眠分", "Sleep score"), value: sleepScore.map { Self.fmt($0) },
           fraction: (sleepScore ?? 0) / 100, tone: .sleep),
      Ring(key: "recovery", label: tr("恢复", "Recovery"), value: rec.map { Self.fmt($0) },
           fraction: (rec ?? 0) / 100, tone: Tone(zone: scores["recovery_zone"] as? String)),
      Ring(key: "strain", label: tr("昨天负荷", "Prev. strain"), value: strain.map { Self.fmt($0, digits: 1) },
           fraction: (strain ?? 0) / 21, tone: .train),
    ]
    let empty = scores["recovery_empty"] as? [String: Any]
    word = (scores["recovery_word"] as? String) ?? (rec == nil ? empty?["label"] as? String : nil)
    if rec == nil, let last = scores["last_recovery"] as? [String: Any], let v = Self.num(last["recovery"]) {
      sub = "\(tr("上次", "Last")) \(Self.fmt(v)) · \(Self.shortDay(last["date"] as? String))"
    }
    var chips: [String] = []
    if let load = (scores["load_status"] as? [String: Any])?["label"] as? String { chips.append(load) }
    if let reminders = json["reminders"] as? [Any], !reminders.isEmpty {
      chips.append(tr("\(reminders.count) 条提醒", "\(reminders.count) reminders"))
    }
    self.chips = chips
    hrv = Self.num(today["hrv_night_ms"]).map { Self.fmt($0) }
    rhr = Self.num(today["rhr_night"]).map { Self.fmt($0) }
    sleep = Self.num(today["sleep_actual_min"]).map { Self.hm($0) }
  }

  static func num(_ v: Any?) -> Double? {
    guard let n = v as? NSNumber, !(v is Bool) else { return nil }
    let d = n.doubleValue
    return d.isFinite ? d : nil
  }

  /// I18N.num of the page: at most `digits` decimals, thousands with commas.
  static func fmt(_ v: Double, digits: Int = 0) -> String {
    let f = NumberFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.numberStyle = .decimal
    f.usesGroupingSeparator = true
    f.groupingSeparator = ","
    f.groupingSize = 3
    f.decimalSeparator = "."
    f.maximumFractionDigits = digits
    f.minimumFractionDigits = 0
    f.roundingMode = .halfUp
    return f.string(from: NSNumber(value: v)) ?? "\(v)"
  }

  /// Minutes as h:mm.
  static func hm(_ minutes: Double) -> String {
    let r = Int(minutes.rounded())
    return "\(r / 60):" + String(format: "%02d", r % 60)
  }

  /// 10/25 in Chinese, Oct 25 in English.
  static func shortDay(_ iso: String?) -> String {
    guard let iso, iso.count >= 10 else { return "" }
    let p = iso.prefix(10).split(separator: "-").compactMap { Int($0) }
    guard p.count == 3 else { return "" }
    let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return Lang.isZh ? "\(p[1])/\(p[2])" : "\(months[max(0, min(11, p[1] - 1))]) \(p[2])"
  }
}
