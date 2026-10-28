import Foundation

/// The documents the Today tab is drawn from, as they are in the local database (section 5.2: server views kept as they
/// came until N2's tables carry them), plus the phone's own score rows.
struct TodayInputs {
  /// `GET /v1/users/me/today[?date=]`: scores, sleep, reminders.
  var today: [String: Any]?
  /// `GET /training/day/{date}` → `daily`: steps, active energy, exercise minutes.
  var activity: [String: Any]?
  /// `GET /strain-coach?date=`.
  var coach: [String: Any]?
  /// `GET /health-monitor?date=`.
  var monitor: [String: Any]?
  /// `GET /memory` → `status`: the body status cards.
  var memory: [String: Any]?
  /// `GET /sync/overview?in_app=1`.
  var sync: [String: Any]?
  /// `GET /v1/friends/overview` (N7 owns the page; Today only shows the entry).
  var circles: [String: Any]?
  /// Rows the phone computed (origin `app`) for the day and the day before, used only where the server has no number.
  var localDay: DailyScore?
  var localPrev: DailyScore?
  /// The user's own today (to tell a look-back day).
  var userToday: String?
  var circlesOn = false
}

/// Everything the Today tab draws, built from `TodayInputs`. Same numbers, rules and short labels as the page (app.js
/// scoresHtml / activityHtml / strainCoachHtml / monitorHtml / remindersHtml / statusHtml, ui-ai-first.js today) and the
/// 1.0.9 native Today (native_ui.dart todayModel). Only numbers and short labels: the texts stay in the documents for
/// the explain sheets and the AI.
struct TodayModel: Equatable {
  enum Action: Equatable {
    case recovery, strain, load, coach, monitor, weight, allReminders
    case sleepDetail(String?)
    case explainMetric(String, String)
    case explainStatus(String, String)
    case reminder(Int)
  }

  struct Ring: Equatable {
    var key: String
    var label: String
    var value: String?
    var fraction: Double
    var tone: Tone
    var action: Action
  }

  struct Metric: Equatable, Identifiable {
    var id: String
    var label: String
    var value: String?
    var unit: String?
    var tone: Tone = .text
    var action: Action?
  }

  struct Section: Equatable, Identifiable {
    var id: String
    var title: String
    var metrics: [Metric]
  }

  struct Coach: Equatable {
    var low: Double
    var high: Double
    var current: Double?
    var stateLabel: String
    var tone: Tone
  }

  struct Vital: Equatable, Identifiable {
    var id: String
    var name: String
    var short: String
    var value: Double?
    var unit: String?
    var low: Double?
    var high: Double?
    var off: Bool
    var missing: String?
    /// Metric key of its explain sheet (rhr / hrv only).
    var explainKey: String?
  }

  struct StatusItem: Equatable, Identifiable {
    var id: String { key }
    var key: String
    var label: String
    var value: String
    var unit: String?
    var tone: Tone
    var trend: String?
    var trendTone: Tone
    var spark: [Double?]
    var labKey: String?
  }

  struct StatusGroup: Equatable, Identifiable {
    var id: String { name }
    var name: String
    var items: [StatusItem]
  }

  struct Reminder: Equatable, Identifiable {
    var id: Int
    var title: String
    var level: String
    var status: String
    var date: String?
    var tone: Tone {
      switch level {
      case "alert": return .bad
      case "warning": return .mid
      default: return .text
      }
    }
  }

  struct SyncCard: Equatable {
    var state: String
    var label: String
    var tone: Tone
    var ago: String?
  }

  struct Circles: Equatable {
    var members: Int
    var hasCircles: Bool
  }

  /// What the recovery / strain / load sheets show (numbers only).
  struct Detail: Equatable {
    var recovery: Double?
    var zone: Tone = .text
    var zoneLabel: String?
    var hrv: Double?
    var rhr: Double?
    var lastRecovery: Double?
    var lastDate: String?
    var strain: Double?
    var strainDate: String?
    var muscleLoad: Double?
    var ctl: Double?
    var atl: Double?
    var tsb: Double?
    var loadLabel: String?
    var loadMissingDays: Int = 0
    var flags: [String] = []
    var strainFlags: [String] = []
  }

