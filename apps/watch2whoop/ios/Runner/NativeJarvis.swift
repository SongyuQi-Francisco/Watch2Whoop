import SwiftUI
import UIKit

// The native Jarvis key and its menu (#247 phase 2, flag native_jarvis; NativeUI.swift hosts it). Same layout and sequence as
// the page's jarvis_dial (web/static/jarvis-entry.js openDial): press = the key squeezes and springs back with a flash in the
// middle; open = three thin rings spread out turning opposite ways, a scan light passes over the arc, the four keys flow out of
// the Jarvis key one after the other, their outlines close and a glow breathes once; close = everything is drawn back in.
// One ink (the page's --text, black / white), springs, transform and opacity only, nothing loops; reduce motion = no motion.
// - iOS 26+: the keys are system Liquid Glass (`.glassEffect(.regular.interactive())` in one GlassEffectContainer); the four keys
//   carry glassEffectIDs, so they come out of (and go back into) the Jarvis key's glass as the system's liquid morph.
// - before iOS 26: `.ultraThinMaterial` discs with the page's thin outline, the keys fly out of the key on the same springs.
// What a key does is decided by the page (native-bridge.js): a tap on the Jarvis key opens this menu only when the page says a
// tap means the menu (Jarvis, online, no recording); otherwise the tap goes to the page. A long press (0.35 s) = "hold".
// Icons: SF Symbols (system icons).

private struct JKey {
  let id, icon, fallback: String
  let deg: Double
}

private let kKeys = [
  JKey(id: "chat", icon: "bubble.left", fallback: "对话", deg: -72), JKey(id: "voice", icon: "waveform", fallback: "语音", deg: -36),
  JKey(id: "photo", icon: "camera.viewfinder", fallback: "拍照", deg: 36), JKey(id: "quick", icon: "bolt", fallback: "快记", deg: 72),
]
private let kArc: CGFloat = 120      // jarvis-entry.js R_ARC
private let kArcDY: CGFloat = 22     // ARC_DY: the arc's centre is this far below the key's centre
private let kHit: [Double] = [0.105, 0.128, 0.151, 0.174]   // DIAL_HIT: when the scan passes each key (s)
private let kHoldS = 0.35            // HOLD_MS
private let kMoveTol: CGFloat = 10   // MOVE_TOL
private let kSub: CGFloat = 46       // a key of the menu

extension View {
  /// onChange without the iOS 17 deprecation warning.
  @ViewBuilder func onChangeCompat<V: Equatable>(_ v: V, _ f: @escaping (V) -> Void) -> some View {
    if #available(iOS 17.0, *) { onChange(of: v) { _, n in f(n) } } else { onChange(of: v, perform: f) }
  }
}

struct NativeJarvisView: View {
  @ObservedObject var model: NativeUIModel
  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  @Namespace private var ns

  @State private var pressed = false
  @State private var flash = false
  @State private var rings = [false, false, false]
  @State private var scan: Double = -84
  @State private var scanOn = false
  @State private var keys = [false, false, false, false]
  @State private var outlines = [false, false, false, false]
  @State private var halo = false
  @State private var gen = 0
  @State private var pressAt: Date?
  @State private var moved = false
  @State private var held = false
  @State private var holdWork: DispatchWorkItem?

  private var st: JarvisState { model.jarvis }
  private var open: Bool { model.menuOpen }
  @Environment(\.colorScheme) private var scheme
  private var ink: Color { st.dark == (scheme == .dark) ? st.theme.text : Theme(nil, dark: scheme == .dark).text }
  private var isDark: Bool { scheme == .dark }
  private var keySize: CGFloat { max(44, min(st.rect.width, 72)) }
  private var c: CGPoint { CGPoint(x: st.rect.midX, y: st.rect.midY) }
  private func pos(_ i: Int) -> CGPoint {
    let r = kKeys[i].deg * .pi / 180
    return CGPoint(x: c.x + CGFloat(sin(r)) * kArc, y: c.y + kArcDY - CGFloat(cos(r)) * kArc)
  }

