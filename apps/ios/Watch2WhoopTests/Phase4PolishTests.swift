import XCTest
@testable import Watch2Whoop

/// 2.0.1 phase 4 (polish): the Jarvis key on phones with a home button, the short-screen rhythm, the backfill's start
/// time, the library's placeholder and the weekly volume's group names.
final class Phase4PolishTests: XCTestCase {
  // MARK: - the Jarvis key and its band

  func testTheKeyClearsTheFloatingBarOnAPhoneWithAHomeButton() throws {
    guard #available(iOS 26.0, *) else { throw XCTSkip("the floating tab bar is iOS 26") }
    // iPhone SE (no home indicator): the bar reaches 34 pt further into the safe area than on a Face ID phone
    XCTAssertEqual(JarvisLayer.barHeight(bottomInset: 34), 62)
    XCTAssertEqual(JarvisLayer.barHeight(bottomInset: 0), 96)
    // measured from the screen's bottom edge the key sits at the same height on both
    let pro = JarvisLayer.keyCenter(in: CGSize(width: 402, height: 874 - 62 - 34), bottomInset: 34)
    let se = JarvisLayer.keyCenter(in: CGSize(width: 375, height: 667 - 20), bottomInset: 0)
    XCTAssertEqual(874 - (pro.y + 62), 667 - (se.y + 20), accuracy: 0.5)
  }

  func testTheBandCoversTheKey() {
    XCTAssertGreaterThanOrEqual(JarvisLayer.keyClearance, JarvisLayer.keySize + 10)
  }

  // MARK: - short screens

  func testGapsShrinkOnlyOnShortScreens() {
    XCTAssertEqual(Rhythm.gap(40, short: false), 40)
    XCTAssertEqual(Rhythm.gap(40, short: true), 14)
    XCTAssertEqual(Rhythm.gap(44, short: true), 15)
    // the first glance of Today on a 667 pt phone fits above the key band: header 44, dial, readings 59, faces 120
    let glance = 44 + Rhythm.gap(32, short: true) + 213 + Rhythm.gap(40, short: true) + 59 + Rhythm.gap(44, short: true) + 120
    let room: CGFloat = 667 - 20 - JarvisLayer.barHeight(bottomInset: 0) - JarvisLayer.keyClearance
    XCTAssertLessThanOrEqual(glance, room + 30)   // the band starts a little below the key's top
  }

  // MARK: - backfill start time (#445 F)

  func testAnEveningPickedOnAnotherDayIsNotSentAsAFutureTimeToday() {
    var c = DateComponents()
    c.year = 2026; c.month = 11; c.day = 12; c.hour = 9; c.minute = 30
    let cal = Calendar.current
    let now = cal.date(from: c)!
    let evening = cal.date(bySettingHour: 21, minute: 15, second: 0, of: now)!
    XCTAssertEqual(CockpitBackfillForm.startTime(evening, day: "2026-11-12", today: "2026-11-12", now: now), "09:30")
    XCTAssertEqual(CockpitBackfillForm.startTime(evening, day: "2026-11-11", today: "2026-11-12", now: now), "21:15")
    let morning = cal.date(bySettingHour: 7, minute: 5, second: 0, of: now)!
    XCTAssertEqual(CockpitBackfillForm.startTime(morning, day: "2026-11-12", today: "2026-11-12", now: now), "07:05")
  }

  // MARK: - library placeholder and group names

  func testAnExerciseWithoutAMappedMuscleGetsThePlaceholder() {
    XCTAssertFalse(PlanExerciseThumb.hasMap(primary: [], secondary: []))
    XCTAssertFalse(PlanExerciseThumb.hasMap(primary: ["no-such-muscle"], secondary: []))
    XCTAssertTrue(PlanExerciseThumb.hasMap(primary: ["quadriceps"], secondary: []))
  }

  func testWeeklyVolumeNamesTheGroupsByKey() {
    XCTAssertEqual(RecordGlance.groupName("back"), RecordGlance.groupTitle("back"))
    XCTAssertEqual(RecordGlance.groupName("shoulders"), RecordGlance.groupTitle("shoulders"))
    XCTAssertNil(RecordGlance.groupName("neck"))
    XCTAssertNil(RecordGlance.groupName(nil))
  }
}