  var date: String?
  var isPast = false
  var rings: [Ring]
  var word: String?
  var wordMuted = false
  var sub: String?
  /// The rings come from the phone's own computation (no server score for this day yet).
  var local = false
  var loadChip: String?
  var sections: [Section] = []
  var coach: Coach?
  var vitals: [Vital] = []
  var vitalsOff = 0
  var status: [StatusGroup] = []
  var reminders: [Reminder] = []
  var sync: SyncCard?
  var circles: Circles?
  var sleepDate: String?
  var detail = Detail()
  var error: String?
  /// Server's hrv kind and the user's zone (for the phone's own computation).
  var hrvKind: String?
  var timezone: String?

  static let empty = TodayModel(TodayInputs())

  /// Back-compat for N0's tests and callers: only the today document.
  init(json: [String: Any]) { self.init(TodayInputs(today: json)) }

  init(_ inp: TodayInputs) {
    let doc = inp.today ?? [:]
    let sc = doc["scores"] as? [String: Any] ?? [:]
    let t = sc["today"] as? [String: Any] ?? [:]
    let y = sc["yesterday"] as? [String: Any] ?? [:]
    date = doc["date"] as? String ?? inp.activity?["date"] as? String
    isPast = date != nil && inp.userToday != nil && date != inp.userToday
    timezone = (doc["user"] as? [String: Any])?["timezone"] as? String
    hrvKind = t["hrv_kind"] as? String ?? y["hrv_kind"] as? String
    if sc["error"] is String { error = tr("暂时算不出评分", "Scores unavailable") }

    var rec = Self.num(t["recovery"]), sleepScore = Self.num(t["sleep_score"]), strain = Self.num(y["strain"])
    var zone = Tone(zone: sc["recovery_zone"] as? String)
    // The phone's own rows fill only what the server has not scored (LocalScoring).
    if rec == nil, let l = inp.localDay?.recovery { rec = l; zone = Self.zone(l); local = true }
    if sleepScore == nil, let l = inp.localDay?.sleepScore { sleepScore = l; local = true }
    if strain == nil, let l = inp.localPrev?.strain { strain = l; local = true }

    rings = [
      Ring(key: "sleep", label: tr("睡眠分", "Sleep score"), value: sleepScore.map { Self.fmt($0) },
           fraction: min(1, max(0, (sleepScore ?? 0) / 100)), tone: .sleep, action: .sleepDetail(nil)),
      Ring(key: "recovery", label: tr("恢复", "Recovery"), value: rec.map { Self.fmt($0) },
           fraction: min(1, max(0, (rec ?? 0) / 100)), tone: zone, action: .recovery),
      Ring(key: "strain", label: isPast ? tr("前一天负荷", "Day-before strain") : tr("昨天负荷", "Prev. strain"),
           value: strain.map { Self.fmt($0, digits: 1) }, fraction: min(1, max(0, (strain ?? 0) / 21)), tone: .train,
           action: .strain),
    ]
    let empty = sc["recovery_empty"] as? [String: Any]
    if let w = sc["recovery_word"] as? String { word = w }
    else if rec == nil, let e = empty?["label"] as? String { word = e; wordMuted = true }
    if rec == nil, let last = sc["last_recovery"] as? [String: Any], let v = Self.num(last["recovery"]) {
      sub = "\(tr("上次", "Last")) \(Self.fmt(v)) · \(Self.shortDay(last["date"] as? String))"
    }
    let load = sc["load_status"] as? [String: Any]
    loadChip = load?["label"] as? String

    detail = Detail(
      recovery: rec, zone: zone, zoneLabel: Self.zoneLabel(zone), hrv: Self.num(t["hrv_night_ms"]), rhr: Self.num(t["rhr_night"]),
      lastRecovery: Self.num((sc["last_recovery"] as? [String: Any])?["recovery"]),
      lastDate: (sc["last_recovery"] as? [String: Any])?["date"] as? String,
      strain: strain, strainDate: y["date"] as? String, muscleLoad: Self.num(y["muscle_load"]),
      ctl: Self.num(y["ctl"]), atl: Self.num(y["atl"]), tsb: Self.num(y["tsb"]), loadLabel: loadChip,
      loadMissingDays: (sc["load_missing_days"] as? NSNumber)?.intValue ?? 0,
      flags: Self.flagLabels(t["flags"], skip: ["load_missing", "day_in_progress", "no_raw_hr"]),
      strainFlags: Self.flagLabels(y["flags"], skip: []))

    // ---- core metrics, three per row (native_ui.dart todayModel sections)
    let sleepDoc = doc["sleep"] as? [String: Any]
    sleepDate = sleepDoc?["date"] as? String
    let sleepSrc: [String: Any] = Self.num(t["sleep_actual_min"]) != nil ? t : (sc["last_sleep"] as? [String: Any] ?? t)
    let daily = inp.activity?["daily"] as? [String: Any] ?? [:]
    let hrv = Self.num(t["hrv_night_ms"]) ?? inp.localDay?.hrvMs, rhr = Self.num(t["rhr_night"]) ?? inp.localDay?.rhr
    sections = [
      Section(id: "recovery", title: tr("恢复", "Recovery"), metrics: [
        Metric(id: "hrv", label: "HRV", value: hrv.map { Self.fmt($0) }, unit: "ms", action: .explainMetric("hrv", "HRV")),
        Metric(id: "rhr", label: tr("静息心率", "Resting HR"), value: rhr.map { Self.fmt($0) }, unit: "bpm",
               action: .explainMetric("resting_hr", tr("静息心率", "Resting HR"))),
      ]),
      Section(id: "sleep", title: tr("睡眠", "Sleep"), metrics: [
        Metric(id: "sleep", label: tr("睡眠", "Sleep"), value: Self.hmOpt(sleepSrc["sleep_actual_min"]), action: .sleepDetail(sleepDate)),
        Metric(id: "need", label: tr("需求", "Need"), value: Self.hmOpt(sleepSrc["sleep_need_min"]),
               action: .explainMetric("sleep_need", tr("睡眠需求", "Sleep need"))),
        Metric(id: "debt", label: tr("睡眠债", "Sleep debt"), value: Self.hmOpt(sleepSrc["sleep_debt_min"]),
               action: .explainMetric("sleep_debt", tr("睡眠债", "Sleep debt"))),
      ]),
      Section(id: "activity", title: tr("活动", "Activity"), metrics: [
        Metric(id: "steps", label: tr("步数", "Steps"), value: Self.num(daily["steps"]).map { Self.fmt($0) },
               action: .explainMetric("steps", tr("步数", "Steps"))),
        Metric(id: "active", label: tr("活动", "Activity"), value: Self.num(daily["active_kcal"]).map { Self.fmt($0) }, unit: "kcal",
               action: .explainMetric("active_kcal", tr("活动消耗", "Active energy"))),
        Metric(id: "exercise", label: tr("锻炼", "Exercise"), value: Self.num(daily["exercise_min"]).map { Self.fmt($0) },
               unit: tr("分", "min"), action: .explainMetric("exercise_min", tr("锻炼分钟", "Exercise minutes"))),
      ]),
    ]

    // ---- strain target (app.js strainCoachHtml): hidden on a past day without a strain
    if let c = inp.coach, let low = Self.num(c["target_low"]), let high = Self.num(c["target_high"]) {
      let cur = Self.num(c["current_strain"])
      if !(isPast && cur == nil) {
        let status = c["status"] as? String
        let label: String, tone: Tone
        switch status {
        case "in_range": label = tr("达标", "On target"); tone = .good
        case "above": label = tr("超出", "Over"); tone = .mid
        default:
          if let cur {
            let left = Self.fmt(Self.num(c["remaining_to_low"]) ?? low - cur, digits: 1)
            label = tr("差 \(left)", "\(left) to go")
          } else { label = "—" }
          tone = .text
        }
        coach = Coach(low: low, high: high, current: cur, stateLabel: label, tone: tone)
      }
    }

    // ---- health monitor (app.js monitorHtml): the card only when at least one vital has a value
    let list = (inp.monitor?["metrics"] as? [[String: Any]]) ?? []
    if list.contains(where: { Self.num($0["value"]) != nil }) {
      vitals = list.map { x in
        let key = x["key"] as? String ?? ""
        let value = Self.num(x["value"])
        let st = x["status"] as? String
        return Vital(id: key, name: Self.vitalName(key, x["label"] as? String), short: Self.vitalShort(key, x["label"] as? String),
                     value: value, unit: x["unit"] as? String, low: Self.num(x["low"]), high: Self.num(x["high"]),
                     off: value != nil && (st == "high" || st == "low"), missing: x["missing_reason"] as? String,
                     explainKey: ["rhr": "resting_hr", "hrv": "hrv"][key])
      }
      vitalsOff = vitals.filter(\.off).count
    }

    // ---- body status (me.status cards, grouped like the page)
    var groups: [StatusGroup] = []
    for it in (inp.memory?["status"] as? [[String: Any]]) ?? [] {
      guard let key = it["key"] as? String, let label = it["label"] as? String else { continue }
      let g = it["group"] as? String ?? ""
      let item = StatusItem(
        key: key, label: label, value: Self.statusValue(it["value"]), unit: it["unit"] as? String,
        tone: Self.statusTone(it["tone"] as? String), trend: Self.trendChar(it["trend"] as? String),
        trendTone: Self.trendTone(it["trend"] as? String, good: it["good_direction"] as? String),
        spark: (it["spark"] as? [Any])?.map { Self.num($0) } ?? [], labKey: it["lab_key"] as? String)
      if let i = groups.firstIndex(where: { $0.name == g }) { groups[i].items.append(item) }
      else { groups.append(StatusGroup(name: g, items: [item])) }
    }
    status = groups

    // ---- reminders: new first, then seen (app.js remindersHtml)
    if let list = doc["reminders"] as? [[String: Any]] {
      let all = list.compactMap { r -> Reminder? in
        guard let id = (r["id"] as? NSNumber)?.intValue, let title = r["title"] as? String else { return nil }
        return Reminder(id: id, title: Self.reminderTitle(title), level: r["level"] as? String ?? "info",
                        status: r["status"] as? String ?? "new", date: (r["date"] as? String).map { String($0.prefix(10)) })
      }
      reminders = all.filter { $0.status == "new" } + all.filter { $0.status != "new" }
    }

    // ---- sync card: own data, today only
    if !isPast, let s = inp.sync, let state = s["state"] as? String {
      let tone: Tone
      switch state {
      case "ok": tone = .good
      case "syncing": tone = .sleep
      case "issue": tone = (s["issue"] as? [String: Any])?["tone"] as? String == "bad" ? .bad : .mid
      default: tone = .text
      }
      let mins = (s["minutes_ago"] as? NSNumber)?.intValue
      sync = SyncCard(state: state, label: s["label"] as? String ?? "", tone: tone, ago: mins.map(Self.ago))
    }

    if inp.circlesOn && !isPast {
      let members = (inp.circles?["members"] as? [Any])?.count ?? 0
      circles = Circles(members: members, hasCircles: inp.circles?["has_circles"] as? Bool ?? (members > 0))
    }
  }

