import SwiftUI

// The voice screens of docs/design/watch (watch.js `SC.listen`, `SC.confirm`, `SC.think`, `SC.answer`, `SC.more`,
// `SC.undo`), drawn in the mockups' coordinates where they are one drawing (M1, M2, J2) and as a crown-scrolled column
// where the answer may be longer than the screen (J3 / J4). Ice and the live core belong to Jarvis only (plan 3).

// MARK: - M1 / J1 listening (over home, under the finger)

struct ListeningView: View {
  @EnvironmentObject var voice: WatchVoiceController
  var c: CanvasMetrics
  /// The finger slid far enough up: releasing now cancels.
  var cancelling: Bool

  var body: some View {
    let s = c.s
    let cx = c.size.width / 2
    let cy = 116 * c.sy
    let T = voice.now.timeIntervalSince1970 * 1000
    let en = voice.level
    ZStack(alignment: .topLeading) {
      Color.black.frame(width: c.size.width, height: c.size.height)
      // the seconds in the sync mark's place (mockup: mic + 「0:02」)
      HStack(spacing: 3 * s) {
        Image(systemName: "mic.fill").font(.system(size: 11 * s, weight: .semibold)).foregroundStyle(WJarvis.ice)
        Text(String(format: "0:%02d", min(59, Int(voice.seconds)))).font(.system(size: 12.5 * s, weight: .medium).monospacedDigit())
          .foregroundStyle(W.text2).fixedSize()
      }
      .place(leading: CGFloat(c.clock.side), centerY: CGFloat(c.clock.centreY))
      Image(systemName: "chevron.up").font(.system(size: 12 * s, weight: .semibold)).foregroundStyle(cancelling ? W.text : W.text3)
        .place(centerX: cx, centerY: 40 * c.sy)
      VoiceWaves(level: cancelling ? 0 : en, t: T, scale: s)
        .frame(width: 130 * s, height: 130 * s)
        .place(centerX: cx, centerY: cy)
      JarvisCoreMark(size: 64 * s, dim: cancelling, live: !cancelling, ra: T * 0.06, rb: -T * 0.1)
        .place(centerX: cx, centerY: cy)
      Text(cancelling ? tr("松手取消", "Release to cancel") : tr("松手发送", "Release to send"))
        .font(.system(size: 12.5 * s, weight: .medium)).foregroundStyle(cancelling ? W.text : W.text2).fixedSize()
        .place(centerX: cx, centerY: 202 * c.sy)
      Text(tr("上滑取消", "Slide up to cancel")).font(.system(size: 11 * s, weight: .medium)).foregroundStyle(W.text3).fixedSize()
        .opacity(cancelling ? 0 : 1)
        .place(centerX: cx, centerY: 220 * c.sy)
    }
  }
}

/// The two rings that move with the voice (watch.js `wave`): radius 45 and 53 (of a 64 pt core), amplitude from the
/// loudness, a slow phase.
struct VoiceWaves: View {
  var level: Double
  /// Milliseconds (the phase).
  var t: Double
  var scale: CGFloat

  var body: some View {
    Canvas { ctx, size in
      let c = CGPoint(x: size.width / 2, y: size.height / 2)
      func wave(_ r0: Double, _ amp: Double, _ ph: Double) -> Path {
        var p = Path()
        for i in 0...120 {
          let th = Double(i) * 3 * .pi / 180
          let r = r0 + amp * (sin(3 * th + ph) * sin(ph * 0.7) + 0.6 * sin(5 * th - ph * 1.4))
          let q = CGPoint(x: c.x + CGFloat(r * sin(th)), y: c.y - CGFloat(r * cos(th)))
          if i == 0 { p.move(to: q) } else { p.addLine(to: q) }
        }
        p.closeSubpath()
        return p
      }
      let k = Double(scale)
      var g = ctx
      g.addFilter(.shadow(color: WJarvis.ice.opacity(0.45), radius: 5))
      g.stroke(wave(45 * k, (1.2 + 5 * level) * k, t / 260), with: .color(WJarvis.ice.opacity(0.95)), lineWidth: 1.6)
      g.stroke(wave(53 * k, (0.8 + 3.6 * level) * k, -t / 340 + 2), with: .color(WJarvis.ice.opacity(0.4)), lineWidth: 1)
    }
    .accessibilityHidden(true)
  }
}

// MARK: - the voice route: M2, J2, J3 / J4, M4 (a call), failures

struct VoiceScreen: View {
  @EnvironmentObject var m: WatchModel
  @EnvironmentObject var voice: WatchVoiceController

  var body: some View {
    Group {
      switch voice.stage {
      case .idle, .listening:
        Color.clear.onAppear { if m.path.last == .voice { m.path.removeLast() } }
      case .transcribing:
        ThinkingView(question: nil)
      case .confirming(let text, let timer):
        ConfirmWordsView(text: text, progress: timer.progress(voice.now))
      case .thinking(let q):
        ThinkingView(question: q)
      case .answer(let q, let a, let conv):
        AnswerView(question: q, answer: a, conversation: conv)
      case .confirmCall(let q, let call):
        CallConfirmView(question: q, call: call)
      case .failed(let f):
        VoiceFailureView(failure: f)
      }
    }
    // back from any voice screen = cancel (a turn already sent still finishes on the server; its answer is not waited for)
    .onDisappear { if !voice.flow.isIdle { voice.cancel() } }
  }
}

