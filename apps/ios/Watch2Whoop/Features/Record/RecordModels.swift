import Foundation

/// The 记录 tab's data (日记 and 训练): server views with the pending writes laid over them. Pure, so tests feed it
/// JSON (RecordTests). Field names are the server's (`/journal…`, `/training/…`, `/exercises`).

enum RecordDocs {
  static let journalItems = "journal:items"
  static let journalFrequent = "journal:frequent"
  static func journal(_ day: String) -> String { "journal:\(day)" }
  static func week(_ monday: String) -> String { "training:week:\(monday)" }
  static func session(_ day: String) -> String { "training:session:\(day)" }
  static func calendar(_ month: String) -> String { "training:calendar:\(month)" }
  static func trainingDay(_ day: String) -> String { "training:day:\(day)" }
  static let muscle = "training:muscle"
  static let rules = "exercise:rules"

  static let kinds = [RecordKind.journal, RecordKind.session, RecordKind.submit]
}

enum RecordKind {
  /// One journal answer (`PUT` / `DELETE /journal/{day}/{key}`); supplement check-ins are journal answers too.
  static let journal = "journal"
  /// The training day's set-by-set draft (`PUT /training/session/{day}`, whole day, last write wins).
  static let session = "training_session"
  static let submit = "training_submit"
}

enum RecordAPI {
  static let journalItems = APIRequest.get("/v1/users/me/journal/items", ["active_only": "true"])
  static let journalFrequent = APIRequest.get("/v1/users/me/journal/frequent")
  static func journal(_ day: String) -> APIRequest { .get("/v1/users/me/journal", ["start": day, "end": day]) }
  static func journalEntry(_ day: String, _ key: String) -> String {
    "/v1/users/me/journal/\(day)/\(key.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? key)"
  }
  static func week(_ day: String) -> APIRequest { .get("/v1/users/me/training/week", ["date": day]) }
  static func session(_ day: String) -> APIRequest { .get("/v1/users/me/training/session", ["date": day]) }
  static func sessionPath(_ day: String) -> String { "/v1/users/me/training/session/\(day)" }
  static func calendar(_ month: String) -> APIRequest { .get("/v1/users/me/training/calendar", ["month": month]) }
  static func day(_ day: String) -> APIRequest { .get("/v1/users/me/training/day/\(day)") }
  static let muscle = APIRequest.get("/v1/users/me/training/muscle-load")
  static let rules = APIRequest.get("/v1/users/me/exercises")
  static let events = "/v1/users/me/events"
}

// MARK: - journal

struct JournalItem: Equatable, Identifiable {
  var key: String
  var label: String
  var isBool: Bool
  var unit: String?
  var category: String
  var id: String { key }

