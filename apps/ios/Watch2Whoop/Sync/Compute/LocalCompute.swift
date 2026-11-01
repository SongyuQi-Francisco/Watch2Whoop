import Foundation

/// 2.0.0: every computation of the user's own data runs on the phone (docs/ios-native-2.0.md). After each sync round the
/// stages run in this fixed order, each on what the ones before it wrote to the local tables:
///
///     derive → workouts → scores (the existing `ScoringSync`) → stress → healthspan → rules
///
/// One stage per package, each in its own file (`<Stage>Stage.swift`), so packages never edit this file. A stage runs only
/// when it is implemented in this build and the server says its package is `shadow` or `primary` for this user
/// (`GET /v1/sync/compute`, `ComputeState`); otherwise it is skipped and the page keeps reading the server's documents.
/// While no stage is implemented (P0) the server is not even asked, so a sync round does exactly what it did before.
struct LocalCompute {
  /// The last `GET /v1/sync/compute` answer, kept as a document (offline: the stages use the cached state).
  static let stateDoc = "compute:state"
  /// The last run's stage reports (sync center / debugging), JSON in `meta`.
  static let lastKey = "compute.last"
  static let order = ["derive", "workouts", "scores", "stress", "healthspan", "rules"]

  let db: AppDatabase
  let api: APIClient
  /// Before the scores (their inputs) and after them (they read the scores).
  var before: [any ComputeStage] = [DeriveStage(), WorkoutsStage()]
  var after: [any ComputeStage] = [StressStage(), HealthspanStage(), RulesStage()]

  /// Runs the stages around `scores` (the existing scoring run) and returns the scoring report unchanged.
  func run(reason: String, now: Date = Date(), scores: () async -> ScoringSync.Report) async -> ScoringSync.Report {
    let stages = before + after
    let state = stages.contains(where: { type(of: $0).implemented }) ? await fetchState() : .off
    let ctx = ComputeContext(db: db, api: api, state: state, reason: reason, now: now)
    var reports: [ComputeStageReport] = []
    for s in before { reports.append(await Self.runStage(s, ctx)) }
    let scored = await scores()
    for s in after { reports.append(await Self.runStage(s, ctx)) }
    if reports.contains(where: { $0.state != "skipped" }),
       let data = try? JSONEncoder.api.encode(reports), let text = String(data: data, encoding: .utf8) {
      try? db.setMeta(Self.lastKey, text)
    }
    return scored
  }

  static func runStage(_ s: any ComputeStage, _ ctx: ComputeContext) async -> ComputeStageReport {
    let t = type(of: s)
    guard t.implemented, ctx.state.mode(t.package) != .off else {
      return ComputeStageReport(stage: t.name, state: "skipped")
    }
    return await s.run(ctx)
  }

  /// The server's answer, stored; the cached one when offline; `.off` when the server has no such endpoint (older server)
  /// or nothing was ever fetched.
  func fetchState() async -> ComputeState {
    do {
      let data = try await api.data(Endpoint.compute())
      try? db.saveDoc(Self.stateDoc, json: data)
      return ComputeState.decode(data)
    } catch APIError.notFound {
      try? db.deleteDoc(Self.stateDoc)
      return .off
    } catch {
      return Self.cachedState(db)
    }
  }

  static func cachedState(_ db: AppDatabase) -> ComputeState {
    guard let d = try? db.doc(stateDoc) else { return .off }
    return ComputeState.decode(d.body)
  }
}

/// One package's work on the phone (docs/ios-native-2.0.md section 4).
protocol ComputeStage {
  /// The stage's name in `LocalCompute.order`.
  static var name: String { get }
  /// Its package in `GET /v1/sync/compute` (`compute.packages`).
  static var package: String { get }
  /// false until the package's PR implements it: the stage is then never run.
  static var implemented: Bool { get }
  func run(_ ctx: ComputeContext) async -> ComputeStageReport
}

struct ComputeContext {
  let db: AppDatabase
  let api: APIClient
  let state: ComputeState
  let reason: String
  let now: Date
}

struct ComputeStageReport: Codable, Equatable {
  var stage: String
  /// skipped | done | partial | waiting_sync | failed
  var state: String
  var written: [String] = []
  var uploaded: [String] = []
  var detail: String?
}

/// Where each package is computed for this user: the `compute` block of `GET /v1/sync/compute`.
struct ComputeState: Equatable {
  enum Mode: String, Codable {
    case off, shadow, primary
  }

  struct Package: Equatable {
    var state: Mode
    var flag: Bool
    var implemented: Bool
    var kinds: [String: Mode]
  }

  var packages: [String: Package]
  var primaryFlag: Bool

  static let off = ComputeState(packages: [:], primaryFlag: false)

  /// off for anything the server did not name.
  func mode(_ package: String) -> Mode { packages[package]?.state ?? .off }

  /// The whole answer of `GET /v1/sync/compute`, or only its `compute` block. Unknown states count as off.
  static func decode(_ data: Data) -> ComputeState {
    guard let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return .off }
    let c = (o["compute"] as? [String: Any]) ?? o
    guard let ps = c["packages"] as? [String: Any] else { return .off }
    var out: [String: Package] = [:]
    for (name, v) in ps {
      guard let p = v as? [String: Any] else { continue }
      var kinds: [String: Mode] = [:]
      for (k, kv) in (p["kinds"] as? [String: Any]) ?? [:] {
        kinds[k] = ((kv as? [String: Any])?["state"] as? String).flatMap(Mode.init(rawValue:)) ?? .off
      }
      out[name] = Package(state: (p["state"] as? String).flatMap(Mode.init(rawValue:)) ?? .off,
                          flag: p["flag"] as? Bool ?? false, implemented: p["implemented"] as? Bool ?? false, kinds: kinds)
    }
    return ComputeState(packages: out, primaryFlag: c["primary_flag"] as? Bool ?? false)
  }
}
