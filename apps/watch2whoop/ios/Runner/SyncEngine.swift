import Flutter
import Foundation
import HealthKit

/// The app's own HealthKit sync engine, step A1 (docs/apple-sync-engine.md §3.1, #219): the native reader. Dart
/// (lib/src/sync_engine.dart) decides what to read from the registry (assets/healthkit_types.json, the same table as the
/// server's rawdata/hk_types.ENGINE_TYPES), uploads the pages to POST /v1/ingest/healthkit and only then commits the
/// anchor here, so an interrupted upload is read again (the server skips UUIDs it has). Runs only while the server's
/// `engine` block says it takes the engine fields (flag apple_sync_engine, off by default): nothing here runs otherwise.
///
/// Channel watch2whoop/native, methods engine*:
/// - engineRead {type, kind: quantity|category, unit, anchor (key), daysBack, limit, recent}: one page, columnar
///   {uuid, start, end, value, src, tz, sources, deleted, anchor, more, count, tzName}. `recent` = the newest samples of
///   the last 48 hours (HKSampleQuery, newest first, no anchor): the Today page has data within a minute while the
///   anchored crawl (oldest first) is still going.
/// - engineCommit {anchor (key), value}; engineReset {} (sign-out: every engine anchor).
/// Times are UTC seconds since 1970; values are in the registry unit; a category's value is HealthKit's integer.
/// No health values are logged. A1 reads quantities (raw) and categories only; workouts, statistics buckets and
/// background delivery come with A2 / A3.
final class SyncEngine {
  static let shared = SyncEngine()
  private let store = HKHealthStore()
  private let anchorPrefix = "w2w.sync.anchor."
  static let recentWindow: TimeInterval = 48 * 3600

  func handle(_ call: FlutterMethodCall, _ result: @escaping FlutterResult) -> Bool {
    let args = call.arguments as? [String: Any] ?? [:]
    switch call.method {
    case "engineRead":
      read(args) { out in DispatchQueue.main.async { result(out) } }
    case "engineCommit":
      if let key = args["anchor"] as? String, !key.isEmpty, let value = args["value"] as? String {
        UserDefaults.standard.set(value, forKey: anchorPrefix + key)
      }
      result(nil)
    case "engineReset":
      for key in UserDefaults.standard.dictionaryRepresentation().keys where key.hasPrefix(anchorPrefix) {
        UserDefaults.standard.removeObject(forKey: key)
      }
      result(nil)
    default:
      return false
    }
    return true
  }

