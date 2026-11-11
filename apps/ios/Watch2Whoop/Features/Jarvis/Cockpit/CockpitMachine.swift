import CoreGraphics
import Foundation

// The Jarvis cockpit (2.0.1, third pass approved by the owner on 2026-11-12): the state machine and the springs, with no UI.
// Every number below comes from docs/design/2.0.1/jarvis-cockpit.html (the interactive prototype: `S`, `spr`, `step`,
// `tap`, `act`); the prototype works in a 402 × 874 screen with the core at (201, 751), so every distance here is relative
// to the core's centre. Every value moves on a spring that is integrated each frame, so any motion can be reversed halfway
// (tap elsewhere while the panels fan out and they fold back from where they are).

/// Sizes in points, angles in degrees (0 = 12 o'clock, clockwise).
enum CockpitGeometry {
  /// The core: its disc and the touch area around it.
  static let coreRadius: CGFloat = 29
  static let coreHit: CGFloat = 34
  /// The panels: one ring sector each (RI, RO, HW, RM).
  static let panelInner: CGFloat = 80
  static let panelOuter: CGFloat = 136
  static let panelHalfAngle: Double = 16
  static let panelMid: CGFloat = 108
  static let panelHit: CGFloat = 31
  /// The panels grow from this radius (a little outside the core).
  static let panelStart: CGFloat = 34
  /// The selected panel moves out by this much.
  static let selectPush: CGFloat = 7
  /// The tick ring outside the panels: 90 ticks (every 4°), a major one every 9.
  static let tickInner: CGFloat = 143
  static let tickMajor: CGFloat = 150
  static let tickMinor: CGFloat = 146.5
  static let tickSpan: Double = 100
  static let selectArc: CGFloat = 153
  /// The second ring's way back: the chosen key, docked above the core.
  static let crumbDistance: CGFloat = 54
  static let crumbRadius: CGFloat = 13
  static let crumbHit: CGFloat = 18
  /// Listening: the × left of the core.
  static let cancelOffset: CGFloat = -80
  static let cancelRadius: CGFloat = 17
  static let cancelHit: CGFloat = 24
  /// Listening: the language chip right of the core (中文 / English), when the phone recognises both.
  static let langOffset: CGFloat = 80
  /// The reply card: its bottom edge is this far above the core, its width at most `cardWidth`.
  static let cardGap: CGFloat = 99
  static let cardWidth: CGFloat = 354
  /// The transcript block (words, hint, 发送) ends this far above the core.
  static let transcriptBottom: CGFloat = 104

  /// The point at `radius` and `degrees` around `c`.
  static func point(_ radius: CGFloat, _ degrees: Double, around c: CGPoint) -> CGPoint {
    let a = degrees * .pi / 180
    return CGPoint(x: c.x + radius * CGFloat(sin(a)), y: c.y - radius * CGFloat(cos(a)))
  }
}

/// The timings, in seconds.
enum CockpitTiming {
  /// Each panel starts this much after the one 36° nearer the middle (the stagger of the fan).
  static let stagger = 0.06
  /// A tapped panel lights up, then acts after this.
  static let select = 0.26
  /// A first-ring key that opens a second ring: the first folds, then the second fans out.
  static let subRing = 0.30
  static let back = 0.28
  /// Listening: words that stay the same this long are a pause (the prototype: the end of speech + 0.5 s).
  static let pause = 0.7
  /// The ring that closes before the words are sent.
  static let autoSend = 1.0
  /// Listening with no words at all for this long: give up.
  static let noWords = 8.0
  /// Springs are integrated in steps no longer than this (the prototype: 8 ms).
  static let maxStep = 1.0 / 120
}

/// What the key turns into.
enum CockpitMode: Equatable {
  case idle, menu, listen, confirm, think, card
}

/// What a voice message is for: the reply card it ends in.
enum CockpitFlow: Equatable {
  /// 说话: a question → the verdict card.
  case qa
  /// 记一餐 → 说: a meal → 「已记到午餐」.
  case meal
  /// 记训练 → 说一组: a set → 「已记到 …」 with the set dots.
  case set
}

/// Every key of the two rings.
enum CockpitKey: Hashable {
  // first ring
  case meal, train, talk, photo, ask
  // 记一餐
  case mealPhoto, mealTalk, mealMore
  /// One of the two most used quick meals, by its template id (never by place: the order can change before the tap acts).
  case template(Int)
  // 记训练
  case trainStart, trainTalk, trainBackfill, trainPlan
}

