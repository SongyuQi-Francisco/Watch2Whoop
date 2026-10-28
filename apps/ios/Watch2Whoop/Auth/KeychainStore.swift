import Foundation
import Security

/// Generic-password items, readable after the first unlock (background sync), this device only, never synced to iCloud.
/// (The copied Backfill.swift has its own `Keychain` enum for the backfill upload token; this one is for everything else.)
protocol KeychainBackend {
  func read(service: String, account: String) -> Data?
  @discardableResult func write(_ data: Data, service: String, account: String) -> Bool
  func delete(service: String, account: String)
}

struct SystemKeychain: KeychainBackend {
  private func base(_ service: String, _ account: String) -> [String: Any] {
    [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
  }

  func read(service: String, account: String) -> Data? {
    var q = base(service, account)
    q[kSecReturnData as String] = true
    q[kSecMatchLimit as String] = kSecMatchLimitOne
    var out: CFTypeRef?
    guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess else { return nil }
    return out as? Data
  }

  @discardableResult
  func write(_ data: Data, service: String, account: String) -> Bool {
    let q = base(service, account)
    let attrs: [String: Any] = [kSecValueData as String: data,
                                kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
    if SecItemUpdate(q as CFDictionary, attrs as CFDictionary) == errSecSuccess { return true }
    var add = q
    add.merge(attrs) { $1 }
    SecItemDelete(q as CFDictionary)
    return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
  }

  func delete(service: String, account: String) {
    SecItemDelete(base(service, account) as CFDictionary)
  }
}

/// In-memory Keychain for tests and previews.
final class MemoryKeychain: KeychainBackend {
  private(set) var items: [String: Data] = [:]
  func read(service: String, account: String) -> Data? { items["\(service)|\(account)"] }
  @discardableResult func write(_ data: Data, service: String, account: String) -> Bool {
    items["\(service)|\(account)"] = data
    return true
  }
  func delete(service: String, account: String) { items["\(service)|\(account)"] = nil }
}
