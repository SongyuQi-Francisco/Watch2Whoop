import CryptoKit
import Foundation

enum AlgorithmError: Error, Equatable {
  case load(String)
  case apiMismatch(Int)
  case exception(String)
  case function(code: String, message: String?)
  case badOutput
  case verification(String)
}

/// What the server says about the bundle (`GET /v1/algo/manifest`, N1 + N2's signature).
struct AlgorithmManifest: Codable, Equatable {
  var version: String
  /// The entry-point contract (`OpenBodyScoreRuntime.apiVersion`); the server's manifest leaves it out while it is 1.
  var api: Int?
  /// Hex sha256 of the bundle file.
  var sha256: String
  /// Base64 Ed25519 signature over the bundle file's bytes, by the maintainers' signing key (`body-engine algo-sign`).
  /// nil = the bundle was not signed (accepted by Debug builds only, see `AlgoSigningKeys.requireSignature`).
  var signature: String?
  /// Oldest app version that may run it ("1.0.9").
  var minApp: String?
  /// Which key signed it (first 16 hex digits of sha256 of the raw public key); informational, every built-in key is tried.
  var keyId: String? = nil
  /// Download path relative to the server ("/v1/algo/openbodyscore-0.2.0.js").
  var url: String? = nil
  /// The algorithm versions inside (`{scores, healthspan, hrv, stress, trends}`).
  var algoVersions: [String: String]? = nil

  enum CodingKeys: String, CodingKey {
    case version, api, sha256, signature, url
    case minApp = "min_app"
    case keyId = "key_id"
    case algoVersions = "algo_versions"
  }
}

/// A downloaded bundle is only used after both checks pass; otherwise the previous one stays active.
enum BundleVerifier {
  /// sha256 first, then the Ed25519 signature against any of `publicKeys` (current and next, for rotation).
  /// Unsigned: allowed only when `requireSignature` is false (Debug builds).
  static func verify(_ data: Data, manifest: AlgorithmManifest,
                     publicKeys: [Curve25519.Signing.PublicKey] = AlgoSigningKeys.all,
                     requireSignature: Bool = AlgoSigningKeys.requireSignature) throws {
    if let api = manifest.api, api != OpenBodyScoreRuntime.apiVersion { throw AlgorithmError.apiMismatch(api) }
    let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    guard digest == manifest.sha256.lowercased() else { throw AlgorithmError.verification("sha256") }
    guard let signature = manifest.signature else {
      if requireSignature { throw AlgorithmError.verification("unsigned") }
      return
    }
    guard let sig = Data(base64Encoded: signature),
          publicKeys.contains(where: { $0.isValidSignature(sig, for: data) }) else {
      throw AlgorithmError.verification("signature")
    }
  }

  static func verify(_ data: Data, manifest: AlgorithmManifest, publicKey: Curve25519.Signing.PublicKey) throws {
    try verify(data, manifest: manifest, publicKeys: [publicKey], requireSignature: true)
  }
}
