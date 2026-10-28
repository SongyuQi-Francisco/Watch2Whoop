import Foundation

// Beat-to-beat HRV series, ECG recordings and drinks straight from HealthKit to POST /v1/ingest/healthkit (the Flutter
// app's lib/src/raw_sync.dart RawSyncRunner, ported for #340 N8). RawSync.swift reads one page after the stored anchor;
// this pass uploads it and only then commits the anchor, so nothing is lost when an upload fails and nothing is sent twice
// once it succeeded (the server also skips UUIDs it already has).
// - The registry's quantity types are the sync engine's job in the native app (1.0.9 runs only in engine mode `app`), so
//   only the three RawSync kinds go here.
// - Water only while the server says `water_enabled` (the anchor stays otherwise, the drinks go once it is on).
// - Beat-to-beat readings and ECGs need the newer consent (409): water still goes.
// - Time-boxed (budget, batches): the rest goes next time from the last committed anchor.

struct RawPage {
  var items: [[String: Any]] = []
  var deleted: [String] = []
  var anchor: String?
  var more = false

  var isEmpty: Bool { items.isEmpty && deleted.isEmpty }

  init(items: [[String: Any]] = [], deleted: [String] = [], anchor: String? = nil, more: Bool = false) {
    self.items = items
    self.deleted = deleted
    self.anchor = anchor
    self.more = more
  }

  init(_ m: [String: Any]) {
    items = (m["items"] as? [Any])?.compactMap { $0 as? [String: Any] } ?? []
    deleted = (m["deleted"] as? [Any])?.map { "\($0)" } ?? []
    anchor = m["anchor"] as? String
    more = m["more"] as? Bool ?? false
  }
}

/// Where pages come from (RawSync.swift in the app, a fake in tests).
protocol RawPageSource {
  /// [kind] may carry an anchor set ("resync:heartbeat").
  func read(kind: String, daysBack: Int, limit: Int) async throws -> RawPage
  func commit(kind: String, anchor: String) async
}

enum RawUploadOutcome {
  case ok([String: Any])
  case unauthorized, consent, tooLarge, failed, offline
}

struct RawPassResult {
  var heartbeats = 0, ecgs = 0, water = 0, batches = 0
  /// nil = everything read; unauthorized / consent / failed / offline / budget.
  var stop: String?
  var uploaded = Set<String>()
  /// A kind could not be read from HealthKit: `not_asked` (HealthKit error 5) or `healthkit`.
  var readProblem: String?
  var readError: String?
  var waterOff = false
  /// The server's `status` of the last accepted batch (water flag, totals, engine block).
  var serverStatus: [String: Any]?
}

final class RawUploadPass {
  static let kinds = ["heartbeat", "ecg", "water"]
  /// Records per HealthKit page: a reading is ~1 KB as JSON, an ECG ~40 KB, a drink ~100 bytes.
  static let pageSize = ["heartbeat": 200, "ecg": 8, "water": 500]
  static let batchField = ["heartbeat": "heartbeats", "ecg": "ecgs", "water": "water"]
  /// HealthKit's "Authorization not determined": the type was never asked for.
  static let notDetermined = "5"

  private let source: RawPageSource
  private let upload: ([String: Any]) async -> RawUploadOutcome
  private let device: String
  private let appVersion: String?
  private let maxBatches: Int
  private let maxBytes: Int
  private let budget: TimeInterval
  private let now: () -> Date
  private let batchId: () -> String
  /// A page the server took (after its anchor was committed): the beat-to-beat readings also go into the local tables the
  /// phone scores from (#340 N2b, `LocalRawStore.ingestBeats`).
  private let stored: ((String, RawPage) -> Void)?

  init(source: RawPageSource, upload: @escaping ([String: Any]) async -> RawUploadOutcome, device: String = "iPhone",
       appVersion: String? = nil, maxBatches: Int = 40, maxBytes: Int = 1_000_000, budget: TimeInterval = 120,
       now: @escaping () -> Date = Date.init, batchId: @escaping () -> String = { UUID().uuidString.lowercased() },
       stored: ((String, RawPage) -> Void)? = nil) {
    self.stored = stored
    self.source = source
    self.upload = upload
    self.device = device
    self.appVersion = appVersion
    self.maxBatches = maxBatches
    self.maxBytes = maxBytes
    self.budget = budget
    self.now = now
    self.batchId = batchId
  }

