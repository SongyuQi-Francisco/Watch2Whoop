import XCTest
@testable import Watch2Whoop

// What the training page got when the page's web version goes away (#340): the library, plan edits, custom exercises,
// muscle detail, the Today cards. Fictional numbers only.

private func json(_ s: String) -> [String: Any] { (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String: Any] ?? [:] }

final class ExerciseModelsTests: XCTestCase {
  func testSearchRequestSendsOnlyWhatIsSet() {
    let plain = ExerciseAPI.search(query: "  ", group: "", equipment: "")
    XCTAssertEqual(plain.path, "/v1/users/me/training/exercises")
    XCTAssertEqual(plain.query.map(\.name), ["limit"])
    let full = ExerciseAPI.search(query: " row ", group: "back", equipment: "body only")
    XCTAssertEqual(Dictionary(uniqueKeysWithValues: full.query.map { ($0.name, $0.value ?? "") }),
                   ["limit": "100", "q": "row", "muscle": "back", "equipment": "body only"])
  }

  func testPathsAreLeftForTheClientToEncode() throws {
    // APIClient assigns URLComponents.path, which encodes; encoding here too would send %2520.
    let client = APIClient(baseURL: URL(string: "https://example.test")!, token: { "t" })
    let url = try client.urlRequest(ExerciseAPI.muscle("middle back")).url
    XCTAssertEqual(url?.absoluteString, "https://example.test/v1/users/me/training/muscle-load/middle%20back")
  }

  func testLibraryListKeepsTheServersStatus() {
    let list = LibraryExercise.list(json("""
    {"exercises": [
      {"slug": "a", "name_zh": "动作甲", "name_en": "Move A", "status": "banned", "primary_muscles": ["chest"], "primary_muscles_zh": ["胸"], "equipment": ["barbell"], "equipment_zh": ["杠铃"]},
      {"slug": "my_1", "name_zh": "我的", "status": "caution", "source": "user_custom", "primary_muscles": [], "equipment": []},
      {"name_zh": "no slug"}
    ]}
    """))
    XCTAssertEqual(list.map(\.slug), ["a", "my_1"])
    XCTAssertEqual(list[0].status, "banned")
    XCTAssertEqual(list[0].name, tr("动作甲", "Move A"))
    XCTAssertEqual(list[0].subtitle, tr("胸 · 杠铃", "Chest · Barbell"))
    XCTAssertTrue(list[1].custom)
    XCTAssertEqual(list[1].status, "caution")
  }

  func testDetailParsesPicturesLoadAndAlternatives() throws {
    let d = try XCTUnwrap(ExerciseDetail.parse(json("""
    {"slug": "bench", "name_zh": "卧推", "name_en": "Bench press", "status": "caution", "reason": "my shoulder", "cues": "Slow",
     "image_urls": ["/app/media/exercises/x/0.jpg", "https://cdn.example.test/1.jpg"],
     "media_credits": [{"author": "A", "license": "CC-BY-SA", "source": "wger"}, {"author": "A", "license": "CC-BY-SA", "source": "wger"}],
     "equipment": ["barbell"], "equipment_zh": ["杠铃"], "movement_pattern": "horizontal_push", "movement_pattern_zh": "水平推",
     "joint_stress": ["shoulder_ir", "shoulder_internal_rotation", "wrist"],
     "primary_muscles": ["chest"], "primary_muscles_zh": ["胸"], "secondary_muscles": ["triceps"], "secondary_muscles_zh": ["三头"],
     "load": {"kg": 40, "basis": "history", "per": "单只", "target_reps": 10, "last": {"date": "2026-01-02"}},
     "recent": [{"date": "2026-01-02", "sets": ["40×10", "40×9"]}], "instructions_zh": ["Lie down", "Press"],
     "alternatives": [{"slug": "alt", "name_zh": "替代", "status": "active", "thumb": "/app/media/t.jpg", "primary_muscles": ["chest"],
                       "load": {"kg": 12.5, "basis": "estimate", "reason": "no_history"}}]}
    """)))
    XCTAssertEqual(d.name, tr("卧推", "Bench press"))
    XCTAssertEqual(d.otherName, tr("Bench press", "卧推"))
    XCTAssertEqual(d.images.count, 2)
    XCTAssertEqual(d.credits, ["A · CC BY SA · wger"], "one credit per author / licence")
    XCTAssertEqual(d.joints.count, 2, "two keys of one word are one badge")
    XCTAssertEqual(d.load?.value, "40 kg")
    XCTAssertEqual(d.load?.note, tr("单只 · × 10 · 同上次", "each · × 10 · Same as last time"))
    XCTAssertEqual(d.load?.basisText, tr("同上次（1/2）", "Same as last time (2 Jan)"))
    XCTAssertEqual(d.recent.first?.sets, "40×10 · 40×9")
    XCTAssertEqual(d.steps.count, 2)
    XCTAssertEqual(d.alternatives.first?.slug, "alt")
    XCTAssertEqual(d.alternatives.first?.load?.chip?.text, "≈ 12.5 kg")
    XCTAssertFalse(d.banned)
    XCTAssertEqual(ExerciseMedia.url(d.images[0], base: URL(string: "https://example.test")!)?.absoluteString, "https://example.test/app/media/exercises/x/0.jpg")
    XCTAssertEqual(ExerciseMedia.url(d.images[1], base: URL(string: "https://example.test")!)?.absoluteString, "https://cdn.example.test/1.jpg")
  }

