import Foundation

/// The exercise library, the plan's edits, custom exercises and training settings (web: app.js exerciseDetailSheet /
/// exerciseLibrarySheet / splitPickerSheet, exercise-plan.js, custom-exercises.js, muscle-load.js): the server's answers
/// reduced to what the pages draw. Pure, so tests feed them JSON (TrainingParityTests). Whether an exercise is banned, and
/// what it may be replaced with, is the server's call: `status` and `alternatives` are shown as they come.

enum ExerciseAPI {
  private static let base = "/v1/users/me/training"

  static func search(query: String, group: String, equipment: String) -> APIRequest {
    var q = ["limit": "100"]
    let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
    if !text.isEmpty { q["q"] = text }
    if !group.isEmpty { q["muscle"] = group }
    if !equipment.isEmpty { q["equipment"] = equipment }
    return .get("\(base)/exercises", q)
  }

  // Paths are plain here: APIClient percent-encodes them (a muscle key like "middle back", a name standing in for a slug).
  static func exercise(_ slug: String) -> APIRequest { .get("\(base)/exercises/\(slug)") }
  static func muscle(_ key: String) -> APIRequest { .get("\(base)/muscle-load/\(key)") }
  static func addOptions(_ slug: String) -> APIRequest { .get("\(base)/plan/add-options", ["exercise": slug]) }
  static let goals = "\(base)/goals"
  static let plan = "\(base)/plan"
  static let addExercise = "\(base)/plan/add-exercise"
  static let removeExercise = "\(base)/plan/remove-exercise"
  static let restoreExercise = "\(base)/plan/restore-exercise"
  static let customExercises = "\(base)/custom-exercises"
  static func customExercise(_ id: Int) -> String { "\(base)/custom-exercises/\(id)" }
  static let connectLog = "\(base)/sparky/connect"
}

enum ExerciseDocs {
  static func exercise(_ slug: String) -> String { "exercise:detail:\(slug)" }
  static func muscle(_ key: String) -> String { "training:muscle:\(key)" }
}

/// The catalog's words in the app's language. The server sends Chinese next to the keys; the English of a key lives here
/// (the page's is i18n-en.js), the same wording as `training/muscle_load.py MUSCLE_EN` and `custom.py EQUIPMENT_EN`.
enum ExerciseVocab {
  private static let muscleEn = ["chest": "Chest", "lats": "Lats", "middle back": "Mid back", "lower back": "Lower back", "traps": "Traps",
                                 "shoulders": "Shoulders", "quadriceps": "Quads", "hamstrings": "Hamstrings", "glutes": "Glutes", "calves": "Calves",
                                 "biceps": "Biceps", "triceps": "Triceps", "forearms": "Forearms", "abdominals": "Core / abs", "adductors": "Inner thigh",
                                 "abductors": "Glute med / abductors", "neck": "Neck"]
  private static let equipmentEn = ["barbell": "Barbell", "dumbbell": "Dumbbell", "cable": "Cable", "machine": "Machine", "smith machine": "Smith machine",
                                    "body only": "Bodyweight", "bands": "Bands", "kettlebell": "Kettlebell", "ez curl bar": "EZ bar", "foam roll": "Foam roller",
                                    "other": "Other"]

  static func muscles(keys: [String], zh: [String]) -> [String] {
    if Lang.isZh { return zh.isEmpty ? keys : zh }
    return keys.map { muscleEn[$0] ?? sentence($0) }
  }

  static func equipment(keys: [String], zh: [String]) -> [String] {
    if Lang.isZh { return zh.isEmpty ? keys : zh }
    return keys.map { equipmentEn[$0] ?? sentence($0) }
  }

  static func pattern(key: String?, zh: String?) -> String? {
    if Lang.isZh { return zh ?? key }
    return key.map(sentence)
  }

  /// A joint stress key (`training/catalog_data.py JOINT_STRESS_ZH`).
  static func joint(_ key: String) -> String {
    switch key {
    case "shoulder_anterior": return tr("肩前侧受压", "Front-of-shoulder compression")
    case "shoulder_ir", "shoulder_internal_rotation": return tr("要求肩内旋", "Needs shoulder internal rotation")
    case "upper_trap": return tr("易激活上斜方", "Easily recruits the upper traps")
    case "lumbar": return tr("下背剪切力", "Lower-back shear")
    case "knee": return tr("膝关节负荷", "Knee load")
    case "elbow": return tr("肘关节负荷", "Elbow load")
    case "wrist": return tr("腕关节负荷", "Wrist load")
    default: return sentence(key)
    }
  }

  /// 1 = Monday.
  static func weekday(_ n: Int) -> String {
    let names = [tr("周一", "Mon"), tr("周二", "Tue"), tr("周三", "Wed"), tr("周四", "Thu"), tr("周五", "Fri"), tr("周六", "Sat"), tr("周日", "Sun")]
    return names[max(0, min(6, n - 1))]
  }