/// One panel: where it sits and what it shows (an icon and a label, or a quick meal's name and kcal).
struct CockpitItem: Equatable {
  var key: CockpitKey
  var angle: Double
  var icon: Icon.Name?
  var title: String
  var sub: String?

  static func firstRing() -> [CockpitItem] {
    [CockpitItem(key: .meal, angle: -72, icon: .soup, title: tr("记一餐", "Meal")),
     CockpitItem(key: .train, angle: -36, icon: .dumbbell, title: tr("记训练", "Workout")),
     CockpitItem(key: .talk, angle: 0, icon: .mic, title: tr("说话", "Talk")),
     CockpitItem(key: .photo, angle: 36, icon: .camera, title: tr("拍照", "Photo")),
     CockpitItem(key: .ask, angle: 72, icon: .messageCircle, title: tr("提问", "Ask"))]
  }

  /// `meals`: the two most used quick meals (template id, name, kcal); a missing one leaves its place empty.
  static func mealRing(_ meals: [CockpitMeal]) -> [CockpitItem] {
    var out = [CockpitItem(key: .mealPhoto, angle: -72, icon: .camera, title: tr("拍照", "Photo")),
               CockpitItem(key: .mealTalk, angle: -36, icon: .mic, title: tr("说", "Say"))]
    for (i, m) in meals.prefix(2).enumerated() {
      out.append(CockpitItem(key: .template(m.id), angle: i == 0 ? 0 : 36, icon: nil, title: m.name,
                             sub: m.kcal.map { "\(Int($0.rounded())) kcal" }))
    }
    out.append(CockpitItem(key: .mealMore, angle: 72, icon: .ellipsis, title: tr("更多", "More")))
    return out
  }

  static func trainRing() -> [CockpitItem] {
    [CockpitItem(key: .trainStart, angle: -54, icon: .zap, title: tr("开始", "Start")),
     CockpitItem(key: .trainTalk, angle: -18, icon: .mic, title: tr("说一组", "Say a set")),
     CockpitItem(key: .trainBackfill, angle: 18, icon: .clock, title: tr("补记", "Backfill")),
     CockpitItem(key: .trainPlan, angle: 54, icon: .calendarDays, title: tr("计划", "Plan"))]
  }
}

/// A quick meal on the 记一餐 ring.
struct CockpitMeal: Equatable {
  var id: Int
  var name: String
  var kcal: Double?
}

/// What the machine asks of the app (the controller does the real work).
enum CockpitEffect: Equatable {
  /// The fan came out / everything went back into the key (haptics).
  case opened, closed
  /// Start the recogniser for this flow.
  case listen(CockpitFlow)
  /// The language chip: recognise the other language from now on.
  case switchLanguage
  /// Drop the recording (×, a tap outside, giving up).
  case stopListening
  /// Nothing was heard: stop and say so.
  case gaveUp
  /// The pause was heard or the core tapped: take the words and send them.
  case send(CockpitFlow)
  /// A key with its own action (a quick meal, or one that opens a page of the app).
  case run(CockpitKey)
}

/// A damped spring (the prototype's `spr`): x moves towards the target, v is its speed.
struct CockpitSpring: Equatable {
  var x: Double = 0
  var v: Double = 0

  mutating func step(to target: Double, k: Double, c: Double, dt: Double) {
    v += (-k * (x - target) - c * v) * dt
    x += v * dt
  }

  /// Clamped to 0…1, for opacities and shares.
  var unit: Double { min(1, max(0, x)) }
}

