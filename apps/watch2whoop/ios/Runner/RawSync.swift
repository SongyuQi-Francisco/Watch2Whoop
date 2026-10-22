import Flutter
import Foundation
import HealthKit
import UIKit

/// Reads the HealthKit types the OW SDK (iOS 0.15) does not sync, for upload straight to Body Engine
/// (POST /v1/ingest/healthkit, done in Dart: lib/src/raw_sync.dart):
/// - beat-to-beat series of each HRV reading (HKHeartbeatSeriesSample + HKHeartbeatSeriesQuery): seconds since the
///   series start and precededByGap per beat;
/// - ECG recordings (HKElectrocardiogram + HKElectrocardiogramQuery): Lead I-like voltage in µV as int16 little-endian
///   (base64), sampling frequency, Apple's classification, average heart rate, symptoms status;
/// - drinks (HKQuantityTypeIdentifierDietaryWater, issue #135): start time and millilitres (read only);
/// - the quantity types of the registry (lib/src/hk_types.dart: stand time, exercise minutes, wrist temperature …): kind =
///   the HealthKit identifier, read in the unit Dart passes, {uuid, start, end, value, source} per sample.
/// Incremental with one HKAnchoredObjectQuery anchor per type. A kind "set:kind" (e.g. "resync:heartbeat") reads with a
/// separate anchor set: the forced re-sync reads a range again from scratch without moving the regular anchors. The anchor of a read is handed to Dart and only stored
/// (`rawCommit`) after the server accepted that batch, so an interrupted upload is read again next time (the server skips
/// UUIDs it already has). Anchors live in UserDefaults (app container, gone with the app).
final class RawSync {
  static let shared = RawSync()
  private let store = HKHealthStore()
  private let anchorKey = "w2w.raw.anchor."
  private let iso: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
  }()

  private enum Kind: String, CaseIterable {
    case heartbeat, ecg, water
  }

  /// HRV SDNN is the parent type of the heartbeat series: HealthKit raises NSInvalidArgumentException (an uncatchable
  /// crash in Swift) when the series is requested without its parent in the same call ("Authorization for
  /// HKQuantityTypeIdentifierHeartRateVariabilitySDNN should also be requested…"), which made build 1.0.0+4 crash on open.
  /// The OW sheet already asks for HRV SDNN, so repeating it here adds nothing to the sheet. Dietary water has no parent type.
  private var baseReadTypes: Set<HKObjectType> {
    [HKQuantityType(.heartRateVariabilitySDNN), HKSeriesType.heartbeat(), HKObjectType.electrocardiogramType(),
     HKQuantityType(.dietaryWater)]
  }

  /// The registry's quantity types (identifiers from Dart) this iOS knows; unknown ones (older iOS) are left out.
  private static func quantityTypes(_ ids: [String]) -> Set<HKObjectType> {
    Set(ids.compactMap { HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: $0)) })
  }

  private func readTypes(_ args: [String: Any]) -> Set<HKObjectType> {
    baseReadTypes.union(Self.quantityTypes(args["types"] as? [String] ?? []))
  }

  /// Anchor of a kind: "heartbeat" / "ecg" / "water" / an identifier, optionally "set:kind" for a separate set.
  private func anchorName(_ kind: String) -> String { anchorKey + kind }

  /// Handles the raw* methods of the watch2whoop/native channel; false = not one of them.
  func handle(_ call: FlutterMethodCall, _ result: @escaping FlutterResult) -> Bool {
    let args = call.arguments as? [String: Any] ?? [:]
    switch call.method {
    case "rawAuthStatus":
      authStatus(readTypes(args)) { s in DispatchQueue.main.async { result(s) } }
    case "rawRequestAuth":
      guard HKHealthStore.isHealthDataAvailable() else { result(["asked": false, "error": "unavailable"]); return true }
      requestAuth(readTypes(args)) { out in result(out) }
    case "rawRead":
      let name = args["kind"] as? String ?? ""
      let base = name.split(separator: ":").last.map(String.init) ?? name
      let limit = max(1, args["limit"] as? Int ?? 100), days = args["daysBack"] as? Int ?? 90
      if let kind = Kind(rawValue: base) {
        read(kind, anchor: name, daysBack: days, limit: limit) { out in DispatchQueue.main.async { result(out) } }
      } else if base.hasPrefix("HKQuantityTypeIdentifier"),
                let type = HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: base)) {
        readQuantity(type, unit: HKUnit(from: args["unit"] as? String ?? "count"), anchor: name, daysBack: days, limit: limit) {
          out in DispatchQueue.main.async { result(out) }
        }
      } else if base.hasPrefix("HKQuantityTypeIdentifier") {
        result(["items": [], "deleted": [], "anchor": NSNull(), "more": false, "unsupported": true])   // older iOS
      } else {
        result(FlutterError(code: "bad_args", message: "kind", details: nil))
      }
    case "rawCommit":
      if let kind = args["kind"] as? String, !kind.isEmpty, let anchor = args["anchor"] as? String {
        UserDefaults.standard.set(anchor, forKey: anchorName(kind))
      }
      result(nil)
    case "healthAskStatus":
      askStatus { out in DispatchQueue.main.async { result(out) } }
    case "healthLatest":
      latest(args["types"] as? [String] ?? []) { out in DispatchQueue.main.async { result(out) } }
    case "rawReset":
      // set: only that anchor set ("resync"); none: every regular anchor (sign out, range change)
      let prefix = anchorKey + ((args["set"] as? String).map { $0 + ":" } ?? "")
      for key in UserDefaults.standard.dictionaryRepresentation().keys where key.hasPrefix(prefix) {
        if args["set"] != nil || !key.dropFirst(anchorKey.count).contains(":") { UserDefaults.standard.removeObject(forKey: key) }
      }
      result(nil)
    default:
      return false
    }
    return true
  }

  /// The data-status types (lib/src/data_status.dart) -> the HealthKit types the app asks for. The heartbeat series goes
  /// with its parent HRV SDNN, as in readTypes. The registry types (wrist temperature …) go with the RawSync sheet.
  private var askTypes: [String: Set<HKObjectType>] {
    [
      "heart_rate": [HKQuantityType(.heartRate)],
      "hrv": [HKQuantityType(.heartRateVariabilitySDNN)],
      "resting_heart_rate": [HKQuantityType(.restingHeartRate)],
      "sleep": [HKCategoryType(.sleepAnalysis)],
      "steps": [HKQuantityType(.stepCount)],
      "active_energy": [HKQuantityType(.activeEnergyBurned)],
      "workouts": [HKObjectType.workoutType()],
      "weight": [HKQuantityType(.bodyMass)],
      "blood_oxygen": [HKQuantityType(.oxygenSaturation)],
      "respiratory_rate": [HKQuantityType(.respiratoryRate)],
      "vo2max": [HKQuantityType(.vo2Max)],
      "heartbeats": [HKQuantityType(.heartRateVariabilitySDNN), HKSeriesType.heartbeat()],
      "ecg": [HKObjectType.electrocardiogramType()],
      "water": [HKQuantityType(.dietaryWater)],
    ]
  }

  /// Per type: was the iOS prompt already shown for it (true) or would asking show the sheet (false)? HealthKit says
  /// nothing about what the user chose for reading; types it cannot answer are left out.
  private func askStatus(_ done: @escaping ([String: Bool]) -> Void) {
    guard HKHealthStore.isHealthDataAvailable() else { done([:]); return }
    let group = DispatchGroup()
    let lock = NSLock()
    var out: [String: Bool] = [:]
    for (key, types) in askTypes {
      group.enter()
      store.getRequestStatusForAuthorization(toShare: [], read: types) { status, error in
        lock.lock()
        if error == nil && status != .unknown { out[key] = status == .unnecessary }
        lock.unlock()
        group.leave()
      }
    }
    group.notify(queue: .global()) { done(out) }
  }

  /// Whether iOS still has to show the sheet for readTypes: "unnecessary" (already asked; what the user chose stays
  /// unknown), "shouldRequest" or "unknown". Reading a type that was never asked fails with "Authorization not
  /// determined" (HKError 5), so this, not a flag of our own, decides whether to ask.
  private func authStatus(_ types: Set<HKObjectType>, _ done: @escaping (String) -> Void) {
    guard HKHealthStore.isHealthDataAvailable() else { done("unknown"); return }
    store.getRequestStatusForAuthorization(toShare: [], read: types) { status, error in
      switch status {
      case .unnecessary where error == nil: done("unnecessary")
      case .shouldRequest where error == nil: done("shouldRequest")
      default: done("unknown")
      }
    }
  }

  /// The sheet for readTypes: {asked: Bool (iOS no longer needs to ask), error: String?}, on the main thread.
  /// Called right after the OW sheet closed, HealthKit cannot present the second sheet while the first one is still being
  /// dismissed ("Attempt to present HKHealthPrivacyHostAuthorizationViewController … whose view is not in the window
  /// hierarchy") and never calls the completion: the permissions step then waited forever and the types stayed undetermined.
  /// So: wait until nothing is presented, and when no sheet came up shortly after the request, answer instead of waiting.
  private func requestAuth(_ types: Set<HKObjectType>, _ done: @escaping ([String: Any]) -> Void) {
    var answered = false
    let finish = { (error: String?) in
      self.authStatus(types) { s in
        DispatchQueue.main.async {
          guard !answered else { return }
          answered = true
          var out: [String: Any] = ["asked": s == "unnecessary"]
          if let error { out["error"] = error }
          done(out)
        }
      }
    }
    whenNothingPresented { [self] in
      store.requestAuthorization(toShare: nil, read: types) { _, error in finish(error?.localizedDescription) }
      // No answer and still no sheet after this long: the presentation failed (the sheet itself took ~2.5 s to come up on
      // a cold start in the simulator).
      DispatchQueue.main.asyncAfter(deadline: .now() + 8) {
        if !answered && Self.topPresented() == nil { finish("not_presented") }
      }
    }
  }

  /// The view controller presented over the app's root (the Health sheet while it is up), nil when nothing is.
  private static func topPresented() -> UIViewController? {
    let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap { $0.windows }
    return (windows.first { $0.isKeyWindow } ?? windows.first)?.rootViewController?.presentedViewController
  }

  /// Runs [then] on the main thread once no view controller is presented or being dismissed (at most ~8 s), plus a short
  /// pause for the dismissal animation to finish.
  private func whenNothingPresented(_ then: @escaping () -> Void, tries: Int = 40) {
    DispatchQueue.main.async {
      if Self.topPresented() != nil, tries > 0 {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { self.whenNothingPresented(then, tries: tries - 1) }
        return
      }
      DispatchQueue.main.asyncAfter(deadline: .now() + 0.4, execute: then)
    }
  }

  private func storedAnchor(_ name: String) -> HKQueryAnchor? {
    guard let s = UserDefaults.standard.string(forKey: anchorName(name)), let data = Data(base64Encoded: s) else { return nil }
    return try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: data)
  }

  private func encode(_ anchor: HKQueryAnchor?) -> String? {
    guard let anchor, let data = try? NSKeyedArchiver.archivedData(withRootObject: anchor, requiringSecureCoding: true) else { return nil }
    return data.base64EncodedString()
  }

  /// One page of new samples after the stored anchor: {items, deleted, anchor, more} or a FlutterError.
  private func read(_ kind: Kind, anchor name: String, daysBack: Int, limit: Int, done: @escaping (Any) -> Void) {
    let type: HKSampleType = switch kind {
    case .heartbeat: HKSeriesType.heartbeat()
    case .ecg: HKObjectType.electrocardiogramType()
    case .water: HKQuantityType(.dietaryWater)
    }
    let predicate = daysBack > 0
      ? HKQuery.predicateForSamples(withStart: Date().addingTimeInterval(-Double(daysBack) * 86400), end: nil)
      : nil
    let q = HKAnchoredObjectQuery(type: type, predicate: predicate, anchor: storedAnchor(name), limit: limit) {
      [weak self] _, samples, deleted, newAnchor, error in
      guard let self else { return }
      if let error {
        done(FlutterError(code: "healthkit", message: error.localizedDescription, details: (error as NSError).code))
        return
      }
      let samples = samples ?? []
      let finish = { (items: [[String: Any]]) in
        done([
          "items": items,
          "deleted": (deleted ?? []).map { $0.uuid.uuidString.lowercased() },
          "anchor": self.encode(newAnchor) ?? NSNull(),
          "more": samples.count >= limit,
        ])
      }
      switch kind {
      case .heartbeat: self.beats(samples.compactMap { $0 as? HKHeartbeatSeriesSample }, finish)
      case .ecg: self.ecgs(samples.compactMap { $0 as? HKElectrocardiogram }, finish)
      case .water: finish(self.drinks(samples.compactMap { $0 as? HKQuantitySample }))
      }
    }
    store.execute(q)
  }

  /// One page of a registry quantity type after its anchor: {uuid, start, end, value (in `unit`), source}. A sample in an
  /// incompatible unit is left out (doubleValue would raise an uncatchable exception).
  private func readQuantity(_ type: HKQuantityType, unit: HKUnit, anchor name: String, daysBack: Int, limit: Int,
                            done: @escaping (Any) -> Void) {
    guard type.is(compatibleWith: unit) else {
      done(FlutterError(code: "bad_unit", message: "\(type.identifier) / \(unit.unitString)", details: nil)); return
    }
    let predicate = daysBack > 0
      ? HKQuery.predicateForSamples(withStart: Date().addingTimeInterval(-Double(daysBack) * 86400), end: nil)
      : nil
    let q = HKAnchoredObjectQuery(type: type, predicate: predicate, anchor: storedAnchor(name), limit: limit) {
      [weak self] _, samples, deleted, newAnchor, error in
      guard let self else { return }
      if let error {
        done(FlutterError(code: "healthkit", message: error.localizedDescription, details: (error as NSError).code))
        return
      }
      let samples = samples ?? []
      let items: [[String: Any]] = samples.compactMap { $0 as? HKQuantitySample }.compactMap { s in
        guard s.quantity.is(compatibleWith: unit) else { return nil }
        let v = s.quantity.doubleValue(for: unit)
        guard v.isFinite else { return nil }
        return ["uuid": s.uuid.uuidString.lowercased(), "start": self.iso.string(from: s.startDate),
                "end": self.iso.string(from: s.endDate), "value": v, "source": s.sourceRevision.source.name]
      }
      done([
        "items": items,
        "deleted": (deleted ?? []).map { $0.uuid.uuidString.lowercased() },
        "anchor": self.encode(newAnchor) ?? NSNull(),
        "more": samples.count >= limit,
      ])
    }
    store.execute(q)
  }

  /// The newest sample on the phone per type identifier (ISO 8601 of its start; missing = none, or not allowed to read):
  /// the sync center's "on the phone" column.
  private func latest(_ ids: [String], _ done: @escaping ([String: String]) -> Void) {
    guard HKHealthStore.isHealthDataAvailable() else { done([:]); return }
    let group = DispatchGroup()
    let lock = NSLock()
    var out: [String: String] = [:]
    for id in ids {
      let type: HKSampleType?
      switch id {
      case "HKWorkoutTypeIdentifier": type = HKObjectType.workoutType()
      case "HKDataTypeIdentifierHeartbeatSeries": type = HKSeriesType.heartbeat()
      case "HKDataTypeIdentifierElectrocardiogram": type = HKObjectType.electrocardiogramType()
      default:
        type = HKQuantityType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: id))
          ?? HKCategoryType.categoryType(forIdentifier: HKCategoryTypeIdentifier(rawValue: id))
      }
      guard let type else { continue }
      group.enter()
      let q = HKSampleQuery(sampleType: type, predicate: nil, limit: 1,
                            sortDescriptors: [NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: false)]) {
        _, samples, _ in
        if let s = samples?.first {
          lock.lock()
          out[id] = self.iso.string(from: s.startDate)
          lock.unlock()
        }
        group.leave()
      }
      store.execute(q)
    }
    group.notify(queue: .global()) { done(out) }
  }

  /// Beat times of each series, one HKHeartbeatSeriesQuery after the other.
  private func beats(_ list: [HKHeartbeatSeriesSample], _ done: @escaping ([[String: Any]]) -> Void) {
    var out: [[String: Any]] = []
    func next(_ i: Int) {
      guard i < list.count else { done(out); return }
      let s = list[i]
      var t: [Double] = []
      var gaps: [Bool] = []
      let q = HKHeartbeatSeriesQuery(heartbeatSeries: s) { _, sinceStart, gap, finished, error in
        if error == nil {
          t.append((sinceStart * 10000).rounded() / 10000)
          gaps.append(gap)
        }
        if finished || error != nil {
          if error == nil && t.count >= 2 {
            out.append([
              "uuid": s.uuid.uuidString.lowercased(),
              "start": self.iso.string(from: s.startDate),
              "end": self.iso.string(from: s.endDate),
              "source": s.sourceRevision.source.name,
              "beats": t,
              "gaps": gaps,
            ])
          }
          next(i + 1)
        }
      }
      store.execute(q)
    }
    next(0)
  }

  private func ecgs(_ list: [HKElectrocardiogram], _ done: @escaping ([[String: Any]]) -> Void) {
    var out: [[String: Any]] = []
    let microvolt = HKUnit.voltUnit(with: .micro)
    func next(_ i: Int) {
      guard i < list.count else { done(out); return }
      let e = list[i]
      var data = Data()
      data.reserveCapacity(e.numberOfVoltageMeasurements * 2)
      let q = HKElectrocardiogramQuery(e) { _, result in
        switch result {
        case .measurement(let m):
          let uv = m.quantity(for: .appleWatchSimilarToLeadI)?.doubleValue(for: microvolt) ?? 0
          // Int(Double) traps on NaN / ±inf / beyond Int's range: clamp in Double first.
          var v = Int16(uv.isFinite ? max(-32768, min(32767, uv.rounded())) : 0).littleEndian
          withUnsafeBytes(of: &v) { data.append(contentsOf: $0) }
        case .done:
          if let hz = e.samplingFrequency?.doubleValue(for: .hertz()), hz > 0, !data.isEmpty {
            var item: [String: Any] = [
              "uuid": e.uuid.uuidString.lowercased(),
              "start": self.iso.string(from: e.startDate),
              "sample_hz": hz,
              "voltage_b64": data.base64EncodedString(),
              "classification": Self.classification(e.classification),
              "symptoms_status": Self.symptoms(e.symptomsStatus),
            ]
            if let hr = e.averageHeartRate?.doubleValue(for: HKUnit.count().unitDivided(by: .minute())) { item["avg_hr"] = hr }
            if let name = e.device?.name { item["device"] = name }
            if let sw = e.device?.softwareVersion { item["software_version"] = sw }
            out.append(item)
          }
          next(i + 1)
        case .error:
          next(i + 1)
        @unknown default:
          next(i + 1)
        }
      }
      store.execute(q)
    }
    next(0)
  }

  /// Drinks as {uuid, start, ml}. Samples the server would refuse (rawdata/api.py WaterSampleIn: 0 < ml <= 5000) are left
  /// out here, otherwise one odd entry would block the whole page forever; doubleValue is only called on a compatible unit.
  private func drinks(_ list: [HKQuantitySample]) -> [[String: Any]] {
    let ml = HKUnit.literUnit(with: .milli)
    return list.compactMap { s in
      guard s.quantity.is(compatibleWith: ml) else { return nil }
      let v = s.quantity.doubleValue(for: ml)
      guard v.isFinite, v > 0, v <= 5000 else { return nil }
      return ["uuid": s.uuid.uuidString.lowercased(), "start": self.iso.string(from: s.startDate), "ml": (v * 10).rounded() / 10]
    }
  }

  /// Case names as in rawdata/ecg.py CLASS_ZH (the server maps anything else to "unrecognized").
  static func classification(_ c: HKElectrocardiogram.Classification) -> String {
    switch c {
    case .notSet: return "notSet"
    case .sinusRhythm: return "sinusRhythm"
    case .atrialFibrillation: return "atrialFibrillation"
    case .inconclusiveLowHeartRate: return "inconclusiveLowHeartRate"
    case .inconclusiveHighHeartRate: return "inconclusiveHighHeartRate"
    case .inconclusivePoorReading: return "inconclusivePoorReading"
    case .inconclusiveOther: return "inconclusiveOther"
    case .unrecognized: return "unrecognized"
    @unknown default: return "unrecognized"
    }
  }

  static func symptoms(_ s: HKElectrocardiogram.SymptomsStatus) -> String {
    switch s {
    case .none: return "none"
    case .present: return "present"
    default: return "notSet"
    }
  }
}