  /// Short name on a tag: no bracketed explanation, no leading 「吃了」, the first of 「a / b」 (journal.js shortLabel).
  var short: String {
    var s = label.replacingOccurrences(of: #"（[^）]*）"#, with: "", options: .regularExpression)
      .replacingOccurrences(of: #"\([^)]*\)"#, with: "", options: .regularExpression)
    for prefix in [tr("吃了", "Took ")] where s.hasPrefix(prefix) { s.removeFirst(prefix.count) }
    s = s.components(separatedBy: " / ").first ?? s
    let t = s.trimmingCharacters(in: .whitespaces)
    return t.isEmpty ? label : t
  }

  static func parse(_ j: [String: Any]) -> JournalItem? {
    guard let key = j["key"] as? String else { return nil }
    return JournalItem(key: key, label: (j["label"] as? String) ?? (j["label_zh"] as? String) ?? key, isBool: (j["kind"] as? String ?? "bool") == "bool",
                       unit: j["unit"] as? String, category: j["category"] as? String ?? "other")
  }

  static func categoryTitle(_ c: String) -> String {
    switch c {
    case "supplement": return tr("补剂", "Supplements")
    case "diet": return tr("饮食", "Diet")
    case "lifestyle": return tr("生活", "Lifestyle")
    case "symptom": return tr("症状", "Symptoms")
    case "training": return tr("训练", "Training")
    default: return tr("其他", "Other")
    }
  }
}

/// One day of the journal: the items (frequent first), the values with pending answers applied.
struct JournalDay: Equatable {
  var day: String
  var items: [JournalItem]
  var frequent: [JournalItem]
  var values: [String: Double]
  var pending: [String: PendingRecord]

  /// Items with something recorded: ticked behaviours and numbers with a value.
  var logged: [JournalItem] { items.filter { $0.isBool ? values[$0.key] == 1 : values[$0.key] != nil } }

  func grouped() -> [(String, [JournalItem])] {
    var order: [String] = []
    var groups: [String: [JournalItem]] = [:]
    for i in items {
      if groups[i.category] == nil { order.append(i.category) }
      groups[i.category, default: []].append(i)
    }
    return order.map { ($0, groups[$0] ?? []) }
  }

  static func build(items: [[String: Any]]?, frequent: [[String: Any]]?, entries: [[String: Any]]?, day: String,
                    pending: [PendingRecord]) -> JournalDay? {
    guard let items else { return nil }
    let all = items.compactMap(JournalItem.parse)
    let byKey = Dictionary(all.map { ($0.key, $0) }, uniquingKeysWith: { a, _ in a })
    let freq = (frequent ?? []).compactMap { ($0["key"] as? String).flatMap { byKey[$0] } }
    var values: [String: Double] = [:]
    for e in entries ?? [] where (e["date"] as? String) == day {
      if let k = e["key"] as? String, let v = RecordFormat.double(e["value"]) { values[k] = v }
    }
    var marks: [String: PendingRecord] = [:]
    for p in pending where p.kind == RecordKind.journal && p.date == day {
      guard let k = p.json["key"] as? String else { continue }
      values[k] = RecordFormat.double(p.json["value"])
      marks[k] = p
    }
    return JournalDay(day: day, items: all, frequent: freq.isEmpty ? Array(all.prefix(12)) : freq, values: values, pending: marks)
  }
}

// MARK: - training

/// An exercise of the day's plan (`/training/session` → sessions[].exercises[]), with its restriction.
struct PlannedExercise: Equatable, Identifiable {
  var slug: String
  var name: String
  /// The catalog's Chinese name: the name the training log keeps (training-day.js sends it too).
  var logName: String
  var status: String         // active | caution | banned
  var reason: String?
  var cues: String?
  var weighted: Bool
  var timed: Bool
  var perSide: Bool
  var prescription: String
  var target: SetRow
  var targetSets: Int
  var last: String?
  var id: String { slug }

  var banned: Bool { status == "banned" }
  var caution: Bool { status == "caution" }

  static func parse(_ c: [String: Any]) -> PlannedExercise? {
    guard let slug = c["slug"] as? String else { return nil }
    let t = c["target"] as? [String: Any] ?? [:]
    let timed = t["duration_s"] != nil && !(t["duration_s"] is NSNull)
    let modality = c["modality"] as? String ?? "weight_reps"
    let name = Lang.isZh ? (c["name_zh"] as? String) : ((c["name_en"] as? String) ?? (c["name_zh"] as? String))
    let last = ((c["last"] as? [[String: Any]])?.first).flatMap { l -> String? in
      guard let d = l["date"] as? String, let sets = l["sets"] as? [String], !sets.isEmpty else { return nil }
      return "\(RecordDay.short(d)) · \(sets.joined(separator: " · "))"
    }
    let reps = RecordFormat.double(t["reps"]) ?? RecordFormat.double(t["rep_low"])
    let lo = RecordFormat.double(t["rep_low"]), hi = RecordFormat.double(t["rep_high"])
    var rx = "\(RecordFormat.num(RecordFormat.double(t["sets"]))) × "
    let secs = RecordFormat.num(RecordFormat.double(t["duration_s"]))
    if timed { rx += tr("\(secs) 秒", "\(secs) s") }
    else if let lo, let hi, lo != hi { rx += "\(RecordFormat.num(lo))–\(RecordFormat.num(hi))" }
    else { rx += RecordFormat.num(reps) }
    if let kg = RecordFormat.double((c["load"] as? [String: Any])?["kg"]) {
      rx += " · \(RecordFormat.num(kg, 1)) kg"
      if ((c["load"] as? [String: Any])?["basis"] as? String) == "estimate" { rx += tr(" 估算", " est.") }
    }
    let perSide = (c["per_side"] as? Bool) ?? false
    if perSide { rx += tr(" · 每侧", " · per side") }
    return PlannedExercise(slug: slug, name: name ?? slug, logName: (c["name_zh"] as? String) ?? name ?? slug, status: c["status"] as? String ?? "active", reason: c["reason"] as? String,
                           cues: c["cues"] as? String, weighted: !timed && modality == "weight_reps", timed: timed, perSide: perSide,
                           prescription: rx,
                           target: SetRow(reps: reps.map(Int.init), weightKg: RecordFormat.double(t["weight_kg"]),
                                          durationS: RecordFormat.double(t["duration_s"]), done: false),
                           targetSets: (t["sets"] as? NSNumber)?.intValue ?? 3, last: last)
  }
}

/// One set of the day's log (the draft the server keeps).
struct SetRow: Equatable {
  var reps: Int?
  var weightKg: Double?
  var durationS: Double?
  var done: Bool

  var json: [String: Any] {
    var o: [String: Any] = ["done": done]
    if let reps { o["reps"] = reps }
    if let weightKg { o["weight_kg"] = weightKg }
    if let durationS { o["duration_s"] = durationS }
    return o
  }

  init(reps: Int?, weightKg: Double?, durationS: Double?, done: Bool) {
    self.reps = reps
    self.weightKg = weightKg
    self.durationS = durationS
    self.done = done
  }

  init(_ j: [String: Any]) {
    reps = RecordFormat.double(j["reps"]).map(Int.init)
    weightKg = RecordFormat.double(j["weight_kg"])
    durationS = RecordFormat.double(j["duration_s"])
    done = (j["done"] as? Bool) ?? false
  }

  var hasValue: Bool { reps != nil || weightKg != nil || durationS != nil }
}

/// The training day (`/training/session?date=`): the plan's exercises, the draft's sets (a pending save wins over the
/// server's copy), whether it was submitted.
struct TrainingSession: Equatable {
  var day: String
  var title: String
  var minutes: Int?
  var restDay: Bool
  var exercises: [PlannedExercise]
  var rows: [String: [SetRow]]
  var canLog: Bool
  var submitted: Bool
  var changedSinceSubmit: Bool
  var readiness: (label: String, tone: Tone)?
  var pendingSave: PendingRecord?
  var pendingSubmit: PendingRecord?

  static func == (a: TrainingSession, b: TrainingSession) -> Bool {
    a.day == b.day && a.exercises == b.exercises && a.rows == b.rows && a.submitted == b.submitted && a.canLog == b.canLog
      && a.pendingSave == b.pendingSave && a.pendingSubmit == b.pendingSubmit && a.changedSinceSubmit == b.changedSinceSubmit
      && a.title == b.title && a.readiness?.label == b.readiness?.label
  }

  var loggable: [PlannedExercise] { exercises.filter { !$0.banned } }
  var doneSets: Int { loggable.reduce(0) { n, e in n + (rows[e.slug] ?? []).filter { $0.done && $0.hasValue }.count } }
  var totalSets: Int { loggable.reduce(0) { $0 + (rows[$1.slug] ?? []).count } }

  /// The PUT body of the draft.
  func payload() -> [String: Any] {
    ["exercises": loggable.map { e in ["slug": e.slug, "name": e.logName, "sets": (rows[e.slug] ?? []).map(\.json)] as [String: Any] }]
  }

  static func build(_ j: [String: Any]?, day: String, pending: [PendingRecord]) -> TrainingSession? {
    guard let j else { return nil }
    let sessions = (j["sessions"] as? [[String: Any]]) ?? []
    let exercises = sessions.flatMap { ($0["exercises"] as? [[String: Any]]) ?? [] }.compactMap(PlannedExercise.parse)
    let log = j["log"] as? [String: Any]
    let save = pending.last { $0.kind == RecordKind.session && $0.date == day }
    let submit = pending.last { $0.kind == RecordKind.submit && $0.date == day }
    let source = save.map { $0.json } ?? log
    var saved: [String: [SetRow]] = [:]
    for e in (source?["exercises"] as? [[String: Any]]) ?? [] {
      guard let slug = e["slug"] as? String else { continue }
      saved[slug] = ((e["sets"] as? [[String: Any]]) ?? []).map(SetRow.init)
    }
    var rows: [String: [SetRow]] = [:]
    for e in exercises where !e.banned {
      rows[e.slug] = saved[e.slug] ?? Array(repeating: e.target, count: max(1, e.targetSets))
    }
    var readiness: (String, Tone)?
    if let r = j["readiness"] as? [String: Any], let label = r["label"] as? String, let tier = r["tier"] as? String,
       tier == "red" || tier == "yellow" {
      readiness = (label, tier == "red" ? .bad : .mid)
    }
    let names = sessions.compactMap { $0["name"] as? String }
    return TrainingSession(day: day, title: names.joined(separator: " · "), minutes: (j["minutes"] as? NSNumber)?.intValue,
                           restDay: (j["rest_day"] as? Bool) ?? false, exercises: exercises, rows: rows,
                           canLog: (j["can_log"] as? Bool) ?? false, submitted: (log?["status"] as? String) == "submitted" && save == nil,
                           changedSinceSubmit: (log?["changed_since_submit"] as? Bool) ?? false,
                           readiness: readiness.map { (label: $0.0, tone: $0.1) }, pendingSave: save, pendingSubmit: submit)
  }
}

/// The week strip (`/training/week`): plan label per day, which days were trained (calendar), cautions.
struct TrainingWeek: Equatable {
  struct Day: Equatable, Identifiable {
    var date: String
    var label: String
    var trained: Bool
    var intensity: String?
    var restDay: Bool
    var planned: [String]
    var id: String { date }
  }

  struct Caution: Equatable, Identifiable {
    var slug: String
    var name: String
    var banned: Bool
    var reason: String?
    var cues: String?
    var id: String { slug }
  }

  var planName: String?
  var days: [Day]
  var cautions: [Caution]
  var readiness: (label: String, tone: Tone)?
  var volume: [(String, Int, Bool)]   // group name, direct sets, in the optimal range

  static func == (a: TrainingWeek, b: TrainingWeek) -> Bool {
    a.planName == b.planName && a.days == b.days && a.cautions == b.cautions && a.readiness?.label == b.readiness?.label
      && a.volume.map(\.0) == b.volume.map(\.0) && a.volume.map(\.1) == b.volume.map(\.1)
  }

  static func build(_ w: [String: Any]?, calendar: [[String: Any]], monday: String) -> TrainingWeek? {
    guard let w else { return nil }
    let done = Dictionary(calendar.compactMap { d -> (String, [String: Any])? in
      (d["date"] as? String).map { ($0, d) }
    }, uniquingKeysWith: { a, _ in a })
    let plan = Dictionary(((w["days"] as? [[String: Any]]) ?? []).compactMap { d -> (String, [String: Any])? in
      (d["date"] as? String).map { ($0, d) }
    }, uniquingKeysWith: { a, _ in a })
    let days = (0..<7).map { k -> Day in
      let iso = RecordDay.add(monday, k)
      let p = plan[iso], d = done[iso]
      let planned = ((p?["planned"] as? [[String: Any]]) ?? []).compactMap { $0["name"] as? String }
      let trained = ((d?["sessions_count"] as? NSNumber)?.intValue ?? 0) > 0 && (d?["intensity"] as? String) != "rest"
      let rest = (p?["rest_day"] as? Bool) ?? false
      let label = planned.first.map(Self.shortPlan) ?? (rest ? tr("休", "Rest") : "")
      return Day(date: iso, label: label, trained: trained, intensity: d?["intensity"] as? String, restDay: rest, planned: planned)
    }
    let cautions = ((w["cautions"] as? [[String: Any]]) ?? []).compactMap { c -> Caution? in
      guard let name = c["name"] as? String else { return nil }
      return Caution(slug: (c["slug"] as? String) ?? name, name: name, banned: (c["status"] as? String) == "banned",
                     reason: c["reason"] as? String, cues: c["cues"] as? String)
    }
    var readiness: (String, Tone)?
    if let r = w["readiness"] as? [String: Any], let label = r["label"] as? String, let tier = r["tier"] as? String, tier != "unknown" {
      readiness = (label, tier == "green" ? .good : tier == "red" ? .bad : .mid)
    }
    let volume = ((w["volume_by_group"] as? [String: Any]) ?? [:]).values.compactMap { $0 as? [String: Any] }
      .compactMap { g -> (String, Int, Bool)? in
        guard let n = g["name_zh"] as? String, let s = (g["direct_sets"] as? NSNumber)?.intValue else { return nil }
        return (n, s, (g["status"] as? String) == "optimal")
      }
      .sorted { $0.1 > $1.1 }
    return TrainingWeek(planName: (w["plan"] as? [String: Any])?["name"] as? String, days: days, cautions: cautions,
                        readiness: readiness.map { (label: $0.0, tone: $0.1) }, volume: volume)
  }

  /// 「上肢 B（推为主）」 → 「上肢 B」.
  static func shortPlan(_ s: String) -> String {
    s.replacingOccurrences(of: #"[（(].*$"#, with: "", options: .regularExpression).trimmingCharacters(in: .whitespaces)
  }
}

/// A month of training (`/training/calendar?month=`).
struct TrainingMonth: Equatable {
  struct Day: Equatable, Identifiable {
    var date: String
    var intensity: String?
    var label: String?
    var sessions: Int
    var strain: Double?
    var future: Bool
    var id: String { date }
  }

  var month: String
  var days: [Day]
  var trainingDays: Int?
  var totalMinutes: Double?
  var totalKcal: Double?
  var longestStreak: Int?

  static func build(_ j: [String: Any]?, month: String) -> TrainingMonth? {
    guard let j else { return nil }
    let days = ((j["days"] as? [[String: Any]]) ?? []).compactMap { d -> Day? in
      guard let date = d["date"] as? String else { return nil }
      return Day(date: date, intensity: d["intensity"] as? String, label: (d["labels"] as? [String])?.first,
                 sessions: (d["sessions_count"] as? NSNumber)?.intValue ?? 0, strain: RecordFormat.double(d["strain"]),
                 future: (d["future"] as? Bool) ?? false)
    }
    let s = j["summary"] as? [String: Any]
    return TrainingMonth(month: month, days: days, trainingDays: (s?["training_days"] as? NSNumber)?.intValue,
                         totalMinutes: RecordFormat.double(s?["total_duration_min"]),
                         totalKcal: RecordFormat.double(s?["total_workout_kcal"]) ?? RecordFormat.double(s?["total_active_kcal"]),
                         longestStreak: (s?["longest_streak"] as? NSNumber)?.intValue)
  }

  static func tone(_ intensity: String?) -> Tone? {
    switch intensity {
    case "light": return .good
    case "moderate": return .mid
    case "hard", "max": return .bad
    default: return nil
    }
  }

  static func add(_ month: String, _ n: Int) -> String {
    let p = month.split(separator: "-").compactMap { Int($0) }
    guard p.count == 2 else { return month }
    let m0 = p[0] * 12 + (p[1] - 1) + n
    return String(format: "%04d-%02d", m0 / 12, m0 % 12 + 1)
  }
}

/// A restricted exercise of the user's own list (`/exercises`: exercise_meta).
struct ExerciseRule: Equatable, Identifiable {
  var ref: String
  var name: String
  var banned: Bool
  var reason: String?
  var cues: String?
  var id: String { ref }

  static func parseList(_ list: [[String: Any]]?) -> [ExerciseRule] {
    (list ?? []).compactMap { r in
      guard let ref = r["exercise_ref"] as? String, let st = r["status"] as? String, st == "banned" || st == "caution" else { return nil }
      let name = Lang.isZh ? (r["name_zh"] as? String) : ((r["name_en"] as? String) ?? (r["name_zh"] as? String))
      return ExerciseRule(ref: ref, name: name ?? ref, banned: st == "banned", reason: r["reason"] as? String, cues: r["cues"] as? String)
    }
    .sorted { ($0.banned ? 0 : 1, $0.name) < ($1.banned ? 0 : 1, $1.name) }
  }
}
