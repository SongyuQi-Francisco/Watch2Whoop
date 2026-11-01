import CryptoKit
import Foundation

/// Public keys that may sign the algorithm bundle (#340 N2, docs/ios-native-app.md section 6.3).
///
/// Two slots for rotation: `current` signs today's bundles, `next` is the key the maintainer switches to later. An app
/// accepts a bundle signed by either, so a release that ships the next key goes out before the first bundle signed with
/// it. Values are base64 of the raw 32-byte Ed25519 public key, exactly as `body-engine algo-keygen` prints them, and must
/// equal `packages/openbodyscore-js/signing-keys.json` (body_engine/tests/test_native_sync.py compares the two).
/// The private keys never leave the maintainer's machine (`secrets/algo_signing_key`, not in git, not on any server).
enum AlgoSigningKeys {
  static let current: String? = "FvOt1FIeUlsAcfaGWpSk02DbSLNTSfISXZJqDe5OYJg="
  static let next: String? = "PZeBHFxsHJ+rB58Lp3ogBu2ZWy4vQPaMxufPda8khbo="

  static var all: [Curve25519.Signing.PublicKey] {
    [current, next].compactMap { b64 in
      guard let b64, let raw = Data(base64Encoded: b64) else { return nil }
      return try? Curve25519.Signing.PublicKey(rawRepresentation: raw)
    }
  }

  /// Release builds refuse an unsigned bundle; Debug builds take it (local development against an unsigned dist/).
  #if DEBUG
  static let requireSignature = false
  #else
  static let requireSignature = true
  #endif
}