  /// The library's muscle group filter (app.js GROUPS_FILTER), `key` as the server's `muscle` parameter.
  static var groupFilters: [(key: String, title: String)] {
    [("", tr("全部", "All")), ("back", tr("背", "Back")), ("chest", tr("胸", "Chest")), ("legs", tr("腿", "Legs")),
     ("shoulders", tr("肩", "Shoulders")), ("arms", tr("手臂", "Arms")), ("core", tr("核心", "Core"))]
  }

  /// The library's equipment filter, `key` as the server's `equipment` parameter.
  static var equipmentFilters: [(key: String, title: String)] {
    [("", tr("全部器械", "All equipment")), ("barbell", tr("杠铃", "Barbell")), ("dumbbell", tr("哑铃", "Dumbbell")),
     ("cable", tr("绳索", "Cable")), ("machine", tr("固定器械", "Machine")), ("smith machine", tr("史密斯机", "Smith machine")),
     ("body only", tr("自重", "Bodyweight")), ("bands", tr("弹力带", "Bands")), ("kettlebell", tr("壶铃", "Kettlebell")),
     ("ez curl bar", tr("曲杆", "EZ bar"))]
  }

  private static func sentence(_ s: String) -> String {
    let t = s.replacingOccurrences(of: "_", with: " ")
    return t.prefix(1).uppercased() + t.dropFirst()
  }
}

/// A name the page language picks from `name_zh` / `name_en`, and a built-in template's name next to its stored Chinese one.
enum ExerciseNames {
  static func exercise(_ j: [String: Any]) -> String? {
    let zh = (j["name_zh"] as? String) ?? (j["name"] as? String)
    return Lang.isZh ? zh : ((j["name_en"] as? String) ?? zh)
  }

  static func template(_ j: [String: Any]?) -> String? {
    (Lang.isZh ? nil : j?["name_en"] as? String) ?? j?["name"] as? String
  }
}

// MARK: - library

struct LibraryExercise: Equatable, Identifiable {
  var slug: String
  var name: String
  var status: String
  var custom: Bool
  var subtitle: String
  var id: String { slug }

  static func parse(_ j: [String: Any]) -> LibraryExercise? {
    guard let slug = j["slug"] as? String else { return nil }
    let muscles = ExerciseVocab.muscles(keys: (j["primary_muscles"] as? [String]) ?? [], zh: (j["primary_muscles_zh"] as? [String]) ?? [])
    let gear = ExerciseVocab.equipment(keys: (j["equipment"] as? [String]) ?? [], zh: (j["equipment_zh"] as? [String]) ?? [])
    return LibraryExercise(slug: slug, name: ExerciseNames.exercise(j) ?? slug, status: j["status"] as? String ?? "active",
                           custom: (j["source"] as? String) == "user_custom", subtitle: (muscles + gear).joined(separator: " · "))
  }

  static func list(_ j: [String: Any]?) -> [LibraryExercise] {
    ((j?["exercises"] as? [[String: Any]]) ?? []).compactMap(parse)
  }
}

/// The recommended weight of an exercise (`load`, training/loads.py): its words are Chinese-only on the server, so they
/// are chosen here by `basis` / `reason` (app.js loadBadge, loadBasisText).
struct ExerciseLoad: Equatable {
  var kg: Double?
  var basis: String?
  var reason: String?
  var per: String?
  var targetReps: Int?
  var lastDate: String?
  var direction: String?
  var readiness: String?
  var bodyWeightKg: Double?
  var bwFraction: Double?

  init?(_ j: [String: Any]?) {
    guard let j else { return nil }
    kg = RecordFormat.double(j["kg"])
    basis = j["basis"] as? String
    reason = j["reason"] as? String
    per = j["per"] as? String
    targetReps = (j["target_reps"] as? NSNumber)?.intValue
    lastDate = (j["last"] as? [String: Any])?["date"] as? String
    direction = j["direction"] as? String
    readiness = j["readiness"] as? String
    bodyWeightKg = RecordFormat.double(j["body_weight_kg"])
    bwFraction = RecordFormat.double(j["bw_fraction"])
  }

  private var word: String? {
    if reason == "no_body_weight" { return tr("缺体重", "No body weight") }
    switch basis {
    case "progression": return tr("按进阶", "Progression")
    case "history": return tr("同上次", "Same as last time")
    case "e1rm": return tr("换算", "Converted")
    case "estimate": return tr("估算", "Estimated")
    case "bodyweight": return tr("自重", "Bodyweight")
    case "plan": return tr("计划", "Plan")
    default: return nil
    }
  }

  private var perWord: String? {
    switch per {   // the server's two words (training/loads.py _per): 每侧, 单只
    case "\u{6BCF}\u{4FA7}": return tr("每侧", "per side")
    case "\u{5355}\u{53EA}": return tr("单只", "each")
    default: return per
    }
  }

