import CryptoKit
import Foundation
import JavaScriptCore

/// Runs the versioned JS algorithm bundle (#340 N1) in JavaScriptCore, no WebView (docs/ios-native-app.md section 6).
///
/// Calling convention (api 1), fixed here, the functions themselves are N1's:
/// - the bundle is one classic script (ES2020, no modules, no DOM, no timers, no network, no `Date.now()` for results)
///   that defines `globalThis.OBS = { apiVersion: 1, version: "x.y.z", functions: ["…"], call(name, inputJson) }`;
/// - `OBS.call` takes the function name and its input as a JSON string and returns a JSON string: the result, or
///   `{"error": {"code": "…", "message": "…"}}`; it is synchronous and pure (same input → same output on every device
///   and on the server, golden tests);
/// - inputs / outputs use the server's conventions: days `YYYY-MM-DD` in the user's zone, times UTC seconds, kg / kcal /
///   ms / bpm, nulls for missing values;
/// - one runtime per bundle version, calls serialised on its own queue (JSContext is not thread-safe); a call that
///   throws or returns `error` raises `AlgorithmError`.
final class AlgorithmRuntime {
  static let apiVersion = 1

  let version: String
  let functions: [String]
  private let context: JSContext
  private let queue = DispatchQueue(label: "w2w.algo", qos: .userInitiated)

  init(source: String, name: String = "bundle.js") throws {
    guard let vm = JSVirtualMachine(), let ctx = JSContext(virtualMachine: vm) else { throw AlgorithmError.load("no JSContext") }
    var thrown: String?
    ctx.exceptionHandler = { _, e in thrown = e?.toString() }
    ctx.evaluateScript(source, withSourceURL: URL(string: "w2w-algo:///\(name)"))
    if let thrown { throw AlgorithmError.load(thrown) }
    guard let obs = ctx.objectForKeyedSubscript("OBS"), obs.isObject else { throw AlgorithmError.load("no OBS object") }
    let api = obs.objectForKeyedSubscript("apiVersion")?.toInt32() ?? 0
    guard Int(api) == Self.apiVersion else { throw AlgorithmError.apiMismatch(Int(api)) }
    version = obs.objectForKeyedSubscript("version")?.toString() ?? "?"
    functions = (obs.objectForKeyedSubscript("functions")?.toArray() as? [String]) ?? []
    context = ctx
  }

  /// The built-in bundle shipped in the app (used until a downloaded one is verified and active).
  static func builtin(bundle: Bundle = .main) throws -> AlgorithmRuntime {
    guard let url = bundle.url(forResource: "algo-builtin", withExtension: "js"),
          let src = try? String(contentsOf: url, encoding: .utf8) else { throw AlgorithmError.load("built-in bundle missing") }
    return try AlgorithmRuntime(source: src, name: "algo-builtin.js")
  }

  /// JSON in, JSON out.
  func callJSON(_ function: String, _ input: String) throws -> String {
    try queue.sync {
      var thrown: String?
      context.exceptionHandler = { _, e in thrown = e?.toString() }
      let obs = context.objectForKeyedSubscript("OBS")
      let out = obs?.invokeMethod("call", withArguments: [function, input])
      if let thrown { throw AlgorithmError.exception(thrown) }
      guard let s = out, s.isString, let text = s.toString() else { throw AlgorithmError.badOutput }
      if let d = text.data(using: .utf8), let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
         let err = o["error"] as? [String: Any] {
        throw AlgorithmError.function(code: err["code"] as? String ?? "error", message: err["message"] as? String)
      }
      return text
    }
  }

  func call<Input: Encodable, Output: Decodable>(_ function: String, _ input: Input, as: Output.Type = Output.self) throws -> Output {
    let i = String(decoding: try JSONEncoder.api.encode(input), as: UTF8.self)
    let o = try callJSON(function, i)
    return try JSONDecoder.api.decode(Output.self, from: Data(o.utf8))
  }
}

enum AlgorithmError: Error, Equatable {
  case load(String)
  case apiMismatch(Int)
  case exception(String)
  case function(code: String, message: String?)
  case badOutput
  case verification(String)
}

/// What the server says about a bundle (N1: `GET /v1/algo/manifest`).
struct AlgorithmManifest: Codable, Equatable {
  var version: String
  var api: Int
  /// Hex sha256 of the bundle file.
  var sha256: String
  /// Base64 Ed25519 signature over the bundle file's bytes, by the maintainers' signing key (`body-engine algo-sign`).
  /// nil = the bundle was not signed (accepted by Debug builds only, see `AlgoSigningKeys.requireSignature`).
  var signature: String?
  /// Oldest app version that may run it ("1.0.9").
  var minApp: String?
  /// Which key signed it (first 16 hex digits of sha256 of the raw public key); informational, every built-in key is tried.
  var keyId: String? = nil

  enum CodingKeys: String, CodingKey {
    case version, api, sha256, signature
    case minApp = "min_app"
    case keyId = "key_id"
  }
}

/// A downloaded bundle is only used after both checks pass; otherwise the previous one stays active.
enum BundleVerifier {
  /// sha256 first, then the Ed25519 signature against any of `publicKeys` (current and next, for rotation).
  /// Unsigned: allowed only when `requireSignature` is false (Debug builds).
  static func verify(_ data: Data, manifest: AlgorithmManifest,
                     publicKeys: [Curve25519.Signing.PublicKey] = AlgoSigningKeys.all,
                     requireSignature: Bool = AlgoSigningKeys.requireSignature) throws {
    guard manifest.api == AlgorithmRuntime.apiVersion else { throw AlgorithmError.apiMismatch(manifest.api) }
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
