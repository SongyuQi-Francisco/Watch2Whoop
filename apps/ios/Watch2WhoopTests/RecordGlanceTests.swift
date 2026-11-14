import XCTest
@testable import Watch2Whoop

/// 2.0.1 记录 (docs/design/2.0.1 screens 10–15): the set ring's arcs, the muscles inside it, the week's dots, the weekly
/// muscle rows, the history calendar and the food dial. Every number is made up.
final class RecordGlanceTests: XCTestCase {
  override func setUp() {
    super.setUp()
    Lang.override = "zh"
  }

  override func tearDown() {
    Lang.override = nil
    super.tearDown()
  }

  // MARK: - fixtures

  private func exercise(_ slug: String, sets: Int, primary: [String], secondary: [String] = [], status: String = "active") -> [String: Any] {
    let target: [String: Any] = ["sets": sets, "reps": 8, "weight_kg": 40]
    return ["slug": slug, "name_zh": slug, "status": status, "target": target, "primary_muscles": primary, "secondary_muscles": secondary]
  }

  private func session() throws -> TrainingSession {
    var list: [[String: Any]] = []
    list.append(exercise("bench", sets: 3, primary: ["chest"], secondary: ["triceps"]))
    list.append(exercise("row", sets: 2, primary: ["lats"]))
    list.append(exercise("upright", sets: 3, primary: ["shoulders"], status: "banned"))
    let s: [String: Any] = ["name": "上肢 A", "exercises": list]
    let j: [String: Any] = ["sessions": [s], "can_log": true]
    return try XCTUnwrap(TrainingSession.build(j, day: "2026-11-12", pending: []))
  }

  // MARK: - set ring

  func testOneArcPerSetWithWiderGapsBetweenExercises() throws {
    let s = try session()
    var rows = s.rows
    rows["bench"]?[0].done = true
    let sets = RecordGlance.sets(s, rows: rows)
    XCTAssertEqual(sets.count, 5)   // the banned exercise has no sets
    XCTAssertEqual(sets.filter(\.done).count, 1)
    let arcs = RecordGlance.arcs(sets, setGap: 0.01, exerciseGap: 0.05)
    XCTAssertEqual(arcs.count, 5)
    XCTAssertTrue(arcs[0].done)
    XCTAssertFalse(arcs[1].done)
    // three set gaps + one exercise gap + the gap at 12 o'clock
    let gaps: Double = 3 * 0.01 + 2 * 0.05
    let length: Double = (1 - gaps) / 5
    XCTAssertEqual(arcs[0].start, 0.025, accuracy: 1e-9)
    XCTAssertEqual(arcs[0].end - arcs[0].start, length, accuracy: 1e-9)
    XCTAssertEqual(arcs[1].start - arcs[0].end, 0.01, accuracy: 1e-9)
    XCTAssertEqual(arcs[3].start - arcs[2].end, 0.05, accuracy: 1e-9)   // bench → row
    let last = try XCTUnwrap(arcs.last)
    XCTAssertEqual(last.end, 1 - 0.025, accuracy: 1e-9)
  }

  func testNoSetsNoArcs() {
    XCTAssertTrue(RecordGlance.arcs([]).isEmpty)
    XCTAssertTrue(RecordGlance.exerciseEdges([]).isEmpty)
  }

  func testExerciseEdgesMarkWhereTheNextExerciseStarts() throws {
    let s = try session()
    let edges = RecordGlance.exerciseEdges(RecordGlance.sets(s, rows: s.rows))
    XCTAssertEqual(edges, [3])
  }

  // MARK: - muscles inside the ring

  func testRegionSharesWeighPrimaryOverSecondary() throws {
    let s = try session()
    let shares = RecordGlance.regionShares(s.exercises, rows: s.rows)
    XCTAssertEqual(shares["chest"] ?? 0, 1, accuracy: 1e-9)          // 3 sets primary
    XCTAssertEqual(shares["upper-back"] ?? 0, 2.0 / 3.0, accuracy: 1e-9)   // 2 sets primary
    XCTAssertEqual(shares["triceps"] ?? 0, 0.5, accuracy: 1e-9)      // 3 sets × 0.5
    XCTAssertNil(shares["deltoids"])                                 // banned: not done
  }

  func testDoneOnlySharesFillSetBySet() throws {
    let s = try session()
    XCTAssertTrue(RecordGlance.regionShares(s.exercises, rows: s.rows, doneOnly: true).isEmpty)
    var rows = s.rows
    rows["row"]?[0].done = true
    let shares = RecordGlance.regionShares(s.exercises, rows: rows, doneOnly: true)
    XCTAssertEqual(shares["upper-back"] ?? 0, 1, accuracy: 1e-9)
    XCTAssertNil(shares["chest"])
  }