  func testLoadWords() {
    let body = ExerciseLoad(["kg": NSNull(), "basis": "bodyweight"])
    XCTAssertEqual(body?.value, tr("自重", "Bodyweight"))
    XCTAssertNil(body?.note)
    let noWeight = ExerciseLoad(["kg": NSNull(), "basis": "estimate", "reason": "no_body_weight"])
    XCTAssertNil(noWeight?.value)
    XCTAssertEqual(noWeight?.chip?.text, tr("缺体重", "No body weight"))
    XCTAssertEqual(noWeight?.basisText, tr("缺体重：记一次体重后可估算", "No body weight: log a weigh-in to get an estimate"))
    XCTAssertEqual(ExerciseLoad(["kg": 50, "basis": "progression", "direction": "up"])?.chip?.tone, .good)
    XCTAssertEqual(ExerciseLoad(["kg": 50, "basis": "history", "readiness": "red"])?.chip?.tone, .mid)
    XCTAssertNil(ExerciseLoad(nil))
  }

  func testMuscleDetail() throws {
    let d = try XCTUnwrap(MuscleDetail.parse(json("""
    {"key": "chest", "name": "Chest", "status": {"week": "low", "last7": "ok"}, "target": {"low": 8, "high": 16},
     "ranges": {"week": {"sets": 4.5, "volume_kg": 900}, "last7": {"sets": 6, "volume_kg": 1000}, "today": {"sets": 0, "volume_kg": 0}},
     "prev_week": {"sets": 3, "volume_kg": 500}, "delta_sets": 1.5,
     "last": {"date": "2026-01-02", "exercises": [{"name": "卧推", "name_en": "Bench", "sets": 3, "top": 40, "primary": true}, {"name": "夹胸", "sets": 2}]},
     "sports": {"week": [{"name": "Swim", "min": 30, "primary": false}]},
     "recommended": [{"slug": "a", "name_zh": "甲", "name_en": "A", "status": "active", "equipment_zh": ["哑铃"]}, {"slug": "b", "name_zh": "乙", "status": "caution"}]}
    """)))
    XCTAssertEqual(d.status, "low")
    XCTAssertEqual(d.weekSets, 4.5)
    XCTAssertEqual(d.delta, 1.5)
    XCTAssertEqual(d.last.first?.top, "40")
    XCTAssertFalse(d.last[1].primary)
    XCTAssertEqual(d.sports[.week]?.first?.minutes, 30)
    XCTAssertEqual(d.recommended.map(\.slug), ["a", "b"])
    XCTAssertTrue(d.recommended[1].caution)
    XCTAssertEqual(MuscleLoad.siblings(of: "lats"), ["middle back"])
    XCTAssertEqual(MuscleLoad.siblings(of: "chest"), [])
    let regions = MuscleLoad.exerciseRegions(primary: ["lats"], secondary: ["middle back", "biceps"])
    XCTAssertEqual(regions["upper-back"]?.status, "primary", "primary wins a shared region")
    XCTAssertEqual(regions["biceps"]?.status, "secondary")
  }