/// The cockpit's state. A value type: the view keeps one in a reference box and steps it every frame; the tests step it by
/// hand.
struct CockpitMachine {
  private(set) var mode: CockpitMode = .idle
  /// Seconds since the machine started, and since the mode last changed.
  private(set) var time: Double = 0
  private(set) var since: Double = 0
  /// The panels on screen (first or second ring).
  private(set) var items: [CockpitItem] = CockpitItem.firstRing()
  private(set) var level = 1
  /// The panels are (being) fanned out; `fanSince` drives the stagger.
  private(set) var fanOpen = false
  private(set) var fanSince: Double = 0
  /// The tapped panel, lit while its action waits.
  private(set) var selected: Int?
  private(set) var flow: CockpitFlow = .qa
  /// The icon docked above the core in the second ring.
  private(set) var crumbIcon: Icon.Name = .soup
  /// The words heard so far, and when they last changed.
  private(set) var words = ""
  private(set) var wordsAt: Double?
  /// The microphone's loudness, 0…1 (moves the listening ring).
  var voiceLevel: Double = 0
  /// 「减弱动态效果」: no stagger, no turning, no spinning; the views fade instead of moving.
  var reduceMotion = false
  /// The two quick meals of the 记一餐 ring.
  var meals: [CockpitMeal] = []
  /// A pause sends by itself after the one-second ring (flag `jarvis_voice_direct`). Off: the words wait in the confirm
  /// state until the user sends them (the core, or 发送) or edits them; nothing goes out by itself.
  var autoSend = true
  /// The language chip is shown (the phone recognises more than one language).
  var canSwitchLanguage = false

  // springs (names as in the prototype)
  private(set) var on = CockpitSpring()
  private(set) var panels = [CockpitSpring](repeating: CockpitSpring(), count: 5)
  private(set) var sel = CockpitSpring()
  private(set) var crumb = CockpitSpring()
  private(set) var rot = CockpitSpring()
  private(set) var listening = CockpitSpring()
  private(set) var envelope = CockpitSpring()
  private(set) var confirming = CockpitSpring()
  private(set) var thinking = CockpitSpring()
  private(set) var transcript = CockpitSpring()
  private(set) var cardWidth = CockpitSpring()
  private(set) var cardHeight = CockpitSpring()
  private(set) var press = CockpitSpring()
  /// The core's tick ring and its three segments turn by themselves (degrees).
  private(set) var tickAngle: Double = 0
  private(set) var segmentAngle: Double = 0
  /// When the core was last tapped (its pulse ring).
  private(set) var pulseAt: Double = -100
  private var rotTarget: Double = 0
  private var pending: (at: Double, action: Pending)?

  private enum Pending {
    case act(CockpitItem)
    case subRing(CockpitKey)
    case back
  }

  /// Something is moving: the view draws every frame (otherwise it can slow down).
  var isActive: Bool {
    mode != .idle || on.x > 0.002 || panels.contains { $0.x > 0.002 } || cardWidth.x > 0.002 || abs(press.x) > 0.002
      || time - pulseAt < 0.6 || transcript.x > 0.002 || thinking.x > 0.002 || listening.x > 0.002 || crumb.x > 0.002
      || abs(rot.x - rotTarget) > 0.05
  }

  /// The share of the fan that is out (the leading panel), 0…1.
  var fan: Double { panels.map(\.unit).max() ?? 0 }

  // MARK: - input

  /// A touch at `p` (in the same space as the core's centre `c`). Touches act on the way down, as in the prototype.
  mutating func tap(at p: CGPoint, center c: CGPoint) -> [CockpitEffect] {
    let g = CockpitGeometry.self
    if hypot(p.x - c.x, p.y - c.y) < g.coreHit { return tapCore() }
    if mode == .listen || mode == .confirm {
      if hypot(p.x - (c.x + g.cancelOffset), p.y - c.y) < g.cancelHit { return close() }
      if canSwitchLanguage, hypot(p.x - (c.x + g.langOffset), p.y - c.y) < g.cancelHit {
        words = ""
        wordsAt = nil
        set(.listen)
        return [.switchLanguage]
      }
    }
    if mode == .menu {
      if selected != nil { return [] }
      if level == 2, hypot(p.x - c.x, p.y - (c.y - g.crumbDistance)) < g.crumbHit {
        goBack()
        return []
      }
      for (i, it) in items.enumerated() {
        let q = g.point(g.panelMid, it.angle, around: c)
        if hypot(p.x - q.x, p.y - q.y) < g.panelHit {
          select(i)
          return []
        }
      }
      return close()
    }
    return mode == .idle ? [] : close()
  }

  /// The core: open, stop listening and wait for the send, send now, or close.
  mutating func tapCore() -> [CockpitEffect] {
    press.v = -3
    pulseAt = time
    switch mode {
    case .idle:
      open(CockpitItem.firstRing(), level: 1)
      return [.opened]
    case .listen:
      if words.isEmpty { return close() }
      set(.confirm)
      return []
    case .confirm:
      return send()
    default:
      return close()
    }
  }

