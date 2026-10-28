import Foundation

// What the sync center shows, parsed from the native engine's status map (SyncEngine.swift statusMap), the backfill's
// (Backfill.swift statusMap) and the server's answers (GET /v1/ingest/healthkit, GET /v1/ingest/sync-status,
// docs/apple-sync-engine-app-contract.md sections 2 and 6). Times, counts and codes only: no health values.

private func num(_ v: Any?) -> Double? { (v as? NSNumber)?.doubleValue }
private func date(_ v: Any?) -> Date? {
  guard let t = num(v), t > 0 else { return nil }
  return Date(timeIntervalSince1970: t)
}

/// The server's `engine` block plus the RawSync fields of GET / POST /v1/ingest/healthkit.
struct IngestInfo: Equatable {
  var engineEnabled = false
  var mode = "ow"
  var accepts: Set<String> = []
  var types: Set<String> = []
  var waterEnabled = false
  var syncCenter = false
  var lastUploadAt: Date?
  /// The whole `engine` block as the server sent it (handed to the native engine).
  var engineRaw: [String: Any] = [:]

  /// The engine uploads at all: the server takes its fields and the user is in shadow / app mode.
  var engineActive: Bool { engineEnabled && (mode == "shadow" || mode == "app") && accepts.contains("phase") }

  init() {}

  init(_ j: [String: Any]) {
    let e = j["engine"] as? [String: Any] ?? [:]
    engineRaw = e
    engineEnabled = e["enabled"] as? Bool ?? false
    mode = e["mode"] as? String ?? "ow"
    accepts = Set((e["accepts"] as? [Any])?.compactMap { $0 as? String } ?? [])
    types = Set((e["types"] as? [Any])?.compactMap { $0 as? String } ?? [])
    waterEnabled = j["water_enabled"] as? Bool ?? false
    syncCenter = j["sync_center"] as? Bool ?? false
    lastUploadAt = (j["last_upload_at"] as? String).flatMap(ISO8601.parse)
  }

  static func == (a: IngestInfo, b: IngestInfo) -> Bool {
    a.engineEnabled == b.engineEnabled && a.mode == b.mode && a.accepts == b.accepts && a.types == b.types
      && a.waterEnabled == b.waterEnabled && a.syncCenter == b.syncCenter && a.lastUploadAt == b.lastUploadAt
  }
}

/// The native engine's state (SyncEngine.status).
struct EngineSnapshot: Equatable {
  struct TypeState: Equatable {
    var firstDone = false
    var okAt: Date?
    var error: String?
  }

  var mode = "ow"
  var active = false
  var cellularHistory = false
  var running = false
  var firstTypes = 0
  var firstDone = 0
  var types: [String: TypeState] = [:]
  var log: [String] = []
  var rangePercent: Int?
  var rangeError: String?
  var lastRunAt: Date?
  var lastRunResult: String?
  var lastRunSamples = 0
  var lastBackgroundAt: Date?
  var lastStop: String?
  var observing = 0

  var firstPercent: Int { firstTypes == 0 ? 0 : firstDone * 100 / firstTypes }
  var firstSyncDone: Bool { firstTypes > 0 && firstDone >= firstTypes }

  init() {}

  init(_ m: [String: Any]) {
    mode = m["mode"] as? String ?? "ow"
    active = m["active"] as? Bool ?? false
    cellularHistory = m["cellularHistory"] as? Bool ?? false
    running = m["running"] as? Bool ?? false
    let first = m["firstSync"] as? [String: Any] ?? [:]
    firstTypes = Int(num(first["types"]) ?? 0)
    firstDone = Int(num(first["done"]) ?? 0)
    for (k, v) in m["types"] as? [String: Any] ?? [:] {
      guard let t = v as? [String: Any] else { continue }
      types[k] = TypeState(firstDone: t["backfilled"] as? Bool ?? false, okAt: date(t["okAt"]), error: t["error"] as? String)
    }
    log = (m["log"] as? [Any])?.compactMap { $0 as? String } ?? []
    if let r = m["range"] as? [String: Any] {
      rangePercent = Int(num(r["percent"]) ?? 0)
      rangeError = r["error"] as? String
    }
    if let r = m["lastRun"] as? [String: Any] {
      lastRunAt = date(r["started"])
      lastRunResult = r["result"] as? String
      lastRunSamples = Int(num(r["samples"]) ?? 0)
    }
    lastBackgroundAt = date(m["lastBackgroundAt"])
    let stop = m["lastStop"] as? String
    lastStop = (stop?.isEmpty ?? true) ? nil : stop
    observing = Int(num(m["observing"]) ?? 0)
  }
}

