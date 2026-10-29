import Foundation

/// M1c (docs/ios-native-2.0.md section 4): workouts, TRIMP, five-zone minutes, HR recovery (uploads `workouts`); runs before the scores, whose strain reads the workouts.
/// Empty until M1c lands: `implemented` is false, so `LocalCompute` skips it. The package fills in `run` and sets
/// `implemented` in this file only.
struct WorkoutsStage: ComputeStage {
  static let name = "workouts"
  static let package = "workouts"
  static let implemented = false

  func run(_ ctx: ComputeContext) async -> ComputeStageReport {
    ComputeStageReport(stage: Self.name, state: "skipped")
  }
}