  /// VoiceOver and the tests: act on a panel by its key, as a tap on it would.
  mutating func activate(_ key: CockpitKey) {
    guard mode == .menu, selected == nil, let i = items.firstIndex(where: { $0.key == key }) else { return }
    select(i)
  }

  /// VoiceOver: the second ring's way back.
  mutating func back() {
    guard mode == .menu, level == 2, selected == nil else { return }
    goBack()
  }

  /// Start listening for `flow` without the menu (a long press on the core).
  mutating func startListening(_ flow: CockpitFlow) -> [CockpitEffect] {
    guard mode == .idle else { return [] }
    listen(flow)
    return [.listen(flow)]
  }

  /// The recogniser's words so far.
  mutating func heard(_ text: String) {
    guard mode == .listen || mode == .confirm else { return }
    let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard t != words else { return }
    words = t
    wordsAt = time
    if mode == .confirm && !t.isEmpty { set(.listen) }   // they went on talking: listen again
  }

  /// The server's corrected words, while they wait for 发送 (flag off): replace what is shown, stay waiting.
  mutating func correct(_ text: String) {
    guard mode == .confirm else { return }
    let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
    if !t.isEmpty { words = t }
  }

  /// The reply is ready (think → card). Also from idle: the cockpit was closed while a write was on its way, and its result
  /// (with 撤销) must still be seen.
  mutating func showCard() {
    guard mode == .think || mode == .idle else { return }
    fanOpen = false
    selected = nil
    set(.card)
  }

  /// Listening for too long (the recogniser's 60 s): stop and go on as if the core was tapped.
  mutating func timeUp() -> [CockpitEffect] {
    guard mode == .listen else { return [] }
    if words.isEmpty { return close() + [.gaveUp] }
    set(.confirm)
    return []
  }

  /// 发送 (the confirm state's button, and the core there).
  mutating func sendNow() -> [CockpitEffect] {
    guard mode == .confirm || (mode == .listen && !words.isEmpty) else { return [] }
    return send()
  }

  /// A quick meal was logged at once (no voice): think until the card is ready.
  mutating func beginThinking(_ flow: CockpitFlow) {
    self.flow = flow
    fanOpen = false
    selected = nil
    set(.think)
  }

  /// Anything that ends the cockpit from outside (a failure, a page opened, the tab changed).
  @discardableResult
  mutating func close() -> [CockpitEffect] {
    let wasListening = mode == .listen || mode == .confirm
    let wasIdle = mode == .idle
    fanOpen = false
    pending = nil
    selected = nil
    set(.idle)
    var out: [CockpitEffect] = []
    if wasListening { out.append(.stopListening) }
    if !wasIdle { out.append(.closed) }
    return out
  }

  // MARK: - frames

  /// Moves time on by `dt` seconds (split into steps of at most 1/120 s, as the prototype's 8 ms).
  mutating func step(_ dt: Double) -> [CockpitEffect] {
    var out: [CockpitEffect] = []
    var left = max(0, min(dt, 0.25))   // a long stall (background) does not replay seconds of motion
    while left > 0 {
      let d = min(CockpitTiming.maxStep, left)
      out += tick(d)
      left -= d
    }
    return out
  }

  private mutating func tick(_ dt: Double) -> [CockpitEffect] {
    time += dt
    since += dt
    fanSince += dt
    var out: [CockpitEffect] = []
    if let p = pending, time >= p.at {
      pending = nil
      out += fire(p.action)
    }
    switch mode {
    case .listen:
      if let w = wordsAt, !words.isEmpty, time - w > CockpitTiming.pause {
        set(.confirm)
      } else if words.isEmpty, since > CockpitTiming.noWords {
        out += close()
        out.append(.gaveUp)
      }
    case .confirm:
      if autoSend && since > CockpitTiming.autoSend { out += send() }
    default:
      break
    }
    springs(dt)
    return out
  }

