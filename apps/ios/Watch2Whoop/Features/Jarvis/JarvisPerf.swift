import os
import QuartzCore
import UIKit

/// Frame timing of the Jarvis animations (W2W_PERF=1, or the demo): a display link counts the frames of a window after
/// the dial opens / closes or a hold starts, and logs `W2W_PERF jarvis-open fps=… worst=…ms dropped=… first=…ms`. Off
/// otherwise. `first` = from the call to the first frame after it: how long the main thread was busy with what the step
/// started (PerfScript uses the same meter for tab switches, sheets and pages).
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
    /// From the start of the window to its first frame (0 when the window had no start time).
    var firstMs: Double = 0
    var fps: Double { seconds > 0 ? Double(frames) / seconds : 0 }

    var line: String {
      String(format: "W2W_PERF %@ fps=%.1f/%d worst=%.1fms dropped=%d frames=%d first=%.1fms", name, fps, maxFps, worstMs,
             dropped, frames, firstMs)
    }
  }

  private(set) var samples: [Sample] = []
  private var link: CADisplayLink?
  private var name = ""
  private var stamps: [CFTimeInterval] = []
  private var started: CFTimeInterval = 0
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
    started = CACurrentMediaTime()
    until = started + seconds
    let l = CADisplayLink(target: self, selector: #selector(tick(_:)))
    l.preferredFrameRateRange = CAFrameRateRange(minimum: 60, maximum: 120, preferred: 120)
    l.add(to: .main, forMode: .common)
    link = l
  }

  /// A duration (not frames): `W2W_PERF <name> ms=…` from `since` (CACurrentMediaTime) to now, e.g. 完成 → the text in the
  /// box (voice). Kept in `marks` for the demo's summary.
  func mark(_ name: String, since: CFTimeInterval) {
    guard enabled else { return }
    let ms = (CACurrentMediaTime() - since) * 1000
    marks.append((name, ms))
    let line = String(format: "W2W_PERF %@ ms=%.0f", name, ms)
    log.info("\(line, privacy: .public)")
    print(line)
  }

  private(set) var marks: [(String, Double)] = []

  @objc private func tick(_ l: CADisplayLink) {
    stamps.append(l.timestamp)
    if l.timestamp >= until { finish() }
  }

  private func finish() {
    guard let l = link else { return }
    l.invalidate()
    link = nil
    let s = Self.summarise(name, stamps, maxFps: UIScreen.main.maximumFramesPerSecond, start: started)
    samples.append(s)
    log.info("\(s.line, privacy: .public)")
    print(s.line)
  }

  /// Frames, worst gap and frames dropped (a gap over 1.5 frame periods counts the frames it skipped).
  /// `start`: when the step began; the gap to the first frame counts as a gap of the window too.
  nonisolated static func summarise(_ name: String, _ t: [CFTimeInterval], maxFps: Int, start: CFTimeInterval? = nil) -> Sample {
    let first = start.flatMap { s in t.first.map { max(0, $0 - s) * 1000 } } ?? 0
    guard t.count > 1 else {
      return Sample(name: name, frames: t.count, seconds: 0, worstMs: first, dropped: 0, maxFps: maxFps, firstMs: first)
    }
    let period = 1.0 / Double(max(1, maxFps))
    var worst = 0.0, dropped = 0
    for i in 1..<t.count {
      let gap = t[i] - t[i - 1]
      worst = max(worst, gap)
      if gap > period * 1.5 { dropped += Int((gap / period).rounded()) - 1 }
    }
    return Sample(name: name, frames: t.count - 1, seconds: t.last! - t.first!, worstMs: max(worst * 1000, first),
                  dropped: dropped, maxFps: maxFps, firstMs: first)
  }
}

#if DEBUG
/// Debug builds only: drives Jarvis without touching the screen, for frame timing, screenshots and recordings
/// (simulator, synthetic data). `SIMCTL_CHILD_W2W_JARVIS_DEMO=` …
/// - `dial`: open / close the cockpit five times, then print the frame summary;
/// - `script`: nothing but the scripted recogniser (UI tests drive the key with real touches);
/// - `open`: open the cockpit and leave it open; `chat`: open the chat and ask a quick question;
/// - the cockpit's flows (2.0.1): `cockpit-meal` / `cockpit-train` (second rings), `cockpit-back` (second ring, then back),
///   `cockpit-interrupt` (tap the core again while the panels fan out), `cockpit-qa` / `cockpit-mealvoice` /
///   `cockpit-set` (talk through 说话 / 记一餐 → 说 / 记训练 → 说一组 with scripted words, then the reply card),
///   `cockpit-template` (a quick meal logged at once), `cockpit-undo` (the same, then 撤销);
/// - `posture`: the chat with the posture sheet open; `posture-filled`: with three made-up photos in it;
///   `posture-send`: send three made-up photos and the prefilled question (flat colours, no person in them);
/// - `voice`: talk three times through the chat's own microphone (完成), logging `voice-chat-done-to-text` (the scripted
///   recogniser behaves like the phone's: no final result, so `stop` waits as on a phone, and a Chinese WAV goes to
///   /transcribe; the fake server's `W2W_FAKE_ASR_DELAY=<s>` stands in for the real recogniser's time).
enum JarvisDemo {
  static var mode: String? { ProcessInfo.processInfo.environment["W2W_JARVIS_DEMO"] }
  static var scriptedVoice: Bool { mode != nil }
  private static var ran = false
  /// The chat's own microphone, while the chat is open (start / 完成), for the `voice` demo.
  static var chatMic: (start: () -> Void, done: () -> Void)?
  /// The words the scripted recogniser says in a cockpit demo (two characters at a time, then a pause).
  static var cockpitPhrase: String?