  func testShareOpacity() {
    XCTAssertEqual(RecordGlance.shareOpacity(nil), 0)
    XCTAssertEqual(RecordGlance.shareOpacity(0), 0)
    XCTAssertEqual(RecordGlance.shareOpacity(1), RecordParams.shareOpacity.upperBound, accuracy: 1e-9)
    XCTAssertGreaterThan(RecordGlance.shareOpacity(0.01), RecordParams.shareOpacity.lowerBound - 1e-9)
  }

  // MARK: - the week

  func testWeekDots() throws {
    var plan: [[String: Any]] = []
    plan.append(["date": "2026-11-13", "planned": [["name": "腿 A"]]])
    plan.append(["date": "2026-11-15", "rest_day": true])
    let w: [String: Any] = ["days": plan]
    let cal: [[String: Any]] = [["date": "2026-11-09", "sessions_count": 1, "intensity": "hard"],
                                ["date": "2026-11-10", "sessions_count": 1, "intensity": "rest"]]
    let week = try XCTUnwrap(TrainingWeek.build(w, calendar: cal, monday: "2026-11-09"))
    let dots = RecordGlance.weekDots(week, today: "2026-11-12")
    XCTAssertEqual(dots, [.trained, .none, .none, .today, .planned, .none, .none])
    XCTAssertEqual(RecordGlance.weekTarget(week), 1)   // one trained, one planned
  }

  func testWeekTargetHiddenWhenThePlanStartedMidWeek() throws {
    let w: [String: Any] = ["days": [["date": "2026-11-15", "planned": [["name": "上肢 A"]]]]]
    let cal: [[String: Any]] = [["date": "2026-11-09", "sessions_count": 1, "intensity": "hard"],
                                ["date": "2026-11-10", "sessions_count": 1, "intensity": "light"]]
    let week = try XCTUnwrap(TrainingWeek.build(w, calendar: cal, monday: "2026-11-09"))
    XCTAssertNil(RecordGlance.weekTarget(week))
  }

  // MARK: - weekly muscle map

  private func muscleLoad() throws -> MuscleLoad {
    func m(_ key: String, sets: Double, low: Double, high: Double, status: String) -> [String: Any] {
      let ranges: [String: Any] = ["week": ["sets": sets], "last7": ["sets": sets]]
      return ["key": key, "name": key, "ranges": ranges, "status": ["week": status], "target": ["low": low, "high": high]]
    }
    var list: [[String: Any]] = []
    list.append(m("shoulders", sets: 22, low: 8, high: 16, status: "high"))
    list.append(m("lats", sets: 5, low: 6, high: 12, status: "low"))
    list.append(m("middle back", sets: 3, low: 4, high: 8, status: "low"))
    list.append(m("chest", sets: 10, low: 8, high: 16, status: "ok"))
    list.append(m("calves", sets: 0, low: 4, high: 8, status: "none"))
    let j: [String: Any] = ["sex": "male", "muscles": list]
    return try XCTUnwrap(MuscleLoad.parse(j))
  }

  func testGroupsSumTheirMusclesAndRanges() throws {
    let load = try muscleLoad()
    let rows = RecordGlance.groups(load, range: .week)
    XCTAssertEqual(rows.map(\.key), ["chest", "back", "shoulders", "legs"])   // order of RecordParams.muscleGroups; no arms, no core
    let back = try XCTUnwrap(rows.first { $0.key == "back" })
    XCTAssertEqual(back.sets, 8)
    XCTAssertEqual(back.low, 10)
    XCTAssertEqual(back.high, 20)
    XCTAssertEqual(back.status, "low")
    XCTAssertEqual(rows.first { $0.key == "shoulders" }?.status, "high")
    XCTAssertEqual(rows.first { $0.key == "chest" }?.status, "ok")
    XCTAssertEqual(rows.first { $0.key == "legs" }?.status, "none")
    XCTAssertEqual(back.title, "背")
  }

  func testOnTargetCountsTheServerStatus() throws {
    let t = RecordGlance.onTarget(try muscleLoad(), range: .week)
    XCTAssertEqual(t.ok, 1)
    XCTAssertEqual(t.total, 5)
  }

  func testStatusAndScale() {
    XCTAssertEqual(RecordGlance.status(sets: 0, low: 4, high: 8), "none")
    XCTAssertEqual(RecordGlance.status(sets: 3, low: nil, high: nil), "ok")
    XCTAssertEqual(RecordGlance.status(sets: 9, low: 4, high: 8), "high")
    let row = RecordGlance.GroupRow(key: "legs", sets: 4, low: 10, high: 20, status: "low")
    XCTAssertEqual(RecordGlance.groupScale([row]), 23, accuracy: 1e-9)
  }

