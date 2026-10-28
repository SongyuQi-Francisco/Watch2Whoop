import Foundation
import JavaScriptCore

/// The real scoring bundle of N1 (`packages/openbodyscore-js/dist/openbodyscore.js`, global `OpenBodyScore`) in
/// JavaScriptCore. The file is copied into the app as a resource by `project.yml` (one source, no second copy in git).
///
/// N1 shipped the bundle with its own entry points (`OpenBodyScore.buildInputs`, `.computeDays`, …) rather than the
/// `OBS.call(name, json)` convention of `AlgorithmRuntime` (api 1); this wrapper calls those entry points directly
/// through one small JS glue function, JSON in and out. The download / signature path (`/v1/algo/manifest`,
/// `BundleVerifier`) is N2's; until then the built-in copy is what runs (docs/ios-native-app.md section 6.4).
///
/// Loading parses ~140 kB of JS (tens of ms), so pages create it off the main thread and only when they need it.
final class OpenBodyScoreRuntime {
  let version: String
  /// `{scores, healthspan, hrv, stress, trends}` → the server's algorithm version strings.
  let algoVersions: [String: String]
  private let context: JSContext
  private let queue = DispatchQueue(label: "w2w.openbodyscore", qos: .utility)

  private static let glue = """
    globalThis.__w2wInvoke = function (fn, argsJson) {
      try {
        var f = OpenBodyScore[fn];
        if (typeof f !== "function") return JSON.stringify({ error: "no function " + fn });
        return JSON.stringify({ ok: f.apply(OpenBodyScore, JSON.parse(argsJson)) });
      } catch (e) { return JSON.stringify({ error: String((e && e.message) || e) }); }
    };
    globalThis.__w2wScore = function (srcJson, optsJson) {
      try {
        var inputs = OpenBodyScore.buildInputs(JSON.parse(srcJson));
        return JSON.stringify({ ok: OpenBodyScore.computeDays(inputs, JSON.parse(optsJson)) });
      } catch (e) { return JSON.stringify({ error: String((e && e.message) || e) }); }
    };
    """

  init(source: String, name: String = "openbodyscore.js") throws {
    guard let vm = JSVirtualMachine(), let ctx = JSContext(virtualMachine: vm) else { throw AlgorithmError.load("no JSContext") }
    var thrown: String?
    ctx.exceptionHandler = { _, e in thrown = e?.toString() }
    ctx.evaluateScript(source, withSourceURL: URL(string: "w2w-algo:///\(name)"))
    if let thrown { throw AlgorithmError.load(thrown) }
    guard let obs = ctx.objectForKeyedSubscript("OpenBodyScore"), obs.isObject else {
      throw AlgorithmError.load("no OpenBodyScore object")
    }
    ctx.evaluateScript(Self.glue)
    if let thrown { throw AlgorithmError.load(thrown) }
    version = obs.objectForKeyedSubscript("VERSION")?.toString() ?? "?"
    algoVersions = (obs.objectForKeyedSubscript("ALGO_VERSIONS")?.toDictionary() as? [String: String]) ?? [:]
    context = ctx
  }

  /// The copy built into the app.
  static func builtin(bundle: Bundle = .main) throws -> OpenBodyScoreRuntime {
    guard let url = bundle.url(forResource: "openbodyscore", withExtension: "js"),
          let src = try? String(contentsOf: url, encoding: .utf8) else { throw AlgorithmError.load("openbodyscore.js missing") }
    return try OpenBodyScoreRuntime(source: src)
  }

  /// `OpenBodyScore.<fn>(...args)`; arguments and result go through JSON.
  func invoke(_ fn: String, _ args: [Any]) throws -> Any {
    try run("__w2wInvoke", [fn, try Self.json(args)])
  }

  /// `computeDays(buildInputs(src), opts)`: the server's daily_scores rows for `[first, last]` from local raw rows.
  func scoreDays(source: [String: Any], first: String?, last: String, today: String) throws -> [[String: Any]] {
    var opts: [String: Any] = ["last": last, "today": today]
    if let first { opts["first"] = first }
    let out = try run("__w2wScore", [try Self.json(source), try Self.json(opts)])
    return out as? [[String: Any]] ?? []
  }

  private func run(_ glue: String, _ args: [String]) throws -> Any {
    try queue.sync {
      var thrown: String?
      context.exceptionHandler = { _, e in thrown = e?.toString() }
      let out = context.objectForKeyedSubscript(glue)?.call(withArguments: args)
      if let thrown { throw AlgorithmError.exception(thrown) }
      guard let s = out, s.isString, let text = s.toString(), let data = text.data(using: .utf8),
            let obj = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) as? [String: Any] else {
        throw AlgorithmError.badOutput
      }
      if let err = obj["error"] as? String { throw AlgorithmError.function(code: "exception", message: err) }
      return obj["ok"] ?? NSNull()
    }
  }

  private static func json(_ v: Any) throws -> String {
    String(decoding: try JSONSerialization.data(withJSONObject: v, options: [.fragmentsAllowed]), as: UTF8.self)
  }
}
