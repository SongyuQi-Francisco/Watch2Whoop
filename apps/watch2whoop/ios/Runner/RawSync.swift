import Flutter
import Foundation
import HealthKit

/// Reads the two HealthKit types the OW SDK (iOS 0.15) does not sync, for upload straight to Body Engine
/// (POST /v1/ingest/healthkit, done in Dart: lib/src/raw_sync.dart):
/// - beat-to-beat series of each HRV reading (HKHeartbeatSeriesSample + HKHeartbeatSeriesQuery): seconds since the
///   series start and precededByGap per beat;
/// - ECG recordings (HKElectrocardiogram + HKElectrocardiogramQuery): Lead I-like voltage in µV as int16 little-endian
///   (base64), sampling frequency, Apple's classification, average heart rate, symptoms status.
/// Incremental with one HKAnchoredObjectQuery anchor per type. The anchor of a read is handed to Dart and only stored
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

  private enum Kind: String {
    case heartbeat, ecg
  }

  /// HRV SDNN is the parent type of the heartbeat series: HealthKit raises NSInvalidArgumentException (an uncatchable
  /// crash in Swift) when the series is requested without its parent in the same call ("Authorization for
  /// HKQuantityTypeIdentifierHeartRateVariabilitySDNN should also be requested…"), which made build 1.0.0+4 crash on open.
  /// The OW sheet already asks for HRV SDNN, so repeating it here adds nothing to the sheet.
  private var readTypes: Set<HKObjectType> {
    [HKQuantityType(.heartRateVariabilitySDNN), HKSeriesType.heartbeat(), HKObjectType.electrocardiogramType()]
  }

  /// Handles the raw* methods of the watch2whoop/native channel; false = not one of them.
  func handle(_ call: FlutterMethodCall, _ result: @escaping FlutterResult) -> Bool {
    let args = call.arguments as? [String: Any] ?? [:]
    switch call.method {
    case "rawRequestAuth":
      guard HKHealthStore.isHealthDataAvailable() else { result(false); return true }
      store.requestAuthorization(toShare: nil, read: readTypes) { ok, _ in DispatchQueue.main.async { result(ok) } }
    case "rawRead":
      guard let kind = Kind(rawValue: args["kind"] as? String ?? "") else {
        result(FlutterError(code: "bad_args", message: "kind", details: nil)); return true
      }
      read(kind, daysBack: args["daysBack"] as? Int ?? 90, limit: max(1, args["limit"] as? Int ?? 100)) { out in
        DispatchQueue.main.async { result(out) }
      }
    case "rawCommit":
      if let kind = Kind(rawValue: args["kind"] as? String ?? ""), let anchor = args["anchor"] as? String {
        UserDefaults.standard.set(anchor, forKey: anchorKey + kind.rawValue)
      }
      result(nil)
    case "rawReset":
      for k in [Kind.heartbeat, .ecg] { UserDefaults.standard.removeObject(forKey: anchorKey + k.rawValue) }
      result(nil)
    default:
      return false
    }
    return true
  }

  private func storedAnchor(_ kind: Kind) -> HKQueryAnchor? {
    guard let s = UserDefaults.standard.string(forKey: anchorKey + kind.rawValue), let data = Data(base64Encoded: s) else { return nil }
    return try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: data)
  }

  private func encode(_ anchor: HKQueryAnchor?) -> String? {
    guard let anchor, let data = try? NSKeyedArchiver.archivedData(withRootObject: anchor, requiringSecureCoding: true) else { return nil }
    return data.base64EncodedString()
  }

  /// One page of new samples after the stored anchor: {items, deleted, anchor, more} or a FlutterError.
  private func read(_ kind: Kind, daysBack: Int, limit: Int, done: @escaping (Any) -> Void) {
    let type: HKSampleType = kind == .heartbeat ? HKSeriesType.heartbeat() : HKObjectType.electrocardiogramType()
    let predicate = daysBack > 0
      ? HKQuery.predicateForSamples(withStart: Date().addingTimeInterval(-Double(daysBack) * 86400), end: nil)
      : nil
    let q = HKAnchoredObjectQuery(type: type, predicate: predicate, anchor: storedAnchor(kind), limit: limit) {
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
      }
    }
    store.execute(q)
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