  func testSplitsPickABuiltInStartAndSayIfTheLogWasNotSynced() {
    let splits = SplitOption.list(json("""
    {"s": [{"split_type": "3_day_ppl", "name": "三天", "name_en": "Three", "summary": "概要", "summary_en": "Summary", "days": [{"day_of_week": 1, "name": "推", "name_en": "Push", "exercise_count": 5}]},
           {"split_type": "4_day_upper_lower", "name": "四天", "days": []}]}
    """)["s"])
    XCTAssertEqual(splits.map(\.type), ["3_day_ppl", "4_day_upper_lower"])
    XCTAssertEqual(splits[0].name, tr("三天", "Three"))
    XCTAssertEqual(splits[0].summary, tr("概要", "Summary"))
    XCTAssertEqual(splits[1].name, "四天", "no English twin: as stored")
    XCTAssertEqual(SplitOption.initial(splits, current: "3_day_ppl"), "3_day_ppl")
    XCTAssertEqual(SplitOption.initial(splits, current: "custom"), "4_day_upper_lower", "a custom plan never sends a type the server would refuse")
    XCTAssertEqual(SplitOption.initial(splits, current: nil), "4_day_upper_lower")
    XCTAssertEqual(SplitOption.enabledMessage(["sparky_sync": ["synced": true]], shown: "X"), tr("已启用：X", "Now using: X"))
    XCTAssertEqual(SplitOption.enabledMessage(["sparky_sync": ["synced": false, "not_needed": true]], shown: "X"), tr("已启用：X", "Now using: X"))
    XCTAssertEqual(SplitOption.enabledMessage(["sparky_sync": ["synced": false, "link": ["state": "unreadable"]]], shown: "X"),
                   tr("已启用：X · 训练记录未同步", "Now using: X · training log not synced"))
  }

  func testAddFormChecksLikeThePage() {
    var f = AddPlanForm(slug: "a", sets: "3", repLow: "8", repHigh: "12")
    XCTAssertEqual(f.request().error, tr("先选一个训练组", "Pick a session first"))
    f.session = AddPlanOptions.Session(dayOfWeek: 2, rawName: "上肢", name: "Upper", count: 5, hasExercise: false)
    let body = f.request().body
    XCTAssertEqual(body?["session_name"] as? String, "上肢", "the stored name, not the shown one")
    XCTAssertEqual(body?["day_of_week"] as? Int, 2)
    XCTAssertEqual(body?["rep_high"] as? Int, 12)
    f.repLow = "13"
    XCTAssertNotNil(f.request().error, "low above high")
    f.repLow = "8"
    f.sets = "21"
    XCTAssertEqual(f.request().error, tr("组数要在 1–20 之间", "Sets must be between 1 and 20"))
    f.sets = "x"
    XCTAssertNotNil(f.request().error)
    f = AddPlanForm(slug: "a", scope: .day, day: nil, timed: true, sets: "1", durationSec: "60")
    XCTAssertEqual(f.request().error, tr("先选一天", "Pick a day first"))
    f.day = "2026-01-02"
    XCTAssertEqual(f.request().body?["duration_sec"] as? Int, 60)
    XCTAssertNil(f.request().body?["rep_low"])
    f.durationSec = "7201"
    XCTAssertNotNil(f.request().error)
  }

  func testAddOptionsBlockedComesFromTheServer() throws {
    let o = try XCTUnwrap(AddPlanOptions.parse(json("""
    {"exercise": {"slug": "a", "name_zh": "甲", "status": "banned", "reason": "why", "modality": "weight_reps"}, "blocked": true, "plan": null,
     "defaults": {"sets": 3, "rep_low": 8, "rep_high": 12}, "sessions": [], "days": [], "alternatives": [{"slug": "b", "name_zh": "乙"}]}
    """)))
    XCTAssertTrue(o.blocked)
    XCTAssertFalse(o.hasPlan)
    XCTAssertEqual(o.alternatives.map(\.slug), ["b"])
    XCTAssertEqual(o.reason, "why")
  }