/// M2: the words, a ring that fills in `voiceSendAfterSec`, ✕ = cancel, the core (or Double Tap) = send now.
struct ConfirmWordsView: View {
  @EnvironmentObject var voice: WatchVoiceController
  var text: String
  var progress: Double

  var body: some View {
    let T = voice.now.timeIntervalSince1970 * 1000
    DesignCanvas { c in
      let s = c.s
      let cx = c.size.width / 2
      let ry = 180 * c.sy
      Text(text).font(.system(size: 21 * s, weight: .semibold)).foregroundStyle(W.text)
        .multilineTextAlignment(.center).lineLimit(3).minimumScaleFactor(0.6)
        .frame(width: 184 * s)
        .place(centerX: cx, centerY: 92 * c.sy)
      ZStack {
        Circle().stroke(WJarvis.ice.opacity(0.22), lineWidth: 1)
        Circle().trim(from: 0, to: max(0.003, progress)).stroke(WJarvis.ice.opacity(0.95), style: StrokeStyle(lineWidth: 2, lineCap: .round))
          .rotationEffect(.degrees(-90))
          .shadow(color: WJarvis.ice.opacity(0.45), radius: 4)
      }
      .frame(width: 66 * s, height: 66 * s)
      .place(centerX: cx, centerY: ry)
      Button { voice.sendNow() } label: {
        JarvisCoreMark(size: 48 * s, live: true, ra: T * 0.03, rb: -T * 0.05)
      }
      .buttonStyle(.plain)
      .handGestureShortcut(.primaryAction)   // Double Tap = send now (M2)
      .accessibilityLabel(tr("发送", "Send"))
      .place(centerX: cx, centerY: ry)
      Button { voice.cancel() } label: {
        Image(systemName: "xmark").font(.system(size: 15 * s, weight: .semibold)).foregroundStyle(W.text)
          .frame(width: 34 * s, height: 34 * s)
          .background(Circle().fill(WJarvis.panel).overlay(Circle().stroke(Color.white.opacity(0.25), lineWidth: 1)))
      }
      .buttonStyle(.plain)
      .accessibilityLabel(tr("取消", "Cancel"))
      .place(centerX: 40 * s, centerY: ry)
    }
    .toolbar(.hidden, for: .navigationBar)   // ✕ is the way out (M2 has no back key)
  }
}

/// J2 (and the moment the words are being recognised): the question, a turning arc, the live core (tap = cancel).
struct ThinkingView: View {
  @EnvironmentObject var voice: WatchVoiceController
  var question: String?

  var body: some View {
    let T = voice.now.timeIntervalSince1970 * 1000
    let a = T * 0.36
    DesignCanvas { c in
      let s = c.s
      let cx = c.size.width / 2
      let cy = 124 * c.sy
      if let q = question {
        Text(q).font(.system(size: 13 * s, weight: .medium)).foregroundStyle(W.text2).lineLimit(1)
          .frame(width: 150 * s)
          .place(centerX: cx, centerY: 50 * c.sy)
      }
      ZStack {
        Circle().stroke(WJarvis.ice.opacity(0.22), lineWidth: 1)
        Circle().trim(from: 0, to: 96.0 / 360).stroke(WJarvis.ice.opacity(0.95), style: StrokeStyle(lineWidth: 2, lineCap: .round))
          .rotationEffect(.degrees(a.truncatingRemainder(dividingBy: 360) - 90))
          .shadow(color: WJarvis.ice.opacity(0.45), radius: 4)
      }
      .frame(width: 90 * s, height: 90 * s)
      .place(centerX: cx, centerY: cy)
      Button { voice.cancel() } label: {
        JarvisCoreMark(size: 64 * s, live: true, ra: T * 0.14, rb: -T * 0.24)
      }
      .buttonStyle(.plain)
      .accessibilityLabel(tr("取消", "Cancel"))
      .place(centerX: cx, centerY: cy)
    }
  }
}

/// J3 / J4: the conclusion big, at most a sentence under it, 「AI 生成」, and 「在手机上继续」 when the answer is longer than
/// the watch should show (the crown scrolls when it does not fit).
struct AnswerView: View {
  @EnvironmentObject var voice: WatchVoiceController
  var question: String
  var answer: WatchAnswer
  var conversation: Int?

  var body: some View {
    VStack(spacing: 0) {
      ScrollView { column }
      // J4: the one key stays at the bottom while the crown scrolls the words (Double Tap presses it)
      if answer.more, let conversation {
        PrimaryButton(label: tr("在手机上继续", "Continue on iPhone"), icon: "iphone", kind: .quiet, height: 42) {
          voice.continueOnPhone(conversation)
        }
      }
    }
    .ignoresSafeArea(edges: .bottom)
  }