  /// 推荐重量 of the detail page; nil when there is nothing to recommend.
  var value: String? {
    if let kg { return "\(RecordFormat.num(kg, 1)) kg" }
    return basis == "bodyweight" ? word : nil
  }

  /// What the number is: 单只 · × 10 · 同上次.
  var note: String? {
    guard kg != nil else { return nil }
    let parts = [perWord, targetReps.map { "× \($0)" }, word].compactMap { $0 }
    return parts.isEmpty ? nil : parts.joined(separator: " · ")
  }

  /// 依据 of the detail page.
  var basisText: String? {
    let date = lastDate.map(RecordDay.short) ?? ""
    switch basis == "estimate" ? reason : basis {
    case "progression": return tr("按进阶规则", "Progression rule")
    case "history": return tr("同上次（\(date)）", "Same as last time (\(date))")
    case "e1rm": return tr("按上次（\(date)）换算", "Converted from last time (\(date))")
    case "plan": return tr("计划里写的重量", "Weight set in the plan")
    case "bodyweight": return tr("自重动作", "Bodyweight exercise")
    case "no_history": return tr("按体重 × 系数估算：这个动作还没有记录", "Estimated from body weight × factor: no log for this exercise yet")
    case "not_linked": return tr("按体重 × 系数估算：还没连训练记录", "Estimated from body weight × factor: training log not connected")
    case "history_unreadable": return tr("按体重 × 系数估算：训练记录读不到", "Estimated from body weight × factor: training log unreadable")
    case "no_body_weight": return tr("缺体重：记一次体重后可估算", "No body weight: log a weigh-in to get an estimate")
    default: return nil
    }
  }

  var basisNote: String? {
    guard let w = bodyWeightKg, let f = bwFraction else { return nil }
    return "\(RecordFormat.num(w, 1)) kg × \(RecordFormat.num(f, 2))"
  }

  /// The weight on a row of a day's plan (app.js loadBadge).
  var chip: (text: String, tone: Tone)? {
    if reason == "no_body_weight" { return (tr("缺体重", "No body weight"), .text) }
    guard let kg else { return basis == "bodyweight" ? word.map { ($0, .text) } : nil }
    let text = "\(RecordFormat.num(kg, 1)) kg"
    if basis == "estimate" { return ("≈ \(text)", .text) }
    if readiness == "red" || direction == "down" { return (text, .mid) }
    if basis == "progression", direction == "up" { return ("→ \(text)", .good) }
    return (text, .text)
  }
}

/// A safe alternative of an exercise, as the server chose it (`alternatives[]`).
struct ExerciseAlternative: Equatable, Identifiable {
  var slug: String
  var name: String
  var status: String
  var thumb: String?
  var primaryKeys: [String]
  var secondaryKeys: [String]
  var subtitle: String
  var load: ExerciseLoad?
  var id: String { slug }

  static func parse(_ j: [String: Any]) -> ExerciseAlternative? {
    guard let slug = j["slug"] as? String else { return nil }
    let keys = (j["primary_muscles"] as? [String]) ?? []
    let muscles = ExerciseVocab.muscles(keys: keys, zh: (j["primary_muscles_zh"] as? [String]) ?? [])
    let gear = ExerciseVocab.equipment(keys: (j["equipment"] as? [String]) ?? [], zh: (j["equipment_zh"] as? [String]) ?? [])
    return ExerciseAlternative(slug: slug, name: ExerciseNames.exercise(j) ?? slug, status: j["status"] as? String ?? "active",
                               thumb: j["thumb"] as? String, primaryKeys: keys, secondaryKeys: (j["secondary_muscles"] as? [String]) ?? [],
                               subtitle: (gear + muscles).joined(separator: " · "), load: ExerciseLoad(j["load"] as? [String: Any]))
  }
}

/// `GET /training/exercises/{slug}`.
struct ExerciseDetail: Equatable {
  struct Recent: Equatable, Identifiable {
    var date: String
    var sets: String
    var id: String { date }
  }

  var slug: String
  var name: String
  var otherName: String?
  var status: String
  var reason: String?
  var cues: String?
  var images: [String]
  var credits: [String]
  var noDemo: Bool
  var equipment: [String]
  var pattern: String?
  var joints: [String]
  var load: ExerciseLoad?
  var primaryKeys: [String]
  var secondaryKeys: [String]
  var primary: [String]
  var secondary: [String]
  var recent: [Recent]
  var steps: [String]
  var alternatives: [ExerciseAlternative]

  var banned: Bool { status == "banned" }

