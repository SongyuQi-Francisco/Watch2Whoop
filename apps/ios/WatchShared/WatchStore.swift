import Foundation

/// Everything the watch keeps between launches, in one place: the phone's last context, the events not yet with the
/// phone, the watch's quick logs and a running session, each a plain Codable value under its own key.
///
/// One place on purpose: the watch-face widgets (plan 2.5, phase 3) run in their own process and cannot read the app's
/// standard defaults. Their step makes `defaults` an App Group suite that the app and the widget extension share (the
/// suite name is the only change here), and the widget reads `context()` / `session()` and applies the same pure rules
/// (`WatchHome.derive`, `WatchSyncState.derive`) from this folder, so a complication and the home screen can never
/// disagree. Nothing here may depend on the app process (no WatchConnectivity, no HealthKit).
struct WatchStore {
  enum Key: String, CaseIterable {
    case context = "w2w.watch.context.v1"
    case queue = "w2w.watch.queue.v1"
    case quickLogs = "w2w.watch.quicklogs.v1"
    case session = "w2w.watch.session.v1"
    /// What the watch knows about its own token (server, account, paired?); the token itself is in the Keychain.
    case pairing = "w2w.watch.pairing.v1"
  }

  let defaults: UserDefaults

  init(defaults: UserDefaults = .standard) {
    self.defaults = defaults
  }

  func context() -> WatchContext? { WatchCodec.decode(WatchContext.self, defaults.data(forKey: Key.context.rawValue)) }
  func queue() -> WatchEventQueue { WatchEventQueue.load(defaults.data(forKey: Key.queue.rawValue)) }
  func quickLogs() -> WatchQuickLogs { WatchQuickLogs.load(defaults.data(forKey: Key.quickLogs.rawValue)) }
  func session() -> SavedSession? { WatchCodec.decode(SavedSession.self, defaults.data(forKey: Key.session.rawValue)) }
  func pairing() -> WatchPairing { WatchCodec.decode(WatchPairing.self, defaults.data(forKey: Key.pairing.rawValue)) ?? WatchPairing() }

  func save(_ c: WatchContext) { defaults.set(WatchCodec.encode(c), forKey: Key.context.rawValue) }
  func save(_ q: WatchEventQueue) { defaults.set(q.data(), forKey: Key.queue.rawValue) }
  func save(_ l: WatchQuickLogs) { defaults.set(l.data(), forKey: Key.quickLogs.rawValue) }
  func save(_ p: WatchPairing) { defaults.set(WatchCodec.encode(p), forKey: Key.pairing.rawValue) }
  func save(_ s: SavedSession?) {
    if let s { defaults.set(WatchCodec.encode(s), forKey: Key.session.rawValue) } else { defaults.removeObject(forKey: Key.session.rawValue) }
  }

  /// Forget everything (the seed's `reset`; a sign-out later, when the watch has a token of its own).
  func clear() {
    for k in Key.allCases { defaults.removeObject(forKey: k.rawValue) }
  }
}
