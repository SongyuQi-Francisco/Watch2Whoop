import Foundation
import JavaScriptCore

/// The scoring bundle (`packages/openbodyscore-js/dist/openbodyscore.js`, global `OpenBodyScore`) in JavaScriptCore.
///
/// The one calling convention (docs/ios-native-app.md section 6.1, decided in #340 N2b): the bundle's own entry points —
/// `OpenBodyScore.buildInputs`, `.computeDays`, `.analyzeHrv`, `.stressDays`, `.trend`, … — called through one small JS
/// glue function, JSON in and out, on this runtime's serial queue. The `OBS.call(name, json)` convention of N0's
/// placeholder is gone; `apiVersion` names this entry-point contract (a manifest with another `api` is refused).
///
/// The built-in copy is a resource of the app (`project.yml`); a newer signed download replaces it (`AlgoBundles`).
/// Loading parses ~145 kB of JS (tens of ms), so it is created off the main thread and only when needed.
final class OpenBodyScoreRuntime {
  static let apiVersion = 1

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
    guard let src = builtinSource(bundle: bundle) else { throw AlgorithmError.load("openbodyscore.js missing") }
    return try OpenBodyScoreRuntime(source: src)
  }

  static func builtinSource(bundle: Bundle = .main) -> String? {
    guard let url = bundle.url(forResource: "openbodyscore", withExtension: "js") else { return nil }
    return try? String(contentsOf: url, encoding: .utf8)
  }

  /// `OpenBodyScore.<fn>(...args)`; arguments and result go through JSON.
  func invoke(_ fn: String, _ args: [Any]) throws -> Any {
    try run("__w2wInvoke", [fn, try Self.json(args)])
  }

  /// `computeDays(buildInputs(src), opts)`: the server's daily_scores rows for `[first, last]` from local rows.
  /// `origin` = the server's scoring origin (only places the CTL warm-up flag of a seeded run).
  func scoreDays(source: [String: Any], first: String?, last: String, today: String, origin: String? = nil) throws -> [[String: Any]] {
    var opts: [String: Any] = ["last": last, "today": today]
    if let first { opts["first"] = first }
    if let origin { opts["origin"] = origin }
    let out = try run("__w2wScore", [try Self.json(source), try Self.json(opts)])
    return out as? [[String: Any]] ?? []
  }

  /// Frees what the last calls left on the JS heap now rather than whenever JavaScriptCore decides: a chunked recompute
  /// (`ScoringSync`) calls this between chunks so the heap stays at one chunk's size (local history).
  func collectGarbage() {
    queue.sync { JSGarbageCollect(context.jsGlobalContextRef) }
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

  /// "0.10.0" > "0.9.3": numeric parts compared in order.
  static func isNewer(_ a: String, than b: String) -> Bool {
    let x = a.split(separator: ".").map { Int($0) ?? 0 }, y = b.split(separator: ".").map { Int($0) ?? 0 }
    for i in 0..<max(x.count, y.count) {
      let p = i < x.count ? x[i] : 0, q = i < y.count ? y[i] : 0
      if p != q { return p > q }
    }
    return false
  }
}
