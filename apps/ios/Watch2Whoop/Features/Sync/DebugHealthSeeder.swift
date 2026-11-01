#if DEBUG && targetEnvironment(simulator)
import Foundation
import HealthKit

/// Simulator debug builds only (#340 N9 regression): writes synthetic Health data so the whole chain (first sync, server
/// derivation, on-device scoring, upload) can run on a simulator. The native counterpart of the Flutter app's
/// DebugHealthSeeder (apps/watch2whoop/ios/Runner/AppDelegate.swift), which the native app had no equivalent of.
///
/// `SIMCTL_CHILD_W2W_DEV_SEED_HEALTH=<days>` (default 21) at launch: asks for write access once (the simulator's sheet),
/// then writes `days` days ending now. Every sample carries metadata `W2WTestData = 1`; the values are obviously made up
/// (weight 99.9 kg). The history is written once per simulator: when test heart rate samples already exist (any app),
/// only one new heart rate reading is added (new data for the next round), so a reinstall finds the same history in
/// Health. The result is printed as one line `W2W_SEED …`.
enum DebugHealthSeeder {
  static let store = HKHealthStore()
  static let meta: [String: Any] = ["W2WTestData": 1]

  static func runIfRequested() {
    guard let raw = ProcessInfo.processInfo.environment["W2W_DEV_SEED_HEALTH"], !raw.isEmpty else { return }
    let days = max(1, min(Int(raw) ?? 21, 120))
    Task.detached {
      let line: String
      do { line = try await seed(days: days) } catch { line = "failed: \(error.localizedDescription)" }
      NSLog("W2W_SEED %@", line)
    }
  }

  static func seed(days: Int, now: Date = Date()) async throws -> String {
    let q = { (id: HKQuantityTypeIdentifier) in HKQuantityType(id) }
    let share: Set<HKSampleType> = [
      q(.heartRate), q(.restingHeartRate), q(.heartRateVariabilitySDNN), q(.stepCount), q(.activeEnergyBurned),
      q(.basalEnergyBurned), q(.oxygenSaturation), q(.respiratoryRate), q(.bodyMass), q(.dietaryWater),
      q(.distanceWalkingRunning), HKCategoryType(.sleepAnalysis), HKObjectType.workoutType(), HKSeriesType.heartbeat(),
    ]
    try await store.requestAuthorization(toShare: share, read: [q(.heartRate)])
    if try await alreadySeeded() {
      // already there: one new heart rate reading, like a watch adding data (the next round has something new)
      let bpm = HKUnit.count().unitDivided(by: .minute())
      try await store.save(HKQuantitySample(type: q(.heartRate), quantity: HKQuantity(unit: bpm, doubleValue: 71),
                                            start: now, end: now, metadata: meta))
      return "already seeded; added one heart rate reading"
    }

    let cal = Calendar.current
    let bpm = HKUnit.count().unitDivided(by: .minute())
    var total = 0, workouts = 0
    for d in stride(from: days - 1, through: 0, by: -1) {
      let day = cal.startOfDay(for: now.addingTimeInterval(-Double(d) * 86400))
      var samples: [HKSample] = []
      func qs(_ id: HKQuantityTypeIdentifier, _ unit: HKUnit, _ v: Double, _ start: Date, _ end: Date? = nil) {
        guard start <= now else { return }
        samples.append(HKQuantitySample(type: q(id), quantity: HKQuantity(unit: unit, doubleValue: v),
                                        start: start, end: min(end ?? start, now), metadata: meta))
      }
      let wobble = Double((d * 37) % 11) - 5            // a different made-up day each day, the same on every run
      let trains = d % 2 == 1                           // a strength workout every other evening
      // Night before this day: 23:30 → about 07:00, ninety-minute cycles (the day's sleep wakes up on it)
      let bed = day.addingTimeInterval(-30 * 60 + wobble * 60)
      let stages: [(HKCategoryValueSleepAnalysis, Double)] = [
        (.asleepCore, 35), (.asleepDeep, 45), (.asleepCore, 20), (.asleepREM, 20), (.awake, 4),
        (.asleepCore, 40), (.asleepDeep, 30), (.asleepREM, 25), (.asleepCore, 45), (.asleepREM, 35),
        (.awake, 3), (.asleepCore, 50), (.asleepREM, 30 + wobble), (.awake, 5),
      ]
      var t = bed
      for (stage, minutes) in stages {
        let end = t.addingTimeInterval(minutes * 60)
        if end <= now {
          samples.append(HKCategorySample(type: HKCategoryType(.sleepAnalysis), value: stage.rawValue, start: t,
                                          end: end, metadata: meta))
        }
        t = end
      }
      let wake = t
      // Overnight readings
      qs(.restingHeartRate, bpm, 56 + wobble * 0.6, wake.addingTimeInterval(3600))
      for k in 0..<3 {
        let at = bed.addingTimeInterval(Double(k + 1) * 2 * 3600)
        qs(.heartRateVariabilitySDNN, .secondUnit(with: .milli), 52 - wobble * 1.5 + Double(k * 4), at)
        qs(.respiratoryRate, bpm, 14.2 + wobble * 0.05, at)
        qs(.oxygenSaturation, .percent(), 0.97 - Double(k) * 0.005, at)
      }
      // Heart rate every 10 minutes: asleep in the 50s, awake 65–85, the workout 120–150
      let workoutStart = day.addingTimeInterval(18 * 3600)
      for i in 0..<144 {
        let at = day.addingTimeInterval(Double(i) * 600)
        let asleep = at < wake || at >= day.addingTimeInterval(23.5 * 3600)
        let inWorkout = trains && at >= workoutStart && at < workoutStart.addingTimeInterval(45 * 60)
        let base = inWorkout ? 128 + Double(i % 4) * 7 : asleep ? 52 + Double(i % 5) : 66 + Double((i * 7) % 19)
        qs(.heartRate, bpm, base + wobble * 0.4, at)
      }
      // Steps, distance and energy every hour from 07:00 to 22:00; resting energy all day
      for h in 0..<24 {
        let s = day.addingTimeInterval(Double(h) * 3600)
        qs(.basalEnergyBurned, .kilocalorie(), 78, s, s.addingTimeInterval(3600))
        guard (7...21).contains(h) else { continue }
        let steps = Double(250 + ((h * 13 + d * 5) % 9) * 60)
        qs(.stepCount, .count(), steps, s, s.addingTimeInterval(3000))
        qs(.distanceWalkingRunning, .meter(), steps * 0.72, s, s.addingTimeInterval(3000))
        qs(.activeEnergyBurned, .kilocalorie(), 12 + Double(h % 5) * 3, s, s.addingTimeInterval(3000))
      }
      if d == 0 {
        qs(.bodyMass, .gramUnit(with: .kilo), 99.9, now.addingTimeInterval(-1800))
        qs(.dietaryWater, .literUnit(with: .milli), 330, now.addingTimeInterval(-5400))
      }
      if !samples.isEmpty { try await store.save(samples) }
      total += samples.count
      if trains, workoutStart.addingTimeInterval(45 * 60) <= now {
        try await workout(start: workoutStart, minutes: 45)
        workouts += 1
      }
    }
    let beats = try await heartbeats(now)
    return "saved \(total) samples, \(workouts) workouts, \(beats) heartbeat series over \(days) days"
  }

