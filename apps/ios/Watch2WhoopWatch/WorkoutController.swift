import Foundation
import HealthKit
import WatchKit

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

  /// The permission sheet (read heart rate, write the workout). Only the person can answer it, so it waits until the app
  /// is on screen: a launch in the background (a phone push for the complications) must not use up the one ask.
  func requestAuthorization() async {
    guard HKHealthStore.isHealthDataAvailable() else { return }
    await Self.untilActive()
    try? await store.requestAuthorization(toShare: [HKObjectType.workoutType()], read: [Self.heartRate])
  }

  @MainActor private static func untilActive() async {
    guard WKApplication.shared().applicationState != .active else { return }
    for await _ in NotificationCenter.default.notifications(named: WKApplication.didBecomeActiveNotification) { return }
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

  /// The app was closed or the watch restarted during a session (phase 2a: a running session is resumed on launch):
  /// take the system's running workout back, or end it when the session on the watch was left for hours. No workout to
  /// recover = the light mode (sets only).
  func recover(thenEnd: Bool = false) {
    guard session == nil, HKHealthStore.isHealthDataAvailable() else { light = session == nil; return }
    store.recoverActiveWorkoutSession { [weak self] s, _ in
      DispatchQueue.main.async {
        guard let self else { return }
        guard let s else {
          self.light = !thenEnd
          return
        }
        let b = s.associatedWorkoutBuilder()
        b.dataSource = HKLiveWorkoutDataSource(healthStore: self.store, workoutConfiguration: s.workoutConfiguration)
        s.delegate = self
        b.delegate = self
        self.session = s
        self.builder = b
        self.light = false
        if thenEnd { s.end() }
      }
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
