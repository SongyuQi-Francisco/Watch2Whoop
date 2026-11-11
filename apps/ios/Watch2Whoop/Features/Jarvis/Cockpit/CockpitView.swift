import SwiftUI

/// The cockpit's colours: used on the Jarvis key and what grows out of it only (the rest of the app keeps its own tokens).
/// jarvis-cockpit.html: A (ice accent with a soft glow), INK, DIM, GOOD, PANEL.
enum CockpitPalette {
  static let accent = Color(red: 0x8F / 255, green: 0xDC / 255, blue: 0xFF / 255)
  static let ink = Color(red: 0xF4 / 255, green: 0xF4 / 255, blue: 0xF6 / 255)
  static let dim = Color(red: 0x8D / 255, green: 0x8D / 255, blue: 0x94 / 255)
  static let good = Color(red: 0x72 / 255, green: 0xBF / 255, blue: 0x8E / 255)
  static let panel = Color(red: 0x0B / 255, green: 0x0E / 255, blue: 0x12 / 255)
  static let core = Color(red: 0x08 / 255, green: 0x09 / 255, blue: 0x0B / 255)
  /// Text on a lit (selected) panel.
  static let onAccent = Color(red: 0x04 / 255, green: 0x12 / 255, blue: 0x1A / 255)
  /// The earlier sets of an exercise (the set card's dots).
  static let past = Color(red: 0x5F / 255, green: 0xB7 / 255, blue: 0xC2 / 255)
}

/// The Jarvis cockpit over the tabs (2.0.1): the core, the panels, listening, thinking and the reply card. Drawn with one
/// Canvas per frame from the machine's springs (TimelineView); the card is real SwiftUI (buttons, VoiceOver).
struct CockpitView: View {
  @ObservedObject var controller: CockpitController
  let center: CGPoint
  let size: CGSize
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  @State private var downAt: Date?
  @State private var downPoint: CGPoint = .zero
  @State private var holdWork: DispatchWorkItem?
  @State private var held = false

  static let holdSeconds = 0.35

  private var full: Bool { controller.mode != .idle }

  var body: some View {
    TimelineView(.animation(minimumInterval: controller.active ? nil : 1.0 / 30, paused: !controller.active && reduceMotion)) { tl in
      let _ = controller.frame(tl.date.timeIntervalSinceReferenceDate)
      let m = controller.machine
      ZStack(alignment: .topLeading) {
        CockpitCanvas(m: m, center: center, time: tl.date.timeIntervalSinceReferenceDate)
          .frame(width: size.width, height: size.height)
          .contentShape(CockpitHitShape(center: center, full: full))
          .gesture(touch)
          .accessibilityHidden(true)
        transcript(m)
        if let r = controller.reply, m.cardWidth.x > 0.02 {
          CockpitCardFrame(reply: r, controller: controller, widthShare: m.cardWidth.unit, heightShare: m.cardHeight.x,
                           reduceMotion: m.reduceMotion)
            .position(x: center.x, y: center.y - CockpitGeometry.cardGap - r.height / 2)
            .frame(width: size.width, height: size.height, alignment: .topLeading)
        }
      }
    }
    .frame(width: size.width, height: size.height)
    .overlay(accessibilityLayer)
    .onAppear { controller.machine.reduceMotion = reduceMotion }
    .onChange(of: reduceMotion) { controller.machine.reduceMotion = $0 }
  }

  // MARK: - transcript

