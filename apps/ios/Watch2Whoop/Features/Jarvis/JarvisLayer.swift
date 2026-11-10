import SwiftUI
import UIKit

/// The Jarvis key at the bottom centre, above the tab bar, and everything that grows out of it (#340 N6). MainTabView
/// lays it over the tabs: `.overlay { JarvisLayer(tab: $selection) }`.
///
/// - Tap (in-app AI): the menu opens like a Mission-style dial: the four keys appear just outside the key's rim, slide out
///   to both sides, then rise onto an arc whose centre sits below the key and grow to just under its size. Closing
///   folds them a third of the way back while they fade, so no key ever touches the Jarvis key or another key
///   (`pose`, tested). No ticks, no connecting lines; one ink (the text colour), springs on transform and opacity only.
///   Only the Jarvis key is Liquid Glass (iOS 26; a thin material disc before): the menu keys are plain discs with a
///   hairline over the wash, which costs nothing to draw while they move (2.0.1).
/// - Hold (0.35 s): talk at once, recognised on the phone. Slide up-left = cancel, up-right = lock (hands free, then
///   完成). Release = done: Chinese is finalised by the server's recogniser, English stays on the phone; both are
///   corrected with the user's own terms, then go to the chat.
/// - Not an in-app AI (butler link, subscription, none): a tap does what the Today page's key does (JarvisRouter.tapKey).
struct JarvisLayer: View {
  @Binding var tab: AppTab
  @EnvironmentObject private var session: AccountSession
  @StateObject private var router = JarvisRouter()
  @StateObject private var voice = JarvisVoice()
  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  @Environment(\.colorScheme) private var scheme

  // dial
  @State private var steps = [DialStep](repeating: .parked, count: 4)
  /// The keys exist only while the dial is out.
  @State private var present = false
  @State private var gen = 0
  // press / hold
  @State private var pressed = false
  @State private var pressAt: Date?
  @State private var moved = false
  @State private var held = false
  @State private var holdWork: DispatchWorkItem?
  @State private var holdFill: CGFloat = 0
  // voice
  @State private var mode: VoiceMode = .off
  @State private var zone: Zone?
  @State private var finger: CGPoint = .zero
  @State private var start: CGPoint = .zero
  @State private var lastZoneHaptic: Zone?
  @State private var center: CGPoint = .zero
  @State private var voiceRun = 0
  /// For a moment after the tabs appear, the voice panel is drawn once, invisible: its first layout and glyphs then
  /// do not cost frames on the first hold.
  @State private var warm = true

  enum VoiceMode: Equatable { case off, hold, locked, final }
  enum Zone: Equatable { case cancel, lock }

  static let keySize: CGFloat = 58
  /// The menu keys: a little smaller than the Jarvis key.
  static let subSize: CGFloat = 50
  static let arcRadius: CGFloat = 118
  /// The arc's centre is this far below the key's centre ("圆心偏低").
  static let arcDrop: CGFloat = 22
  static let holdSeconds = 0.35
  static let ringDelay = 0.12
  static let moveTolerance: CGFloat = 10
  static let minSeconds = 0.6
  static let targetDX: CGFloat = 104
  static let targetDY: CGFloat = 132

  private var ink: Color { Palette.text }