  private func storedAnchor(_ key: String) -> HKQueryAnchor? {
    guard let s = UserDefaults.standard.string(forKey: anchorPrefix + key), let data = Data(base64Encoded: s) else { return nil }
    return try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: data)
  }

  private func encode(_ anchor: HKQueryAnchor?) -> String? {
    guard let anchor, let data = try? NSKeyedArchiver.archivedData(withRootObject: anchor, requiringSecureCoding: true) else { return nil }
    return data.base64EncodedString()
  }

  private func sampleType(_ id: String, kind: String) -> HKSampleType? {
    switch kind {
    case "quantity": return HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: id))
    case "category": return HKCategoryType.categoryType(forIdentifier: HKCategoryTypeIdentifier(rawValue: id))
    default: return nil
    }
  }

  private static func empty(_ extra: [String: Any] = [:]) -> [String: Any] {
    ["uuid": [], "start": [], "end": [], "value": [], "src": [], "tz": [], "sources": [], "deleted": [],
     "anchor": NSNull(), "more": false, "count": 0, "tzName": TimeZone.current.identifier].merging(extra) { $1 }
  }

  private func read(_ args: [String: Any], _ done: @escaping (Any) -> Void) {
    let id = args["type"] as? String ?? "", kind = args["kind"] as? String ?? ""
    let key = args["anchor"] as? String ?? id
    let limit = max(1, args["limit"] as? Int ?? 200), days = args["daysBack"] as? Int ?? 90
    let recent = args["recent"] as? Bool ?? false
    guard HKHealthStore.isHealthDataAvailable(), let type = sampleType(id, kind: kind) else {
      done(Self.empty(["unsupported": true])); return   // this iOS does not know the type (min_ios)
    }
    var unit: HKUnit?
    if kind == "quantity" {
      // the unit strings come from the registry; Dart skips types newer than this iOS, so their units exist here
      let u = HKUnit(from: args["unit"] as? String ?? "count")
      guard let q = type as? HKQuantityType, q.is(compatibleWith: u) else {
        done(FlutterError(code: "bad_unit", message: id, details: nil)); return
      }
      unit = u
    }
    let handle: ([HKSample], [HKDeletedObject], HKQueryAnchor?, Bool) -> Void = { samples, deleted, anchor, more in
      done(self.page(samples, unit: unit, deleted: deleted, anchor: anchor, more: more))
    }
    if recent {
      let start = Date().addingTimeInterval(-Self.recentWindow)
      let q = HKSampleQuery(sampleType: type, predicate: HKQuery.predicateForSamples(withStart: start, end: nil),
                            limit: limit, sortDescriptors: [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: false)]) {
        _, samples, error in
        if let error {
          done(FlutterError(code: "healthkit", message: error.localizedDescription, details: (error as NSError).code)); return
        }
        handle(samples ?? [], [], nil, false)
      }
      store.execute(q)
      return
    }
    let predicate = days > 0
      ? HKQuery.predicateForSamples(withStart: Date().addingTimeInterval(-Double(days) * 86400), end: nil) : nil
    let q = HKAnchoredObjectQuery(type: type, predicate: predicate, anchor: storedAnchor(key), limit: limit) {
      _, samples, deleted, newAnchor, error in
      if let error {
        done(FlutterError(code: "healthkit", message: error.localizedDescription, details: (error as NSError).code)); return
      }
      let samples = samples ?? []
      handle(samples, deleted ?? [], newAnchor, samples.count >= limit)
    }
    store.execute(q)
  }

  /// Columnar page with a source table (samples refer to it by index).
  private func page(_ samples: [HKSample], unit: HKUnit?, deleted: [HKDeletedObject], anchor: HKQueryAnchor?, more: Bool)
    -> [String: Any] {
    var uuid: [String] = [], start: [Double] = [], end: [Double] = [], value: [Double] = [], src: [Int] = []
    var tz: [Any] = []
    var sources: [[String: Any]] = [], index: [String: Int] = [:]
    for s in samples {
      let v: Double
      if let unit, let q = s as? HKQuantitySample {
        guard q.quantity.is(compatibleWith: unit) else { continue }   // doubleValue would raise an uncatchable exception
        v = q.quantity.doubleValue(for: unit)
      } else if let c = s as? HKCategorySample {
        v = Double(c.value)
      } else {
        continue
      }
      guard v.isFinite else { continue }
      let rev = s.sourceRevision, dev = s.device
      let product = rev.productType ?? ""
      let sourceKey = [rev.source.bundleIdentifier, product, dev?.name ?? "", dev?.model ?? "",
                       Self.osVersion(rev.operatingSystemVersion)].joined(separator: "|")
      var i = index[sourceKey]
      if i == nil {
        i = sources.count
        index[sourceKey] = i
        sources.append([
          "name": String(rev.source.name.prefix(120)),
          "bundle": String(rev.source.bundleIdentifier.prefix(200)),
          "product": String(product.prefix(60)),
          "device_type": Self.deviceType(product: product, device: dev),
          "device_name": String((dev?.name ?? "").prefix(120)),
          "device_model": String((dev?.model ?? "").prefix(120)),
          "manufacturer": String((dev?.manufacturer ?? "").prefix(120)),
          "os_version": Self.osVersion(rev.operatingSystemVersion),
        ])
      }
      uuid.append(s.uuid.uuidString.lowercased())
      start.append(s.startDate.timeIntervalSince1970)
      end.append(s.endDate.timeIntervalSince1970)
      value.append(v)
      src.append(i!)
      tz.append((s.metadata?[HKMetadataKeyTimeZone] as? String).map { String($0.prefix(64)) } ?? NSNull())
    }
    return [
      "uuid": uuid, "start": start, "end": end, "value": value, "src": src, "tz": tz, "sources": sources,
      "deleted": deleted.map { $0.uuid.uuidString.lowercased() },
      "anchor": encode(anchor) ?? NSNull(), "more": more, "count": samples.count, "tzName": TimeZone.current.identifier,
    ]
  }

  /// watch / phone / other from the product type ("Watch7,1", "iPhone17,3") or the device (as OW's _inferDeviceType).
  static func deviceType(product: String, device: HKDevice?) -> String {
    let p = product.lowercased(), m = (device?.model ?? "").lowercased(), n = (device?.name ?? "").lowercased()
    if p.hasPrefix("watch") || m.contains("watch") || n.contains("watch") { return "watch" }
    if p.hasPrefix("iphone") || m.contains("iphone") || n.contains("iphone") { return "phone" }
    return "other"
  }

  static func osVersion(_ v: OperatingSystemVersion) -> String {
    "\(v.majorVersion).\(v.minorVersion)\(v.patchVersion > 0 ? ".\(v.patchVersion)" : "")"
  }
}