  @ViewBuilder private func transcript(_ m: CockpitMachine) -> some View {
    let o = m.transcript.unit
    if o > 0.01 {
      let words = m.words
      let live = m.mode == .listen
      let cut = live ? max(0, words.count - 2) : words.count
      VStack(spacing: 10) {
        (Text(String(words.prefix(cut))).foregroundColor(CockpitPalette.ink)
         + Text(String(words.dropFirst(cut))).foregroundColor(CockpitPalette.accent.opacity(0.75)))
          .font(.system(size: 22, weight: .semibold))
          .multilineTextAlignment(.center)
          .lineLimit(2)
          .frame(minHeight: 28)
        Text(live ? tr("说完停一下，自动发送", "Pause when done; it sends itself") : tr("点文字可修改", "Tap the words to edit"))
          .font(.system(size: 11, weight: .medium)).kerning(0.3)
          .foregroundColor(CockpitPalette.dim)
      }
      .frame(width: min(size.width - 48, 354))
      .contentShape(Rectangle())
      .onTapGesture { controller.editWords() }
      .opacity(o)
      .offset(y: m.reduceMotion ? 0 : -10 * (1 - o))
      .position(x: center.x, y: center.y - CockpitGeometry.transcriptRise + 6)
      .accessibilityElement(children: .combine)
      .accessibilityLabel(words.isEmpty ? tr("在听", "Listening") : words)
      .accessibilityHint(tr("点按可修改文字", "Tap to edit the words"))
      .accessibilityAddTraits(.updatesFrequently)
    }
  }

  // MARK: - touches

  /// Touches act on the way down (as the prototype), except on the closed core, where a hold talks at once.
  private var touch: some Gesture {
    DragGesture(minimumDistance: 0)
      .onChanged { v in
        if downAt == nil {
          downAt = Date()
          downPoint = v.location
          JarvisHaptics.prepare()
          let onCore = hypot(v.location.x - center.x, v.location.y - center.y) < CockpitGeometry.coreHit
          if controller.mode == .idle && onCore {
            let w = DispatchWorkItem { held = true; controller.holdCore() }
            holdWork = w
            DispatchQueue.main.asyncAfter(deadline: .now() + Self.holdSeconds, execute: w)
          } else {
            controller.tap(at: v.location, center: center)
          }
        } else if hypot(v.location.x - downPoint.x, v.location.y - downPoint.y) > 10 {
          holdWork?.cancel()
        }
      }
      .onEnded { v in
        let pending = holdWork != nil && !(holdWork?.isCancelled ?? true) && !held
        holdWork?.cancel()
        holdWork = nil
        if pending && hypot(v.location.x - downPoint.x, v.location.y - downPoint.y) <= 10 {
          controller.tap(at: downPoint, center: center)
        }
        downAt = nil
        held = false
      }
  }

  // MARK: - VoiceOver

  /// The Canvas is one picture: VoiceOver gets one element per key at the key's place.
  private var accessibilityLayer: some View {
    let m = controller.machine
    let g = CockpitGeometry.self
    return ZStack(alignment: .topLeading) {
      element(at: center, size: 58,
              label: controller.mode == .idle ? "Jarvis" : (controller.mode == .confirm ? tr("立即发送", "Send now") : tr("收起", "Close")),
              hint: controller.mode == .idle && controller.inApp ? tr("点按展开功能键；按住直接说话", "Tap for the keys; hold to talk") : nil) {
        controller.tapCore()
      }
      if controller.mode == .menu {
        ForEach(Array(m.items.enumerated()), id: \.offset) { _, it in
          element(at: g.point(g.panelMid, it.angle, around: center), size: 56,
                  label: [it.title, it.sub].compactMap { $0 }.joined(separator: " "), hint: nil) { controller.activate(it.key) }
        }
        if controller.level == 2 {
          element(at: CGPoint(x: center.x, y: center.y - g.crumbDistance), size: 34, label: tr("返回", "Back"), hint: nil) {
            controller.back()
          }
        }
      }
      if controller.mode == .listen || controller.mode == .confirm {
        element(at: CGPoint(x: center.x + g.cancelOffset, y: center.y), size: 40, label: tr("取消", "Cancel"), hint: nil) {
          controller.close()
        }
      }
    }
    .frame(width: size.width, height: size.height, alignment: .topLeading)
    .allowsHitTesting(false)
  }

