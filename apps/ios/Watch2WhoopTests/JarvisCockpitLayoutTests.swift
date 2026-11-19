import XCTest
@testable import Watch2Whoop

/// 2.1.0 Jarvis cockpit, option B 「卫星」 (docs/design/2.0.1/jarvis-soften/README.md §2 B): the pure orbit layout, the
/// touch wedges and the labels. Made-up labels only.
@MainActor
final class JarvisCockpitLayoutTests: XCTestCase {
  private let orbit = CockpitOrbit.phone

  /// The README's numbers: 116 pt orbit, 48 pt keys, 36° apart, 23.7 pt of empty space (49 % of the key).
  func testOrbitMatchesTheDesign() {
    XCTAssertEqual(orbit.step, 36)
    XCTAssertEqual(orbit.emptyBetweenKeys, 23.7, accuracy: 0.05)
    XCTAssertEqual(orbit.emptyBetweenKeys / (2 * orbit.keyRadius), 0.49, accuracy: 0.01)
    // share of the orbit's arc covered by keys, between the outer key centres (README: 67 %)
    XCTAssertEqual(Double(2 * orbit.keyRadius / orbit.pitch), 0.67, accuracy: 0.01)
  }

  /// One rule for both rings: (orbit, key, gap, count) → angles, centred on 12 o'clock.
  func testAnglesTable() {
    let table: [(count: Int, angles: [Double])] = [
      (5, [-72, -36, 0, 36, 72]),   // first ring, 记一餐
      (3, [-36, 0, 36]),            // 记训练
      (4, [-54, -18, 18, 54]),      // the rule takes any count
      (1, [0]),
    ]
    for row in table {
      XCTAssertEqual(orbit.angles(count: row.count), row.angles, "\(row.count) keys")
    }
    XCTAssertEqual(CockpitItem.firstRing().map(\.angle), [-72, -36, 0, 36, 72])
    XCTAssertEqual(CockpitItem.trainRing().map(\.angle), [-36, 0, 36])
    let meals = [CockpitMeal(id: 1, name: "Sample A", kcal: 500), CockpitMeal(id: 2, name: "Sample B", kcal: 400)]
    XCTAssertEqual(CockpitItem.mealRing(meals).map(\.angle), [-72, -36, 0, 36, 72])
    // one quick meal: its slot stays where it is, the next one is simply empty
    XCTAssertEqual(CockpitItem.mealRing(Array(meals.prefix(1))).map(\.angle), [-72, -36, 0, 72])
  }

  /// Neighbouring keys never touch, and the gap follows the inputs.
  func testGapFollowsTheInputs() {
    let table: [(r: CGFloat, kr: CGFloat, gap: CGFloat, step: Double)] = [
      (116, 24, 23.7, 36), (116, 24, 10, 29), (140, 24, 23.7, 30), (116, 20, 23.7, 32)]
    for row in table {
      let o = CockpitOrbit(radius: row.r, keyRadius: row.kr, gap: row.gap, hitInner: 72, hitOuter: 164)
      XCTAssertEqual(o.step, row.step, "\(row)")
      let a = o.center(0, around: .zero), b = o.center(o.step, around: .zero)
      XCTAssertGreaterThan(hypot(a.x - b.x, a.y - b.y), 2 * row.kr, "keys apart: \(row)")
    }
  }

  /// The smallest phone (375 × 667) and the largest (440 × 956): the keys, their labels and the tick ring stay on screen
  /// with at least 16 pt to the edge, and the top of the drawing stays below the status bar.
  func testFitsTheSmallestAndLargestPhone() {
    let label = CGSize(width: orbit.labelWidth, height: 13)
    for (w, h) in [(CGFloat(375), CGFloat(667)), (402, 874), (440, 956)] {
      let c = CGPoint(x: w / 2, y: h - 123)
      for n in [5, 3] {
        let e = orbit.extent(count: n, around: c, label: label)
        XCTAssertGreaterThanOrEqual(e.minX, 16, "\(w) pt, \(n) keys")
        XCTAssertLessThanOrEqual(e.maxX, w - 16, "\(w) pt, \(n) keys")
        XCTAssertGreaterThan(e.minY, 60, "\(w) × \(h)")
        XCTAssertLessThan(e.maxY, h, "\(w) × \(h)")
      }
    }
  }

