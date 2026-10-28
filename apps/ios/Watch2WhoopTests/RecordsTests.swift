import GRDB
import SwiftUI
import XCTest
@testable import Watch2Whoop

// N5 (#340): 饮食, 记录 and 健康档案. Fictional numbers only.

private func json(_ s: String) -> [String: Any] { (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String: Any] ?? [:] }
private func list(_ s: String) -> [[String: Any]] { (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [[String: Any]] ?? [] }

private func pending(_ db: AppDatabase, _ kinds: [String]) throws -> [PendingRecord] {
  try db.writer.read { try LocalRecords.pending($0, kinds: kinds) }
}

final class LocalRecordsTests: XCTestCase {
  func testWriteKeepsARowAndAQueueEntryWithTheSameClientId() throws {
    let db = try AppDatabase.inMemory()
    let cid = try LocalRecords.write(db, kind: "water", date: "2026-01-02", group: "water", method: "POST", path: "/v1/users/me/water",
                                     body: ["ml": 250], overlay: ["ml": 250, "at": "08:00"])
    let p = try XCTUnwrap(try pending(db, ["water"]).first)
    XCTAssertEqual(p.clientId, cid)
    XCTAssertEqual(p.state, .pending)
    XCTAssertEqual(p.json["at"] as? String, "08:00")
    let item = try XCTUnwrap(OfflineQueue(db: db).all().first)
    XCTAssertEqual(item.clientId, cid)
    let sent = try XCTUnwrap(item.body.flatMap { try JSONSerialization.jsonObject(with: $0) as? [String: Any] })
    XCTAssertEqual(sent["client_id"] as? String, cid, "the endpoint dedups resends by the body's client_id")
    XCTAssertEqual(sent["ml"] as? Int, 250)
  }

  func testDeleteWithoutBodyCarriesNoClientIdAndOverlaysTheId() throws {
    let db = try AppDatabase.inMemory()
    try LocalRecords.write(db, kind: "food.delete", date: "2026-01-02", group: "food:a1", method: "DELETE",
                           path: "/v1/users/me/nutrition/entries/a1?date=2026-01-02", body: nil, overlay: ["id": "a1"], clientIdInBody: false)
    XCTAssertNil(try OfflineQueue(db: db).all().first?.body)
    XCTAssertEqual(try pending(db, ["food.delete"]).first?.json["id"] as? String, "a1")
  }

  func testCancelDropsAQueuedWriteButNotOneBeingSent() async throws {
    let db = try AppDatabase.inMemory()
    let a = try LocalRecords.write(db, kind: "water", date: "2026-01-02", group: "water", method: "POST", path: "/w", body: ["ml": 250])
    XCTAssertTrue(try LocalRecords.cancel(db, clientId: a))
    XCTAssertTrue(try pending(db, ["water"]).isEmpty)
    XCTAssertTrue(try OfflineQueue(db: db).all().isEmpty)

    let b = try LocalRecords.write(db, kind: "water", date: "2026-01-02", group: "water", method: "POST", path: "/w", body: ["ml": 500])
    try await db.writer.write { try $0.execute(sql: "UPDATE outbox SET state = 'sending'") }
    XCTAssertFalse(try LocalRecords.cancel(db, clientId: b), "being sent: the undo has to go to the server")
  }

  func testSupersedeKeepsOnlyTheLastStateOfAGroup() throws {
    let db = try AppDatabase.inMemory()
    try LocalRecords.write(db, kind: "grocery.item", date: nil, group: "grocery:4", method: "PATCH", path: "/g/4", body: ["bought": true])
    try LocalRecords.supersede(db, group: "grocery:4")
    try LocalRecords.write(db, kind: "grocery.item", date: nil, group: "grocery:4", method: "PATCH", path: "/g/4", body: ["bought": false])
    let rows = try pending(db, ["grocery.item"])
    XCTAssertEqual(rows.count, 1)
    XCTAssertEqual(rows.first?.json["bought"] as? Bool, false)
    XCTAssertEqual(try OfflineQueue(db: db).all().count, 1)
  }

  func testSentRowsArePrunedOnlyOnceTheServersCopyIsSaved() async throws {
    let db = try AppDatabase.inMemory()
    let q = OfflineQueue(db: db)
    try LocalRecords.write(db, kind: "water", date: "2026-01-02", group: "water", method: "POST", path: "/w", body: ["ml": 250])
    _ = try await q.drain { _ in .done }
    let rows = try pending(db, ["water"])
    XCTAssertEqual(rows.count, 1, "sent, but still drawn until the refresh brings it")
    XCTAssertNil(rows.first?.state)
    let sent = try await db.writer.read { try LocalRecords.sent($0, kinds: ["water"]) }
    try await db.writer.write { try LocalRecords.prune($0, clientIds: sent) }
    XCTAssertTrue(try pending(db, ["water"]).isEmpty)
  }

  func testRefusedWritesNeedTheUser() async throws {
    let db = try AppDatabase.inMemory()
    try LocalRecords.write(db, kind: "meal_log", date: "2026-01-02", group: "meal", method: "POST", path: "/m/1/log", body: [:])
    _ = try await OfflineQueue(db: db).drain { _ in .conflict("already logged") }
    let p = try XCTUnwrap(try pending(db, ["meal_log"]).first)
    XCTAssertEqual(p.state, .conflict)
    XCTAssertTrue(p.needsAttention)
  }

  func testRecordDays() {
    XCTAssertEqual(RecordDay.add("2026-02-28", 1), "2026-03-01")
    XCTAssertEqual(RecordDay.add("2026-01-01", -1), "2025-12-31")
    XCTAssertEqual(RecordDay.weekday("2026-01-05"), 1)   // a Monday
    XCTAssertEqual(RecordDay.weekday("2026-01-11"), 7)
    XCTAssertEqual(RecordDay.monday("2026-01-08"), "2026-01-05")
    XCTAssertEqual(TrainingMonth.add("2026-12", 1), "2027-01")
    XCTAssertEqual(TrainingMonth.add("2026-01", -1), "2025-12")
    XCTAssertEqual(TrainingCalendarView.daysIn("2028-02"), 29)
    XCTAssertEqual(RecordFormat.num(1234.5, 1), "1234.5")
    XCTAssertEqual(RecordFormat.num(nil), "—")
  }
}

final class FoodModelTests: XCTestCase {
  private let today = json("""
  {"date": "2026-01-02", "nutrition": {"date": "2026-01-02", "goal": {"name": "Training day", "calories": 2000, "protein_g": 150, "carbs_g": 200, "fat_g": 60},
   "intake": {"calories": 700, "protein_g": 40, "carbs_g": 80, "fat_g": 20},
   "entries": [
     {"id": "e1", "date": "2026-01-02", "time": "07:30", "meal_type": "breakfast", "food": "Oats", "quantity": 1, "unit": "1 bowl", "calories": 400, "protein_g": 15, "carbs_g": 60, "fat_g": 8, "fiber_g": 6},
     {"id": "e2", "date": "2026-01-02", "time": "15:00", "meal_type": "snacks", "food": "Yoghurt", "quantity": 1, "unit": "170 g", "calories": 300, "protein_g": 25, "carbs_g": 20, "fat_g": 12}
   ]}}
  """)

  private func rec(_ kind: String, _ payload: [String: Any], date: String = "2026-01-02", state: OutboxItem.State? = .pending) -> PendingRecord {
    PendingRecord(kind: kind, clientId: UUID().uuidString, date: date,
                  payload: try! JSONSerialization.data(withJSONObject: payload), createdAt: 0, state: state, lastError: nil)
  }

  func testTheServersDayAsItCame() throws {
    let d = try XCTUnwrap(NutritionDay.build(today: today, day: "2026-01-02", pending: []))
    XCTAssertEqual(d.goal.kcal, 2000)
    XCTAssertEqual(d.goalName, "Training day")
    XCTAssertEqual(d.intake.kcal, 700)
    XCTAssertEqual(d.meals().map(\.0), [.breakfast, .snack])
    XCTAssertEqual(d.entries.first?.extras.count, 1, "only the optional values the entry has")
  }

  func testPendingWritesAreLaidOverTheDay() throws {
    let d = try XCTUnwrap(NutritionDay.build(today: today, day: "2026-01-02", pending: [
      rec(FoodKind.foodDelete, ["id": "e2"]),
      rec(FoodKind.portion, ["id": "e1", "quantity": 1.5]),
      rec(FoodKind.mealLog, ["template_id": 3, "name": "Shake", "meal": "snacks", "calories": 120, "protein_g": 24, "carbs_g": 3, "fat_g": 2]),
      rec(FoodKind.water, ["ml": 250]),                                 // another kind: ignored here
      rec(FoodKind.foodDelete, ["id": "e1"], date: "2026-01-01"),      // another day
    ]))
    XCTAssertEqual(d.entries.map(\.food), ["Oats", "Shake"])
    XCTAssertEqual(d.entries[0].macros.kcal, 600)
    XCTAssertEqual(d.entries[0].quantity, 1.5)
    XCTAssertNotNil(d.entries[0].pending)
    XCTAssertTrue(d.entries[1].isLocal)
    XCTAssertEqual(d.intake.kcal, 720)
    XCTAssertEqual(d.intake.protein, 46.5)
  }

  func testWaterCountsQueuedDrinksAndHidesOnesBeingUndone() throws {
    let w = json(#"{"date": "2026-01-02", "total_ml": 750, "goal": {"ml": 2500}, "entries": [{"id": 2, "ml": 250}, {"id": 1, "ml": 500}], "quick_ml": [250, 500]}"#)
    let day = try XCTUnwrap(WaterDay.build(w, day: "2026-01-02", pending: [rec(FoodKind.water, ["ml": 500]), rec(FoodKind.waterDelete, ["id": 2])]))
    XCTAssertEqual(day.total, 1000)
    XCTAssertEqual(day.goal, 2500)
    XCTAssertEqual(day.last?.ml, 500)
    XCTAssertNotNil(day.last?.pending, "the newest is the queued one: its undo needs no network")
    XCTAssertEqual(day.fraction, 0.4, accuracy: 0.001)
    XCTAssertNil(WaterDay.build(nil, day: "2026-01-02", pending: []), "no water feature: no card")
  }

  func testTemplatesKnowTodaysLogging() {
    let l = list("""
    [{"id": 1, "name": "Oats", "meal": "breakfast", "items": [{"food": "oats"}, {"food": "milk"}], "totals": {"kcal": 390, "protein_g": 16}, "today_log": {"log_id": 9}},
     {"id": 2, "name": "Shake", "meal": "snacks", "items": [], "totals": {"kcal": 120, "protein_g": 24}, "today_log": null}]
    """)
    let t = MealTemplate.parseList(l, pending: [rec(FoodKind.mealLog, ["template_id": 2], date: "2026-01-02")], today: "2026-01-02")
    XCTAssertEqual(t[0].todayLog, 9)
    XCTAssertEqual(t[0].items, "oats · milk")
    XCTAssertEqual(t[1].meal, .snack)
    XCTAssertNotNil(t[1].pending)
  }

  func testRecipesBySearchAndMeal() throws {
    let l = list("""
    [{"id": 1, "name": "Tomato eggs", "slots": ["lunch", "dinner"], "tags": ["quick"], "kcal": 300, "protein_g": 18, "favorite": false, "prep_min": 10, "cook_min": 5,
      "constraint_flags": {"purine_level": "high", "sat_fat_level": "low", "red_meat_g": 0}},
     {"id": 2, "name": "Salmon rice", "slots": ["dinner"], "tags": [], "kcal": 400, "protein_g": 40, "favorite": true, "badges": [{"label": "High protein", "tone": "good"}]},
     {"id": 3, "name": "Pancakes", "slots": ["breakfast"], "tags": [], "kcal": 380, "protein_g": 16, "ingredients": [{"food": "banana", "grams": 120}], "steps": ["Mix", {"text": "Fry"}]}]
    """)
    let r = l.compactMap(Recipe.parse)
    XCTAssertEqual(r[0].minutes, 15)
    XCTAssertEqual(r[0].badges.first?.0, tr("嘌呤高", "High purine"))
    XCTAssertEqual(r[1].badges.first?.1, .good)
    XCTAssertEqual(r[2].steps, ["Mix", "Fry"])
    XCTAssertEqual(Recipe.grouped(r, query: "").map(\.0), [.breakfast, .lunch, .dinner])
    XCTAssertEqual(Recipe.grouped(r, query: "BANANA").first?.1.map(\.name), ["Pancakes"])
    XCTAssertEqual(r[1].perServing.scaled(1.5).kcal, 600)
  }

  func testGroceryTicksStillQueuedAreShown() throws {
    let g = json("""
    {"week_start": "2026-01-05", "items": [{"id": 1, "name": "Chicken", "qty_label": "1 kg", "category_label": "Meat", "bought": false},
     {"id": 2, "name": "Oats", "qty_label": "1", "category_label": "Grains", "bought": true}, {"id": 3, "name": "Beef", "category_label": "Meat", "bought": false}]}
    """)
    let l = try XCTUnwrap(GroceryList.build(g, pending: [rec(FoodKind.grocery, ["id": 1, "bought": true], date: "")]))
    XCTAssertEqual(l.bought, 2)
    XCTAssertEqual(l.byCategory().map(\.0), ["Meat", "Grains"])
    XCTAssertNotNil(l.items[0].pending)
  }

  func testMealSlots() {
    XCTAssertEqual(MealSlot("snacks"), .snack)
    XCTAssertEqual(MealSlot.snack.logName, "snacks")
    let c = Calendar.current
    XCTAssertEqual(MealSlot.now(c.date(bySettingHour: 8, minute: 0, second: 0, of: Date())!), .breakfast)
    XCTAssertEqual(MealSlot.now(c.date(bySettingHour: 19, minute: 0, second: 0, of: Date())!), .dinner)
  }
}

final class RecordModelTests: XCTestCase {
  private func rec(_ kind: String, _ payload: [String: Any], date: String) -> PendingRecord {
    PendingRecord(kind: kind, clientId: UUID().uuidString, date: date,
                  payload: try! JSONSerialization.data(withJSONObject: payload), createdAt: 0, state: .pending, lastError: nil)
  }

  func testJournalValuesWithPendingAnswers() throws {
    let items = list("""
    [{"key": "supp_a", "label": "Took vitamin A (demo)", "kind": "bool", "category": "supplement"},
     {"key": "coffee", "label": "Coffee / tea", "kind": "bool", "category": "diet"},
     {"key": "water_l", "label": "Water", "kind": "number", "unit": "L", "category": "diet"}]
    """)
    let entries = list(#"[{"date": "2026-01-02", "key": "coffee", "value": 1}, {"date": "2026-01-02", "key": "water_l", "value": 2.5}, {"date": "2026-01-01", "key": "supp_a", "value": 1}]"#)
    let j = try XCTUnwrap(JournalDay.build(items: items, frequent: list(#"[{"key": "coffee"}]"#), entries: entries, day: "2026-01-02",
                                           pending: [rec("journal", ["key": "coffee", "value": NSNull()], date: "2026-01-02"),
                                                     rec("journal", ["key": "supp_a", "value": 1], date: "2026-01-02")]))
    XCTAssertNil(j.values["coffee"], "a pending clear wins over the server's tick")
    XCTAssertEqual(j.values["supp_a"], 1)
    XCTAssertEqual(j.values["water_l"], 2.5)
    XCTAssertEqual(j.logged.map(\.key), ["supp_a", "water_l"])
    XCTAssertEqual(j.frequent.map(\.key), ["coffee"])
    XCTAssertEqual(j.grouped().map(\.0), ["supplement", "diet"])
    XCTAssertEqual(j.items[1].short, "Coffee", "「a / b」 → a")
  }

  private let session = json("""
  {"date": "2026-01-02", "is_today": true, "rest_day": false, "minutes": 45, "can_log": true,
   "readiness": {"tier": "yellow", "label": "Take it easier"},
   "sessions": [{"name": "Upper B", "exercises": [
     {"slug": "press", "name_zh": "推", "name_en": "Press", "status": "active", "modality": "weight_reps", "per_side": false,
      "load": {"kg": 20, "basis": "estimate"}, "target": {"sets": 3, "reps": 10, "rep_low": 8, "rep_high": 12, "weight_kg": 20, "duration_s": null},
      "last": [{"date": "2025-12-30", "sets": ["20×10", "20×9"]}]},
     {"slug": "upright_row", "name_zh": "直立划船", "name_en": "Upright row", "status": "banned", "reason": "Shoulder (demo)", "target": {"sets": 3, "reps": 10}},
     {"slug": "plank", "name_zh": "平板", "name_en": "Plank", "status": "caution", "cues": "Neutral spine", "modality": "duration", "target": {"sets": 2, "duration_s": 45}}
   ]}],
   "log": {"status": "submitted", "exercises": [{"slug": "press", "sets": [{"reps": 10, "weight_kg": 20, "done": true}]}]}}
  """)

  func testTrainingDayRowsAndRestrictions() throws {
    let s = try XCTUnwrap(TrainingSession.build(session, day: "2026-01-02", pending: []))
    XCTAssertEqual(s.exercises.map(\.slug), ["press", "upright_row", "plank"])
    XCTAssertTrue(s.exercises[1].banned)
    XCTAssertTrue(s.exercises[2].caution && s.exercises[2].timed)
    XCTAssertEqual(s.loggable.map(\.slug), ["press", "plank"], "banned exercises are not logged")
    XCTAssertEqual(s.rows["press"]?.count, 1, "the saved draft wins over the prescription")
    XCTAssertEqual(s.rows["plank"]?.count, 2)
    XCTAssertEqual(s.rows["plank"]?.first?.durationS, 45)
    XCTAssertTrue(s.submitted)
    XCTAssertEqual(s.doneSets, 1)
    XCTAssertEqual(s.readiness?.tone, .mid)
    XCTAssertTrue(s.exercises[0].prescription.contains("8–12"))
    XCTAssertEqual(s.exercises[0].logName, "推")
    let body = s.payload()["exercises"] as? [[String: Any]]
    XCTAssertEqual(body?.map { $0["slug"] as? String }, ["press", "plank"])
  }

  func testAPendingDraftWinsAndIsNotSubmittedYet() throws {
    let draft: [String: Any] = ["exercises": [["slug": "press", "sets": [["reps": 8, "weight_kg": 22.5, "done": true], ["reps": 8, "done": false]]]]]
    let s = try XCTUnwrap(TrainingSession.build(session, day: "2026-01-02", pending: [rec(RecordKind.session, draft, date: "2026-01-02")]))
    XCTAssertEqual(s.rows["press"]?.map(\.weightKg), [22.5, nil])
    XCTAssertFalse(s.submitted, "changed since the submit: submit again")
    XCTAssertNotNil(s.pendingSave)
  }

  func testWeekStripAndMonth() throws {
    let w = json("""
    {"plan": {"name": "Upper / lower"}, "days": [{"date": "2026-01-05", "planned": [{"name": "Upper A（push）"}]}, {"date": "2026-01-07", "planned": [], "rest_day": true}],
     "cautions": [{"slug": "face_pull", "name": "Face pull", "status": "caution", "reason": "demo"}],
     "readiness": {"tier": "green", "label": "Green"}, "volume_by_group": {"chest": {"name_zh": "Chest", "direct_sets": 7, "status": "low"}}}
    """)
    let cal = list(#"[{"date": "2026-01-05", "sessions_count": 1, "intensity": "hard"}, {"date": "2026-01-06", "sessions_count": 1, "intensity": "rest"}]"#)
    let week = try XCTUnwrap(TrainingWeek.build(w, calendar: cal, monday: "2026-01-05"))
    XCTAssertEqual(week.days.count, 7)
    XCTAssertEqual(week.days[0].label, "Upper A")
    XCTAssertTrue(week.days[0].trained)
    XCTAssertFalse(week.days[1].trained, "a rest day in the calendar is not a workout")
    XCTAssertEqual(week.days[2].label, tr("休", "Rest"))
    XCTAssertEqual(week.cautions.first?.banned, false)
    XCTAssertEqual(week.readiness?.tone, .good)
    let m = try XCTUnwrap(TrainingMonth.build(json(#"{"days": [{"date": "2026-01-05", "sessions_count": 2, "intensity": "max", "labels": ["Run"]}], "summary": {"training_days": 1, "total_duration_min": 90, "total_workout_kcal": 700}}"#), month: "2026-01"))
    XCTAssertEqual(m.trainingDays, 1)
    XCTAssertEqual(TrainingMonth.tone(m.days.first?.intensity), .bad)
  }

  func testExerciseRulesOnlyRestricted() {
    let r = ExerciseRule.parseList(list("""
    [{"exercise_ref": "face_pull", "name_zh": "面拉", "name_en": "Face pull", "status": "caution", "cues": "light"},
     {"exercise_ref": "squat", "name_zh": "深蹲", "status": "active"},
     {"exercise_ref": "upright_row", "name_zh": "直立划船", "name_en": "Upright row", "status": "banned", "reason": "demo"}]
    """))
    XCTAssertEqual(r.map(\.ref), ["upright_row", "face_pull"], "banned first, active ones left out")
  }

  func testMuscleLoadRegions() throws {
    let m = try XCTUnwrap(MuscleLoad.parse(json("""
    {"sex": "female", "unmatched": ["Mystery move"], "sports": {"week": [{"name": "Swim", "min": 30}]}, "muscles": [
      {"key": "lats", "name": "Lats", "ranges": {"today": {"sets": 0}, "week": {"sets": 4, "volume_kg": 900}, "last7": {"sets": 4}},
       "status": {"week": "low", "last7": "low"}, "trained_today": false, "target": {"low": 10, "high": 20}, "sport_min": {"week": 30}},
      {"key": "middle back", "name": "Mid back", "ranges": {"today": {"sets": 3}, "week": {"sets": 12}, "last7": {"sets": 12}},
       "status": {"week": "ok", "last7": "ok"}, "trained_today": true},
      {"key": "chest", "name": "Chest", "ranges": {"week": {"sets": 25}}, "status": {"week": "high"}, "trained_today": false}]}
    """)))
    XCTAssertEqual(m.sex, "female")
    let week = m.regions(.week)
    XCTAssertEqual(week["upper-back"]?.key, "middle back", "the muscle with more sets decides a shared region")
    XCTAssertEqual(week["upper-back"]?.status, "ok")
    XCTAssertEqual(week["upper-back"]?.today, true)
    XCTAssertEqual(week["upper-back"]?.sport, true)
    XCTAssertEqual(week["chest"]?.status, "high")
    XCTAssertEqual(m.regions(.today)["chest"]?.status, "none", "今天 greys what was not trained today")
    XCTAssertEqual(m.muscles.first?.volume[.week], 900)
  }
}

final class MuscleMapTests: XCTestCase {
  func testSVGPathCommands() {
    let p = SVGPath.parse("M 10 10 L 20 10 h 10 v 10 H 10 Z")
    XCTAssertEqual(p.boundingRect, CGRect(x: 10, y: 10, width: 20, height: 10))
    let rel = SVGPath.parse("m10,10 l10,0 0,10 -10,0 z")   // implicit line-tos after m / l
    XCTAssertEqual(rel.boundingRect, CGRect(x: 10, y: 10, width: 10, height: 10))
    XCTAssertEqual(SVGPath.parse("M0 0C0-.5.5-1 1-1").currentPoint, CGPoint(x: 1, y: -1), "compact numbers: -.5 and .5 split right")
    let arc = SVGPath.parse("M 0 0 a 5 5 0 0 1 10 0")   // half circle above the x axis
    XCTAssertEqual(arc.currentPoint?.x ?? 0, 10, accuracy: 0.001)
    XCTAssertEqual(arc.boundingRect.minY, -5, accuracy: 0.05)
    let flags = SVGPath.parse("M0 0a5 5 0 0110 0")          // flags written together
    XCTAssertEqual(flags.currentPoint?.x ?? 0, 10, accuracy: 0.001)
    XCTAssertTrue(SVGPath.parse("").isEmpty)
  }

  func testTheBundledBodiesParse() throws {
    let raw = try XCTUnwrap(MuscleBodies.load(bundle: Bundle(for: AppDatabase.self)), "muscle-body-data.js is a resource of the app")
    for sex in ["male", "female"] {
      for side in ["front", "back"] {
        let d = try XCTUnwrap((raw[sex] as? [String: Any])?[side] as? [String: Any])
        let s = try XCTUnwrap(MuscleBodies.build(d))
        XCTAssertGreaterThan(s.regions.count, 9)
        XCTAssertFalse(s.outline.isEmpty)
        for (slug, path) in s.regions {
          XCTAssertTrue(s.box.insetBy(dx: -40, dy: -40).contains(path.boundingRect), "\(sex) \(side) \(slug) inside its view box")
        }
      }
    }
    let regions = Set(MuscleLoad.bodyRegions.values.flatMap { $0 })
    let drawn = Set(try XCTUnwrap(MuscleBodies.build((raw["male"] as! [String: Any])["front"] as! [String: Any])).regions.map(\.0)
      + MuscleBodies.build((raw["male"] as! [String: Any])["back"] as! [String: Any])!.regions.map(\.0))
    XCTAssertTrue(regions.isSubset(of: drawn), "every catalog muscle has somewhere to be drawn: \(regions.subtracting(drawn))")
  }
}

final class HealthModelTests: XCTestCase {
  func testSupplementsWithTodaysCheckIn() {
    let l = list("""
    [{"id": 1, "name": "Creatine (demo)", "dose": "5g", "status": "active", "journal_key": "supp_creatine"},
     {"id": 2, "name": "Fish oil (demo)", "status": "active", "journal_key": "supp_fish"},
     {"id": 3, "name": "Vitamin D (demo)", "status": "paused", "journal_key": null}]
    """)
    let p = PendingRecord(kind: "journal", clientId: "c1", date: "2026-01-02",
                          payload: try! JSONSerialization.data(withJSONObject: ["key": "supp_fish", "value": 1]), createdAt: 0, state: .pending, lastError: nil)
    let s = Supplement.build(l, journal: list(#"[{"date": "2026-01-02", "key": "supp_creatine", "value": 1}]"#), today: "2026-01-02", pending: [p])
    XCTAssertEqual(s.map(\.takenToday), [true, true, false])
    XCTAssertNotNil(s[1].pending)
    XCTAssertEqual(s.filter(\.active).count, 2)
  }

  func testLabsAndTheirTrend() throws {
    let sum = LabSummary.parseList(list("""
    [{"test_key": "uric_acid", "test_name": "Uric acid", "status": "high", "latest": {"collected_on": "2026-01-02", "value": 430, "unit": "μmol/L"},
      "history": [{"value": 520}, {"value": 470}, {"value": 430}], "change_from_previous": {"delta": -40}}]
    """))
    XCTAssertEqual(sum.first?.history, [520, 470, 430])
    XCTAssertEqual(LabSummary.tone(sum.first?.status), .bad)
    let h = try XCTUnwrap(LabHistory.parse(json("""
    {"test_key": "uric_acid", "test_name": "Uric acid", "unit": "μmol/L", "ref_low": 200, "ref_high": 420, "good_direction": "down",
     "rows": [{"id": 1, "collected_on": "2025-11-01", "value": 470, "source": "home_device", "fasting": false},
              {"id": 2, "collected_on": "2026-01-02", "value": 430, "source": "lab", "fasting": true, "delta": -40}]}
    """)))
    XCTAssertTrue(h.rows[0].homeDevice)
    XCTAssertEqual(h.deltaTone(-40), .good)
    XCTAssertEqual(h.deltaTone(15), .bad)
    XCTAssertEqual(LabTrendView.band(h, [430, 470]).map { [$0.0, $0.1] }, [200, 420])
    let domain = LabTrendView.domain(h, [430, 470])
    XCTAssertTrue(domain.contains(200) && domain.contains(470))
    var oneSided = h
    oneSided.refLow = nil
    XCTAssertEqual(LabTrendView.band(oneSided, [430, 470])?.1, 420)
  }

  func testEventsNewestFirst() {
    let e = BodyEvent.parseList(list("""
    [{"id": 1, "title": "Cold", "local_date": "2026-01-01", "category": "illness", "severity": 3},
     {"id": 2, "title": "Sore knee", "occurred_at": "2026-01-05T08:00:00+00:00", "category": "pain"}]
    """))
    XCTAssertEqual(e.map(\.id), [2, 1])
    XCTAssertEqual(e[0].date, "2026-01-05")
    XCTAssertEqual(BodyEvent.categoryTitle("pain"), tr("疼痛", "Pain"))
  }
}
