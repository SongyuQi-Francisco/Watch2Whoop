import Foundation
import HealthKit

/// The HealthKit workout behind a session (plan 4.2): `HKWorkoutSession` + `HKLiveWorkoutBuilder`, traditional strength
/// training, indoors. It keeps the app running with the wrist down, gives live heart rate and, when it ends, saves one
/// workout to Health, which reaches the server through the phone's existing sync (no new channel).
///
/// When the session cannot start (no permission, or another app's workout is running: V2 in plan 4.10) the watch goes on
/// in the light mode: sets are logged the same way, only without heart rate.
final class WorkoutController: NSObject {
  var onHeartRate: ((Int) -> Void)?
  /// No workout session: logging only.
  private(set) var light = false

  private let store = HKHealthStore()
  private var session: HKWorkoutSession?
  private var builder: HKLiveWorkoutBuilder?

  private static let heartRate = HKQuantityType(.heartRate)

  /// The permission sheet (read heart rate, write the workout). Only the person can answer it.
  func requestAuthorization() async {
    guard HKHealthStore.isHealthDataAvailable() else { return }
    try? await store.requestAuthorization(toShare: [HKObjectType.workoutType()], read: [Self.heartRate])
  }

  func start() {
    guard session == nil else { return }
    guard HKHealthStore.isHealthDataAvailable() else { light = true; return }
    let config = HKWorkoutConfiguration()
    config.activityType = .traditionalStrengthTraining
    config.locationType = .indoor
    do {
      let s = try HKWorkoutSession(healthStore: store, configuration: config)
      let b = s.associatedWorkoutBuilder()
      b.dataSource = HKLiveWorkoutDataSource(healthStore: store, workoutConfiguration: config)
      s.delegate = self
      b.delegate = self
      session = s
      builder = b
      light = false
      let now = Date()
      s.startActivity(with: now)
      b.beginCollection(withStart: now) { _, _ in }
    } catch {
      light = true
    }
  }

  /// Ends the session; the builder saves the workout once the session says it has ended.
  func end() {
    guard let s = session else { return }
    s.end()
  }

  private func finish(at date: Date) {
    guard let b = builder else { return }
    b.endCollection(withEnd: date) { [weak self] _, _ in
      b.finishWorkout { _, _ in
        DispatchQueue.main.async {
          self?.session = nil
          self?.builder = nil
        }
      }
    }
  }
}

extension WorkoutController: HKWorkoutSessionDelegate {
  func workoutSession(_ workoutSession: HKWorkoutSession, didChangeTo toState: HKWorkoutSessionState,
                      from fromState: HKWorkoutSessionState, date: Date) {
    if toState == .ended { finish(at: date) }
  }

  func workoutSession(_ workoutSession: HKWorkoutSession, didFailWithError error: Error) {
    DispatchQueue.main.async {
      self.light = true
      self.session = nil
      self.builder = nil
    }
  }
}

extension WorkoutController: HKLiveWorkoutBuilderDelegate {
  func workoutBuilderDidCollectEvent(_ workoutBuilder: HKLiveWorkoutBuilder) {}

  func workoutBuilder(_ workoutBuilder: HKLiveWorkoutBuilder, didCollectDataOf collectedTypes: Set<HKSampleType>) {
    guard collectedTypes.contains(Self.heartRate),
          let q = workoutBuilder.statistics(for: Self.heartRate)?.mostRecentQuantity() else { return }
    let bpm = Int(q.doubleValue(for: HKUnit.count().unitDivided(by: .minute())).rounded())
    DispatchQueue.main.async { self.onHeartRate?(bpm) }
  }
}