  private func element(at p: CGPoint, size s: CGFloat, label: String, hint: String?, action: @escaping () -> Void) -> some View {
    Color.clear
      .frame(width: s, height: s)
      .position(p)
      .accessibilityElement()
      .accessibilityLabel(label)
      .accessibilityHint(hint ?? "")
      .accessibilityAddTraits(.isButton)
      .accessibilityAction { action() }
  }
}

/// Where the cockpit takes touches: only the core while it is closed (the tabs stay usable), the whole screen otherwise.
struct CockpitHitShape: Shape {
  var center: CGPoint
  var full: Bool

  func path(in rect: CGRect) -> Path {
    if full { return Path(rect) }
    let r = CockpitGeometry.coreHit
    return Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: 2 * r, height: 2 * r))
  }
}

// MARK: - the drawing

/// Everything except the card and the transcript, from one machine state (jarvis-cockpit.html `draw`).
struct CockpitCanvas: View {
  let m: CockpitMachine
  let center: CGPoint
  let time: TimeInterval

  private static let icons: [Icon.Name] = [.soup, .dumbbell, .mic, .camera, .messageCircle, .ellipsis, .zap, .clock, .calendarDays, .x]

  var body: some View {
    Canvas(rendersAsynchronously: false) { ctx, size in
      draw(&ctx, size)
    } symbols: {
      ForEach(Self.icons, id: \.self) { n in
        Icon(n, size: 22).foregroundColor(CockpitPalette.ink).tag(n.rawValue)
        Icon(n, size: 22).foregroundColor(CockpitPalette.onAccent).tag(n.rawValue + "-on")
      }
    }
  }

  private typealias G = CockpitGeometry

  /// An icon or a label, drawn above the glow layer.
  private struct Mark {
    var symbol: String?
    var text: Text?
    var at: CGPoint
    var rect: CGRect?
    var opacity: Double

    init(symbol: String, at: CGPoint, rect: CGRect? = nil, opacity: Double) {
      self.symbol = symbol
      self.at = at
      self.rect = rect
      self.opacity = opacity
    }

    init(text: Text, at: CGPoint, opacity: Double) {
      self.text = text
      self.at = at
      self.opacity = opacity
    }
  }
  private var A: Color { CockpitPalette.accent }

  private func cl(_ x: Double) -> Double { min(1, max(0, x)) }
  private func ease(_ x: Double) -> Double { 1 - pow(1 - cl(x), 3) }
  private func P(_ r: CGFloat, _ d: Double) -> CGPoint { G.point(r, d, around: center) }

  /// An arc around the core, `a0` to `a1` clockwise (sampled: no doubt about the direction of y).
  private func arc(_ r: CGFloat, _ a0: Double, _ a1: Double, into p: inout Path, move: Bool = true) {
    let n = max(2, Int(abs(a1 - a0) / 3) + 1)
    for i in 0...n {
      let q = P(r, a0 + (a1 - a0) * Double(i) / Double(n))
      if i == 0 && move { p.move(to: q) } else { p.addLine(to: q) }
    }
  }

  private func arcPath(_ r: CGFloat, _ a0: Double, _ a1: Double) -> Path {
    var p = Path()
    arc(r, a0, a1, into: &p)
    return p
  }

  private func sector(_ ri: CGFloat, _ ro: CGFloat, _ a0: Double, _ a1: Double) -> Path {
    var p = Path()
    arc(ri, a0, a0, into: &p)
    arc(ro, a0, a1, into: &p, move: false)
    arc(ri, a1, a0, into: &p, move: false)
    p.closeSubpath()
    return p
  }

  private func line(_ r0: CGFloat, _ r1: CGFloat, _ d: Double) -> Path {
    var p = Path()
    p.move(to: P(r0, d))
    p.addLine(to: P(r1, d))
    return p
  }

