import SwiftUI

/// Option A's reveal rule for the tab roots that have to scroll (记录 · 训练 / 饮食, 圈子; drilldown README section 8,
/// rule 1 form 2): no blank floor under the first glance, but a small joint with a down chevron, and the next block cut by
/// the fold with about `LevelParams.peekTarget` of it showing above the Jarvis key. The first glances were composed to end
/// right above the key, so on each phone the page measures the room above the key and where its glance ends, and the glance
/// gives up just enough height: first its gaps (down to `LevelParams.minGapScale`), then, only where that is not enough (the
/// 667 pt phones, whose gaps are already short), its dial or ring (at most `LevelParams.maxHeroShrink`). Unit-tested in
/// `ReachabilityTests`.
enum FoldFit {
  /// The scroll content's coordinate space the glance's end is measured in.
  static let space = "fold"

  /// What a first glance gives up: the factor of its gaps and the points its dial / ring loses.
  struct Fit: Equatable {
    var scale: CGFloat = 1
    var shrink: CGFloat = 0
  }

  /// `bottom`: where the glance ends in the scroll content, measured with `applied`; `gaps`: the sum of its gaps at factor
  /// 1; `visible`: the content height above the key band. Not measured yet: `applied`. The gaps never grow above the
  /// approved rhythm (factor 1) and the hero never grows above its own size.
  static func fit(bottom: CGFloat, gaps: CGFloat, applied: Fit, visible: CGFloat, joint: CGFloat = LevelParams.joint,
                  peek: CGFloat = LevelParams.peekTarget, floor: CGFloat = LevelParams.minGapScale,
                  maxShrink: CGFloat = LevelParams.maxHeroShrink) -> Fit {
    guard bottom > 0, visible > 0, gaps > 0 else { return applied }
    // the glance with its full hero and without gaps (the same whatever was applied when it was measured)
    let fixed = bottom - gaps * applied.scale + applied.shrink
    let room = visible - peek - joint
    let scale = min(1, max(floor, (room - fixed) / gaps))
    let deficit = fixed + gaps * scale - room
    return Fit(scale: scale, shrink: min(maxShrink, max(0, deficit)).rounded(.up))
  }
}

/// The joint between a first glance and the block below it: one small down chevron, the only hint (drilldown `.joint`).
struct FoldJoint: View {
  var body: some View {
    Icon(.chevronDown, size: 15).foregroundColor(Palette.text2)
      .frame(maxWidth: .infinity)
      .frame(height: LevelParams.joint)
      .accessibilityHidden(true)
  }
}

/// Hands its content the fit of the scroll view it sits in (for a page whose glance is drawn by the page itself).
struct FoldScaled<Content: View>: View {
  @Environment(\.foldFit) private var fit
  @ViewBuilder let content: (FoldFit.Fit) -> Content

  var body: some View { content(fit) }
}

private struct FoldGlanceEndKey: PreferenceKey {
  static var defaultValue: CGFloat = 0
  static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

private struct FoldFitKey: EnvironmentKey {
  static let defaultValue = FoldFit.Fit()
}

extension EnvironmentValues {
  /// What a first glance gives up so its next block peeks (`FoldFit`); nothing everywhere else.
  var foldFit: FoldFit.Fit {
    get { self[FoldFitKey.self] }
    set { self[FoldFitKey.self] = newValue }
  }
}

extension View {
  /// On the last element of a first glance: reports where it ends in the scroll content.
  func foldGlanceEnd() -> some View {
    background(GeometryReader { g in
      Color.clear.preference(key: FoldGlanceEndKey.self, value: g.frame(in: .named(FoldFit.space)).maxY)
    })
  }

  /// On a tab root's page, after `clearOfJarvisKey()` (its scroll content carries the coordinate space `FoldFit.space` and
  /// starts at the page's top): measures the room above the key and where the glance ends, and hands the glance its fit
  /// through the environment.
  func foldFit(gaps: CGFloat) -> some View { modifier(FoldFitModifier(gaps: gaps)) }
}

private struct FoldFitModifier: ViewModifier {
  let gaps: CGFloat
  @State private var visible: CGFloat = 0
  @State private var bottom: CGFloat = 0
  @State private var fit = FoldFit.Fit()

  func body(content: Content) -> some View {
    content
      .environment(\.foldFit, fit)
      .onPreferenceChange(FoldGlanceEndKey.self) { b in
        bottom = b
        refit()
      }
      .background(GeometryReader { g in
        // the page (between the status bar and the tab bar) less the key's band at its bottom
        let h = g.size.height - JarvisLayer.keyClearance
        Color.clear
          .onAppear { visible = h; refit() }
          .onChange(of: h) { v in visible = v; refit() }
      })
  }

  private func refit() {
    let f = FoldFit.fit(bottom: bottom, gaps: gaps, applied: fit, visible: visible)
    #if DEBUG
    if ProcessInfo.processInfo.environment["W2W_DEV_FOLD"] == "1" { print("W2W_FOLD bottom=\(bottom) visible=\(visible) -> \(f)") }
    #endif
    if abs(f.scale - fit.scale) > 0.005 || abs(f.shrink - fit.shrink) >= 1 { fit = f }
  }
}

extension Rhythm {
  /// A gap of a scrolling tab's first glance, shrunk by its fit (`FoldFit`) so the next block peeks above the key.
  static func gap(_ v: CGFloat, fold: FoldFit.Fit) -> CGFloat { (gap(v) * fold.scale).rounded() }
}
