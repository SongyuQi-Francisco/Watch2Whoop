import Foundation
import GRDB

/// Which scoring bundle runs (docs/ios-native-app.md section 6.2): the copy built into the app, or a newer one the server
/// published (`GET /v1/algo/manifest` → `GET /v1/algo/openbodyscore-<version>.js`). A download is used only after the
/// sha256 and Ed25519 checks (`BundleVerifier`; Release builds refuse an unsigned one) and a test load in JavaScriptCore;
/// it is then written to `Application Support/W2W/algo/` and marked `active` in `algo_bundles`. Whichever is newer of the
/// active download and the built-in copy runs, so an app update never runs an older bundle than it ships.
/// After a switch the caller re-scores the last `rescoreDays` days and uploads them (`ScoringSync`).
final class AlgoBundles {
  static let checkEvery: TimeInterval = 6 * 3600
  static let rescoreDays = 14

  private let db: AppDatabase
  private let dir: URL
  private let builtinSource: () -> String?
  private let lock = NSLock()
  private var cached: OpenBodyScoreRuntime?

  init(db: AppDatabase, dir: URL? = nil, builtinSource: @escaping () -> String? = { OpenBodyScoreRuntime.builtinSource() }) {
    self.db = db
    self.dir = dir ?? Self.defaultDirectory()
    self.builtinSource = builtinSource
  }

  static func defaultDirectory() -> URL {
    let base = (try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil,
                                             create: true)) ?? FileManager.default.temporaryDirectory
    return base.appendingPathComponent("W2W/algo", isDirectory: true)
  }

  /// The runtime to score with (loaded once per version; a broken download falls back to the built-in copy).
  func runtime() throws -> OpenBodyScoreRuntime {
    lock.lock()
    defer { lock.unlock() }
    if let cached { return cached }
    let builtin = builtinSource()
    let builtinVersion = builtin.flatMap(Self.version(of:))
    if let active = try? activeDownload(), let src = try? String(contentsOfFile: active.path, encoding: .utf8),
       builtinVersion.map({ !OpenBodyScoreRuntime.isNewer($0, than: active.version) }) ?? true {
      if let rt = try? OpenBodyScoreRuntime(source: src, name: "openbodyscore-\(active.version).js") {
        cached = rt
        return rt
      }
      try? deactivate(active.version)   // it does not load: back to the built-in copy
    }
    guard let builtin else { throw AlgorithmError.load("openbodyscore.js missing") }
    let rt = try OpenBodyScoreRuntime(source: builtin)
    cached = rt
    return rt
  }

  /// The version in the bundle's banner comment (`/*! openbodyscore-js 0.2.0 | …`), without loading it.
  static func version(of source: String) -> String? {
    guard let r = source.range(of: "openbodyscore-js ") else { return nil }
    let rest = source[r.upperBound...].prefix(20)
    let v = rest.prefix { $0.isNumber || $0 == "." }
    return v.isEmpty ? nil : String(v)
  }

  struct Installed { var version: String; var path: String }

  func activeDownload() throws -> Installed? {
    try db.writer.read { db in
      try Row.fetchOne(db, sql: "SELECT version, path FROM algo_bundles WHERE active = 1 AND source = 'download' LIMIT 1")
        .map { Installed(version: $0["version"], path: $0["path"]) }
    }
  }

  private func deactivate(_ version: String) throws {
    try db.writer.write { db in try db.execute(sql: "UPDATE algo_bundles SET active = 0 WHERE version = ?", arguments: [version]) }
    cached = nil
  }

  /// Asks the server for its bundle (at most every `checkEvery` unless forced, e.g. after `stale_algo`); downloads,
  /// verifies and activates a version newer than the one running. Returns the version switched to, or nil.
  @discardableResult
  func update(api: APIClient, force: Bool = false, now: Date = Date()) async -> String? {
    let last = (try? db.meta("algo.checked_at")).flatMap(Double.init) ?? 0
    if !force, now.timeIntervalSince1970 - last < Self.checkEvery { return nil }
    try? db.setMeta("algo.checked_at", String(now.timeIntervalSince1970))
    guard let data = try? await api.data(.get(Endpoint.algoManifest)),
          let m = try? JSONDecoder().decode(AlgorithmManifest.self, from: data) else { return nil }
    let running = (try? runtime())?.version ?? "0"
    guard OpenBodyScoreRuntime.isNewer(m.version, than: running) else { return nil }
    let path = m.url ?? "/v1/algo/openbodyscore-\(m.version).js"
    guard let file = try? await api.data(.get(path)) else { return nil }
    do {
      try BundleVerifier.verify(file, manifest: m)
      let src = String(decoding: file, as: UTF8.self)
      let rt = try OpenBodyScoreRuntime(source: src, name: "openbodyscore-\(m.version).js")
      guard rt.version == m.version else { throw AlgorithmError.verification("version") }
      try install(file, manifest: m)
      lock.lock()
      cached = rt
      lock.unlock()
      return m.version
    } catch {
      try? db.setMeta("algo.last_error", "\(m.version): \(error)")
      return nil
    }
  }

  private func install(_ file: Data, manifest m: AlgorithmManifest) throws {
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let url = dir.appendingPathComponent("openbodyscore-\(m.version).js")
    try file.write(to: url, options: .atomic)
    try db.writer.write { db in
      try db.execute(sql: "UPDATE algo_bundles SET active = 0")
      try db.execute(sql: """
        INSERT INTO algo_bundles (version, sha256, api, path, source, installed_at, active) VALUES (?, ?, ?, ?, 'download', ?, 1)
        ON CONFLICT(version) DO UPDATE SET sha256 = excluded.sha256, path = excluded.path, active = 1
        """, arguments: [m.version, m.sha256, m.api ?? OpenBodyScoreRuntime.apiVersion, url.path, Date().timeIntervalSince1970])
    }
  }
}
