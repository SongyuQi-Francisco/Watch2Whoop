import Foundation
import HealthKit

/// The scoring types straight from HealthKit into the account's local tables (#340 N2b, `LocalRawStore`), before each
/// scoring run. The sync engine (SyncEngine.swift) uploads the same samples, but it is a byte-for-byte copy of the Flutter
/// app's file and has no hook, so this reads them itself: an anchored query per type (anchors in the account database,
/// so a sign-out forgets them with it), only the last `LocalRawStore.keepDays` days, deletions included. Cheap after the
/// first run: only what changed since the last anchor. HealthKit unreadable (locked phone, not allowed) = nothing new.
final class HealthKitScoringReader {
  static let pageSize = 5000
  private let db: AppDatabase
  private let store = HKHealthStore()

  init(db: AppDatabase) {
    self.db = db
  }

  /// Reads every type; returns the local days that changed. [uploadedSince]: the server has everything this phone read
  /// from HealthKit only from there on (the first sync's window, or as far as the history backfill got,
  /// `HealthSyncService.uploadedSince`); older samples stay out, so the phone and the server's spot check score the same
  /// inputs (#340 N9: a new user with more history in Health than the first sync's 14 days failed every check).
  func read(into raw: LocalRawStore, now: Date = Date(), uploadedSince: Date? = nil) async -> Set<String> {
    guard HKHealthStore.isHealthDataAvailable() else { return [] }
    var days = Set<String>()
    let since = Self.since(now: now, uploadedSince: uploadedSince)
    // An earlier start (the backfill got further) needs fresh anchors: an anchored query never returns samples its
    // predicate left out before. Re-reading is cheap, `ingest` keeps samples by UUID.
    let sinceKey = "hk.scoring.since"
    if let previous = (try? db.meta(sinceKey)).flatMap({ $0 }).flatMap(Double.init),
       Self.needsReread(previous: previous, next: since.timeIntervalSince1970) {
      try? await db.writer.write { db in try db.execute(sql: "DELETE FROM meta WHERE key LIKE 'hk.scoring.anchor.%'") }
    }
    try? db.setMeta(sinceKey, String(since.timeIntervalSince1970))
    if (try? db.meta(Self.sourcesKey)) ?? nil == nil {
      // samples read before 2.0.0 M1 have no source: read the window once more (ingest keeps them by UUID)
      try? await db.writer.write { db in try db.execute(sql: "DELETE FROM meta WHERE key LIKE 'hk.scoring.anchor.%'") }
      try? db.setMeta(Self.sourcesKey, "1")
    }
    for (type, unit) in LocalRawStore.types {
      guard let t = Self.sampleType(type) else { continue }
      let u = unit.isEmpty ? nil : HKUnit(from: unit)
      for _ in 0..<40 {   // pages; the rest comes next time
        let key = "hk.scoring.anchor.\(type)"
        let anchor = (try? db.meta(key)).flatMap { $0 }.flatMap { Data(base64Encoded: $0) }
          .flatMap { try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: $0) }
        guard let page = await query(t, anchor: anchor, since: since) else { break }
        let samples = page.samples.compactMap { Self.sample($0, type: type, unit: u) }
        if let d = try? raw.ingest(samples, deleted: page.deleted.map { $0.uuid.uuidString.lowercased() }) { days.formUnion(d) }
        if let a = page.anchor, let data = try? NSKeyedArchiver.archivedData(withRootObject: a, requiringSecureCoding: true) {
          try? db.setMeta(key, data.base64EncodedString())
        }
        if page.samples.count + page.deleted.count < Self.pageSize { break }
      }
    }
    return days
  }

  /// meta: set once the samples carry their source (2.0.0 M1: the derivation groups sleep and the phase-1 sums by
  /// source); a phone that read before reads its window once more.
  static let sourcesKey = "hk.scoring.sources_v1"
  /// The statistics buckets are read again for the last `statsOverlapDays` days each time (a watch that syncs late).
  static let statsOverlapDays = 3.0
  static let statsKey = "hk.derive.stats_through"

  /// 2.0.0 M1 (DeriveStage): the registry's other raw types (anchors `hk.derive.anchor.<type>`, the same window as
  /// `read`) and the 15-minute statistics buckets of its cumulative types (`hk_stat_buckets`: Apple's own de-duplication
  /// of iPhone + watch, what the sync engine uploads). Both mark the derivation's dirty days; returns them.
  func readEngine(into raw: LocalRawStore, now: Date = Date(), uploadedSince: Date? = nil) async -> Set<String> {
    guard HKHealthStore.isHealthDataAvailable() else { return [] }
    var days = Set<String>()
    let since = Self.since(now: now, uploadedSince: uploadedSince)
    let sinceKey = "hk.derive.since"
    var restart = false
    if let previous = (try? db.meta(sinceKey)).flatMap({ $0 }).flatMap(Double.init),
       Self.needsReread(previous: previous, next: since.timeIntervalSince1970) {
      try? await db.writer.write { db in try db.execute(sql: "DELETE FROM meta WHERE key LIKE 'hk.derive.anchor.%'") }
      restart = true
    }
    try? db.setMeta(sinceKey, String(since.timeIntervalSince1970))
    for (type, unit) in LocalRawStore.deriveTypes {
      guard let t = Self.sampleType(type) else { continue }
      let u = unit.isEmpty ? nil : HKUnit(from: unit)
      for _ in 0..<40 {   // pages; the rest comes next time
        let key = "hk.derive.anchor.\(type)"
        let anchor = (try? db.meta(key)).flatMap { $0 }.flatMap { Data(base64Encoded: $0) }
          .flatMap { try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: $0) }
        guard let page = await query(t, anchor: anchor, since: since) else { break }
        let samples = page.samples.compactMap { Self.sample($0, type: type, unit: u) }
        _ = try? raw.ingest(samples, deleted: page.deleted.map { $0.uuid.uuidString.lowercased() })
        for s in samples { days.formUnion(raw.deriveDays(s)) }
        if let a = page.anchor, let data = try? NSKeyedArchiver.archivedData(withRootObject: a, requiringSecureCoding: true) {
          try? db.setMeta(key, data.base64EncodedString())
        }
        if page.samples.count + page.deleted.count < Self.pageSize { break }
      }
    }
    let through = restart ? nil : (try? db.meta(Self.statsKey)).flatMap { $0 }.flatMap(Double.init)
    let step = LocalRawStore.bucketSeconds
    let from = (max(since.timeIntervalSince1970, (through ?? 0) - Self.statsOverlapDays * 86_400) / step).rounded(.down) * step
    let end = (now.timeIntervalSince1970 / step).rounded(.up) * step
    var complete = true
    for (type, unit) in LocalRawStore.statTypes {
      guard let qt = HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: type)) else { continue }
      let u = HKUnit(from: unit)
      guard qt.is(compatibleWith: u) else { continue }
      var s = from
      while s < end {
        let n = min(Int(((end - s) / step).rounded()), EngineLimits.buckets)
        guard n > 0, let values = try? await EngineHK.buckets(store, qt, unit: u, start: s, count: n) else {
          complete = false
          break
        }
        let pairs = values.enumerated().map { (s + Double($0.offset) * step, $0.element) }
        if let d = try? raw.ingestBuckets(type: type, from: s, to: s + Double(n) * step, values: pairs) { days.formUnion(d) }
        s += Double(n) * step
      }
    }
    if complete { try? db.setMeta(Self.statsKey, String(now.timeIntervalSince1970)) }
    return days
  }

  /// The start of what is read: the last `keepDays` days, but not before the server has the phone's data.
  static func since(now: Date, uploadedSince: Date?) -> Date {
    let window = now.addingTimeInterval(-Double(LocalRawStore.keepDays) * 86_400)
    guard let uploadedSince else { return window }
    return max(window, uploadedSince)
  }

  /// The anchors are dropped when the start moved back by more than an hour.
  static func needsReread(previous: Double, next: Double) -> Bool { next < previous - 3600 }

  static func sampleType(_ id: String) -> HKSampleType? {
    if id.hasPrefix("HKCategoryTypeIdentifier") { return HKObjectType.categoryType(forIdentifier: HKCategoryTypeIdentifier(rawValue: id)) }
    return HKObjectType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: id))
  }

  static func sample(_ s: HKSample, type: String, unit: HKUnit?) -> LocalRawStore.Sample? {
    let value: Double
    if let q = s as? HKQuantitySample, let unit, q.quantity.is(compatibleWith: unit) {
      value = q.quantity.doubleValue(for: unit)
    } else if let c = s as? HKCategorySample {
      value = Double(c.value)
    } else {
      return nil
    }
    return LocalRawStore.Sample(uuid: s.uuid.uuidString.lowercased(), type: type, start: s.startDate.timeIntervalSince1970,
                                end: s.endDate.timeIntervalSince1970, value: value, source: source(s))
  }

  /// The sample's source as the sync engine sends it (EngineSources: name, bundle, product type, device; the device type
  /// by SyncEngine.deviceType), so the phone groups samples by source as the server does.
  static func source(_ s: HKSample) -> LocalRawStore.Source {
    let rev = s.sourceRevision, dev = s.device, product = rev.productType ?? ""
    return LocalRawStore.Source(name: String(rev.source.name.prefix(120)), bundle: String(rev.source.bundleIdentifier.prefix(200)),
                                product: String(product.prefix(60)), deviceType: SyncEngine.deviceType(product: product, device: dev),
                                deviceName: String((dev?.name ?? "").prefix(120)), deviceModel: String((dev?.model ?? "").prefix(120)))
  }

  private struct Page {
    var samples: [HKSample]
    var deleted: [HKDeletedObject]
    var anchor: HKQueryAnchor?
  }

  private func query(_ type: HKSampleType, anchor: HKQueryAnchor?, since: Date) async -> Page? {
    await withCheckedContinuation { cont in
      let predicate = HKQuery.predicateForSamples(withStart: since, end: nil, options: [])
      let q = HKAnchoredObjectQuery(type: type, predicate: predicate, anchor: anchor, limit: Self.pageSize) { _, added, deleted, next, error in
        if error != nil { cont.resume(returning: nil); return }
        cont.resume(returning: Page(samples: added ?? [], deleted: deleted ?? [], anchor: next))
      }
      store.execute(q)
    }
  }
}