  static func parse(_ j: [String: Any]?) -> ExerciseDetail? {
    guard let j, let slug = j["slug"] as? String else { return nil }
    let zh = (j["name_zh"] as? String) ?? slug, en = j["name_en"] as? String
    let main = Lang.isZh ? zh : (en ?? zh), other = Lang.isZh ? en : zh
    let primaryKeys = (j["primary_muscles"] as? [String]) ?? [], secondaryKeys = (j["secondary_muscles"] as? [String]) ?? []
    var joints: [String] = []
    for k in (j["joint_stress"] as? [String]) ?? [] {
      let word = ExerciseVocab.joint(k)
      if !joints.contains(word) { joints.append(word) }
    }
    // One credit per author / licence (#248): the page's line under the pictures.
    var credits: [String] = []
    for c in (j["media_credits"] as? [[String: Any]]) ?? [] {
      let license = (c["license"] as? String)?.replacingOccurrences(of: "-", with: " ")
      let line = [c["author"] as? String, license, c["source"] as? String].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
      if !line.isEmpty, !credits.contains(line) { credits.append(line) }
    }
    let recent = ((j["recent"] as? [[String: Any]]) ?? []).compactMap { r -> Recent? in
      guard let d = r["date"] as? String else { return nil }
      return Recent(date: d, sets: ((r["sets"] as? [String]) ?? []).joined(separator: " · "))
    }
    return ExerciseDetail(slug: slug, name: main, otherName: other == main ? nil : other, status: j["status"] as? String ?? "active",
                          reason: j["reason"] as? String, cues: j["cues"] as? String, images: (j["image_urls"] as? [String]) ?? [],
                          credits: credits, noDemo: (j["no_demo"] as? Bool) ?? false,
                          equipment: ExerciseVocab.equipment(keys: (j["equipment"] as? [String]) ?? [], zh: (j["equipment_zh"] as? [String]) ?? []),
                          pattern: ExerciseVocab.pattern(key: j["movement_pattern"] as? String, zh: j["movement_pattern_zh"] as? String),
                          joints: joints, load: ExerciseLoad(j["load"] as? [String: Any]), primaryKeys: primaryKeys, secondaryKeys: secondaryKeys,
                          primary: ExerciseVocab.muscles(keys: primaryKeys, zh: (j["primary_muscles_zh"] as? [String]) ?? []),
                          secondary: ExerciseVocab.muscles(keys: secondaryKeys, zh: (j["secondary_muscles_zh"] as? [String]) ?? []),
                          recent: recent, steps: (j["instructions_zh"] as? [String]) ?? [],
                          alternatives: ((j["alternatives"] as? [[String: Any]]) ?? []).compactMap(ExerciseAlternative.parse))
  }
}

// MARK: - muscle detail

/// `GET /training/muscle-load/{muscle}`: one muscle's numbers, the last session and recommended exercises (the server
/// leaves banned ones out and marks caution ones).
struct MuscleDetail: Equatable {
  struct Recommended: Equatable, Identifiable {
    var slug: String
    var name: String
    var equipment: String?
    var caution: Bool
    var id: String { slug }
  }

  struct LastExercise: Equatable, Identifiable {
    var name: String
    var sets: Int
    var top: String?
    var primary: Bool
    var id: String { name }
  }

  struct Sport: Equatable, Identifiable {
    var name: String
    var minutes: Double
    var primary: Bool
    var id: String { name }
  }

  var key: String
  var name: String
  var status: String
  var weekSets: Double?
  var weekKg: Double?
  var last7Sets: Double?
  var last7Kg: Double?
  var low: Double?
  var high: Double?
  var prevSets: Double?
  var prevKg: Double?
  var delta: Double?
  var lastDate: String?
  var last: [LastExercise]
  var sports: [MuscleLoad.Range: [Sport]]
  var recommended: [Recommended]

  static func parse(_ j: [String: Any]?) -> MuscleDetail? {
    guard let j, let key = j["key"] as? String else { return nil }
    let ranges = j["ranges"] as? [String: Any]
    func range(_ name: String, _ field: String) -> Double? { RecordFormat.double((ranges?[name] as? [String: Any])?[field]) }
    let prev = j["prev_week"] as? [String: Any], target = j["target"] as? [String: Any], last = j["last"] as? [String: Any]
    let exercises = ((last?["exercises"] as? [[String: Any]]) ?? []).compactMap { e -> LastExercise? in
      guard let name = ExerciseNames.exercise(e) else { return nil }
      return LastExercise(name: name, sets: (e["sets"] as? NSNumber)?.intValue ?? 0, top: RecordFormat.string(e["top"]), primary: (e["primary"] as? Bool) ?? false)
    }
    var sports: [MuscleLoad.Range: [Sport]] = [:]
    for r in MuscleLoad.Range.allCases {
      sports[r] = (((j["sports"] as? [String: Any])?[r.rawValue] as? [[String: Any]]) ?? []).compactMap { s in
        guard let n = s["name"] as? String else { return nil }
        return Sport(name: n, minutes: RecordFormat.double(s["min"]) ?? 0, primary: (s["primary"] as? Bool) ?? false)
      }
    }
    let recommended = ((j["recommended"] as? [[String: Any]]) ?? []).compactMap { x -> Recommended? in
      guard let slug = x["slug"] as? String else { return nil }
      let gear = Lang.isZh ? ((x["equipment_zh"] as? [String]) ?? []).joined(separator: tr("、", ", ")) : nil
      return Recommended(slug: slug, name: ExerciseNames.exercise(x) ?? slug, equipment: gear?.isEmpty == false ? gear : nil, caution: (x["status"] as? String) == "caution")
    }
    return MuscleDetail(key: key, name: j["name"] as? String ?? key, status: (j["status"] as? [String: Any])?["week"] as? String ?? "none",
                        weekSets: range("week", "sets"), weekKg: range("week", "volume_kg"), last7Sets: range("last7", "sets"), last7Kg: range("last7", "volume_kg"),
                        low: RecordFormat.double(target?["low"]), high: RecordFormat.double(target?["high"]),
                        prevSets: RecordFormat.double(prev?["sets"]), prevKg: RecordFormat.double(prev?["volume_kg"]),
                        delta: RecordFormat.double(j["delta_sets"]), lastDate: last?["date"] as? String, last: exercises, sports: sports, recommended: recommended)
  }
}

