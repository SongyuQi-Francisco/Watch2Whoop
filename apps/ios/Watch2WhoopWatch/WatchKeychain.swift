import Foundation
import Security

/// The watch's own token (plan 4.5, #458 A): in the watch's Keychain, this device only (no iCloud Keychain, no backup to
/// another device), readable after the first unlock so a voice request works with the screen on. Never logged, never in
/// the application context or the defaults.
enum WatchKeychain {
  private static let service = "w2w.watch.token"
  private static let account = "voice"

  static func token() -> String? {
    let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                            kSecAttrAccount as String: account, kSecReturnData as String: true,
                            kSecMatchLimit as String: kSecMatchLimitOne]
    var out: AnyObject?
    guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
    return String(data: d, encoding: .utf8)
  }

  @discardableResult
  static func save(_ token: String) -> Bool {
    delete()
    let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                            kSecAttrAccount as String: account, kSecValueData as String: Data(token.utf8),
                            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
    return SecItemAdd(q as CFDictionary, nil) == errSecSuccess
  }

  static func delete() {
    let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                            kSecAttrAccount as String: account]
    SecItemDelete(q as CFDictionary)
  }
}
