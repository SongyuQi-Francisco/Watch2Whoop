import Foundation

/// The rest countdown between sets (mockup T6). Pure value: the screen asks it for the time left at `now`.
struct RestTimer: Equatable {
  var start: Date
  /// Planned rest in seconds; the crown changes it in `WatchParams.restStepSec` steps.
  var total: Int

  init(start: Date, total: Int) {
    self.start = start
    self.total = RestTimer.clamp(total)
  }

  static func clamp(_ s: Int) -> Int { min(WatchParams.maxRestSec, max(WatchParams.minRestSec, s)) }

  /// Seconds left, never below 0.
  func remaining(at now: Date) -> TimeInterval { max(0, Double(total) - now.timeIntervalSince(start)) }

  /// 0…1 of the ring still to run.
  func fraction(at now: Date) -> Double { total > 0 ? remaining(at: now) / Double(total) : 0 }

  func isOver(at now: Date) -> Bool { remaining(at: now) <= 0 }

  /// Seconds rested so far (what the phone keeps as the actual rest).
  func elapsed(at now: Date) -> Int { max(0, Int(now.timeIntervalSince(start).rounded())) }

  /// Crown detents: ±15 s each, within the limits.
  mutating func adjust(steps: Int) {
    total = RestTimer.clamp(total + steps * WatchParams.restStepSec)
  }

  /// 「1:12」.
  static func clock(_ seconds: TimeInterval) -> String {
    let s = Int(seconds.rounded(.up))
    return "\(s / 60):" + String(format: "%02d", s % 60)
  }
}
