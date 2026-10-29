import Foundation

/// M1 (docs/ios-native-2.0.md section 4): daily values and sleep derived from HealthKit (uploads `daily`, `sleep`); runs before the scores, which read the derived daily_metrics and sleep_sessions.
/// Empty until M1 lands: `implemented` is false, so `LocalCompute` skips it. The package fills in `run` and sets
/// `implemented` in this file only.
struct DeriveStage: ComputeStage {
  static let name = "derive"
  static let package = "derive"
  static let implemented = false

  func run(_ ctx: ComputeContext) async -> ComputeStageReport {
    ComputeStageReport(stage: Self.name, state: "skipped")
  }
}