  /// Every reminder shown on the card (at most 3).
  var topReminders: [Reminder] { Array(reminders.prefix(3)) }

  // MARK: - formatting (the page's I18N.num / hm / shortDay)

  static func num(_ v: Any?) -> Double? {
    // JSON booleans are NSNumbers too; `is Bool` would also reject the numbers 0 and 1
    guard let n = v as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() else { return nil }
    let d = n.doubleValue
    return d.isFinite ? d : nil
  }

  private static var formatters: [Int: NumberFormatter] = [:]
  private static let formatterLock = NSLock()

  /// I18N.num of the page: at most `digits` decimals, thousands with commas. Formatters are cached (scrolling redraws).
  static func fmt(_ v: Double, digits: Int = 0) -> String {
    formatterLock.lock()
    defer { formatterLock.unlock() }
    let f: NumberFormatter
    if let cached = formatters[digits] { f = cached } else {
      f = NumberFormatter()
      f.locale = Locale(identifier: "en_US_POSIX")
      f.numberStyle = .decimal
      f.usesGroupingSeparator = true
      f.groupingSeparator = ","
      f.groupingSize = 3
      f.decimalSeparator = "."
      f.maximumFractionDigits = digits
      f.minimumFractionDigits = 0
      f.roundingMode = .halfUp
      formatters[digits] = f
    }
    return f.string(from: NSNumber(value: v)) ?? "\(v)"
  }

