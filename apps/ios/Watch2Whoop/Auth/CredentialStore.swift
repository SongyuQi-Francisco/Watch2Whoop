import Foundation

/// Who is signed in, where: the server address (UserDefaults, not secret), the account token and the upload-only token
/// (Keychain), and the user id once the server confirmed it (names the local database, section 4.1).
struct AccountConfig: Codable, Equatable {
  /// Server origin, e.g. `https://example.com` (no `/app/`, no trailing slash).
  var server: String
  /// Open Wearables host (only while the OW SDK is still in use, #219).
  var owHost: String = ""
  /// Body Engine user id, set after `/v1/web/me` answered once.
  var userId: Int?
  /// Display name from the server (greeting, avatar letter).
  var displayName: String?
}

final class CredentialStore {
  static let service = "org.watch2whoop.native"
  static let accountTokenKey = "account.token"
  static let ingestTokenKey = "ingest.token"
  static let configKey = "w2w.native.account.v1"

  private let keychain: KeychainBackend
  private let defaults: UserDefaults

  init(keychain: KeychainBackend = SystemKeychain(), defaults: UserDefaults = .standard) {
    self.keychain = keychain
    self.defaults = defaults
  }

  var accountToken: String? {
    get { keychain.read(service: Self.service, account: Self.accountTokenKey).flatMap { String(data: $0, encoding: .utf8) }.nonEmpty }
    set {
      if let v = newValue, !v.isEmpty { keychain.write(Data(v.utf8), service: Self.service, account: Self.accountTokenKey) }
      else { keychain.delete(service: Self.service, account: Self.accountTokenKey) }
    }
  }

  /// Upload-only token (scope ingest) when the account has one; the sync engine falls back to the account token.
  var ingestToken: String? {
    get { keychain.read(service: Self.service, account: Self.ingestTokenKey).flatMap { String(data: $0, encoding: .utf8) }.nonEmpty }
    set {
      if let v = newValue, !v.isEmpty { keychain.write(Data(v.utf8), service: Self.service, account: Self.ingestTokenKey) }
      else { keychain.delete(service: Self.service, account: Self.ingestTokenKey) }
    }
  }

  var config: AccountConfig? {
    get { defaults.data(forKey: Self.configKey).flatMap { try? JSONDecoder().decode(AccountConfig.self, from: $0) } }
    set {
      if let v = newValue, let d = try? JSONEncoder().encode(v) { defaults.set(d, forKey: Self.configKey) }
      else { defaults.removeObject(forKey: Self.configKey) }
    }
  }

  var isSignedIn: Bool { accountToken != nil && config != nil }

  /// Sign-out: native items and the Flutter app's items too, so going back to 1.0.8 in TestFlight cannot bring a
  /// revoked session back (section 8).
  func signOut() {
    accountToken = nil
    ingestToken = nil
    config = nil
    FlutterMigration.forgetFlutterCredentials(keychain: keychain, defaults: defaults)
  }
}

extension Optional where Wrapped == String {
  var nonEmpty: String? {
    switch self {
    case .some(let s) where !s.isEmpty: return s
    default: return nil
    }
  }
}