  var body: some View {
    GeometryReader { g in
      let c = Self.keyCenter(in: g.size)
      ZStack(alignment: .topLeading) {
        scrim
        if mode != .off { voiceTargets(c) }
        keys(c)
        labels(c)
        if mode != .off { voicePanel(c, size: g.size) }
        else if warm { voicePanel(c, size: g.size, warming: true).opacity(0.01).allowsHitTesting(false).accessibilityHidden(true) }
        toast(c, width: g.size.width)
      }
      .frame(width: g.size.width, height: g.size.height, alignment: .topLeading)
      .coordinateSpace(name: Self.space)
      .onAppear {
        center = c
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { warm = false }
      }
      .onChange(of: g.size) { center = Self.keyCenter(in: $0) }
    }
    .sheet(item: $router.sheet) { s in sheet(s) }
    .onAppear {
      router.attach(session)
      router.page = tab.rawValue
      router.selectTab = { t in tab = t }
      voice.onInterrupted = { endVoice(message: tr("被系统打断，没有发送", "Interrupted by the system; nothing was sent")) }
      #if DEBUG
      JarvisDemo.run(router: router, layer: demoHooks)
      #endif
    }
    .onChange(of: tab) { t in
      router.page = t.rawValue
      if router.menuOpen { router.menuOpen = false }
    }
    .onChange(of: router.menuOpen) { o in o ? runOpen() : runClose() }
    .onChange(of: router.voiceRequested) { r in
      guard r else { return }
      router.voiceRequested = false
      beginVoice(locked: true)
    }
    .onReceive(NotificationCenter.default.publisher(for: UIApplication.willEnterForegroundNotification)) { _ in
      Task { await router.chat?.resume() }
    }
    .onReceive(NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)) { _ in
      if mode == .hold || mode == .locked { endVoice(message: tr("被系统打断，没有发送", "Interrupted by the system; nothing was sent")) }
    }
  }

  static let space = "jarvis"

  /// Above the tab bar, centred (the tab bar is 49 pt high before iOS 26; iOS 26's floating bar sits a little higher).
  static func keyCenter(in size: CGSize) -> CGPoint {
    var bar: CGFloat = 49
    if #available(iOS 26.0, *) { bar = 62 }
    return CGPoint(x: size.width / 2, y: size.height - bar - keySize / 2 - 10)
  }

  static func position(_ k: JarvisMenuKey, center c: CGPoint) -> CGPoint {
    let r = k.degrees * .pi / 180
    return CGPoint(x: c.x + CGFloat(sin(r)) * arcRadius, y: c.y + arcDrop - CGFloat(cos(r)) * arcRadius)
  }

  /// The steps of a menu key: inserted invisible just outside the key's rim, out to its side, up on the arc, folding away.
  enum DialStep: Equatable { case parked, out, up, folding }

  struct KeyPose: Equatable {
    var point: CGPoint
    var scale: CGFloat
    var opacity: Double
  }

  /// How far out of the key's rim a menu key is parked (inner keys) and how far the outer keys start beyond them.
  static let parkGap: CGFloat = 4
  static let parkStagger: CGFloat = 22
  /// The part of the way back to the key a folding key travels (it is gone before it could touch anything).
  static let foldTravel: CGFloat = 0.35

  /// Where a menu key is drawn in each step. The keys never overlap the Jarvis key or each other in any step, nor on the
  /// way between steps (JarvisTests): the old close slid them all into the key, where they piled up for a moment.
  static func pose(_ k: JarvisMenuKey, _ step: DialStep, center c: CGPoint) -> KeyPose {
    let p = position(k, center: c)
    let side: CGFloat = k.degrees < 0 ? -1 : 1
    let outer = abs(k.degrees) > 50
    switch step {
    case .parked:
      let dx = keySize / 2 + subSize * 0.3 / 2 + parkGap + (outer ? parkStagger : 0)
      return KeyPose(point: CGPoint(x: c.x + side * dx, y: c.y), scale: 0.3, opacity: 0)
    case .out:
      return KeyPose(point: CGPoint(x: p.x, y: c.y), scale: 0.6, opacity: 1)
    case .up:
      return KeyPose(point: p, scale: 1, opacity: 1)
    case .folding:
      return KeyPose(point: CGPoint(x: p.x + (c.x - p.x) * foldTravel, y: p.y + (c.y - p.y) * foldTravel), scale: 0.6, opacity: 0)
    }
  }

  // MARK: - layers

  @ViewBuilder private var scrim: some View {
    let on = router.menuOpen || mode == .locked || mode == .final
    if on || mode == .hold {
      // a plain wash, no blur: a full-screen blur behind the glass keys costs frames while they move
      (scheme == .dark ? Color.black.opacity(0.78) : Color(white: 0.97).opacity(0.9))
        .ignoresSafeArea()
        .contentShape(Rectangle())
        .onTapGesture {
          if router.menuOpen { router.menuOpen = false }
          else if mode == .locked { endVoice(message: tr("已取消，没有发送", "Cancelled; nothing was sent")) }
        }
        .transition(.opacity)
        .accessibilityHidden(true)
    }
  }

  /// The menu keys under the Jarvis key. Only the Jarvis key itself is glass, on its own (no container: nothing can
  /// merge into it).
  private func keys(_ c: CGPoint) -> some View {
    ZStack(alignment: .topLeading) {
      ForEach(Array(JarvisMenuKey.allCases.enumerated()), id: \.element) { i, k in
        if present {
          let pose = Self.pose(k, steps[i], center: c)
          subKey(k)
            .scaleEffect(pose.scale)
            .opacity(pose.opacity)
            .position(pose.point)
            .allowsHitTesting(steps[i] == .up)
            .accessibilityHidden(!router.menuOpen)
        }
      }
      mainKey(glass: !JarvisDemo.forceMaterial).position(c)
    }
  }

  /// The keys' names, only while the keys are up.
  private func labels(_ c: CGPoint) -> some View {
    ZStack(alignment: .topLeading) {
      ForEach(Array(JarvisMenuKey.allCases.enumerated()), id: \.element) { i, k in
        if steps[i] == .up {
          let p = Self.position(k, center: c)
          Text(k.title)
            .font(.system(size: 11, weight: .semibold)).foregroundColor(ink)
            .fixedSize()
            .position(x: p.x, y: p.y + Self.subSize / 2 + 12)
            .transition(.opacity)
        }
      }
    }
    .allowsHitTesting(false)
    .accessibilityHidden(true)
  }

  private func subKey(_ k: JarvisMenuKey) -> some View {
    Button {
      JarvisHaptics.light.impactOccurred()
      router.run(k)
    } label: {
      Icon(k.icon, size: 21).foregroundColor(ink)
        .frame(width: Self.subSize, height: Self.subSize)
        .contentShape(Circle())
        .modifier(KeySurface(style: .plain, filled: false, ink: ink, dark: scheme == .dark))
    }
    .buttonStyle(PressScale())
    .accessibilityLabel(k.title)
  }

  private var keyIcon: Icon.Name {
    if router.menuOpen { return .chevronDown }
    if mode != .off || held { return .mic }
    return .orbit
  }

  private func mainKey(glass: Bool) -> some View {
    let rec = mode == .hold || mode == .locked || held
    return Icon(keyIcon, size: 24)
      .id(keyIcon)
      .transition(reduceMotion ? .identity : .scale(scale: 0.6).combined(with: .opacity))
      .foregroundColor(rec ? Palette.bg : ink)
      .frame(width: Self.keySize, height: Self.keySize)
      .contentShape(Circle())
      .modifier(KeySurface(style: glass ? .glass : .material, filled: rec, ink: ink, dark: scheme == .dark))
      .overlay(   // the hold ring: fills while the finger stays down, then talking starts
        Circle().trim(from: 0, to: holdFill).stroke(ink, style: StrokeStyle(lineWidth: 2, lineCap: .round))
          .rotationEffect(.degrees(-90)).frame(width: Self.keySize + 10, height: Self.keySize + 10).allowsHitTesting(false)
      )
      .scaleEffect(pressed ? 0.9 : 1)
      .animation(reduceMotion ? nil : (pressed ? .easeOut(duration: 0.06) : .spring(response: 0.32, dampingFraction: 0.5)), value: pressed)
      .animation(reduceMotion ? nil : .spring(response: 0.28, dampingFraction: 0.75), value: keyIcon)
      .animation(reduceMotion ? nil : .spring(response: 0.28, dampingFraction: 0.75), value: rec)
      .gesture(pressGesture)
      .accessibilityElement(children: .ignore)
      .accessibilityLabel(router.menuOpen ? tr("收起 Jarvis 菜单", "Close the Jarvis menu") : "Jarvis")
      .accessibilityHint(router.ai.inApp ? tr("点按展开菜单；按住直接说话，松开发送", "Tap for the menu; hold to talk, release to send") : "")
      .accessibilityAddTraits(.isButton)
      .accessibilityAction { tap() }
  }

  // MARK: - the dial's sequence

  private func later(_ s: Double, _ f: @escaping () -> Void) {
    let g = gen
    DispatchQueue.main.asyncAfter(deadline: .now() + s) { if g == gen { f() } }
  }

  private func runOpen() {
    gen += 1
    JarvisPerf.shared.measure("jarvis-open", seconds: 0.6)
    JarvisHaptics.light.impactOccurred()
    var parked = Transaction()
    parked.disablesAnimations = true
    withTransaction(parked) {
      steps = [DialStep](repeating: reduceMotion ? .up : .parked, count: 4)
      present = true
    }
    if reduceMotion { return }
    // 1. out to both sides (outer keys a hair first, so the pair reads as one movement); the keys were just inserted
    //    at the key's rim, so the move starts on the next frame
    later(0) {
      for (i, k) in JarvisMenuKey.allCases.enumerated() {
        let outer = abs(k.degrees) > 50
        withAnimation(.spring(response: 0.22, dampingFraction: 0.92).delay(outer ? 0 : 0.015)) { steps[i] = .out }
      }
    }
    // 2. up onto the arc, growing to size with a little overshoot
    later(0.1) {
      for (i, k) in JarvisMenuKey.allCases.enumerated() {
        let outer = abs(k.degrees) > 50
        withAnimation(.spring(response: 0.34, dampingFraction: 0.68).delay(outer ? 0.03 : 0)) { steps[i] = .up }
      }
    }
  }

  /// All four keys at once: back a third of the way and gone in 0.16 s, then out of the tree.
  private func runClose() {
    gen += 1
    JarvisPerf.shared.measure("jarvis-close", seconds: 0.4)
    withAnimation(reduceMotion ? nil : .easeOut(duration: Self.foldSeconds)) {
      steps = [DialStep](repeating: .folding, count: 4)
    }
    later(reduceMotion ? 0 : Self.foldSeconds + 0.02) {
      present = false
      steps = [DialStep](repeating: .parked, count: 4)
    }
  }

  static let foldSeconds = 0.16

  // MARK: - press: tap / hold to talk

  private var canTalk: Bool { router.ai.inApp && !JarvisVoice.supportedLangs.isEmpty }

  private var pressGesture: some Gesture {
    DragGesture(minimumDistance: 0, coordinateSpace: .named(Self.space))
      .onChanged { v in
        if pressAt == nil { beginPress(at: v.location) }
        else if held { holdMoved(to: v.location) }
        else if !moved && hypot(v.translation.width, v.translation.height) > Self.moveTolerance { moved = true; cancelPress() }
      }
      .onEnded { v in endPress(at: v.location) }
  }

  private func beginPress(at p: CGPoint) {
    pressAt = Date(); moved = false; held = false; pressed = true; start = p; finger = p
    JarvisHaptics.prepare()   // the first haptic of a session otherwise costs a frame or two
    if mode == .locked { return }   // the key again = done (endPress)
    guard canTalk, !router.menuOpen, mode == .off else { return }
    if !reduceMotion { withAnimation(.linear(duration: Self.holdSeconds - Self.ringDelay).delay(Self.ringDelay)) { holdFill = 1 } }
    let w = DispatchWorkItem { beginHold() }
    holdWork = w
    DispatchQueue.main.asyncAfter(deadline: .now() + Self.holdSeconds, execute: w)
  }

  private func cancelPress() {
    pressed = false
    holdWork?.cancel()
    withAnimation(nil) { holdFill = 0 }
  }

  private func endPress(at p: CGPoint) {
    let wasHeld = held, wasMoved = moved
    holdWork?.cancel()
    pressAt = nil; pressed = false; moved = false
    withAnimation(nil) { holdFill = 0 }
    if wasHeld {
      held = false
      holdMoved(to: p)
      holdUp()
    } else if !wasMoved {
      if mode == .locked { finishVoice() } else if mode == .off { tap() }
    }
  }

  private func tap() {
    router.tapKey()
  }

  private func beginHold() {
    guard pressAt != nil, !moved else { return }
    held = true
    pressed = false
    withAnimation(nil) { holdFill = 0 }
    JarvisHaptics.medium.impactOccurred()
    beginVoice(locked: false)
  }

  // MARK: - voice

  private func beginVoice(locked: Bool) {
    guard mode == .off else { return }
    let ai = router.ai
    if ai.mode == .fallback && !ai.consent {
      held = false
      router.run(.chat)   // the terms first (then the user holds again)
      return
    }
    guard !JarvisVoice.supportedLangs.isEmpty else {
      held = false
      router.show(tr("这台手机不能本机识别语音：用键盘上的麦克风听写", "This phone can't recognise speech on device: use the keyboard's dictation"))
      return
    }
    zone = nil
    lastZoneHaptic = nil
    withAnimation(reduceMotion ? nil : .easeOut(duration: 0.18)) { mode = locked ? .locked : .hold }
    JarvisPerf.shared.measure("jarvis-hold", seconds: 0.5)
    var lang = JarvisVoice.lang
    if !JarvisVoice.supportedLangs.contains(lang) { lang = JarvisVoice.supportedLangs[0] }
    voiceRun += 1
    let run = voiceRun
    Task {
      do {
        try await voice.start(lang: lang, keepAudio: lang == "zh")
        // 60 s at most, as the page
        try? await Task.sleep(nanoseconds: UInt64(JarvisVoice.maxSeconds * 1_000_000_000))
        if run == voiceRun && voice.running && (mode == .hold || mode == .locked) { finishVoice() }
      } catch let f as JarvisVoice.Failure {
        endVoice(message: f == .permission
                 ? tr("麦克风或语音识别没打开：在系统设置里允许 Watch2Whoop", "Microphone or speech recognition is off: allow Watch2Whoop in Settings")
                 : tr("语音识别暂时不可用，用键盘上的麦克风听写", "Speech recognition isn't available; use the keyboard's dictation"))
      } catch {
        endVoice(message: nil)
      }
    }
  }

  private func zone(at p: CGPoint) -> Zone? {
    let c = center
    let cancel = CGPoint(x: c.x - Self.targetDX, y: c.y - Self.targetDY), lock = CGPoint(x: c.x + Self.targetDX, y: c.y - Self.targetDY)
    let dC = hypot(p.x - cancel.x, p.y - cancel.y), dL = hypot(p.x - lock.x, p.y - lock.y)
    let up = start.y - p.y
    if dC < 48 || (up > 60 && p.x < c.x && dC < 150) { return .cancel }
    if dL < 48 || (up > 60 && p.x >= c.x && dL < 150) { return .lock }
    return nil
  }

  private func holdMoved(to p: CGPoint) {
    finger = p
    let z = zone(at: p)
    guard z != zone else { return }
    withAnimation(.spring(response: 0.2, dampingFraction: 0.8)) { zone = z }
    if z != nil && z != lastZoneHaptic { JarvisHaptics.selection.selectionChanged() }
    lastZoneHaptic = z
  }

  private func holdUp() {
    switch zone {
    case .cancel: endVoice(message: tr("已取消，没有发送", "Cancelled; nothing was sent"))
    case .lock:
      JarvisHaptics.selection.selectionChanged()
      withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { mode = .locked; zone = nil }
    case nil: finishVoice()
    }
  }

  private func finishVoice() {
    guard mode == .hold || mode == .locked else { return }
    if mode == .hold && voice.elapsed < Self.minSeconds {
      endVoice(message: tr("按住说话，说完再松开", "Hold to talk, release when done"))
      return
    }
    withAnimation(.easeOut(duration: 0.15)) { mode = .final }
    let t0 = CACurrentMediaTime()
    Task {
      let out = await voice.stop()
      guard mode == .final else { return }
      var text = out?.text ?? ""
      if let chat = router.chat {
        if let wav = out?.wav, let t = await chat.transcribe(wav: wav) { text = t }
        else if !text.isEmpty { text = await chat.voiceFix(text) }
      }
      guard mode == .final else { return }
      text = text.trimmingCharacters(in: .whitespacesAndNewlines)
      if text.isEmpty {
        endVoice(message: tr("没听清，按住说话，说完再松开", "Didn't catch that; hold to talk, release when done"))
        return
      }
      endVoice(message: nil)
      JarvisHaptics.notice.notificationOccurred(.success)
      router.deliverVoice(text, seconds: out?.seconds ?? 0)
      JarvisPerf.shared.mark("voice-key-done-to-text", since: t0)
    }
  }

  private func endVoice(message: String?) {
    voice.cancel()
    held = false
    withAnimation(reduceMotion ? nil : .easeOut(duration: 0.18)) { mode = .off; zone = nil }
    if let message {
      JarvisHaptics.notice.notificationOccurred(.warning)
      router.show(message)
    }
  }

  private func voiceTargets(_ c: CGPoint) -> some View {
    ZStack(alignment: .topLeading) {
      if mode == .hold {
        target(.x, tr("取消", "Cancel"), hot: zone == .cancel).position(x: c.x - Self.targetDX, y: c.y - Self.targetDY)
        target(.lock, tr("锁定", "Lock"), hot: zone == .lock).position(x: c.x + Self.targetDX, y: c.y - Self.targetDY)
      }
    }
    .allowsHitTesting(false)
    .accessibilityHidden(true)
  }

  private func target(_ icon: Icon.Name, _ label: String, hot: Bool) -> some View {
    VStack(spacing: 6) {
      Icon(icon, size: 20).foregroundColor(hot ? Palette.bg : ink)
        .frame(width: 52, height: 52)
        .background(Circle().fill(hot ? ink : Color.clear))
        .overlay(Circle().strokeBorder(ink.opacity(hot ? 0 : 0.6), lineWidth: 1))
      Text(label).font(.system(size: 11, weight: .semibold)).foregroundColor(ink)
    }
    .scaleEffect(hot ? 1.15 : 1)
  }

  private func voicePanel(_ c: CGPoint, size: CGSize, warming: Bool = false) -> some View {
    let title: String = {
      switch mode {
      case .hold: return tr("在听", "Listening")
      case .locked: return tr("已锁定", "Locked")
      default: return tr("识别中", "Recognising")
      }
    }()
    let hint: String = {
      switch (mode, zone) {
      case (.hold, .cancel?): return tr("松开 取消", "Release to cancel")
      case (.hold, .lock?): return tr("松开 锁定，可以松手接着说", "Release to lock, then keep talking")
      case (.hold, nil): return tr("松开 发送 · 左上取消 · 右上锁定", "Release to send · up-left cancel · up-right lock")
      case (.locked, _): return router.voiceDirect ? tr("说完点「发送」", "Tap Send when finished") : tr("说完点「完成」", "Tap Done when finished")
      default: return ""
      }
    }()
    let bottom = mode == .hold || warming ? c.y - Self.targetDY - 46 : c.y - Self.keySize / 2 - 22
    return VStack(alignment: .leading, spacing: 10) {
      HStack(spacing: 8) {
        Circle().fill(ink).frame(width: 7, height: 7).opacity(mode == .final ? 0.3 : 1)
        Text(title).font(Typo.bodyStrong).foregroundColor(ink)
        if mode == .locked, JarvisVoice.supportedLangs.count > 1 {
          Button(JarvisVoice.lang == "en" ? "English" : tr("中文", "Chinese")) { switchLang() }
            .font(.system(size: 12, weight: .semibold)).foregroundColor(ink)
            .padding(.vertical, 3).padding(.horizontal, 8)
            .overlay(Capsule().strokeBorder(ink.opacity(0.4), lineWidth: 1))
        }
        Spacer()
        TimelineView(.periodic(from: .now, by: 0.25)) { _ in
          Text(Self.mmss(voice.elapsed)).font(Typo.caption.monospacedDigit()).foregroundColor(Palette.text2)
        }
      }
      Waveform(levels: voice.levels, active: mode != .final)
        .frame(height: 30)
      Text(voice.partial.isEmpty ? (mode == .final ? "…" : tr("说吧，我边听边写", "Go ahead, I'm writing as you talk")) : voice.partial)
        .font(Typo.body).foregroundColor(voice.partial.isEmpty ? Palette.text3 : ink)
        .lineLimit(3).frame(maxWidth: .infinity, alignment: .leading)
      if !hint.isEmpty { Text(hint).font(Typo.caption).foregroundColor(Palette.text2) }
      if mode == .locked {
        HStack(spacing: 10) {
          Button { endVoice(message: tr("已取消，没有发送", "Cancelled; nothing was sent")) } label: {
            Text(tr("取消", "Cancel")).font(Typo.bodyStrong).foregroundColor(ink).frame(maxWidth: .infinity, minHeight: 42)
              .overlay(Capsule().strokeBorder(ink.opacity(0.4), lineWidth: 1))
          }
          Button { finishVoice() } label: {
            HStack(spacing: 6) { Icon(.arrowUp, size: 16); Text(voiceDoneTitle).font(Typo.bodyStrong) }
              .foregroundColor(Palette.bg).frame(maxWidth: .infinity, minHeight: 42)
              .background(Capsule().fill(ink))
          }
        }
        .buttonStyle(PressScale())
      }
    }
    .padding(16)
    .frame(width: min(size.width - 32, 420))
    .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Palette.sheet))
    .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).strokeBorder(ink.opacity(0.12), lineWidth: 1))
    .shadow(color: .black.opacity(0.12), radius: 18, y: 6)
    .accessibilityElement(children: .contain)
    .accessibilityLabel(tr("语音输入", "Voice input"))
    .padding(.bottom, max(0, size.height - bottom))
    .frame(width: size.width, height: size.height, alignment: .bottom)
    .transition(.opacity.combined(with: .scale(scale: 0.96, anchor: .bottom)))
  }

  private var voiceDoneTitle: String { router.voiceDirect ? tr("发送", "Send") : tr("完成", "Done") }

  private func switchLang() {
    let next = JarvisVoice.lang == "zh" ? "en" : "zh"
    JarvisVoice.lang = next
    JarvisHaptics.selection.selectionChanged()
    voice.cancel()
    Task { try? await voice.start(lang: next, keepAudio: next == "zh") }
  }

  static func mmss(_ s: Double) -> String { "\(Int(s) / 60):" + String(format: "%02d", Int(s) % 60) }

  // MARK: - toast and sheets

  @ViewBuilder private func toast(_ c: CGPoint, width: CGFloat) -> some View {
    if let t = router.toast {
      Text(t).font(Typo.caption).foregroundColor(Palette.bg)
        .multilineTextAlignment(.center)
        .padding(.vertical, 10).padding(.horizontal, 16)
        .background(Capsule().fill(ink))
        .frame(maxWidth: width - 48)
        .position(x: c.x, y: c.y - Self.keySize / 2 - 34)
        .transition(.opacity.combined(with: .move(edge: .bottom)))
        .allowsHitTesting(false)
    }
  }

  @ViewBuilder private func sheet(_ s: JarvisRouter.Sheet) -> some View {
    switch s {
    case .chat:
      if let chat = router.chat {
        JarvisChatView(chat: chat, router: router).environmentObject(session)
      }
    case .quick:
      if let chat = router.chat {
        QuickLogSheet(chat: chat, router: router).environmentObject(session)
          .presentationDetents([.medium])
          .presentationDragIndicator(.visible)
      }
    case .camera:
      CameraPicker { img in
        router.sheet = nil
        if let img { DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { router.sendPhoto(img) } } else { router.cancelPhoto() }
      }
      .ignoresSafeArea()
    }
  }

  #if DEBUG
  /// What the demo (JarvisDemo) drives: the press and hold paths with synthetic touches.
  private var demoHooks: JarvisDemo.Hooks {
    JarvisDemo.Hooks(center: { center }, press: { beginPress(at: $0) }, move: { holdMoved(to: $0) }, release: { endPress(at: $0) })
  }
  #endif
}

