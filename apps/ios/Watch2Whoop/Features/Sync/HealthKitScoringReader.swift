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
                                end: s.endDate.timeIntervalSince1970, value: value)
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