  private var column: some View {
      VStack(alignment: .leading, spacing: 0) {
        Capsule().fill(WJarvis.ice).frame(width: 44 * W.k, height: 2)
        HStack(spacing: 5 * W.k) {
          Image(systemName: "arrowtriangle.down").font(.system(size: 8 * W.k, weight: .semibold)).foregroundStyle(WJarvis.ice)
          Text(question).font(W.font(11.5)).foregroundStyle(W.text2).lineLimit(1)
        }
        .padding(.top, 8 * W.k)
        Text(answer.headline).font(W.font(27, .semibold)).foregroundStyle(W.text).lineLimit(3).minimumScaleFactor(0.55)
          .fixedSize(horizontal: false, vertical: true)
          .padding(.top, 10 * W.k)
        if let b = answer.body {
          Text(b).font(W.font(12)).foregroundStyle(W.text2).fixedSize(horizontal: false, vertical: true).padding(.top, 5 * W.k)
        }
        Rectangle().fill(W.hair).frame(height: 1).padding(.top, 10 * W.k)
        AITag().padding(.top, 9 * W.k).padding(.bottom, 6 * W.k)
      }
      .padding(.top, W.pushedTop - 4 * W.k)
      .padding(.horizontal, 4 * W.k)
      .frame(maxWidth: .infinity, alignment: .leading)
  }
}

/// The phone's 「AI 生成」 mark (plan 3: a small tag on the watch, the sentence stays on the phone).
struct AITag: View {
  var body: some View {
    Text(tr("AI 生成", "AI-generated")).font(W.font(9.5)).foregroundStyle(W.text2)
      .padding(.horizontal, 7 * W.k).padding(.vertical, 2 * W.k)
      .overlay(Capsule().stroke(W.text3, lineWidth: 1))
  }
}

/// M4 for a delete / undo the server asks about (one question, two keys; Double Tap = keep).
struct CallConfirmView: View {
  @EnvironmentObject var voice: WatchVoiceController
  var question: String
  var call: WatchConfirmCall

  private var undo: Bool { call.tool.contains("undo") }

  var body: some View {
    VStack(spacing: 8 * W.k) {
      Text(undo ? tr("撤销这一项？", "Undo this?") : tr("删除这一项？", "Delete this?"))
        .font(W.font(16, .semibold)).multilineTextAlignment(.center)
      Text(call.summary).font(W.font(12)).foregroundStyle(W.text2).multilineTextAlignment(.center).lineLimit(3)
        .minimumScaleFactor(0.8).fixedSize(horizontal: false, vertical: true)
      Spacer(minLength: 2)
      Button { voice.answerCall(approve: true) } label: {
        Text(undo ? tr("撤销", "Undo") : tr("删除", "Delete")).font(W.font(17, .semibold)).foregroundStyle(W.bad)
          .frame(maxWidth: .infinity, minHeight: 44 * W.k)
          .background(Capsule().fill(W.bad.opacity(0.16)))
      }
      .buttonStyle(.plain)
      PrimaryButton(label: tr("保留", "Keep"), kind: .quiet, height: 44) { voice.answerCall(approve: false) }
    }
    .padding(.top, W.pushedTop)
    .padding(.horizontal, 2 * W.k)
    .toolbar(.hidden, for: .navigationBar)   // the two keys are the ways out
  }
}

/// A voice request that ended without an answer: the reason's icon and short label, and always a way on (again,
/// dictation, or home); never a dead end.
struct VoiceFailureView: View {
  @EnvironmentObject var m: WatchModel
  @EnvironmentObject var voice: WatchVoiceController
  var failure: WatchVoiceFailure

  var body: some View {
    VStack(spacing: 6 * W.k) {
      Spacer(minLength: W.pushedTop)
      Image(systemName: failure.icon).font(.system(size: 28 * W.k, weight: .light))
        .foregroundStyle(failure == .server || failure == .interrupted ? W.bad : W.mid)
      Text(failure.label).font(W.font(15, .semibold)).multilineTextAlignment(.center).lineLimit(2).minimumScaleFactor(0.8)
      Spacer(minLength: 4)
      if failure.retryable {
        PrimaryButton(label: tr("重试", "Retry"), icon: "arrow.clockwise", height: 42) { voice.retry() }
      }
      if failure.dictation {
        // the system's dictation / scribble sheet (plan 4.3 C): the words go to Jarvis as typed
        TextFieldLink(prompt: Text(tr("说给 Jarvis", "Tell Jarvis"))) {
          HStack(spacing: 6) {
            Image(systemName: "keyboard").font(W.font(14, .semibold))
            Text(tr("听写", "Dictate")).font(W.font(16, .semibold))
          }
          .foregroundStyle(W.text)
          .frame(maxWidth: .infinity, minHeight: 40 * W.k)
          .background(Capsule().fill(W.surface2))
        } onSubmit: { t in
          voice.dictated(t)
        }
        .buttonStyle(.plain)
      }
      if !failure.retryable && !failure.dictation {
        PrimaryButton(label: tr("好", "OK"), kind: .quiet, height: 42) {
          voice.close()
          m.path = []
        }
      }
    }
    .ignoresSafeArea(edges: .bottom)
  }
}