  private static func alreadySeeded() async throws -> Bool {
    try await withCheckedThrowingContinuation { c in
      let pred = HKQuery.predicateForObjects(withMetadataKey: "W2WTestData")
      let query = HKSampleQuery(sampleType: HKQuantityType(.heartRate), predicate: pred, limit: 1,
                                sortDescriptors: nil) { _, list, error in
        if let error { c.resume(throwing: error) } else { c.resume(returning: !(list ?? []).isEmpty) }
      }
      store.execute(query)
    }
  }

  private static func workout(start: Date, minutes: Double) async throws {
    let cfg = HKWorkoutConfiguration()
    cfg.activityType = .traditionalStrengthTraining
    let builder = HKWorkoutBuilder(healthStore: store, configuration: cfg, device: nil)
    let end = start.addingTimeInterval(minutes * 60)
    try await builder.beginCollection(at: start)
    let kcal = HKQuantitySample(type: HKQuantityType(.activeEnergyBurned),
                                quantity: HKQuantity(unit: .kilocalorie(), doubleValue: 240), start: start, end: end,
                                metadata: meta)
    try await builder.addSamples([kcal])
    try await builder.addMetadata(meta)
    try await builder.endCollection(at: end)
    _ = try await builder.finishWorkout()
  }

  /// Three beat-to-beat series (60 beats, RR alternating 980 / 1020 ms) 1–3 hours ago, for RawSync and local RMSSD.
  private static func heartbeats(_ now: Date) async throws -> Int {
    var n = 0
    for i in 0..<3 {
      let b = HKHeartbeatSeriesBuilder(healthStore: store, device: nil,
                                       start: now.addingTimeInterval(-Double(i + 1) * 3600))
      var t = 0.0
      for k in 0..<60 {
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
          b.addHeartbeatWithTimeInterval(sinceSeriesStartDate: t, precededByGap: false) { _, error in
            if let error { c.resume(throwing: error) } else { c.resume() }
          }
        }
        t += k % 2 == 0 ? 0.98 : 1.02
      }
      try await b.addMetadata(meta)
      _ = try await b.finishSeries()
      n += 1
    }
    return n
  }
}
#endif