  var body: some View {
    ZStack(alignment: .topLeading) {
      scrim
      decorations
      keysLayer
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    .onChangeCompat(model.menuOpen) { o in o ? runOpen() : runClose() }
    .onAppear { if model.demo { runDemo() } }
    .onChangeCompat(model.demo) { d in if d { runDemo() } }
  }

  /// Debug builds only (`W2W_NATIVE_UI=…,demo`, NativeUI.swift): press, open, wait, close — five times, for frame timing and
  /// screen recordings without touching the screen.
  @State private var demoRunning = false
  private func runDemo() {
    guard !demoRunning else { return }
    demoRunning = true
    Task { @MainActor in
      try? await Task.sleep(nanoseconds: 2_000_000_000)
      for _ in 0..<5 where model.demo && st.show && st.menu {
        pressed = true
        try? await Task.sleep(nanoseconds: 90_000_000)
        pressed = false
        setOpen(true)
        try? await Task.sleep(nanoseconds: 2_200_000_000)
        setOpen(false)
        try? await Task.sleep(nanoseconds: 1_300_000_000)
      }
      demoRunning = false
    }
  }

  // ------------------------------------------------------------ layers
  private var scrim: some View {
    (isDark ? Color.black.opacity(0.74) : Color(.sRGB, red: 246 / 255, green: 246 / 255, blue: 248 / 255, opacity: 0.86))
      .opacity(open ? 1 : 0)
      .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: open)
      .contentShape(Rectangle())
      .onTapGesture { setOpen(false) }
      .allowsHitTesting(open)
      .accessibilityHidden(true)
  }

  private var decorations: some View {
    let spec: [(d: CGFloat, turn: Double, op: Double, dash: Bool)] = [(80, 28, 0.5, false), (100, -28, 0.4, true), (122, 0, 0.4, true)]
    return ZStack(alignment: .topLeading) {
      ForEach(0..<3, id: \.self) { i in
        Circle()
          .stroke(ink, style: StrokeStyle(lineWidth: 1, dash: spec[i].dash ? [3.5, 3.5] : []))
          .frame(width: spec[i].d, height: spec[i].d)
          .rotationEffect(.degrees(rings[i] ? spec[i].turn : 0))
          .scaleEffect(rings[i] ? 1 : 0.6)
          .opacity(rings[i] ? spec[i].op : 0)
          .position(c)
      }
      // the scan light: a thin bright edge with a faint trail, between r 30 and r 134 round the arc's centre
      Circle()
        .fill(AngularGradient(gradient: Gradient(stops: [
          .init(color: .clear, location: 0), .init(color: ink.opacity(0.1), location: 25 / 360), .init(color: ink.opacity(0.6), location: 26 / 360),
          .init(color: .clear, location: 26.4 / 360), .init(color: .clear, location: 1),
        ]), center: .center, startAngle: .degrees(-116), endAngle: .degrees(244)))
        .frame(width: 268, height: 268)
        .mask(Circle().strokeBorder(Color.black, lineWidth: 104))
        .rotationEffect(.degrees(scan))
        .opacity(scanOn ? 1 : 0)
        .position(x: c.x, y: c.y + kArcDY)
      ZStack(alignment: .topLeading) {
        ForEach(0..<4, id: \.self) { i in
          RadialGradient(colors: [ink.opacity(isDark ? 0.3 : 0.28), ink.opacity(0)], center: .center, startRadius: 0, endRadius: 42)
            .frame(width: 84, height: 84)
            .position(pos(i))
        }
      }
      .opacity(halo ? 0.5 : 0)
      Circle().strokeBorder(ink, lineWidth: 1.25).frame(width: 68, height: 68)
        .scaleEffect(pressed ? 0.86 : 1).opacity(open ? 0.35 : 0)
        .animation(reduceMotion ? nil : .spring(response: 0.25, dampingFraction: 0.55), value: pressed)
        .position(c)
    }
    .allowsHitTesting(false)
    .accessibilityHidden(true)
  }