  /// Minutes as h:mm.
  static func hm(_ minutes: Double) -> String {
    let r = Int(minutes.rounded())
    return "\(r / 60):" + String(format: "%02d", r % 60)
  }

  static func hmOpt(_ v: Any?) -> String? { num(v).map(hm) }

  /// 10/25 in Chinese, Oct 25 in English.
  static func shortDay(_ iso: String?) -> String {
    guard let iso, iso.count >= 10 else { return "" }
    let p = iso.prefix(10).split(separator: "-").compactMap { Int($0) }
    guard p.count == 3 else { return "" }
    return Lang.isZh ? "\(p[1])/\(p[2])" : "\(monthName(p[1])) \(p[2])"
  }

  static func monthName(_ m: Int) -> String {
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][max(0, min(11, m - 1))]
  }

  /// "5 分钟前" / "5 min ago", hours and days beyond that.
  static func ago(_ minutes: Int) -> String {
    if minutes < 1 { return tr("刚刚", "just now") }
    if minutes < 60 { return tr("\(minutes) 分钟前", "\(minutes) min ago") }
    if minutes < 48 * 60 { return tr("\(minutes / 60) 小时前", "\(minutes / 60) h ago") }
    return tr("\(minutes / 1440) 天前", "\(minutes / 1440) d ago")
  }

  static func zone(_ recovery: Double) -> Tone { recovery >= 67 ? .good : recovery >= 34 ? .mid : .bad }

  static func zoneLabel(_ t: Tone) -> String? {
    switch t {
    case .good: return tr("绿", "Green")
    case .mid: return tr("黄", "Yellow")
    case .bad: return tr("红", "Red")
    default: return nil
    }
  }

  /// The page's FLAG_SHORT: data problems of a score row as short labels.
  static func flagLabels(_ flags: Any?, skip: [String]) -> [String] {
    guard let f = flags as? [String: Any] else { return [] }
    let names: [String: String] = [
      "no_sleep": tr("无睡眠", "No sleep"), "hrv_missing": tr("无 HRV", "No HRV"),
      "baseline_insufficient": tr("基线不足", "Short baseline"), "no_raw_hr": tr("无心率", "No heart rate"),
      "load_missing": tr("无负荷", "No load"), "day_in_progress": tr("进行中", "In progress"),
      "hr_coverage_low": tr("心率不全", "Partial HR"), "prev_strain_missing": tr("缺前一天", "No prior day"),
      "muscle_load_unavailable": tr("无力量", "No strength"),
    ]
    return f.keys.sorted().filter { !skip.contains($0) }.compactMap { names[$0] }
  }

  static func vitalName(_ key: String, _ label: String?) -> String {
    switch key {
    case "rhr": return tr("静息心率", "Resting HR")
    case "hrv": return "HRV"
    case "resp_rate": return tr("呼吸", "Breathing")
    case "spo2": return tr("血氧", "Blood oxygen")
    case "wrist_temp": return tr("体温", "Temperature")
    default: return label ?? key
    }
  }

  static func vitalShort(_ key: String, _ label: String?) -> String {
    if Lang.isZh { return key == "rhr" ? tr("静息", "RHR") : vitalName(key, label) }
    return ["rhr": "RHR", "hrv": "HRV", "resp_rate": "Resp.", "spo2": "SpO₂", "wrist_temp": "Temp"][key] ?? (label ?? key)
  }

  static func statusValue(_ v: Any?) -> String {
    if let s = v as? String { return s }
    if let d = num(v) { return fmt(d, digits: 1) }
    return "—"
  }

  static func statusTone(_ s: String?) -> Tone {
    switch s {
    case "good": return .good
    case "warn": return .mid
    case "bad": return .bad
    default: return .text
    }
  }

  static func trendChar(_ t: String?) -> String? {
    switch t {
    case "up": return "↑"
    case "down": return "↓"
    case "flat": return "→"
    default: return nil
    }
  }

  static func trendTone(_ t: String?, good: String?) -> Tone {
    guard let t, t != "flat", let good else { return .text }
    return t == good ? .good : .mid
  }

  /// The page's remTitle: ISO dates in a reminder title become 10/25.
  static func reminderTitle(_ t: String) -> String {
    guard let re = try? NSRegularExpression(pattern: "\\b\\d{4}-(\\d{2})-(\\d{2})\\b") else { return t }
    var out = t
    for m in re.matches(in: t, range: NSRange(t.startIndex..., in: t)).reversed() {
      guard let r = Range(m.range, in: out), let mr = Range(m.range(at: 1), in: t), let dr = Range(m.range(at: 2), in: t),
            let mo = Int(t[mr]), let d = Int(t[dr]) else { continue }
      out.replaceSubrange(r, with: "\(mo)/\(d)")
    }
    return out
  }
}