  func testCustomExerciseForm() {
    XCTAssertEqual(CustomExerciseForm.body(nameZh: "  ", nameEn: "", aliases: "", primary: ["chest"], secondary: [], equipment: []).error, tr("中文名不能为空", "The Chinese name is required"))
    XCTAssertEqual(CustomExerciseForm.body(nameZh: "x", nameEn: "", aliases: "", primary: [], secondary: [], equipment: []).error, tr("至少选一个主要肌群", "Pick at least one primary muscle"))
    XCTAssertNotNil(CustomExerciseForm.body(nameZh: "x", nameEn: "", aliases: "", primary: ["a", "b", "c", "d", "e"], secondary: [], equipment: []).error)
    let body = CustomExerciseForm.body(nameZh: " 我的 ", nameEn: " Mine ", aliases: "甲, 乙，丙、丁\n戊,", primary: ["chest"], secondary: ["chest", "biceps"], equipment: ["cable"]).body
    XCTAssertEqual(body?["name_zh"] as? String, "我的")
    XCTAssertEqual(body?["name_en"] as? String, "Mine")
    XCTAssertEqual(body?["aliases"] as? [String], ["甲", "乙", "丙", "丁", "戊"])
    XCTAssertEqual(body?["secondary_muscles"] as? [String], ["biceps"], "a primary muscle is not also a secondary one")
  }

  func testCustomExerciseList() throws {
    let d = try XCTUnwrap(CustomExerciseList.parse(json("""
    {"exercises": [{"id": 3, "slug": "my_3", "name_zh": "甲", "name_en": "", "aliases": ["a"], "primary_muscles": ["chest"], "secondary_muscles": [], "equipment": [],
                    "primary_names": ["Chest"], "secondary_names": [], "equipment_names": [], "source": "ai"}],
     "muscles": [{"key": "chest", "name": "Chest"}], "equipment": [{"key": "cable", "name": "Cable"}]}
    """)))
    XCTAssertEqual(d.exercises.first?.id, 3)
    XCTAssertEqual(d.exercises.first?.name, "甲", "an empty English name falls back")
    XCTAssertEqual(d.exercises.first?.fromAI, true)
    XCTAssertEqual(d.muscles.first?.key, "chest")
    XCTAssertEqual(d.equipment.first?.name, "Cable")
  }

  func testPromptsNameTheToolsAndNames() {
    XCTAssertTrue(MusclePrompts.analyse.contains("get_muscle_load"))
    let fill = MusclePrompts.fill(["Move A", "Move B"])
    XCTAssertTrue(fill.contains("add_custom_exercise"))
    XCTAssertTrue(fill.contains("Move A"))
    XCTAssertTrue(fill.contains("Move B"))
  }

  func testTrainingLogLink() {
    let link = TrainingLogLink(["state": "not_linked", "hint": "提示", "hint_en": "Hint", "can_connect": true])
    XCTAssertEqual(link?.connected, false)
    XCTAssertEqual(link?.canConnect, true)
    XCTAssertEqual(link?.hint, tr("提示", "Hint"))
    XCTAssertEqual(link?.word.tone, .mid)
    XCTAssertEqual(TrainingLogLink(["state": "connected"])?.word.tone, .good)
    XCTAssertEqual(TrainingLogLink(["state": "unreadable"])?.word.tone, .bad)
    XCTAssertNil(TrainingLogLink(nil))
  }
}

final class TrainingPlanModelTests: XCTestCase {
  private let sessionDoc = json("""
  {"date": "2026-01-02", "can_log": true, "can_edit_plan": true, "training_log": "not_linked", "change": {"from": ["旧"], "source": "ai"},
   "removed": [{"slug": "gone", "name_zh": "移除的", "name_en": "Gone"}],
   "sessions": [{"name": "上肢 A", "name_en": "Upper A", "day_of_week": 5, "added_for_day": false, "exercises": [
     {"slug": "a", "name_zh": "甲", "status": "active", "thumb": "/app/media/a.jpg", "primary_muscles": ["chest"], "secondary_muscles": ["triceps"], "modality": "weight_reps",
      "target": {"sets": 3, "reps": 10}},
     {"slug": "extra", "name_zh": "乙", "status": "banned", "added_for_day": true, "target": {"sets": 3, "reps": 10}}
   ]}]}
  """)