// MARK: - plan edits

/// A day's removal offered back (`removed[]`).
struct RemovedExercise: Equatable, Identifiable {
  var slug: String
  var name: String
  var id: String { slug }

  static func list(_ raw: Any?) -> [RemovedExercise] {
    ((raw as? [[String: Any]]) ?? []).compactMap { r in
      guard let slug = r["slug"] as? String else { return nil }
      return RemovedExercise(slug: slug, name: ExerciseNames.exercise(r) ?? slug)
    }
  }
}

/// What the remove dialogs need to know about a planned exercise (training-day.js askRemove).
struct RemoveTarget: Equatable, Identifiable {
  var slug: String
  var name: String
  var date: String
  var isToday: Bool
  var dayOfWeek: Int?
  var sessionName: String?
  var sessionTitle: String?
  var addedForDay: Bool
  var id: String { "\(date):\(slug)" }

  /// 「以后都不要」 needs a session of the weekly plan; an exercise added to one date only has none.
  var canRemoveFromTemplate: Bool { !addedForDay && dayOfWeek != nil }
}

/// A built-in weekly split (`available_splits[]`).
struct SplitOption: Equatable, Identifiable {
  struct Day: Equatable, Identifiable {
    var dayOfWeek: Int
    var name: String
    var exerciseCount: Int
    var id: String { "\(dayOfWeek):\(name)" }
  }

  var type: String
  var name: String
  var summary: String
  var days: [Day]
  var id: String { type }

  static func list(_ raw: Any?) -> [SplitOption] {
    ((raw as? [[String: Any]]) ?? []).compactMap { s in
      guard let type = s["split_type"] as? String else { return nil }
      let days = ((s["days"] as? [[String: Any]]) ?? []).compactMap { d -> Day? in
        guard let dow = (d["day_of_week"] as? NSNumber)?.intValue else { return nil }
        return Day(dayOfWeek: dow, name: ExerciseNames.template(d) ?? "", exerciseCount: (d["exercise_count"] as? NSNumber)?.intValue ?? 0)
      }
      return SplitOption(type: type, name: ExerciseNames.template(s) ?? type,
                         summary: (Lang.isZh ? nil : s["summary_en"] as? String) ?? (s["summary"] as? String) ?? "", days: days)
    }
  }

  /// What the picker starts on: the plan's split when it is a built-in one, else the 4-day template (app.js splitPickerSheet).
  static func initial(_ splits: [SplitOption], current: String?) -> String {
    if let current, splits.contains(where: { $0.type == current }) { return current }
    let fallback = "4_day_upper_lower"
    return splits.contains { $0.type == fallback } ? fallback : (splits.first?.type ?? fallback)
  }

  /// `PUT /training/plan`'s answer: enabled, and whether it reached the training log.
  static func enabledMessage(_ res: [String: Any], shown: String) -> String {
    let sync = res["sparky_sync"] as? [String: Any]
    if let sync, (sync["synced"] as? Bool) == false, sync["link"] != nil {
      return tr("已启用：\(shown) · 训练记录未同步", "Now using: \(shown) · training log not synced")
    }
    return tr("已启用：\(shown)", "Now using: \(shown)")
  }
}

/// A row of a day's plan other than today's (`days[].planned[].exercises[]`).
struct WeekExercise: Equatable, Identifiable {
  var slug: String?
  var name: String
  var status: String
  var setsText: String?
  var load: ExerciseLoad?
  /// The week has a progression suggestion for it (「加重」 on the Today card).
  var hasSuggestion = false
  /// Its badge, when the weight above does not already say it (app.js exerciseRow).
  var suggestion: (text: String, tone: Tone)?
  var id: String { slug ?? name }