  static func postureFlow() -> PostureFlow {
    var f = PostureFlow()
    for (a, shade) in zip(PostureFlow.Angle.allCases, [0.35, 0.5, 0.65] as [CGFloat]) {
      let img = UIGraphicsImageRenderer(size: CGSize(width: 90, height: 120)).image { c in
        UIColor(white: shade, alpha: 1).setFill()
        c.fill(CGRect(x: 0, y: 0, width: 90, height: 120))
      }
      f.photos[a] = JarvisMedia.photo(img)
    }
    return f
  }

  @MainActor
  static func run(router: JarvisRouter, cockpit: CockpitController) {
    guard let mode, !ran else { return }
    ran = true
    let sec: (Double) async -> Void = { try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) }
    Task { @MainActor in
      await sec(Double(ProcessInfo.processInfo.environment["W2W_JARVIS_DEMO_DELAY"] ?? "") ?? 2.5)
      switch mode {
      case "script":   // only the scripted recogniser (UI tests drive the key with real touches)
        break
      case "open":
        cockpit.tapCore()
      case "cockpit-meal", "cockpit-train", "cockpit-back":
        cockpit.tapCore()
        await sec(0.9)
        cockpit.activate(mode == "cockpit-train" ? .train : .meal)
        if mode == "cockpit-back" {
          await sec(2.5)
          cockpit.back()
        }
      case "cockpit-interrupt":
        cockpit.tapCore()
        await sec(0.09)
        cockpit.tapCore()
      case "cockpit-qa":
        cockpitPhrase = tr("今天适合练腿吗", "Should I train legs today")
        cockpit.tapCore()
        await sec(0.9)
        cockpit.activate(.talk)
      case "cockpit-mealvoice", "cockpit-set":
        let meal = mode == "cockpit-mealvoice"
        cockpitPhrase = meal ? tr("中午吃了鸡胸饭", "Chicken rice for lunch") : tr("卧推六十公斤八个", "Bench press sixty kilos eight reps")
        cockpit.tapCore()
        await sec(0.9)
        cockpit.activate(meal ? .meal : .train)
        await sec(1.2)
        cockpit.activate(meal ? .mealTalk : .trainTalk)
      case "cockpit-template", "cockpit-undo":
        cockpit.tapCore()
        await sec(0.9)
        cockpit.activate(.meal)
        await sec(1.2)
        if let id = cockpit.machine.meals.first?.id { cockpit.activate(.template(id)) }
        if mode == "cockpit-undo" {
          await sec(3)
          cockpit.undoReply()
        }
      case "chat":
        router.present(.chat)
        await sec(1)
        router.chat?.send(JarvisRouter.quick(for: router.page).first?.text ?? "Hi")
      case "cards":   // every card type, from apps/ios/scripts/fake_jarvis_server.py (word "cards")
        router.present(.chat)
        await sec(1)
        router.chat?.send("cards")
        // W2W_JARVIS_OPEN=<page>: then press that page's button, to see where it lands
        if let p = ProcessInfo.processInfo.environment["W2W_JARVIS_OPEN"] {
          await sec(4)
          let s = { (v: String) in JSONValue.string(v) }
          router.open(page: p, card: .object(["test_key": s("uric_acid"), "test_name": s("Sample"), "date": s("2026-11-02"), "id": s("1"), "title": s("HRV")]))
        }
      case "posture", "posture-filled":
        router.present(.chat)
      case "posture-send":
        router.present(.chat)
        await sec(1)
        router.chat?.send(PostureFlow.question, attachments: postureFlow().attachments, purpose: PostureFlow.purpose)
      case "voice":
        router.present(.chat)
        await sec(1.5)
        for _ in 0..<3 {
          chatMic?.start()
          await sec(2)
          chatMic?.done()
          await sec(7)
          router.chat?.draft = ""
          await sec(0.5)
        }
        let ms = JarvisPerf.shared.marks.filter { $0.0 == "voice-chat-done-to-text" }.map(\.1)
        if !ms.isEmpty {
          print(String(format: "W2W_PERF summary voice-chat-done-to-text n=%d avg=%.0fms max=%.0fms", ms.count, ms.reduce(0, +) / Double(ms.count), ms.max() ?? 0))
        }
      default:   // dial: open / close the cockpit
        for _ in 0..<5 {
          cockpit.tapCore()
          await sec(1.2)
          cockpit.tapCore()
          await sec(0.9)
        }
        let all = JarvisPerf.shared.samples
        for name in ["cockpit-open", "cockpit-listen"] {
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
#endif