/// The history backfill (Backfill.status).
struct BackfillState: Equatable {
  var active = false
  var done = false
  /// Target in days; 0 = all history.
  var target = 0
  var percent = 0
  var to: Double = 0
  var error: String?
  var uploading = false
  var batches = 0

  /// Nothing was ever started.
  var isEmpty: Bool { !active && !done && batches == 0 }

  init() {}

  init(_ m: [String: Any]) {
    active = m["active"] as? Bool ?? false
    done = m["done"] as? Bool ?? false
    target = Int(num(m["target"]) ?? 0)
    percent = Int(num(m["percent"]) ?? 0)
    to = num(m["to"]) ?? 0
    error = m["error"] as? String
    uploading = m["uploading"] as? Bool ?? false
    batches = Int(num(m["batches"]) ?? 0)
  }
}

/// GET /v1/ingest/sync-status (contract section 6).
struct ServerSyncStatus: Equatable {
  var mode = "ow"
  /// HealthKit identifier -> newest sample on the server.
  var latest: [String: Date] = [:]
  var lastUploadAt: Date?
  /// Code of the last refused request when nothing went through since.
  var openFailure: String?
  var upToDate = false
  var pendingDays = 0

  init() {}

  init(_ j: [String: Any]) {
    mode = j["mode"] as? String ?? "ow"
    for t in j["types"] as? [Any] ?? [] {
      guard let t = t as? [String: Any], let hk = t["type"] as? String,
            let at = (t["latest_sample_at"] as? String).flatMap(ISO8601.parse) else { continue }
      latest[hk] = at
    }
    lastUploadAt = ((j["last_upload"] as? [String: Any])?["at"] as? String).flatMap(ISO8601.parse)
    if let f = j["failures"] as? [String: Any], f["recovered"] as? Bool == false {
      let last = f["last"] as? [String: Any]
      openFailure = (last?["code"] as? String) ?? (last?["reason"] as? String) ?? "error"
    }
    let p = j["pipeline"] as? [String: Any] ?? [:]
    upToDate = p["up_to_date"] as? Bool ?? false
    pendingDays = (p["pending_days"] as? [Any])?.count ?? Int(num(p["pending_days"]) ?? 0)
  }
}

/// Per type: not recorded on the phone, not synced, or synced (the Flutter app's TypeVerdict).
enum TypeVerdict: Int, Comparable {
  case notSynced = 0, synced, notCollected

  static func < (a: TypeVerdict, b: TypeVerdict) -> Bool { a.rawValue < b.rawValue }

  /// A newer sample on the phone than on the server by more than this = not synced (a round may be under way).
  static let slack: TimeInterval = 30 * 60

  init(phone: Date?, server: Date?) {
    guard let phone else { self = .notCollected; return }
    guard let server else { self = .notSynced; return }
    self = phone > server.addingTimeInterval(Self.slack) ? .notSynced : .synced
  }
}

struct TypeRow: Identifiable, Equatable {
  let type: HealthType
  let phone: Date?
  let server: Date?
  let verdict: TypeVerdict
  var id: String { type.hk }

  /// One row per engine type the server takes (all engine types when it did not say): not synced first, then synced,
  /// then not recorded; the registry's order inside.
  static func rows(_ registry: [HealthType], serverTypes: Set<String>, phone: [String: Date],
                   server: [String: Date]) -> [TypeRow] {
    let rows = registry.enumerated().compactMap { i, t -> (Int, TypeRow)? in
      guard t.isEngine, serverTypes.isEmpty || serverTypes.contains(t.hk) else { return nil }
      return (i, TypeRow(type: t, phone: phone[t.hk], server: server[t.hk],
                         verdict: TypeVerdict(phone: phone[t.hk], server: server[t.hk])))
    }
    return rows.sorted { a, b in a.1.verdict != b.1.verdict ? a.1.verdict < b.1.verdict : a.0 < b.0 }.map(\.1)
  }
}

/// ISO 8601 with or without fractional seconds (the server's `isoformat()`).
enum ISO8601 {
  private static let plain: ISO8601DateFormatter = ISO8601DateFormatter()
  private static let frac: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
  }()

  static func parse(_ s: String) -> Date? { plain.date(from: s) ?? frac.date(from: s) }
}