  func testSessionKnowsWhatARemovalNeeds() throws {
    let s = try XCTUnwrap(TrainingSession.build(sessionDoc, day: "2026-01-02", pending: []))
    XCTAssertEqual(s.title, tr("上肢 A", "Upper A"))
    XCTAssertTrue(s.canEditPlan)
    XCTAssertTrue(s.changed)
    XCTAssertEqual(s.trainingLog, "not_linked")
    XCTAssertEqual(s.removed.map(\.slug), ["gone"])
    let a = s.exercises[0]
    XCTAssertEqual(a.thumb, "/app/media/a.jpg")
    XCTAssertEqual(a.primaryMuscles, ["chest"])
    XCTAssertEqual(a.dayOfWeek, 5)
    XCTAssertEqual(a.sessionName, "上肢 A", "the stored name goes to the server")
    XCTAssertFalse(a.addedForDay)
    XCTAssertTrue(s.exercises[1].addedForDay)
    XCTAssertTrue(s.exercises[1].banned)
    XCTAssertEqual(s.loggable.map(\.slug), ["a"], "a banned exercise has no sets to log")
  }

  func testRemoveFromTemplateOnlyForASessionOfThePlan() {
    let t = RemoveTarget(slug: "a", name: "A", date: "2026-01-02", isToday: true, dayOfWeek: 5, sessionName: "S", sessionTitle: "S", addedForDay: false)
    XCTAssertTrue(t.canRemoveFromTemplate)
    var day = t
    day.addedForDay = true
    XCTAssertFalse(day.canRemoveFromTemplate)
    var none = t
    none.dayOfWeek = nil
    XCTAssertFalse(none.canRemoveFromTemplate)
  }

  func testWeekCarriesPlanDaysSplitsSuggestionsAndTheLog() throws {
    let w = try XCTUnwrap(TrainingWeek.build(json("""
    {"plan": {"name": "四天", "name_en": "Four days", "split_type": "4_day_upper_lower"},
     "available_splits": [{"split_type": "3_day_ppl", "name": "三天", "days": []}],
     "suggestions": [{"id": 9, "name": "卧推", "slug": "bench", "level": "info", "title": "加重", "next_weight_kg": 42.5}],
     "sparky": {"state": "not_linked", "can_connect": true}, "load_history_error": "x",
     "days": [{"date": "2026-01-07", "planned": [{"name": "上肢 A", "name_en": "Upper A (pull-focused)", "day_of_week": 3, "exercises": [
        {"slug": "a", "name": "甲", "status": "caution", "sets_text": "3 x 10", "load": {"kg": 20, "basis": "history"}, "suggestions": [{"next_weight_kg": 22.5}]},
        {"name": "没有 slug", "status": "banned", "load": {"kg": 20, "basis": "history"}}
      ]}], "removed": [{"slug": "gone", "name_zh": "移除的"}], "change": {"from": ["旧"]}}]}
    """), calendar: [], monday: "2026-01-05"))
    XCTAssertEqual(w.planName, tr("四天", "Four days"))
    XCTAssertTrue(w.hasPlan)
    XCTAssertEqual(w.splitType, "4_day_upper_lower")
    XCTAssertEqual(w.splits.map(\.type), ["3_day_ppl"])
    XCTAssertEqual(w.suggestions.first?.nextKg, 42.5)
    XCTAssertEqual(w.suggestions.first?.slug, "bench")
    XCTAssertEqual(w.log?.canConnect, true)
    XCTAssertTrue(w.loadHistoryError)
    let d = try XCTUnwrap(w.days.first { $0.date == "2026-01-07" })
    XCTAssertEqual(d.label, tr("上肢 A", "Upper A"))
    XCTAssertEqual(d.sessions.first?.rawName, "上肢 A")
    XCTAssertEqual(d.sessions.first?.exercises.first?.setsText, "3 x 10")
    XCTAssertEqual(d.sessions.first?.exercises.first?.suggestion?.text, "→ 22.5 kg")
    XCTAssertNil(d.sessions.first?.exercises[1].load, "a banned exercise shows no weight")
    XCTAssertEqual(d.removed.map(\.slug), ["gone"])
    XCTAssertEqual(d.changedFrom, ["旧"])
    XCTAssertNil(w.days.first { $0.date == "2026-01-05" }?.changedFrom)
  }

