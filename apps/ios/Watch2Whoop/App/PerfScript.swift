import SwiftUI

#if DEBUG
/// Debug builds only: steps through the app without touching the screen and logs, for every step, how long the main
/// thread stayed busy with it (`JarvisPerf`: `first` = from the step to the first frame after it, `worst` = the longest
/// frame of the next second, `dropped` = frames lost in it). The numbers before / after a performance change come from
/// the same steps (apps/ios/README.md「模拟器测流畅度」). `SIMCTL_CHILD_W2W_PERF_SCRIPT=`
/// - `tabs`: every tab twice (cold, then warm), then on Today a sheet and the sleep page open and close, then the Jarvis
///   chat (when the AI is in the app);
/// - `W2W_PERF_SCRIPT_DELAY=<s>`: wait that long after the tabs appear first (default 8, for the first sync).
@MainActor
enum PerfScript {
  /// What the Today tab lets the script do (registered by TodayView while it is on screen).
  struct TodayHooks {
    let sheet: (TodaySheet?) -> Void
    let push: (SleepRoute?) -> Void
  }

  static var today: TodayHooks?
  static var mode: String? { ProcessInfo.processInfo.environment["W2W_PERF_SCRIPT"] }
  private static var ran = false

  static func run(selection: Binding<AppTab>, tabs: [AppTab]) async {
    guard let mode, !ran else { return }
    ran = true
    let env = ProcessInfo.processInfo.environment
    func sec(_ s: Double) async { try? await Task.sleep(nanoseconds: UInt64(s * 1_000_000_000)) }
    func step(_ name: String, wait: Double = 1.6, _ f: () -> Void) async {
      JarvisPerf.shared.measure(name, seconds: 1.0)
      f()
      await sec(wait)
    }
    await sec(Double(env["W2W_PERF_SCRIPT_DELAY"] ?? "") ?? 8)
    guard mode == "tabs" else { return }
    for round in ["cold", "warm"] {
      for t in tabs.dropFirst() + [AppTab.today] {
        await step("tab-\(t.rawValue)-\(round)") { selection.wrappedValue = t }
      }
    }
    if let h = today {
      await step("today-sheet-open") { h.sheet(.recovery) }
      await step("today-sheet-close") { h.sheet(nil) }
      await step("today-page-push") { h.push(SleepRoute(date: nil)) }
      await step("today-page-pop") { h.push(nil) }
    }
    if let r = JarvisRouter.current, r.ai.inApp {
      await step("jarvis-chat-open", wait: 2) { r.present(.chat) }
      await step("jarvis-chat-close") { r.sheet = nil }
    }
    let all = JarvisPerf.shared.samples.filter { !$0.name.hasPrefix("jarvis-open") && !$0.name.hasPrefix("jarvis-close") }
    let first = all.map(\.firstMs), worst = all.map(\.worstMs)
    print(String(format: "W2W_PERF script steps=%d first_avg=%.1fms first_max=%.1fms worst_max=%.1fms dropped=%d", all.count,
                 first.reduce(0, +) / Double(max(1, first.count)), first.max() ?? 0, worst.max() ?? 0,
                 all.map(\.dropped).reduce(0, +)))
  }
}
#endif
