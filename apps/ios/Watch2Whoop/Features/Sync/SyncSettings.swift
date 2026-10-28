import Foundation

/// The few sync settings the native app keeps itself (UserDefaults), each taken over from the Flutter app on the first
/// launch after the upgrade so nothing is read twice (docs/ios-native-app.md section 8). What the copied engine files
/// keep (anchors `w2w.sync.anchor.*` / `w2w.raw.anchor.*`, `w2w.sync.engine.*`, `w2w.backfill.state.v1`, the Keychain
/// items `w2w.backfill` and the engine token) is shared with 1.0.8 as it is.
struct SyncSettings {
  let defaults: UserDefaults

  init(defaults: UserDefaults = .standard) { self.defaults = defaults }

  /// Days the first sync reads (Flutter: sync_params.dart kFirstSyncDays).
  static let firstSyncDays = 14
  /// The usual history backfill target ("近 90 天"); 0 = all history.
  static let backfillDays = 90
  /// Seconds the foreground spends on the history backfill after each round of new data.
  static let pumpSeconds: Double = 20
  /// At most one sync-on-open round this often (the Flutter app's SyncOnOpen.minGap).
  static let openGap: TimeInterval = 3 * 60

  private enum Key {
    static let daysBack = "w2w.native.sync.daysBack.v1"
    static let rawRange = "w2w.native.raw.range.v1"
    static let backfillTo = "w2w.native.backfill.to.v1"
    static let resync = "w2w.native.resync.v1"
    static let account = "w2w.native.sync.account.v1"
    static let lastOk = "w2w.native.sync.lastOk.v1"
    // the Flutter app's (shared_preferences: "flutter." + its key)
    static let flutterConfig = "flutter.w2w.config.v1"
    static let flutterRawRange = "flutter.w2w.raw.range.v1"
    static let flutterBackfillTo = "flutter.w2w.backfill.to.v1"
  }

  /// The read range (days; 0 = all history): 14 at first, larger once the history backfill was asked for.
  var daysBack: Int {
    get {
      if defaults.object(forKey: Key.daysBack) != nil { return defaults.integer(forKey: Key.daysBack) }
      if let raw = defaults.string(forKey: Key.flutterConfig), let d = raw.data(using: .utf8),
         let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let n = o["syncDaysBack"] as? NSNumber {
        return n.intValue
      }
      return Self.firstSyncDays
    }
    nonmutating set { defaults.set(newValue, forKey: Key.daysBack) }
  }

  /// The range RawSync's anchors were read with (a larger one needs fresh anchors).
  var rawRange: Int? {
    get {
      if defaults.object(forKey: Key.rawRange) != nil { return defaults.integer(forKey: Key.rawRange) }
      if defaults.object(forKey: Key.flutterRawRange) != nil { return defaults.integer(forKey: Key.flutterRawRange) }
      return nil
    }
    nonmutating set { defaults.set(newValue, forKey: Key.rawRange) }
  }

  /// The start of the first-sync window (UTC seconds), fixed the first time a history backfill starts: the backfill's
  /// cursor walks back from it, and the same value lets Backfill.swift keep the progress of a backfill 1.0.8 started.
  func backfillTo(now: Date = Date(), calendar: Calendar = .current) -> Double {
    if defaults.object(forKey: Key.backfillTo) != nil { return defaults.double(forKey: Key.backfillTo) }
    if defaults.object(forKey: Key.flutterBackfillTo) != nil {
      let v = defaults.double(forKey: Key.flutterBackfillTo)
      defaults.set(v, forKey: Key.backfillTo)
      return v
    }
    let start = calendar.startOfDay(for: now).addingTimeInterval(-Double(Self.firstSyncDays) * 86400).timeIntervalSince1970
    defaults.set(start, forKey: Key.backfillTo)
    return start
  }

  /// A larger read range (or all history after a limited one) needs RawSync's anchors reset once.
  static func needsAnchorReset(previous: Int?, next: Int) -> Bool {
    guard let previous, previous != 0 else { return false }
    return next == 0 || next > previous
  }

  /// "server|user id" of the account the engine state belongs to (nil: none yet, or the state came from 1.0.8).
  var account: String? {
    get { defaults.string(forKey: Key.account) }
    nonmutating set { defaults.set(newValue, forKey: Key.account) }
  }

  var lastOk: Date? {
    get { defaults.object(forKey: Key.lastOk) as? Date }
    nonmutating set { defaults.set(newValue, forKey: Key.lastOk) }
  }

  var resync: ResyncJob? {
    get { defaults.data(forKey: Key.resync).flatMap { try? JSONDecoder().decode(ResyncJob.self, from: $0) } }
    nonmutating set {
      if let v = newValue, let d = try? JSONEncoder().encode(v) { defaults.set(d, forKey: Key.resync) }
      else { defaults.removeObject(forKey: Key.resync) }
    }
  }

  /// Sign-out: the next account starts from scratch (the Health permission belongs to the app, not the account).
  func clear() {
    for k in [Key.daysBack, Key.rawRange, Key.backfillTo, Key.resync, Key.account, Key.lastOk] { defaults.removeObject(forKey: k) }
  }
}

/// The phone part of a server re-sync job (`/v1/ingest/resync`): a forced re-sync of a range, or the history backfill.
struct ResyncJob: Codable, Equatable {
  /// nil = all history.
  var days: Int?
  var startedAt: Date
  /// History backfill (Backfill.swift) rather than the engine's range re-sync.
  var history: Bool
  var rawDone = false
  var engineDone = false
}