/// The disc under a key. `glass`: system Liquid Glass on iOS 26+ (the Jarvis key only; not `.interactive()`: the key's
/// own press scale is the feedback, so a tap gets one crisp response instead of two), a thin material before;
/// `material`: the thin material with a hairline in the ink; `plain`: the page colour with the hairline (the menu keys,
/// over the wash: nothing behind them to blur). Filled (recording) is solid ink in every style: tinted glass would
/// refract the panel's words into the key.
struct KeySurface: ViewModifier {
  enum Style: Equatable { case glass, material, plain }
  let style: Style
  let filled: Bool
  let ink: Color
  let dark: Bool

  @ViewBuilder func body(content: Content) -> some View {
    if filled || style == .material {
      material(content)
    } else if style == .plain {
      content
        .background(Circle().fill(Palette.bg))
        .overlay(Circle().strokeBorder(ink.opacity(0.8), lineWidth: 1))
    } else {
      glass(content)
    }
  }

  @ViewBuilder private func glass(_ content: Content) -> some View {
    #if compiler(>=6.2)   // the iOS 26 SDK; an older Xcode builds the material key only
    if #available(iOS 26.0, *) {
      content.glassEffect(Glass.regular, in: Circle())
    } else {
      material(content)
    }
    #else
    material(content)
    #endif
  }

  private func material(_ content: Content) -> some View {
    content
      .background(Circle().fill(filled ? AnyShapeStyle(ink) : AnyShapeStyle(.ultraThinMaterial)))
      .overlay(Circle().strokeBorder(ink.opacity(filled ? 0 : 0.8), lineWidth: 1))
      .shadow(color: .black.opacity(dark ? 0.45 : 0.1), radius: 10, x: 0, y: 4)
  }
}