  static func == (a: WeekExercise, b: WeekExercise) -> Bool {
    a.slug == b.slug && a.name == b.name && a.status == b.status && a.setsText == b.setsText && a.load == b.load
      && a.hasSuggestion == b.hasSuggestion && a.suggestion?.text == b.suggestion?.text
  }

  static func parse(_ j: [String: Any]) -> WeekExercise? {
    guard let name = j["name"] as? String ?? (j["name_zh"] as? String) else { return nil }
    let status = j["status"] as? String ?? "active"
    let load = status == "banned" ? nil : ExerciseLoad(j["load"] as? [String: Any])
    let first = (j["suggestions"] as? [[String: Any]])?.first
    var badge: (text: String, tone: Tone)?
    if let first, load?.basis != "progression" {
      let next = RecordFormat.double(first["next_weight_kg"])
      badge = (next.map { "→ \(RecordFormat.num($0, 1)) kg" } ?? tr("建议", "Suggestion"), (first["level"] as? String) == "warning" ? .mid : .good)
    }
    return WeekExercise(slug: j["slug"] as? String, name: name, status: status, setsText: j["sets_text"] as? String, load: load,
                        hasSuggestion: first != nil, suggestion: badge)
  }
}

/// A session of a day's plan.
struct PlanSession: Equatable, Identifiable {
  var name: String
  var rawName: String
  var dayOfWeek: Int?
  var addedForDay: Bool
  var exercises: [WeekExercise]
  var id: String { rawName }

  static func parse(_ j: [String: Any]) -> PlanSession {
    PlanSession(name: ExerciseNames.template(j) ?? "", rawName: j["name"] as? String ?? "", dayOfWeek: (j["day_of_week"] as? NSNumber)?.intValue,
                addedForDay: (j["added_for_day"] as? Bool) ?? false,
                exercises: ((j["exercises"] as? [[String: Any]]) ?? []).compactMap(WeekExercise.parse))
  }
}

/// A progression suggestion of the week (`suggestions[]`).
struct TrainingSuggestion: Equatable, Identifiable {
  var id: String
  var name: String
  var slug: String?
  var warning: Bool
  var title: String?
  var nextKg: Double?

  static func list(_ raw: Any?) -> [TrainingSuggestion] {
    ((raw as? [[String: Any]]) ?? []).compactMap { s in
      guard let name = s["name"] as? String else { return nil }
      return TrainingSuggestion(id: RecordFormat.string(s["id"]) ?? name, name: name, slug: s["slug"] as? String, warning: (s["level"] as? String) == "warning",
                                title: s["title"] as? String, nextKg: RecordFormat.double(s["next_weight_kg"]))
    }
  }
}

/// The training log's link (`sparky`, `POST /training/sparky/connect`, issue #66 FB-4).
struct TrainingLogLink: Equatable {
  var state: String
  var hint: String?
  var canConnect: Bool
  var error: String?

  init?(_ j: [String: Any]?) {
    guard let j, let state = j["state"] as? String else { return nil }
    self.state = state
    hint = (Lang.isZh ? nil : j["hint_en"] as? String) ?? j["hint"] as? String
    canConnect = (j["can_connect"] as? Bool) ?? false
    error = (Lang.isZh ? nil : j["error_en"] as? String) ?? j["error"] as? String
  }

  var connected: Bool { state == "connected" }
  var word: (text: String, tone: Tone) {
    switch state {
    case "connected": return (tr("已连接", "Connected"), .good)
    case "not_linked": return (tr("未连接", "Not connected"), .mid)
    default: return (tr("读不到", "Unreadable"), .bad)
    }
  }
}

// MARK: - add to plan (exercise-plan.js)

struct AddPlanOptions: Equatable {
  struct Session: Equatable, Identifiable {
    var dayOfWeek: Int
    var rawName: String
    var name: String
    var count: Int
    var hasExercise: Bool
    var id: String { "\(dayOfWeek):\(rawName)" }
  }

  struct Day: Equatable, Identifiable {
    var date: String
    var names: [String]
    var hasExercise: Bool
    var id: String { date }
  }

  struct Alternative: Equatable, Identifiable {
    var slug: String
    var name: String
    var id: String { slug }
  }

  var slug: String
  var name: String
  var reason: String?
  var timed: Bool
  var blocked: Bool
  var alternatives: [Alternative]
  var hasPlan: Bool
  var sets: Int?
  var repLow: Int?
  var repHigh: Int?
  var durationSec: Int?
  var sessions: [Session]
  var days: [Day]

