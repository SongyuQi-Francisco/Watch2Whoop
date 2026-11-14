import GRDB
import SwiftUI
import XCTest
@testable import Watch2Whoop

// Flag `ui_ai_first` (#279) is retired in 2.0.1 (one Today, docs/design/2.0.1 direction.md 4.2): with it on or off, and with
// its old document still cached, the Today page is the same and never asks for that document. Every value here is made up.

private func json(_ s: String) -> [String: Any] {
  (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String: Any] ?? [:]
}

private let homeDoc = """
  {"focus": ["sleep"], "hidden": ["journal"], "focus_options": ["fat", "muscle", "sleep", "recover", "markers"], "max_modules": 6,
   "modules": [{"key": "muscle_load", "reason": "pinned"}, {"key": "sleep_goal", "reason": "focus"}, {"key": "sleep", "reason": "focus"},
               {"key": "health_monitor", "reason": "finding"}, {"key": "training", "reason": "used"}, {"key": "strain_coach", "reason": null}],
   "pace": {"day": 0, "limit": 3, "veteran": false},
   "overview": {"findings": [{"key": "hrv_low", "module": "health_monitor", "area": "A", "value": "−20%", "label": "L", "tone": "mid", "weight": 2},
                             {"key": "recovery_up", "module": null, "area": "B", "value": "66", "label": "M", "tone": "good", "weight": 1}],
                "stats": {"days": 70, "since": "2026-01-01", "nights": 60, "workouts": 12}}}
  """

/// A today document with data in every block the page reads.
private func todayInputs(past: Bool = false, recovery: Double? = 79, sleep: Double? = 400, need: Double? = 470) -> TodayInputs {
  var t: [String: Any] = ["hrv_night_ms": 48, "rhr_night": 52]
  if let recovery { t["recovery"] = recovery }
  if let sleep { t["sleep_actual_min"] = sleep }
  if let need { t["sleep_need_min"] = need }
  return TodayInputs(
    today: ["date": "2026-01-02", "scores": ["today": t, "yesterday": ["date": "2026-01-01", "strain": 9.5], "recovery_zone": "green"],
            "reminders": [["id": 1, "title": "First", "level": "alert", "status": "new"],
                          ["id": 2, "title": "Second", "level": "info", "status": "new"],
                          ["id": 3, "title": "Third", "level": "info", "status": "seen"],
                          ["id": 4, "title": "Fourth", "level": "info", "status": "seen"]]],
    activity: json(#"{"date": "2026-01-02", "daily": {"steps": 12345, "active_kcal": 512, "exercise_min": 31}}"#),
    coach: json(#"{"target_low": 10, "target_high": 14, "current_strain": 6.5, "status": null, "remaining_to_low": 3.5}"#),
    monitor: json(#"{"metrics": [{"key": "rhr", "value": 52, "status": "normal"}]}"#),
    userToday: past ? "2026-01-09" : "2026-01-02", circlesOn: true)
}

@MainActor
final class TodayHomeFlagOffTests: XCTestCase {
  private func session(flags: Set<String>) throws -> AccountSession {
    let cfg = AccountConfig(server: "https://body.example.com", userId: 7, displayName: "Test")
    return AccountSession(config: cfg, api: PathStub.client(), db: try AppDatabase.inMemory(), flags: flags)
  }

  override func setUp() {
    super.setUp()
    PathStub.replies = [:]
    PathStub.paths = []
  }

  func testTheClassicStoreNeverAsksForTheHomeDocument() async throws {
    let s = try session(flags: [])
    let store = TodayStore(session: s)
    await store.refresh()
    XCTAssertFalse(PathStub.paths.contains { $0.contains("/ui/") }, "\(PathStub.paths)")
    for day in ["2026-01-02"] {
      for today in [true, false] {
        XCTAssertFalse(TodayDocs.fetches(day: day, isToday: today, circles: true).contains { $0.request.path.contains("/ui/") })
      }
    }
  }

  func testACachedHomeDocumentChangesNothingForTheClassicPage() throws {
    let s = try session(flags: [])
    try s.db.saveDoc(DocKey.todayCurrent, json: try JSONSerialization.data(withJSONObject: todayInputs().today ?? [:]))
    let before = TodayStore(session: s).model
    let beforeRead = try s.db.writer.read { db in try TodaySnapshot.read(db, date: nil) }
    try s.db.saveDoc(TodayHome.docKey, json: Data(homeDoc.utf8))
    let after = TodayStore(session: s).model
    let afterRead = try s.db.writer.read { db in try TodaySnapshot.read(db, date: nil) }
    XCTAssertEqual(before, after)
    XCTAssertEqual(beforeRead, afterRead)
    XCTAssertNil(afterRead.docs[TodayHome.docKey])
  }

  /// 2.0.1 has one Today (docs/design/2.0.1 direction.md 4.2): the flag no longer changes the first glance, on or off it is
  /// pixel by pixel the same page. Only the top of the page.
  func testTheFlagDoesNotChangeTheFirstGlance() throws {
    let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
    guard let scene else { throw XCTSkip("no window scene in this test host") }
    let s = try session(flags: [])
    try s.db.saveDoc(DocKey.todayCurrent, json: try JSONSerialization.data(withJSONObject: todayInputs().today ?? [:]))
    try s.db.saveDoc(TodayHome.docKey, json: Data(homeDoc.utf8))
    let host = UIHostingController(rootView: TodayView(session: s).environmentObject(s).environmentObject(AppModel()))
    let window = UIWindow(windowScene: scene)
    window.frame = CGRect(x: 0, y: 0, width: 390, height: 844)
    window.rootViewController = host
    window.isHidden = false
    defer {
      window.isHidden = true
      window.rootViewController = nil
    }

    let off = settled(host.view)
    XCTAssertNotNil(off)
    s.flags = [TodayHome.flag]
    XCTAssertEqual(settled(host.view), off, "one Today: the flag must not change the first glance")
    s.flags = []
    XCTAssertEqual(settled(host.view), off, "with the flag off again the page must be what it was")
  }

  /// The picture once it has stopped changing (the rings animate in).
  private func settled(_ view: UIView) -> Data? {
    func shot() -> Data? {
      UIGraphicsImageRenderer(size: CGSize(width: 390, height: 520)).image { _ in
        view.drawHierarchy(in: view.bounds, afterScreenUpdates: true)
      }.pngData()
    }
    var last = shot(), same = 0
    for _ in 0..<60 where same < 4 {
      RunLoop.main.run(until: Date().addingTimeInterval(0.25))
      let now = shot()
      same = now == last ? same + 1 : 0
      last = now
    }
    return last
  }
}