  /// The top key's label (under the key, towards the core) clears the second ring's way-back key.
  func testTopLabelClearsTheWayBack() {
    let g = CockpitGeometry.self
    let labelTop = g.orbitRadius - g.keyRadius - g.labelBelow - 6.5   // from the core, half a 13 pt line
    XCTAssertGreaterThanOrEqual(labelTop - (g.crumbDistance + g.crumbRadius), 6)
  }

  // MARK: - touch

  /// Every key answers in its whole 36° wedge (72…164 pt from the core), at least 44 pt wide; the way back is 44 pt.
  func testHitTargetsAreAtLeast44() {
    XCTAssertGreaterThanOrEqual(orbit.narrowestHit, 44)
    XCTAssertGreaterThanOrEqual(orbit.hitOuter - orbit.hitInner, 44)
    XCTAssertGreaterThanOrEqual(2 * CockpitGeometry.crumbHit, 44)
    XCTAssertGreaterThanOrEqual(2 * CockpitGeometry.cancelHit, 44)
  }

  func testWedgeHitTable() {
    let c = CGPoint(x: 201, y: 751)
    let five = orbit.angles(count: 5), three = orbit.angles(count: 3)
    let P = { (r: CGFloat, a: Double) in CockpitGeometry.point(r, a, around: c) }
    let table: [(p: CGPoint, angles: [Double], want: Int?, why: String)] = [
      (P(116, -72), five, 0, "centre of the left key"),
      (P(116, 72), five, 4, "centre of the right key"),
      (P(160, 20), five, 3, "outer corner of the wedge, past the drawn key"),
      (P(74, -20), five, 1, "inner corner of the wedge"),
      (P(140, 0), five, 2, "the label under nothing: above the top key, still its wedge"),
      (P(70, 0), five, nil, "inside the wedges: the core's side"),
      (P(166, 0), five, nil, "outside the ring"),
      (P(116, 95), five, nil, "past the last key"),
      (P(116, -72), three, nil, "a 记训练 ring has no key at −72°"),
      (P(116, 50), three, 2, "the 记训练 ring's right wedge"),
    ]
    for row in table {
      XCTAssertEqual(orbit.hit(row.p, center: c, angles: row.angles), row.want, row.why)
    }
  }

  /// Through the machine: a tap on the outer edge of a wedge (outside the drawn circle) selects the key instead of closing.
  func testMachineUsesTheWedge() {
    let c = CGPoint(x: 201, y: 751)
    var m = CockpitMachine()
    _ = m.tapCore()
    for _ in 0..<96 { _ = m.step(1.0 / 120) }
    XCTAssertEqual(m.tap(at: CockpitGeometry.point(158, 50, around: c), center: c), [])
    XCTAssertEqual(m.selected, 3, "拍照 (36°) by its wedge")
  }

  // MARK: - labels

  /// Every label of both rings, in both languages, fits between two keys without being cut.
  func testEveryKeyLabelFits() {
    let all = ["记一餐", "记训练", "说话", "拍照", "提问", "说", "更多", "开始", "补记", "计划",
               "Meal", "Workout", "Talk", "Photo", "Ask", "Say", "More", "Start", "Backfill", "Plan"]
    for s in all {
      XCTAssertLessThanOrEqual(CockpitLabel.width(s), orbit.labelWidth, s)
      XCTAssertEqual(CockpitLabel.fit(s), s)
    }
    // two neighbouring labels never meet: the widest label is narrower than the pitch
    XCTAssertLessThan(orbit.labelWidth, orbit.pitch)
  }

  /// A long quick-meal name is cut with "…" to the room between two keys.
  func testLongNamesAreCut() {
    for s in ["Grilled chicken rice bowl with greens", "西兰花鸡胸肉糙米饭配溏心蛋", "Overnight oats with berries"] {
      let t = CockpitLabel.fit(s)
      XCTAssertLessThanOrEqual(CockpitLabel.width(t), orbit.labelWidth, t)
      XCTAssertTrue(t.hasSuffix("…"), t)
    }
    let item = CockpitItem.mealRing([CockpitMeal(id: 9, name: "Grilled chicken rice bowl", kcal: 612.4)])[2]
    XCTAssertEqual(item.number, "612", "the kcal goes in the key")
    XCTAssertEqual(item.title, "Grilled chicken rice bowl", "VoiceOver keeps the whole name")
    XCTAssertTrue(item.label.hasSuffix("…"))
  }
}