/// Loudness bars, newest on the right, one ink.
struct Waveform: View {
  let levels: [Double]
  var active = true

  var body: some View {
    GeometryReader { g in
      let n = levels.count, gap: CGFloat = 3
      let w = max(1, (g.size.width - gap * CGFloat(n - 1)) / CGFloat(n))
      HStack(alignment: .center, spacing: gap) {
        ForEach(0..<n, id: \.self) { i in
          Capsule().fill(Palette.text.opacity(active ? 1 : 0.3))
            .frame(width: w, height: max(3, g.size.height * CGFloat(0.08 + levels[i] * 0.92)))
        }
      }
      .frame(width: g.size.width, height: g.size.height)
      .animation(.linear(duration: 0.05), value: levels)
    }
    .accessibilityHidden(true)
  }
}

/// Feedback generators made once and warmed up when a finger lands on the key, so the first tick does not stall a frame.
@MainActor
enum JarvisHaptics {
  static let light = UIImpactFeedbackGenerator(style: .light)
  static let medium = UIImpactFeedbackGenerator(style: .medium)
  static let selection = UISelectionFeedbackGenerator()
  static let notice = UINotificationFeedbackGenerator()

  static func prepare() {
    light.prepare()
    medium.prepare()
    selection.prepare()
  }
}