  func testWeekNumbersAndTickingAnEmptyRow() throws {
    let w = try XCTUnwrap(TrainingWeek.build(json(#"{"plan": null, "days": [{"date": "2026-01-06", "planned": [{"name": "A", "exercises": []}]}]}"#),
                                             calendar: [["date": "2026-01-05", "sessions_count": 1, "intensity": "hard", "duration_min": 90, "workout_kcal": 400],
                                                        ["date": "2026-01-07", "sessions_count": 1, "intensity": "light", "duration_min": 30]],
                                             monday: "2026-01-05"))
    XCTAssertEqual(w.trainedDays, 2)
    XCTAssertEqual(w.plannedDays, 1)
    XCTAssertEqual(w.totalMinutes, 120)
    XCTAssertEqual(w.totalKcal, 400)

    let target = SetRow(reps: 10, weightKg: 40, durationS: nil, done: false)
    var empty = SetRow(reps: nil, weightKg: nil, durationS: nil, done: false)
    empty.tick(target: target, timed: false)
    XCTAssertEqual(empty, SetRow(reps: 10, weightKg: 40, durationS: nil, done: true), "ticking an empty row takes the recommendation")
    var typed = SetRow(reps: 6, weightKg: 35, durationS: nil, done: false)
    typed.tick(target: target, timed: false)
    XCTAssertEqual(typed.reps, 6, "what was typed stays")
    typed.tick(target: target, timed: false)
    XCTAssertFalse(typed.done)
  }

  func testDayDetailZonesAndTheCardioLine() {
    XCTAssertEqual(TrainingDaySheet.zones([1, 2.5, "3"]), [1, 2.5, 3])
    XCTAssertEqual(TrainingDaySheet.zones(["z2": 4, "z1": 3]), [3, 4], "an object goes by zone")
    XCTAssertEqual(TrainingDaySheet.zones(nil), [])
    let s: [String: Any] = ["name": "Run", "exercises": [["name": "Run", "sets": [[String: Any]]()], ["name": "Run", "sets": [["reps": 5]]], ["name": "Squat", "sets": [[String: Any]]()]]]
    XCTAssertEqual(TrainingDaySheet.listed(s).count, 2, "the workout's own name without sets is not listed again")
  }

  func testNoPlanStillOffersTheSplits() throws {
    let w = try XCTUnwrap(TrainingWeek.build(json(#"{"plan": null, "days": [], "available_splits": [{"split_type": "3_day_ppl", "name": "三天", "days": []}]}"#), calendar: [], monday: "2026-01-05"))
    XCTAssertFalse(w.hasPlan)
    XCTAssertEqual(w.splits.count, 1)
    XCTAssertNil(w.log)
  }
}

final class TodayTrainingCardTests: XCTestCase {
  func testPlannedDay() throws {
    let t = try XCTUnwrap(TodayTraining.parse(json("""
    {"planned": [{"name": "上肢 A", "name_en": "Upper A", "exercises": [
        {"slug": "a", "name": "甲", "status": "active", "suggestions": [{"next_weight_kg": 20}]},
        {"slug": "b", "name": "乙", "status": "banned"}, {"slug": "c", "name": "丙", "status": "caution"}]}],
     "logged": [{"name": "午练", "exercises": [{"name": "丁", "status": "active", "sets_text": "20×8"}]}],
     "change": {"from": []}, "readiness": {"tier": "yellow", "label": "黄", "advice": "Steady"}, "sparky_error": "nope"}
    """)))
    XCTAssertEqual(t.title, tr("上肢 A", "Upper A"))
    XCTAssertEqual(t.tags.map(\.text), [tr("3 动作", "3 ex."), tr("注意 2", "Caution 2"), tr("加重 1", "Heavier 1"), tr("已调整", "Changed"), tr("已练", "Trained")])
    XCTAssertEqual(t.logged.first?.setsText, "20×8")
    XCTAssertEqual(t.changedFrom, [])
    XCTAssertEqual(t.readiness?.tone, .mid)
    XCTAssertEqual(t.readinessAdvice, "Steady")
    XCTAssertTrue(t.logUnreadable)
  }

  func testTrainedWithoutAPlanAndEmptyDays() throws {
    let trained = try XCTUnwrap(TodayTraining.parse(json(#"{"planned": [], "logged": [{"name": "午练", "exercises": [{"name": "丁"}, {"name": "戊"}]}]}"#)))
    XCTAssertEqual(trained.title, "午练")
    XCTAssertEqual(trained.tags.map(\.text), [tr("2 动作", "2 ex."), tr("已练", "Trained")])
    XCTAssertEqual(try XCTUnwrap(TodayTraining.parse(json(#"{"planned": [], "logged": [], "rest_day": true}"#))).title, tr("休息日", "Rest day"))
    XCTAssertEqual(try XCTUnwrap(TodayTraining.parse(json(#"{"planned": [], "logged": []}"#))).title, tr("无计划", "No plan"))
  }

  func testNoShareNoCardAndAFailedBlockSaysSo() throws {
    XCTAssertNil(TodayTraining.parse(json(#"{"code": "forbidden", "error": "no"}"#)))
    XCTAssertNil(TodayTraining.parse(nil))
    let failed = try XCTUnwrap(TodayTraining.parse(json(#"{"code": "sparky", "error": "down"}"#)))
    XCTAssertTrue(failed.failed)
    XCTAssertEqual(failed.tags.map(\.text), ["—"])
  }

  func testTodayModelReadsTheCards() {
    let m = TodayModel(TodayInputs(today: json(#"{"date": "2026-01-02", "training": {"planned": [], "logged": [], "rest_day": true}}"#),
                                   muscle: json(#"{"sex": "male", "unmatched": [], "muscles": [{"key": "chest", "name": "Chest", "status": {"week": "low"}, "target": {"low": 8, "high": 16}, "ranges": {"week": {"sets": 4, "volume_kg": 100}}, "trained_today": false}]}"#)))
    XCTAssertEqual(m.training?.title, tr("休息日", "Rest day"))
    XCTAssertEqual(m.muscle?.weekTotalSets, 4)
    XCTAssertEqual(m.muscle?.todayCardLine, tr("1 块肌群偏少", "1 muscles low"))
    XCTAssertNil(TodayModel(TodayInputs(today: json(#"{"date": "2026-01-02"}"#))).training)
  }

  func testMuscleCardLine() throws {
    let trained = try XCTUnwrap(MuscleLoad.parse(json("""
    {"sex": "male", "sports": {"week": [{"name": "Swim", "min": 30}, {"name": "Run", "min": 15, "mapped": false}]}, "muscles": [
      {"key": "chest", "name": "Chest", "trained_today": true, "ranges": {"week": {"sets": 4}}, "status": {"week": "low"}},
      {"key": "lats", "name": "Lats", "trained_today": true, "ranges": {"week": {"sets": 6}}, "status": {"week": "ok"}}]}
    """)))
    XCTAssertEqual(trained.todayCardLine, tr("今天练了：Chest、Lats", "Today: Chest, Lats"))
    XCTAssertEqual(trained.weekTotalSets, 10)
    XCTAssertEqual(trained.weekSportMinutes, 45)
    XCTAssertTrue(trained.sexSet)
    XCTAssertEqual(trained.unmappedSports, ["Run"])
    let none = try XCTUnwrap(MuscleLoad.parse(json(#"{"muscles": [{"key": "chest", "name": "Chest", "ranges": {"week": {"sets": 0}}, "status": {"week": "none"}}]}"#)))
    XCTAssertEqual(none.todayCardLine, tr("本周还没有逐组记录", "No sets logged this week"))
    XCTAssertFalse(none.sexSet, "no sex in the profile: the male body and a note")
  }

  func testTodayPullsTheMuscleMapOnlyWithTheFlagAndOnlyToday() {
    XCTAssertTrue(TodayDocs.fetches(day: "2026-01-02", isToday: true, circles: false, muscle: true).map(\.key).contains(RecordDocs.muscle))
    XCTAssertFalse(TodayDocs.fetches(day: "2026-01-02", isToday: true, circles: false).map(\.key).contains(RecordDocs.muscle))
    XCTAssertFalse(TodayDocs.fetches(day: "2026-01-02", isToday: false, circles: false, muscle: true).map(\.key).contains(RecordDocs.muscle))
  }
}