  /// The inverted triangle (vertices at 60°, 180°, 300°).
  private func triangle(_ c: CGPoint, _ r: CGFloat) -> Path {
    var p = Path()
    for (i, g) in [60.0, 180, 300].enumerated() {
      let a = g * .pi / 180
      let q = CGPoint(x: c.x + r * CGFloat(sin(a)), y: c.y - r * CGFloat(cos(a)))
      if i == 0 { p.move(to: q) } else { p.addLine(to: q) }
    }
    p.closeSubpath()
    return p
  }

  /// The soft ring that moves with the voice.
  private func wave(_ r0: CGFloat, _ amp: Double, _ ph: Double) -> Path {
    var p = Path()
    for i in 0...120 {
      let th = Double(i) * 3, x = th * .pi / 180
      let r = r0 + CGFloat(amp * (sin(3 * x + ph) * sin(ph * 0.7) + 0.6 * sin(5 * x - ph * 1.4)))
      let q = P(r, th)
      if i == 0 { p.move(to: q) } else { p.addLine(to: q) }
    }
    p.closeSubpath()
    return p
  }

  private func stroke(_ ctx: inout GraphicsContext, _ p: Path, _ c: Color, _ w: CGFloat, _ o: Double) {
    guard o > 0.005 else { return }
    ctx.stroke(p, with: .color(c.opacity(cl(o))), style: StrokeStyle(lineWidth: w, lineCap: .round, lineJoin: .round))
  }