  static func parse(_ j: [String: Any]?) -> AddPlanOptions? {
    guard let j, let e = j["exercise"] as? [String: Any], let slug = e["slug"] as? String else { return nil }
    let d = j["defaults"] as? [String: Any]
    let sessions = ((j["sessions"] as? [[String: Any]]) ?? []).compactMap { s -> Session? in
      guard let dow = (s["day_of_week"] as? NSNumber)?.intValue, let raw = s["name"] as? String else { return nil }
      return Session(dayOfWeek: dow, rawName: raw, name: ExerciseNames.template(s) ?? raw, count: (s["exercise_count"] as? NSNumber)?.intValue ?? 0,
                     hasExercise: (s["has_exercise"] as? Bool) ?? false)
    }
    let days = ((j["days"] as? [[String: Any]]) ?? []).compactMap { x -> Day? in
      guard let date = x["date"] as? String else { return nil }
      return Day(date: date, names: ((Lang.isZh ? nil : x["names_en"] as? [String]) ?? (x["names"] as? [String])) ?? [], hasExercise: (x["has_exercise"] as? Bool) ?? false)
    }
    let alts = ((j["alternatives"] as? [[String: Any]]) ?? []).compactMap { a -> Alternative? in
      guard let s = a["slug"] as? String else { return nil }
      return Alternative(slug: s, name: ExerciseNames.exercise(a) ?? s)
    }
    return AddPlanOptions(slug: slug, name: ExerciseNames.exercise(e) ?? slug, reason: e["reason"] as? String, timed: (e["modality"] as? String) == "duration",
                          blocked: (j["blocked"] as? Bool) ?? false, alternatives: alts, hasPlan: j["plan"] is [String: Any],
                          sets: (d?["sets"] as? NSNumber)?.intValue, repLow: (d?["rep_low"] as? NSNumber)?.intValue, repHigh: (d?["rep_high"] as? NSNumber)?.intValue,
                          durationSec: (d?["duration_sec"] as? NSNumber)?.intValue, sessions: sessions, days: days)
  }
}

/// The sheet's form → the `POST /training/plan/add-exercise` body, or what to fix (exercise-plan.js addRequest).
struct AddPlanForm: Equatable {
  enum Scope: String { case template, day }

  var slug: String
  var scope = Scope.template
  var session: AddPlanOptions.Session?
  var day: String?
  var timed = false
  var sets = ""
  var repLow = ""
  var repHigh = ""
  var durationSec = ""

  private static func int(_ s: String) -> Int? {
    let t = s.trimmingCharacters(in: .whitespaces)
    return t.isEmpty || !t.allSatisfy(\.isNumber) ? nil : Int(t)
  }

  func request() -> (body: [String: Any]?, error: String?) {
    guard let sets = Self.int(sets), (1...20).contains(sets) else { return (nil, tr("组数要在 1–20 之间", "Sets must be between 1 and 20")) }
    var body: [String: Any] = ["exercise": slug, "scope": scope.rawValue, "sets": sets]
    if timed {
      guard let d = Self.int(durationSec), (1...7200).contains(d) else { return (nil, tr("时长要在 1 秒到 2 小时之间", "Duration must be between 1 second and 2 hours")) }
      body["duration_sec"] = d
    } else {
      guard let lo = Self.int(repLow), let hi = Self.int(repHigh), lo >= 1, hi <= 100, lo <= hi else {
        return (nil, tr("次数要在 1–100 之间，下限不能大于上限", "Reps must be between 1 and 100, low not above high"))
      }
      body["rep_low"] = lo
      body["rep_high"] = hi
    }
    switch scope {
    case .template:
      guard let session else { return (nil, tr("先选一个训练组", "Pick a session first")) }
      body["day_of_week"] = session.dayOfWeek
      body["session_name"] = session.rawName
    case .day:
      guard let day else { return (nil, tr("先选一天", "Pick a day first")) }
      body["day"] = day
    }
    return (body, nil)
  }
}

// MARK: - custom exercises (custom-exercises.js)

struct NamedKey: Equatable, Identifiable, Hashable {
  var key: String
  var name: String
  var id: String { key }

  static func list(_ raw: Any?) -> [NamedKey] {
    ((raw as? [[String: Any]]) ?? []).compactMap { o in
      guard let k = o["key"] as? String else { return nil }
      return NamedKey(key: k, name: o["name"] as? String ?? k)
    }
  }
}

struct CustomExercise: Equatable, Identifiable {
  var id: Int
  var nameZh: String
  var nameEn: String?
  var aliases: [String]
  var primary: [String]
  var secondary: [String]
  var equipment: [String]
  var primaryNames: [String]
  var secondaryNames: [String]
  var equipmentNames: [String]
  var fromAI: Bool

  var name: String { Lang.isZh ? nameZh : ((nameEn?.isEmpty == false ? nameEn : nil) ?? nameZh) }

  var subtitle: String {
    let sep = tr("、", ", ")
    let parts = [primaryNames.joined(separator: sep),
                 secondaryNames.isEmpty ? "" : "\(tr("辅助", "assist")) \(secondaryNames.joined(separator: sep))",
                 equipmentNames.joined(separator: sep)]
    return parts.filter { !$0.isEmpty }.joined(separator: " · ")
  }

