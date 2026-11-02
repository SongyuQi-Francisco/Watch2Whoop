import Foundation
import os
import QuartzCore
import UIKit

/// Timing log for the Today tab's performance targets (#340 N3): cold start → the three rings drawn from local data
/// (< 300 ms), and dropped frames while scrolling (120 Hz). Lines go to the unified log (subsystem `org.watch2whoop`,
/// category `perf`) and stdout with the prefix `W2W_PERF`, and as signposts for Instruments:
///
///     xcrun simctl launch --console-pty <device> <bundle id> | grep W2W_PERF
///
/// The frame meter only runs when the launch environment has `W2W_PERF=1` (`SIMCTL_CHILD_W2W_PERF=1`); with
/// `W2W_PERF_SCROLL=1` the Today tab also scrolls itself down and up a few times so the meter has something to count.
final class PerfProbe {
  static let shared = PerfProbe()
  static let enabled = ProcessInfo.processInfo.environment["W2W_PERF"] == "1"
  static let autoScroll = ProcessInfo.processInfo.environment["W2W_PERF_SCROLL"] == "1"

  private let log = Logger(subsystem: "org.watch2whoop", category: "perf")
  private let signposter = OSSignposter(subsystem: "org.watch2whoop", category: "perf")
  private var dataAt: Double?
  private var ringsDone = false

  /// Seconds since the process started (kernel start time, so it includes dyld and the app's own start-up).
  static func sinceLaunch() -> Double {
    var info = kinfo_proc()
    var size = MemoryLayout<kinfo_proc>.stride
    var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
    guard sysctl(&mib, 4, &info, &size, nil, 0) == 0 else { return 0 }
    let t = info.kp_proc.p_starttime
    let start = Double(t.tv_sec) + Double(t.tv_usec) / 1_000_000
    return Date().timeIntervalSince1970 - start
  }

  private func emit(_ line: String) {
    log.notice("\(line, privacy: .public)")
    print("W2W_PERF \(line)")
  }

  /// The today document is in the model (read from the local database).
  func firstData() {
    guard dataAt == nil else { return }
    dataAt = Self.sinceLaunch()
    signposter.emitEvent("today data")
    emit(String(format: "today_data_ms=%.0f", dataAt! * 1000))
  }

  /// The rings row with data has been laid out on screen (called from its onAppear on the next run-loop turn).
  func ringsShown() {
    guard !ringsDone, dataAt != nil else { return }
    ringsDone = true
    DispatchQueue.main.async { [self] in
      let t = Self.sinceLaunch()
      signposter.emitEvent("today rings")
      emit(String(format: "cold_start_to_rings_ms=%.0f", t * 1000))
    }
  }

  // MARK: - frame meter

  private var link: CADisplayLink?
  private var last: CFTimeInterval = 0
  private var frames = 0, hitches = 0
  private var hitchTime: CFTimeInterval = 0, total: CFTimeInterval = 0
  private var worst: CFTimeInterval = 0

  func startFrames() {
    guard Self.enabled, link == nil else { return }
    let l = CADisplayLink(target: self, selector: #selector(tick(_:)))
    l.preferredFrameRateRange = CAFrameRateRange(minimum: 80, maximum: 120, preferred: 120)
    l.add(to: .main, forMode: .common)
    link = l
    last = 0
    frames = 0; hitches = 0; hitchTime = 0; total = 0; worst = 0
  }

  func stopFrames(label: String) {
    guard let l = link else { return }
    l.invalidate()
    link = nil
    let fps = total > 0 ? Double(frames) / total : 0
    let ratio = total > 0 ? hitchTime / total * 1000 : 0
    emit(String(format: "%@ frames=%d seconds=%.2f fps=%.1f max_fps=%d hitches=%d hitch_ms_per_s=%.1f worst_frame_ms=%.1f",
                label, frames, total, fps, UIScreen.main.maximumFramesPerSecond, hitches, ratio, worst * 1000))
  }

  @objc private func tick(_ l: CADisplayLink) {
    defer { last = l.timestamp }
    guard last > 0 else { return }
    let dt = l.timestamp - last, expected = l.targetTimestamp - l.timestamp
    frames += 1
    total += dt
    worst = max(worst, dt)
    if expected > 0, dt > expected * 1.5 {
      hitches += 1
      hitchTime += dt - expected
    }
  }
}