  private func draw(_ ctx: inout GraphicsContext, _ size: CGSize) {
    let T = m.time * 1000   // the prototype's clock is in ms
    let on = m.on.unit
    let reduce = m.reduceMotion
    // the wash
    if on > 0.005 {
      ctx.fill(Path(CGRect(origin: .zero, size: size)), with: .color(.black.opacity(0.86 * on)))
    }
    var labels: [Mark] = []
    ctx.drawLayer { g in
      g.addFilter(.shadow(color: A.opacity(0.4), radius: 6))
      let F = m.fan
      // the tick ring outside the panels; it turns one notch per level
      if F > 0.02 {
        let span = G.tickSpan * (reduce ? 1 : ease(F)), rot = m.rot.x + (reduce ? 0 : T * 0.004)
        for i in 0..<90 {
          var an = (Double(i) * 4 + rot).truncatingRemainder(dividingBy: 360)
          an = ((an + 540).truncatingRemainder(dividingBy: 360)) - 180
          let ab = abs(an)
          guard ab <= span else { continue }
          let major = i % 9 == 0
          stroke(&g, line(G.tickInner, major ? G.tickMajor : G.tickMinor, an), A, 1, (major ? 0.85 : 0.34) * F * cl((span - ab) / 14))
        }
        if let s = m.selected, s < m.items.count {
          let sa = m.items[s].angle
          stroke(&g, arcPath(G.selectArc, sa - 9, sa + 9), A, 2, m.sel.unit * F)
        }
      }
      // the panels: sectors of one ring, fanning out of the core
      for (k, it) in m.items.enumerated() where k < m.panels.count {
        let v = m.panels[k].x
        guard v >= 0.02 else { continue }
        let geo = reduce ? 1 : v, vc = reduce ? 1 : cl(v)
        let hot = m.selected == k, sv = hot ? m.sel.x : 0
        let od = (hot || m.selected == nil) ? 1 : 1 - 0.65 * m.sel.unit
        let ang = it.angle * geo
        let ri = G.panelStart + (G.panelInner - G.panelStart) * CGFloat(geo) + G.selectPush * CGFloat(sv)
        let ro = ri + (G.panelOuter - G.panelInner) * CGFloat(cl(geo * 1.15))
        let hw = G.panelHalfAngle * (0.25 + 0.75 * vc)
        let o = (reduce ? cl(v) : cl(v * 3)) * od
        let d = sector(ri, ro, ang - hw, ang + hw)
        g.fill(d, with: .color(CockpitPalette.panel.opacity(0.94 * o)))
        g.stroke(d, with: .color(A.opacity(0.5 * o)), style: StrokeStyle(lineWidth: 1, lineJoin: .round))
        if hot { g.fill(d, with: .color(A.opacity(cl(sv) * o))) }
        stroke(&g, arcPath(ro, ang - hw + 3.5, ang + hw - 3.5), A, 2, 0.95 * o)
        let co = (reduce ? cl(v) : cl((v - 0.55) / 0.35)) * od
        let c = P((ri + ro) / 2, ang)
        let lit = hot && sv > 0.5
        if let icon = it.icon {
          labels.append(Mark(symbol: icon.rawValue + (lit ? "-on" : ""), at: CGPoint(x: c.x, y: c.y - 7), opacity: co))
          labels.append(Mark(text: Text(it.title).font(.system(size: 10.5, weight: .medium)).kerning(0.3)
                              .foregroundColor(lit ? CockpitPalette.onAccent : CockpitPalette.ink), at: CGPoint(x: c.x, y: c.y + 15), opacity: 0.9 * co))
        } else {
          labels.append(Mark(text: Text(it.title).font(.system(size: 12.5, weight: .semibold))
                              .foregroundColor(lit ? CockpitPalette.onAccent : CockpitPalette.ink), at: CGPoint(x: c.x, y: c.y - 3), opacity: co))
          if let s = it.sub {
            labels.append(Mark(text: Text(s).font(.system(size: 9.5)).foregroundColor(lit ? CockpitPalette.onAccent : CockpitPalette.dim),
                                at: CGPoint(x: c.x, y: c.y + 13), opacity: co))
          }
        }
      }
      // level 2: the chosen key docks above the core (tap it to go back)
      let cv = m.crumb.x
      if cv > 0.02 {
        let cp = P(G.crumbDistance, 0), r = G.crumbRadius * CGFloat(reduce ? 1 : cv)
        let circle = Path(ellipseIn: CGRect(x: cp.x - r, y: cp.y - r, width: 2 * r, height: 2 * r))
        g.fill(circle, with: .color(CockpitPalette.panel.opacity(reduce ? cl(cv) : 1)))
        g.stroke(circle, with: .color(A.opacity(0.8 * (reduce ? cl(cv) : 1))), lineWidth: 1)
        let s = 7 * CGFloat(reduce ? 1 : cl(cv))
        labels.append(Mark(symbol: m.crumbIcon.rawValue, at: cp, rect: CGRect(x: cp.x - s, y: cp.y - s, width: 2 * s, height: 2 * s), opacity: cl(cv)))
      }
      // listening: the soft ring that moves with the voice
      let l = m.listening.unit
      if l > 0.01 {
        let en = 0.25 + 0.75 * m.envelope.unit
        let ph = reduce ? 0 : T / 260, ph2 = reduce ? 2 : -T / 340 + 2
        stroke(&g, wave(40, 5.5 * l * en * (reduce ? 0 : 1), ph), A, 1.6, 0.95 * l)
        stroke(&g, wave(47, 4 * l * en * (reduce ? 0 : 1), ph2), A, 1, 0.4 * l)
      }
      // pause heard: the ring closes in one second, then it sends
      let cf = m.confirming.unit
      if cf > 0.01 {
        let r: CGFloat = 40
        g.stroke(Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: 2 * r, height: 2 * r)), with: .color(A.opacity(0.22 * cf)), lineWidth: 1)
        let sweep = m.mode == .confirm ? max(0.5, 360 * cl(m.since / CockpitTiming.autoSend)) : 360
        stroke(&g, arcPath(r, 0, sweep), A, 2, 0.95 * cf)
      }
      // thinking: one arc going round
      let k2 = m.thinking.unit
      if k2 > 0.01 {
        let r: CGFloat = 40, a1 = reduce ? 0 : T * 0.36
        g.stroke(Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: 2 * r, height: 2 * r)), with: .color(A.opacity(0.22 * k2)), lineWidth: 1)
        stroke(&g, arcPath(r, a1, a1 + 96), A, 2, 0.95 * k2)
      }
      // listening: the × left of the core
      let tr = m.transcript.unit
      if tr > 0.01 {
        let p = CGPoint(x: center.x + G.cancelOffset, y: center.y), r = G.cancelRadius
        let circle = Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2 * r, height: 2 * r))
        g.fill(circle, with: .color(CockpitPalette.panel.opacity(tr)))
        g.stroke(circle, with: .color(Color.white.opacity(0.25 * tr)), lineWidth: 1)
        labels.append(Mark(symbol: Icon.Name.x.rawValue, at: p, rect: CGRect(x: p.x - 7.5, y: p.y - 7.5, width: 15, height: 15), opacity: tr))
      }
      // the reply card's stem: a line leaves the core
      let cw = m.cardWidth.unit
      if cw > 0.02 {
        stroke(&g, line(37, G.cardGap, 0), A, 1, 0.7 * cw)
        let dot = CGPoint(x: center.x, y: center.y - G.cardGap)
        g.fill(Path(ellipseIn: CGRect(x: dot.x - 2.2, y: dot.y - 2.2, width: 4.4, height: 4.4)), with: .color(A.opacity(cw)))
      }
    }
    for mk in labels where mk.opacity > 0.005 {
      var t = ctx
      t.opacity = min(1, mk.opacity)
      if let id = mk.symbol, let sym = t.resolveSymbol(id: id) {
        if let r = mk.rect { t.draw(sym, in: r) } else { t.draw(sym, at: mk.at) }
      } else if let text = mk.text {
        t.draw(text, at: mk.at)
      }
    }
    drawCore(&ctx, T)
  }

  /// The key: tick ring, three segments, inverted triangle with a lit inner triangle, soft glow.
  private func drawCore(_ ctx: inout GraphicsContext, _ T: Double) {
    let c = center, on = m.on.unit
    let pu = (m.time - m.pulseAt) / 0.52
    ctx.drawLayer { g in
      g.addFilter(.shadow(color: A.opacity(0.6), radius: 5))
      g.addFilter(.shadow(color: A.opacity(0.27), radius: 14))
      if pu > 0 && pu < 1 && !m.reduceMotion {
        let r = 29 + 36 * CGFloat(ease(pu))
        g.stroke(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)),
                 with: .color(A.opacity(0.75 * (1 - pu) * (1 - pu))), lineWidth: 1.3)
      }
      let sc = CGFloat(1 + 0.5 * m.press.x)
      g.translateBy(x: c.x, y: c.y)
      g.scaleBy(x: sc, y: sc)
      g.translateBy(x: -c.x, y: -c.y)
      let r = G.coreRadius
      let disc = Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
      g.fill(disc, with: .color(CockpitPalette.core))
      g.stroke(disc, with: .color(A.opacity(0.5 + 0.45 * on)), lineWidth: 1)
      for i in 0..<24 {
        stroke(&g, line(22, i % 6 == 0 ? 26.5 : 25, m.tickAngle + Double(i) * 15), A, 1, i % 6 == 0 ? 0.95 : 0.5)
      }
      for i in 0..<3 {
        let a = m.segmentAngle + Double(i) * 120
        stroke(&g, arcPath(17.5, a, a + 84), A, 1.5, 0.85)
      }
      g.stroke(triangle(c, 11.5), with: .color(CockpitPalette.ink), style: StrokeStyle(lineWidth: 1.6, lineJoin: .round))
      let inner = triangle(c, 4.6)
      let glow = m.reduceMotion ? 1 : 0.6 + 0.4 * sin(T / 700)
      g.fill(inner, with: .color(A.opacity(glow)))
      g.stroke(inner, with: .color(A.opacity(glow)), style: StrokeStyle(lineWidth: 1, lineJoin: .round))
    }
  }
}