  // MARK: - history

  private func month() throws -> TrainingMonth {
    var days: [[String: Any]] = []
    days.append(["date": "2026-11-02", "sessions_count": 1, "intensity": "hard", "strain": 21, "duration_min": 58,
                 "labels": ["腿 A（下肢）"], "types": ["strength_training"]])
    days.append(["date": "2026-11-05", "sessions_count": 1, "intensity": "light", "strain": 0, "duration_min": 46,
                 "labels": ["篮球"], "types": ["basketball"]])
    days.append(["date": "2026-11-20", "sessions_count": 0, "future": true])
    let j: [String: Any] = ["days": days, "summary": ["training_days": 2, "total_duration_min": 104]]
    return try XCTUnwrap(TrainingMonth.build(j, month: "2026-11"))
  }

  func testHistoryCellsStartOnMonday() throws {
    let cells = RecordGlance.historyCells(try month(), today: "2026-11-12")
    // 2026-11-01 is a Sunday: six empty cells, then 30 days
    XCTAssertEqual(cells.count, 36)
    XCTAssertNil(cells[5])
    XCTAssertEqual(cells[6]?.date, "2026-11-01")
    let big = try XCTUnwrap(cells[7])
    XCTAssertTrue(big.trained)
    XCTAssertEqual(big.diameter, Double(RecordParams.dotDiameter.upperBound), accuracy: 1e-9)
    let small = try XCTUnwrap(cells[10])
    XCTAssertEqual(small.diameter, Double(RecordParams.dotDiameter.lowerBound), accuracy: 1e-9)
    XCTAssertEqual(small.opacity, RecordParams.intensityOpacity["light"] ?? 0, accuracy: 1e-9)
    XCTAssertEqual(cells[17]?.today, true)
    XCTAssertEqual(cells[25]?.future, true)
  }

  func testTrainedDaysNewestFirstAndAverageStrain() throws {
    let m = try month()
    XCTAssertEqual(RecordGlance.trainedDays(m).map(\.date), ["2026-11-05", "2026-11-02"])
    XCTAssertEqual(RecordGlance.averageStrain(m) ?? 0, 10.5, accuracy: 1e-9)
    XCTAssertEqual(m.days.first?.minutes, 58)
    XCTAssertTrue(RecordGlance.isStrength(m.days[0].types))
    XCTAssertFalse(RecordGlance.isStrength(m.days[1].types))
  }

  func testServerAverageStrainWins() throws {
    let j: [String: Any] = ["days": [], "summary": ["avg_strain": 11.7]]
    let m = try XCTUnwrap(TrainingMonth.build(j, month: "2026-11"))
    XCTAssertEqual(RecordGlance.averageStrain(m) ?? 0, 11.7, accuracy: 1e-9)
  }

  // MARK: - food

  func testFoodDialPutsTheGoalBeforeTheEnd() {
    let d = RecordGlance.foodDial(kcal: 420, goal: 2100)
    XCTAssertEqual(d.fraction, 420 / 2520, accuracy: 1e-9)
    XCTAssertEqual(d.target ?? 0, 1 / RecordParams.goalHeadroom, accuracy: 1e-9)
    XCTAssertEqual(d.left ?? 0, 1680, accuracy: 1e-9)
    let over = RecordGlance.foodDial(kcal: 3000, goal: 2100)
    XCTAssertEqual(over.fraction, 1, accuracy: 1e-9)
    XCTAssertEqual(over.left ?? 0, -900, accuracy: 1e-9)
  }

  func testFoodDialWithoutGoal() {
    let d = RecordGlance.foodDial(kcal: 500, goal: nil)
    XCTAssertNil(d.target)
    XCTAssertNil(d.left)
    XCTAssertEqual(d.fraction, 500 / RecordParams.noGoalKcal, accuracy: 1e-9)
    XCTAssertEqual(RecordGlance.foodDial(kcal: nil, goal: 0).fraction, 0)
  }

  func testMacroScaleAndEnergySplit() {
    XCTAssertEqual(RecordGlance.macroScale(32, goal: 150, key: "protein"), 180, accuracy: 1e-9)
    XCTAssertEqual(RecordGlance.macroScale(10, goal: nil, key: "fat"), RecordParams.noGoalMacro["fat"] ?? 0, accuracy: 1e-9)
    let split = RecordGlance.energySplit(Macros(kcal: 380, protein: 20, carbs: 40, fat: 10))
    XCTAssertEqual(split.count, 3)
    XCTAssertEqual(split[0], 80.0 / 330.0, accuracy: 1e-9)
    XCTAssertEqual(split[2], 90.0 / 330.0, accuracy: 1e-9)
    XCTAssertTrue(RecordGlance.energySplit(Macros()).isEmpty)
  }
}
