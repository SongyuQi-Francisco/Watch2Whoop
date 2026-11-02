import os
import QuartzCore
import UIKit

/// Frame timing of the Jarvis animations (W2W_PERF=1, or the demo): a display link counts the frames of a window after
/// the dial opens / closes or a hold starts, and logs `W2W_PERF jarvis-open fps=… worst=…ms dropped=…`. Off otherwise.
@MainActor
final class JarvisPerf: NSObject {
  static let shared = JarvisPerf()

  struct Sample: Equatable {
    let name: String
    let frames: Int
    let seconds: Double
    let worstMs: Double
    let dropped: Int
    let maxFps: Int
    var fps: Double { seconds > 0 ? Double(frames) / seconds : 0 }

    var line: String {
      String(format: "W2W_PERF %@ fps=%.1f/%d worst=%.1fms dropped=%d frames=%d", name, fps, maxFps, worstMs, dropped, frames)
    }
  }

  private(set) var samples: [Sample] = []
  private var link: CADisplayLink?
  private var name = ""
  private var stamps: [CFTimeInterval] = []
  private var until: CFTimeInterval = 0
  private let log = Logger(subsystem: "org.watch2whoop.native", category: "perf")

  var enabled: Bool {
    let env = ProcessInfo.processInfo.environment
    return env["W2W_PERF"] != nil || env["W2W_JARVIS_DEMO"] != nil
  }

  func measure(_ name: String, seconds: Double) {
    guard enabled else { return }
    finish()
    self.name = name
    stamps = []
    until = CACurrentMediaTime() + seconds
    let l = CADisplayLink(target: self, selector: #selector(tick(_:)))
    l.preferredFrameRateRange = CAFrameRateRange(minimum: 60, maximum: 120, preferred: 120)
    l.add(to: .main, forMode: .common)
    link = l
  }

  @objc private func tick(_ l: CADisplayLink) {
    stamps.append(l.timestamp)
    if l.timestamp >= until { finish() }
  }

  private func finish() {
    guard let l = link else { return }
    l.invalidate()
    link = nil
    let s = Self.summarise(name, stamps, maxFps: UIScreen.main.maximumFramesPerSecond)
    samples.append(s)
    log.info("\(s.line, privacy: .public)")
    print(s.line)
  }

  /// Frames, worst gap and frames dropped (a gap over 1.5 frame periods counts the frames it skipped).
  nonisolated static func summarise(_ name: String, _ t: [CFTimeInterval], maxFps: Int) -> Sample {
    guard t.count > 1 else { return Sample(name: name, frames: t.count, seconds: 0, worstMs: 0, dropped: 0, maxFps: maxFps) }
    let period = 1.0 / Double(max(1, maxFps))
    var worst = 0.0, dropped = 0
    for i in 1..<t.count {
      let gap = t[i] - t[i - 1]
      worst = max(worst, gap)
      if gap > period * 1.5 { dropped += Int((gap / period).rounded()) - 1 }
    }
    return Sample(name: name, frames: t.count - 1, seconds: t.last! - t.first!, worstMs: worst * 1000, dropped: dropped, maxFps: maxFps)
  }
}

#if DEBUG
/// Debug builds only: drives Jarvis without touching the screen, for frame timing, screenshots and recordings
/// (simulator, synthetic data). `SIMCTL_CHILD_W2W_JARVIS_DEMO=` …
/// - `dial`: open / close the dial five times, then hold to talk three ways (slide to cancel, slide to lock + done,
///   release), then print the frame summary;
/// - `script`: nothing but the scripted recogniser (UI tests drive the key with real touches);
/// - `open`: open the dial and leave it open; `hold`: start holding and stay; `chat`: open the chat and ask a quick question.
/// `W2W_JARVIS_MATERIAL=1` draws the pre-iOS 26 material keys on iOS 26 too (to compare).
enum JarvisDemo {
  struct Hooks {
    let center: () -> CGPoint
    let press: (CGPoint) -> Void
    let move: (CGPoint) -> Void
    let release: (CGPoint) -> Void
  }

  static var mode: String? { ProcessInfo.processInfo.environment["W2W_JARVIS_DEMO"] }
  static var scriptedVoice: Bool { mode != nil }
  static var forceMaterial: Bool { ProcessInfo.processInfo.environment["W2W_JARVIS_MATERIAL"] != nil }
  private static var ran = false

  @MainActor
  static func run(router: JarvisRouter, layer h: Hooks) {
    guard let mode, !ran else { return }
    ran = true
    let sec: (Double) async -> Void = { try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) }
    Task { @MainActor in
      await sec(Double(ProcessInfo.processInfo.environment["W2W_JARVIS_DEMO_DELAY"] ?? "") ?? 2.5)
      switch mode {
      case "script":   // only the scripted recogniser (UI tests drive the key with real touches)
        break
      case "open":
        router.menuOpen = true
      case "chat":
        router.present(.chat)
        await sec(1)
        router.chat?.send(JarvisRouter.quick(for: router.page).first?.text ?? "Hi")
      case "hold":
        h.press(h.center())
        await sec(1.5)
        h.move(CGPoint(x: h.center().x - 30, y: h.center().y - 70))
      default:
        for _ in 0..<5 {
          router.menuOpen = true
          await sec(1.2)
          router.menuOpen = false
          await sec(0.9)
        }
        for (dx, tapAfter) in [(-JarvisLayer.targetDX, false), (JarvisLayer.targetDX, true), (0, false)] {
          let c = h.center()
          h.press(c)
          await sec(JarvisLayer.holdSeconds + 0.15)
          var p = c
          if dx != 0 {
            for k in 1...24 {
              let f = CGFloat(k) / 24
              p = CGPoint(x: c.x + dx * f, y: c.y - JarvisLayer.targetDY * f)
              h.move(p)
              await sec(1.0 / 60)
            }
          }
          await sec(1.2)
          h.release(p)
          if tapAfter {
            await sec(1.5)
            h.press(c)
            await sec(0.05)
            h.release(c)
          }
          await sec(2.5)
          router.sheet = nil
          await sec(0.8)
        }
        let all = JarvisPerf.shared.samples
        for name in ["jarvis-open", "jarvis-close", "jarvis-hold"] {
          let s = all.filter { $0.name == name }
          guard !s.isEmpty else { continue }
          let fps = s.map(\.fps).reduce(0, +) / Double(s.count)
          let worst = s.map(\.worstMs).max() ?? 0
          let dropped = s.map(\.dropped).reduce(0, +)
          print(String(format: "W2W_PERF summary %@ n=%d avg_fps=%.1f/%d worst=%.1fms dropped=%d", name, s.count, fps, s[0].maxFps, worst, dropped))
        }
      }
    }
  }
}
#else
enum JarvisDemo {
  static let forceMaterial = false
}
#endif