  static func parse(_ x: [String: Any]) -> CustomExercise? {
    guard let id = (x["id"] as? NSNumber)?.intValue else { return nil }
    return CustomExercise(id: id, nameZh: x["name_zh"] as? String ?? "", nameEn: x["name_en"] as? String, aliases: (x["aliases"] as? [String]) ?? [],
                          primary: (x["primary_muscles"] as? [String]) ?? [], secondary: (x["secondary_muscles"] as? [String]) ?? [],
                          equipment: (x["equipment"] as? [String]) ?? [], primaryNames: (x["primary_names"] as? [String]) ?? [],
                          secondaryNames: (x["secondary_names"] as? [String]) ?? [], equipmentNames: (x["equipment_names"] as? [String]) ?? [],
                          fromAI: (x["source"] as? String) == "ai")
  }
}

/// `GET /training/custom-exercises`: the user's own and the keys the form may use.
struct CustomExerciseList: Equatable {
  var exercises: [CustomExercise]
  var muscles: [NamedKey]
  var equipment: [NamedKey]

  static func parse(_ j: [String: Any]?) -> CustomExerciseList? {
    guard let j else { return nil }
    return CustomExerciseList(exercises: ((j["exercises"] as? [[String: Any]]) ?? []).compactMap(CustomExercise.parse),
                              muscles: NamedKey.list(j["muscles"]), equipment: NamedKey.list(j["equipment"]))
  }
}

/// Form values → the request body, or what to fix (custom-exercises.js formBody; the server checks the same and more).
enum CustomExerciseForm {
  static let maxPrimary = 4

  static func body(nameZh: String, nameEn: String, aliases: String, primary: [String], secondary: [String], equipment: [String]) -> (body: [String: Any]?, error: String?) {
    let zh = nameZh.trimmingCharacters(in: .whitespacesAndNewlines)
    if zh.isEmpty { return (nil, tr("中文名不能为空", "The Chinese name is required")) }
    if primary.isEmpty { return (nil, tr("至少选一个主要肌群", "Pick at least one primary muscle")) }
    if primary.count > maxPrimary { return (nil, tr("主要肌群最多 \(maxPrimary) 个", "At most \(maxPrimary) primary muscles")) }
    let separators = CharacterSet(charactersIn: ",，、\n")
    let list = aliases.components(separatedBy: separators).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    return (["name_zh": zh, "name_en": nameEn.trimmingCharacters(in: .whitespacesAndNewlines), "aliases": list, "primary_muscles": primary,
             "secondary_muscles": secondary.filter { !primary.contains($0) }, "equipment": equipment], nil)
  }
}

// MARK: - training settings

/// `GET /training/goals` (我 → 训练设置 on the page, the muscle card here).
struct TrainingGoals: Equatable {
  var focus: [String]
  var goal: String
  var muscles: [NamedKey]
  var maxFocus: Int

  static func parse(_ j: [String: Any]?) -> TrainingGoals? {
    guard let j else { return nil }
    return TrainingGoals(focus: (j["focus_muscles"] as? [String]) ?? [], goal: j["physique_goal"] as? String ?? "", muscles: NamedKey.list(j["muscles"]),
                         maxFocus: (j["max_focus"] as? NSNumber)?.intValue ?? 3)
  }
}

/// The two questions the muscle card hands to Jarvis (muscle-load.js), word for word.
enum MusclePrompts {
  static var analyse: String {
    tr("请分析我的肌群训练：先用 get_muscle_load 看各肌群本周的组数，结合我的重点肌群和体态目标，告诉我哪些偏少、哪些偏多、和目标差在哪、具体怎么改（每周给哪块加几组、换哪些动作，避开我的禁用动作）。要改训练计划的话先给我看改动，我确认后再改。", "Please analyse my muscle training: use get_muscle_load to see this week's sets per muscle, compare with my focus muscles and physique goal, and tell me which are low or high, how far I am from my goal and exactly what to change (how many sets to add to which muscle, which exercises to swap, avoiding my banned exercises). If the training plan should change, show me the change first and edit it only after I confirm.")
  }

  static func fill(_ names: [String]) -> String {
    let list = names.joined(separator: tr("、", ", "))
    return tr("下面这些动作在我的训练记录里，但没对上动作库，所以没计入肌肉图：\(list)。请逐个判断主要肌群和次要肌群（只用动作库的肌群），拿不准的先问我，然后用 add_custom_exercise 加进我自己的动作库，名字和记录里的一致。", "These exercises are in my training log but did not match the exercise library, so the muscle map leaves them out: \(list). Work out the primary and secondary muscles of each (only the library's muscles), ask me when unsure, then add them to my own library with add_custom_exercise, using the names exactly as logged.")
  }
}
