import Foundation
#if os(watchOS) && canImport(WidgetKit)
import WidgetKit
#endif

/// Everything the watch keeps between launches, in one place: the phone's last context, the events not yet with the
/// phone, the watch's quick logs and a running session, each a plain Codable value under its own key.
///
/// One place on purpose: the watch-face complications (plan 2.5) run in their own process (the widget extension) and
/// read `context()` / `queue()` / `session()` from here, through the App Group suite the app and the extension share,
/// and apply the same pure rules (`FaceTimeline` → `WatchHome.derive`, `WatchSyncState.derive`, `SetSession.tick`), so a
/// complication and the home screen can never disagree. Nothing here may depend on the app process (no
/// WatchConnectivity, no HealthKit).
struct WatchStore {
  enum Key: String, CaseIterable {
    case context = "w2w.watch.context.v1"
    case queue = "w2w.watch.queue.v1"
    case quickLogs = "w2w.watch.quicklogs.v1"
    case session = "w2w.watch.session.v1"
    /// What the watch knows about its own token (server, account, paired?); the token itself is in the Keychain.
    case pairing = "w2w.watch.pairing.v1"
  }

  /// Set in the App Group suite once the keys of the app's standard defaults (builds before the complications) were
  /// moved there.
  static let migratedKey = "w2w.watch.store.group.v1"
  /// Info.plist key of the App Group identifier (`$(W2W_APP_GROUP)`, Config/Watch2Whoop.xcconfig).
  static let groupInfoKey = "W2WAppGroup"

  let defaults: UserDefaults
  /// Ask WidgetKit for new timelines when a save changes what the face shows (the app's store; never the seed's).
  var reloadsFace = false

  init(defaults: UserDefaults = .standard, reloadsFace: Bool = false) {
    self.defaults = defaults
    self.reloadsFace = reloadsFace
  }

  /// The store the app and the complications share: the App Group suite named in Info.plist. Without one (a build
  /// whose Info.plist has no group) the standard defaults, as before, and the complications show 「—」.
  /// `migrate` (the app only, never the extension): move the old standard-defaults keys into the suite, once.
  static func shared(migrate: Bool) -> WatchStore {
    let group = (Bundle.main.object(forInfoDictionaryKey: groupInfoKey) as? String)?.trimmingCharacters(in: .whitespaces) ?? ""
    guard !group.isEmpty, let suite = UserDefaults(suiteName: group) else { return WatchStore(defaults: .standard, reloadsFace: migrate) }
    if migrate { moveKeys(from: .standard, to: suite) }
    return WatchStore(defaults: suite, reloadsFace: migrate)
  }

  /// One-time move of the four keys (the queue may hold events the phone has not got: they must not be lost). A value
  /// already in the suite wins; the old copy is removed so there is one source.
  static func moveKeys(from old: UserDefaults, to suite: UserDefaults) {
    guard !suite.bool(forKey: migratedKey) else { return }
    for k in Key.allCases {
      if let v = old.data(forKey: k.rawValue), suite.data(forKey: k.rawValue) == nil { suite.set(v, forKey: k.rawValue) }
      old.removeObject(forKey: k.rawValue)
    }
    suite.set(true, forKey: migratedKey)
  }

  func context() -> WatchContext? { WatchCodec.decode(WatchContext.self, defaults.data(forKey: Key.context.rawValue)) }
  func queue() -> WatchEventQueue { WatchEventQueue.load(defaults.data(forKey: Key.queue.rawValue)) }
  func quickLogs() -> WatchQuickLogs { WatchQuickLogs.load(defaults.data(forKey: Key.quickLogs.rawValue)) }
  func session() -> SavedSession? { WatchCodec.decode(SavedSession.self, defaults.data(forKey: Key.session.rawValue)) }
  func pairing() -> WatchPairing { WatchCodec.decode(WatchPairing.self, defaults.data(forKey: Key.pairing.rawValue)) ?? WatchPairing() }

  /// What the complications read.
  func faceInputs() -> FaceTimeline.Inputs { FaceTimeline.Inputs(context: context(), queue: queue(), session: session()) }

  func save(_ c: WatchContext) { changingFace { defaults.set(WatchCodec.encode(c), forKey: Key.context.rawValue) } }
  func save(_ q: WatchEventQueue) { changingFace { defaults.set(q.data(), forKey: Key.queue.rawValue) } }
  func save(_ l: WatchQuickLogs) { defaults.set(l.data(), forKey: Key.quickLogs.rawValue) }
  func save(_ p: WatchPairing) { defaults.set(WatchCodec.encode(p), forKey: Key.pairing.rawValue) }
  func save(_ s: SavedSession?) {
    changingFace {
      if let s { defaults.set(WatchCodec.encode(s), forKey: Key.session.rawValue) } else { defaults.removeObject(forKey: Key.session.rawValue) }
    }
  }

  /// Forget everything (the seed's `reset`; a sign-out later, when the watch has a token of its own).
  func clear() {
    changingFace { for k in Key.allCases { defaults.removeObject(forKey: k.rawValue) } }
  }

  /// Writes, then reloads the complications' timelines if the face now draws something else (reloads are budgeted:
  /// docs in Watch2WhoopWatch/README.md 「Complications」).
  private func changingFace(_ write: () -> Void) {
    guard reloadsFace else { write(); return }
    let before = faceInputs()
    write()
    let after = faceInputs()
    guard before != after, FaceTimeline.needsReload(before: before, after: after, now: Date()) else { return }
    WatchStore.reloadFace()
  }

  static func reloadFace() {
    #if os(watchOS) && canImport(WidgetKit)
    for c in FaceComplication.allCases { WidgetCenter.shared.reloadTimelines(ofKind: c.kind) }
    #endif
  }
}
