import SwiftUI

/// "Each module is a door: tapped, it grows into its own detail" (2.1.0, drilldown option C). The zoom transition between
/// a door and the level it opens, as one small modifier pair instead of `if #available` at every door:
///
/// - the owner of a navigation stack (or of a full-screen cover) keeps a `@Namespace` and a `ZoomTrail` and puts them on
///   the stack with `.zoomScope(ns, trail)`;
/// - a door marks itself with `.zoomDoor(id)`, an id unique on its page (`glance.dial`, `day.row.hrv`), and the code that
///   opens the level says which door it came from (`trail.open(id, to: route)`);
/// - the opened page grows out of that door with `.zoomLevel(route)`, and shrinks back into it on the way back (back
///   button, edge swipe, or the zoom's own swipe down).
///
/// iOS 18+ only (`matchedTransitionSource` + `navigationTransition(.zoom)`). On iOS 16–17, with Reduce Motion, and for a
/// level opened without a door (a deep link, the small dial going up a level, a debug hook) it is the stack's ordinary
/// push. Rules in `ZoomTransition` are unit-tested (`LevelsTests`).
@MainActor
final class ZoomTrail: ObservableObject {
  /// The door each route was last opened from. Not published: nothing redraws on it, it is read when a level is built.
  private var doors: [AnyHashable: String] = [:]

  /// The next push of `route` comes from `door`.
  func open(_ door: String, to route: AnyHashable) { doors[route] = door }

  /// The door `route` grows out of (nil: opened without one).
  func door(for route: AnyHashable) -> String? { doors[route] }
}

/// The namespace and trail of one stack, handed down to its doors and its levels.
struct ZoomScope {
  let namespace: Namespace.ID
  let trail: ZoomTrail
}

private struct ZoomScopeKey: EnvironmentKey {
  static let defaultValue: ZoomScope? = nil
}

extension EnvironmentValues {
  var zoomScope: ZoomScope? {
    get { self[ZoomScopeKey.self] }
    set { self[ZoomScopeKey.self] = newValue }
  }
}

enum ZoomTransition {
  /// Whether levels zoom on this phone now: iOS 18+, Reduce Motion off. In Debug, `W2W_ZOOM_OFF=1` turns it off, so a
  /// frame-meter run can compare the plain push with the zoom on the same build (apps/ios/README.md).
  static func enabled(reduceMotion: Bool) -> Bool {
    guard #available(iOS 18.0, *), !reduceMotion else { return false }
    #if DEBUG
    if ProcessInfo.processInfo.environment["W2W_ZOOM_OFF"] == "1" { return false }
    #endif
    return true
  }
}

extension View {
  /// The stack below grows its levels out of their doors (put it on the `NavigationStack` or the cover's content).
  func zoomScope(_ namespace: Namespace.ID, _ trail: ZoomTrail) -> some View {
    environment(\.zoomScope, ZoomScope(namespace: namespace, trail: trail))
  }

  /// This view is a door: the level opened from it (`ZoomTrail.open(id, to:)`) grows out of it. nil: not a door.
  func zoomDoor(_ id: String?) -> some View { modifier(ZoomDoor(id: id)) }

  /// This page is the level `route`: it grows out of the door it was opened from, if any.
  func zoomLevel(_ route: AnyHashable) -> some View { modifier(ZoomLevel(route: route)) }
}

private struct ZoomDoor: ViewModifier {
  let id: String?
  @Environment(\.zoomScope) private var scope
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  @ViewBuilder func body(content: Content) -> some View {
    if #available(iOS 18.0, *), let scope, let id, ZoomTransition.enabled(reduceMotion: reduceMotion) {
      content.matchedTransitionSource(id: id, in: scope.namespace)
    } else {
      content
    }
  }
}

private struct ZoomLevel: ViewModifier {
  let route: AnyHashable
  @Environment(\.zoomScope) private var scope
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  @ViewBuilder func body(content: Content) -> some View {
    if #available(iOS 18.0, *), let scope, ZoomTransition.enabled(reduceMotion: reduceMotion), let door = scope.trail.door(for: route) {
      content.navigationTransition(.zoom(sourceID: door, in: scope.namespace))
    } else {
      content
    }
  }
}
