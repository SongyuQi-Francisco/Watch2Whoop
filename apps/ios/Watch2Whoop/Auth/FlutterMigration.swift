import Foundation

/// First launch after the TestFlight upgrade from the Flutter app (1.0.8 → 1.0.9, same bundle ID and team, so the same
/// Keychain access group and the same UserDefaults): copy what the Flutter app stored into the native places.
/// docs/ios-native-app.md section 8. The Flutter items are left in place (TestFlight can go back to 1.0.8) until the
/// cleanup two versions later; native sign-out removes them.
///
/// Where the Flutter app keeps things (apps/watch2whoop):
/// - flutter_secure_storage 11 (darwin plugin 0.4.3): generic password, service `flutter_secure_storage_service`,
///   account = the key, value = UTF-8; keys `w2w.account.token.v1` (account_store.dart) and `w2w.ingest.token.v1`
///   (raw_sync.dart);
/// - shared_preferences: `UserDefaults.standard`, key prefixed `flutter.`: `flutter.w2w.config.v1` (JSON AppConfig:
///   todayUrl, owHost, onboardingDone …, config.dart) and `flutter.w2w.lang.v1` (i18n.dart);
/// - written by Swift directly, no prefix, and read as they are by the copied sync files: `w2w.sync.anchor.*`,
///   `w2w.raw.anchor.*`, `w2w.backfill.state.v1`, the Keychain item `w2w.backfill` / `token`. Nothing to do for those.
enum FlutterMigration {
  static let flutterService = "flutter_secure_storage_service"
  static let flutterAccountToken = "w2w.account.token.v1"
  static let flutterIngestToken = "w2w.ingest.token.v1"
  static let flutterConfigKey = "flutter.w2w.config.v1"
  static let flutterLangKey = "flutter.w2w.lang.v1"
  /// Set once the migration ran (whatever it found), so it never runs twice.
  static let doneKey = "w2w.native.migrated.v1"

  enum Outcome: Equatable {
    /// Nothing from the Flutter app (fresh install): show sign-in.
    case nothingToMigrate
    /// Token and server copied: signed in.
    case migrated(server: String)
    /// Some of it was there but not usable (no token or no server): show sign-in, keep the Flutter items.
    case incomplete
    /// Ran before.
    case alreadyDone
  }

  @discardableResult
  static func run(store: CredentialStore, keychain: KeychainBackend = SystemKeychain(),
                  defaults: UserDefaults = .standard) -> Outcome {
    if defaults.bool(forKey: doneKey) { return .alreadyDone }
    defer { defaults.set(true, forKey: doneKey) }

    if let lang = defaults.string(forKey: flutterLangKey), lang == "zh" || lang == "en",
       defaults.string(forKey: Lang.overrideKey) == nil {
      defaults.set(lang, forKey: Lang.overrideKey)
    }
    if store.isSignedIn { return .alreadyDone }   // signed in natively already (e.g. a debug build)

    let token = keychain.read(service: flutterService, account: flutterAccountToken).flatMap { String(data: $0, encoding: .utf8) }.nonEmpty
    let ingest = keychain.read(service: flutterService, account: flutterIngestToken).flatMap { String(data: $0, encoding: .utf8) }.nonEmpty
    let cfg = flutterConfig(defaults)
    let server = cfg.flatMap { serverOrigin($0["todayUrl"] as? String) }
    if token == nil && ingest == nil && cfg == nil { return .nothingToMigrate }
    guard let token, let server else { return .incomplete }

    store.accountToken = token
    store.ingestToken = ingest
    store.config = AccountConfig(server: server, owHost: (cfg?["owHost"] as? String) ?? "")
    return .migrated(server: server)
  }

  static func flutterConfig(_ defaults: UserDefaults) -> [String: Any]? {
    guard let raw = defaults.string(forKey: flutterConfigKey), let d = raw.data(using: .utf8),
          let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return nil }
    return o
  }

  /// `https://host/app/` or `https://host:8443` → `https://host[:port]` (the Flutter app stored the Today page URL).
  static func serverOrigin(_ s: String?) -> String? {
    guard let s, var c = URLComponents(string: s.trimmingCharacters(in: .whitespaces)),
          let scheme = c.scheme?.lowercased(), scheme == "https" || scheme == "http", let host = c.host, !host.isEmpty else { return nil }
    c.path = ""
    c.query = nil
    c.fragment = nil
    c.user = nil
    c.password = nil
    c.scheme = scheme
    return c.string
  }

  static func forgetFlutterCredentials(keychain: KeychainBackend, defaults: UserDefaults) {
    keychain.delete(service: flutterService, account: flutterAccountToken)
    keychain.delete(service: flutterService, account: flutterIngestToken)
  }
}