  @ViewBuilder private var keysLayer: some View {
    #if compiler(>=6.2)   // the iOS 26 SDK (Xcode 26); an older Xcode still builds, with the material keys only
    if #available(iOS 26.0, *), !model.material {
      GlassEffectContainer(spacing: 28) { keysContent(glass: true) }
    } else {
      keysContent(glass: false)
    }
    #else
    keysContent(glass: false)
    #endif
  }

  private func keysContent(glass: Bool) -> some View {
    ZStack(alignment: .topLeading) {
      ForEach(0..<4, id: \.self) { i in
        if glass {
          if keys[i] { subKey(i, glass: true).position(pos(i)) }
        } else {
          let p = pos(i)
          subKey(i, glass: false)
            .scaleEffect(keys[i] ? 1 : 0.42)
            .offset(x: keys[i] ? 0 : c.x - p.x, y: keys[i] ? 0 : c.y - p.y)
            .opacity(keys[i] ? 1 : 0)
            .position(p)
            .allowsHitTesting(keys[i])
        }
        Circle().strokeBorder(ink, lineWidth: 1.5).frame(width: kSub, height: kSub)
          .scaleEffect(outlines[i] ? 1 : 0.85).opacity(outlines[i] ? 0.9 : 0)
          .position(pos(i)).allowsHitTesting(false)
        Text(st.label(kKeys[i].id, kKeys[i].fallback))
          .font(.system(size: 11, weight: .semibold)).foregroundColor(ink)
          .fixedSize()
          .opacity(keys[i] ? 1 : 0)
          .position(x: pos(i).x, y: pos(i).y + kSub / 2 + 12)
          .allowsHitTesting(false)
          .accessibilityHidden(true)
      }
      mainKey(glass: glass).position(c)
      Circle().fill(ink).frame(width: keySize, height: keySize).opacity(flash ? 0.9 : 0)
        .position(c).allowsHitTesting(false).accessibilityHidden(true)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
  }

  private func subKey(_ i: Int, glass: Bool) -> some View {
    Button {
      UIImpactFeedbackGenerator(style: .light).impactOccurred()
      setOpen(false)
      model.send("action", ["kind": "jarvis", "id": kKeys[i].id])
    } label: {
      Image(systemName: kKeys[i].icon)
        .font(.system(size: 19, weight: .medium))
        .foregroundColor(ink)
        .frame(width: kSub, height: kSub)
        .contentShape(Circle())
        .modifier(KeySurface(glass: glass, ink: ink, filled: false, dark: isDark, id: kKeys[i].id, ns: ns))
    }
    .buttonStyle(PressScale())
    .accessibilityLabel(st.label(kKeys[i].id, kKeys[i].fallback))
  }

  private func mainKey(glass: Bool) -> some View {
    let rec = st.voice != "off"
    let back = st.dark == isDark ? st.theme.bg : Theme(nil, dark: isDark).bg
    return ZStack {
      Image(systemName: "atom").opacity(!open && !rec ? 1 : 0).scaleEffect(open ? 0.7 : 1)
      Image(systemName: "chevron.down").opacity(open && !rec ? 1 : 0).scaleEffect(open ? 1 : 0.7)
      Image(systemName: "mic.fill").opacity(rec ? 1 : 0)
    }
    .font(.system(size: 22, weight: .medium))
    .foregroundColor(rec ? back : ink)
    .frame(width: keySize, height: keySize)
    .contentShape(Circle())
    .modifier(KeySurface(glass: glass, ink: ink, filled: rec, dark: isDark, id: "jarvis", ns: ns))
    .scaleEffect(pressed ? 0.92 : 1)
    .animation(reduceMotion ? nil : (pressed ? .easeOut(duration: 0.05) : .spring(response: 0.32, dampingFraction: 0.45)), value: pressed)
    .animation(reduceMotion ? nil : .spring(response: 0.3, dampingFraction: 0.7), value: open)
    .opacity(st.offline ? 0.4 : 1)
    .gesture(pressGesture)
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(open ? st.label("close", "收起 Jarvis 菜单") : st.label("key", "Jarvis"))
    .accessibilityAddTraits(.isButton)
    .accessibilityAction { tap() }
  }

  // ------------------------------------------------------------ press: tap / hold
  private var pressGesture: some Gesture {
    DragGesture(minimumDistance: 0, coordinateSpace: .global)
      .onChanged { v in
        if pressAt == nil {
          pressAt = Date(); moved = false; held = false; pressed = true
          let w = DispatchWorkItem { hold() }
          holdWork = w
          DispatchQueue.main.asyncAfter(deadline: .now() + kHoldS, execute: w)
        } else if !moved && hypot(v.translation.width, v.translation.height) > kMoveTol {
          moved = true; pressed = false; holdWork?.cancel()
        }
      }
      .onEnded { _ in
        holdWork?.cancel()
        let wasHeld = held, wasMoved = moved
        pressAt = nil; pressed = false; held = false; moved = false
        if !wasHeld && !wasMoved { tap() }
      }
  }

  private func hold() {
    guard pressAt != nil, !moved else { return }
    held = true; pressed = false
    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    if open { setOpen(false) }
    model.send("action", ["kind": "jarvis", "id": "hold"])
  }

  private func tap() {
    if open { setOpen(false); return }
    if st.menu { setOpen(true) } else { model.send("action", ["kind": "jarvis", "id": "tap"]) }
  }

  private func setOpen(_ o: Bool) { if model.menuOpen != o { model.menuOpen = o } }

  // ------------------------------------------------------------ the sequence (jarvis-entry.js openDial / closeDial)
  private func later(_ s: Double, _ f: @escaping () -> Void) {
    let g = gen
    DispatchQueue.main.asyncAfter(deadline: .now() + s) { if g == gen { f() } }
  }

  private func runOpen() {
    gen += 1
    model.meter.mark("jarvis-open", seconds: 0.6)
    UIImpactFeedbackGenerator(style: .light).impactOccurred()
    if reduceMotion {
      rings = [true, true, true]; keys = [true, true, true, true]; outlines = keys
      return
    }
    flash = true
    later(0.016) { withAnimation(.easeOut(duration: 0.1)) { flash = false } }
    for i in 0..<3 {
      withAnimation(.spring(response: 0.3, dampingFraction: 0.8).delay(0.03 + Double(i) * 0.015)) { rings[i] = true }
    }
    later(0.07) {
      withAnimation(.easeOut(duration: 0.02)) { scanOn = true }
      withAnimation(.linear(duration: 0.2)) { scan = 84 }
    }
    later(0.25) { withAnimation(.easeIn(duration: 0.06)) { scanOn = false } }
    for i in 0..<4 {
      withAnimation(.spring(response: 0.34, dampingFraction: 0.62).delay(kHit[i] - 0.01)) { keys[i] = true }
      withAnimation(.spring(response: 0.24, dampingFraction: 0.55).delay(kHit[i] + 0.07)) { outlines[i] = true }
    }
    later(0.24) { withAnimation(.easeInOut(duration: 0.1)) { halo = true } }
    later(0.36) { withAnimation(.easeInOut(duration: 0.22)) { halo = false } }
  }

  private func runClose() {
    gen += 1
    model.meter.mark("jarvis-close", seconds: 0.35)
    let a: Animation? = reduceMotion ? nil : .spring(response: 0.22, dampingFraction: 0.9)
    withAnimation(a) {
      keys = [false, false, false, false]; outlines = keys; rings = [false, false, false]; halo = false; scanOn = false; flash = false
    }
    later(0.25) { scan = -84 }
  }
}

/// The disc under a key: system Liquid Glass on iOS 26+, otherwise a thin material with the page's outline.
private struct KeySurface: ViewModifier {
  let glass: Bool
  let ink: Color
  let filled: Bool
  let dark: Bool
  let id: String
  let ns: Namespace.ID

  @ViewBuilder func body(content: Content) -> some View {
    #if compiler(>=6.2)
    if #available(iOS 26.0, *), glass {
      content
        .glassEffect(filled ? Glass.regular.tint(ink).interactive() : Glass.regular.interactive(), in: Circle())
        .glassEffectID(id, in: ns)
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
      .overlay(Circle().strokeBorder(ink.opacity(filled ? 0 : 0.85), lineWidth: 1.5))
      .shadow(color: .black.opacity(dark ? 0.5 : 0.12), radius: 10, x: 0, y: 4)
  }
}

/// A key squeezes while pressed and springs back.
struct PressScale: ButtonStyle {
  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .scaleEffect(configuration.isPressed ? 0.92 : 1)
      .animation(configuration.isPressed ? .easeOut(duration: 0.05) : .spring(response: 0.3, dampingFraction: 0.5), value: configuration.isPressed)
  }
}
