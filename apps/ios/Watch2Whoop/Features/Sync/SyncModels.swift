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
  /// Where the backfill goes (UTC seconds; 0 = all history) and how far back it got: everything from `cursor` to `to`
  /// is on the server.
  var from: Double = 0
  var cursor: Double = 0
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
    from = num(m["from"]) ?? 0
    cursor = num(m["cursor"]) ?? 0
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
  /// HealthKit identifier -> the newest local day ("yyyy-MM-dd") the server has of it through any way in (an Apple Health
  /// export, Open Wearables, the engine): `latest_day`, servers from 2026-10-31.
  var latestDay: [String: String] = [:]
  var lastUploadAt: Date?
  /// Code of the last refused request when nothing went through since.
  var openFailure: String?
  var upToDate = false
  var pendingDays = 0

  init() {}

  init(_ j: [String: Any]) {
    mode = j["mode"] as? String ?? "ow"
    for t in j["types"] as? [Any] ?? [] {
      guard let t = t as? [String: Any], let hk = t["type"] as? String else { continue }
      if let at = (t["latest_sample_at"] as? String).flatMap(ISO8601.parse) { latest[hk] = at }
      if let d = t["latest_day"] as? String, d.count == 10 { latestDay[hk] = d }
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

/// Per type: not recorded on the phone, not synced, older than what the sync reads, or synced (the Flutter app's
/// TypeVerdict, plus `outsideRange`).
enum TypeVerdict: Int, Comparable {
  case notSynced = 0, outsideRange, synced, notCollected

  static func < (a: TypeVerdict, b: TypeVerdict) -> Bool { a.rawValue < b.rawValue }

  /// A newer sample on the phone than on the server by more than this = not synced (a round may be under way).
  static let slack: TimeInterval = 30 * 60

  /// - serverDay: the newest local day the server has of the type through any way in (`latest_day`): a phone sample of an
  ///   earlier day than today that the server has a day of, or a later one, is there (an export or Open Wearables brought it);
  /// - uploadedSince: from when the sync reads this phone (the first sync's 14 days, or as far back as the history
  ///   backfill got). The owner's report 2026-10-31: the newest weight / height on the phone was older than that, the
  ///   engine never read it, so 「已最新」 and 「N 类没同步」 at once; it is "older than the synced range", fixed by the
  ///   history backfill, not by syncing again.
  init(phone: Date?, server: Date?, serverDay: String? = nil, uploadedSince: Date? = nil, now: Date = Date(),
       calendar: Calendar = .current) {
    guard let phone else { self = .notCollected; return }
    if let server, phone <= server.addingTimeInterval(Self.slack) { self = .synced; return }
    if let serverDay {
      let day = Self.dayString(phone, calendar), today = Self.dayString(now, calendar)
      if day < today && serverDay >= day { self = .synced; return }
    }
    if let uploadedSince, phone < uploadedSince { self = .outsideRange; return }
    self = .notSynced
  }

  /// "yyyy-MM-dd" of a moment in the phone's calendar (string order = date order).
  static func dayString(_ d: Date, _ calendar: Calendar = .current) -> String {
    let c = calendar.dateComponents([.year, .month, .day], from: d)
    return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
  }
}

struct TypeRow: Identifiable, Equatable {
  let type: HealthType
  let phone: Date?
  let server: Date?
  let verdict: TypeVerdict
  var id: String { type.hk }

  /// One row per engine type the server takes (all engine types when it did not say): not synced first, then older than
  /// the synced range, synced, not recorded; the registry's order inside.
  static func rows(_ registry: [HealthType], serverTypes: Set<String>, phone: [String: Date],
                   server: [String: Date], serverDay: [String: String] = [:], uploadedSince: Date? = nil,
                   now: Date = Date()) -> [TypeRow] {
    let rows = registry.enumerated().compactMap { i, t -> (Int, TypeRow)? in
      guard t.isEngine, serverTypes.isEmpty || serverTypes.contains(t.hk) else { return nil }
      return (i, TypeRow(type: t, phone: phone[t.hk], server: server[t.hk],
                         verdict: TypeVerdict(phone: phone[t.hk], server: server[t.hk], serverDay: serverDay[t.hk],
                                              uploadedSince: uploadedSince, now: now)))
    }
    return rows.sorted { a, b in a.1.verdict != b.1.verdict ? a.1.verdict < b.1.verdict : a.0 < b.0 }.map(\.1)
  }
}

/// GET /v1/users/me/data-status, the fields the sync result needs: per data type ok / stale / never and, from servers of
/// 2026-10-31, why (`reason`, `action`, `alarm`) and the phone channel's state (`delivery`). Older servers: no reasons.
struct ServerDataStatus: Equatable {
  struct Kind: Equatable {
    var key = ""
    var label = ""
    var status = "ok"
    var reason: String?
    var action: String?
    var alarm = false
    var daysSince: Int?
  }

  var types: [Kind] = []
  /// ok / behind / never (nil: an older server).
  var delivery: String?
  var deliveryAction: String?

  init() {}

  init(_ j: [String: Any]) {
    for t in j["types"] as? [Any] ?? [] {
      guard let t = t as? [String: Any], let key = t["key"] as? String else { continue }
      types.append(Kind(key: key, label: t["label"] as? String ?? key, status: t["status"] as? String ?? "ok",
                        reason: t["reason"] as? String, action: t["action"] as? String, alarm: t["alarm"] as? Bool ?? false,
                        daysSince: (t["days_since"] as? NSNumber)?.intValue))
    }
    let d = j["delivery"] as? [String: Any]
    delivery = d?["state"] as? String
    deliveryAction = d?["action"] as? String
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