  /// The request body for one page (rawdata/api.py IngestIn).
  static func batch(kind: String, page: RawPage, batchId: String, device: String, appVersion: String?) -> [String: Any] {
    var b: [String: Any] = ["batch_id": batchId, "device": device, "deleted": page.deleted]
    if let appVersion { b["app_version"] = appVersion }
    b[batchField[kind] ?? kind] = page.items
    return b
  }

  /// One pass over heartbeat, ECG, water. [anchorSet] = a separate set of anchors (a forced re-sync reads the range again
  /// without moving the regular ones).
  func run(daysBack: Int, waterEnabled: Bool, anchorSet: String = "") async -> RawPassResult {
    var out = RawPassResult()
    let t0 = now()
    var rawConsent = true
    kinds: for kind in Self.kinds {
      let name = anchorSet.isEmpty ? kind : "\(anchorSet):\(kind)"
      if kind != "water" && !rawConsent { continue }
      if kind == "water" && !waterEnabled { out.waterOff = true; continue }
      var limit = Self.pageSize[kind] ?? 100
      while true {
        if out.batches >= maxBatches || now().timeIntervalSince(t0) > budget { out.stop = "budget"; return out }
        let page: RawPage
        do {
          page = try await source.read(kind: name, daysBack: daysBack, limit: limit)
        } catch let f as HealthSyncBridge.Failure {
          let notAsked = f.code == "healthkit" && f.details == Self.notDetermined
          if out.readProblem != "not_asked" { out.readProblem = notAsked ? "not_asked" : "healthkit" }
          if out.readError == nil { out.readError = f.message ?? f.code }
          continue kinds   // the anchor stays; the next kind may still be readable
        } catch {
          if out.readProblem == nil { out.readProblem = "healthkit" }
          if out.readError == nil { out.readError = String(describing: error) }
          continue kinds
        }
        if page.isEmpty {
          if let a = page.anchor { await source.commit(kind: name, anchor: a) }
          if !page.more { break }
          continue
        }
        let body = Self.batch(kind: kind, page: page, batchId: batchId(), device: device, appVersion: appVersion)
        if let size = try? JSONSerialization.data(withJSONObject: body).count, size > maxBytes, limit > 1 {
          limit = max(1, limit / 2)   // same anchor, smaller page
          continue
        }
        let res = await upload(body)
        out.batches += 1
        switch res {
        case .ok(let b):
          if let s = b["status"] as? [String: Any] { out.serverStatus = s }
          if kind == "water" && b["water_enabled"] as? Bool != true {
            out.waterOff = true   // the flag went off meanwhile: the drinks were not stored, keep the anchor
            continue kinds
          }
          if let a = page.anchor { await source.commit(kind: name, anchor: a) }
          stored?(kind, page)
          out.uploaded.insert(kind)
          out.heartbeats += (b["heartbeats"] as? NSNumber)?.intValue ?? 0
          out.ecgs += (b["ecgs"] as? NSNumber)?.intValue ?? 0
          out.water += (b["water"] as? NSNumber)?.intValue ?? 0
        case .tooLarge where limit > 1:
          limit = max(1, limit / 2)
          continue
        case .unauthorized:
          out.stop = "unauthorized"
          return out
        case .consent:
          out.stop = "consent"
          if kind == "water" { return out }   // the ordinary consent: nothing else goes either
          rawConsent = false                   // the newer consent is not accepted yet: water still goes
          continue kinds
        case .offline:
          out.stop = "offline"
          return out
        default:
          out.stop = "failed"
          return out
        }
        if !page.more { break }
      }
    }
    return out
  }
}

/// RawSync.swift as a page source (method-channel shape through HealthSyncBridge).
struct NativeRawSource: RawPageSource {
  func read(kind: String, daysBack: Int, limit: Int) async throws -> RawPage {
    let out = try await HealthSyncBridge.raw("rawRead", ["kind": kind, "daysBack": daysBack, "limit": limit])
    return RawPage(out as? [String: Any] ?? [:])
  }

  func commit(kind: String, anchor: String) async {
    _ = try? await HealthSyncBridge.raw("rawCommit", ["kind": kind, "anchor": anchor])
  }

  /// Forgets the regular anchors, or only the anchors of [anchorSet].
  func reset(anchorSet: String? = nil) async {
    var args: [String: Any] = [:]
    if let anchorSet { args["set"] = anchorSet }
    _ = try? await HealthSyncBridge.raw("rawReset", args)
  }
}