  private mutating func springs(_ dt: Double) {
    let m = mode
    on.step(to: m == .idle ? 0 : 1, k: 110, c: 21, dt: dt)
    for i in 0..<panels.count {
      let it = i < items.count ? items[i] : nil
      let delay = reduceMotion ? 0 : abs(it?.angle ?? 0) / 36 * CockpitTiming.stagger
      let target: Double = (it != nil && fanOpen && fanSince >= delay) ? 1 : 0
      if reduceMotion { fade(&panels[i], to: target, dt: dt) }
      else { panels[i].step(to: target, k: fanOpen ? 150 : 300, c: fanOpen ? 15.5 : 32, dt: dt) }
    }
    sel.step(to: selected != nil ? 1 : 0, k: 420, c: 26, dt: dt)
    let crumbOn: Double = m == .menu && level == 2 && fanOpen ? 1 : 0
    if reduceMotion { fade(&crumb, to: crumbOn, dt: dt); rot = CockpitSpring(x: rotTarget, v: 0) }
    else { crumb.step(to: crumbOn, k: 220, c: 21, dt: dt); rot.step(to: rotTarget, k: 80, c: 13, dt: dt) }
    listening.step(to: m == .listen ? 1 : 0, k: 90, c: 19, dt: dt)
    envelope.step(to: m == .listen ? max(0.25, voiceLevel) : 0, k: 160, c: 25, dt: dt)
    confirming.step(to: m == .confirm ? 1 : 0, k: 140, c: 24, dt: dt)
    thinking.step(to: m == .think ? 1 : 0, k: 90, c: 19, dt: dt)
    transcript.step(to: m == .listen || m == .confirm ? 1 : 0, k: 120, c: 22, dt: dt)
    let card = m == .card
    if reduceMotion {
      fade(&cardWidth, to: card ? 1 : 0, dt: dt)
      fade(&cardHeight, to: card ? 1 : 0, dt: dt)
    } else {
      cardWidth.step(to: card ? 1 : 0, k: card ? 260 : 120, c: card ? 32 : 22, dt: dt)
      cardHeight.step(to: card && cardWidth.x > 0.6 ? 1 : 0, k: card ? 120 : 260, c: card ? 16.5 : 32, dt: dt)
    }
    press.step(to: 0, k: 320, c: 18, dt: dt)
    if !reduceMotion {
      let speed = 1 + 5 * thinking.unit + 1.5 * on.unit + 2 * listening.unit
      tickAngle += dt * 1000 * 0.02 * speed
      segmentAngle -= dt * 1000 * 0.035 * speed
    }
  }

  /// Reduced motion: a quick fade with no overshoot (critically damped).
  private func fade(_ s: inout CockpitSpring, to target: Double, dt: Double) {
    s.step(to: target, k: 400, c: 40, dt: dt)
  }

  // MARK: - transitions

  private mutating func set(_ m: CockpitMode) {
    mode = m
    since = 0
  }

  private mutating func open(_ items: [CockpitItem], level: Int) {
    self.items = items
    self.level = level
    fanOpen = true
    fanSince = 0
    selected = nil
    sel = CockpitSpring()
    panels = [CockpitSpring](repeating: CockpitSpring(), count: 5)
    set(.menu)
  }

  private mutating func select(_ i: Int) {
    selected = i
    pending = (time + CockpitTiming.select, .act(items[i]))
  }

  private mutating func goBack() {
    fanOpen = false
    pending = (time + CockpitTiming.back, .back)
  }

  private mutating func listen(_ f: CockpitFlow) {
    flow = f
    words = ""
    wordsAt = nil
    fanOpen = false
    selected = nil
    set(.listen)
  }

  private mutating func send() -> [CockpitEffect] {
    set(.think)
    return [.send(flow)]
  }

  private mutating func fire(_ p: Pending) -> [CockpitEffect] {
    switch p {
    case .back:
      if !reduceMotion { rotTarget -= 36 }
      open(CockpitItem.firstRing(), level: 1)
      return []
    case .subRing(let k):
      if !reduceMotion { rotTarget += 36 }
      crumbIcon = k == .meal ? .soup : .dumbbell
      open(k == .meal ? CockpitItem.mealRing(meals) : CockpitItem.trainRing(), level: 2)
      return []
    case .act(let it):
      return act(it.key)
    }
  }

  private mutating func act(_ k: CockpitKey) -> [CockpitEffect] {
    switch k {
    case .meal, .train:
      fanOpen = false
      pending = (time + CockpitTiming.subRing, .subRing(k))
      return []
    case .talk:
      listen(.qa)
      return [.listen(.qa)]
    case .mealTalk:
      listen(.meal)
      return [.listen(.meal)]
    case .trainTalk:
      listen(.set)
      return [.listen(.set)]
    case .template:
      beginThinking(.meal)
      return [.run(k)]
    default:
      return close() + [.run(k)]
    }
  }
}
