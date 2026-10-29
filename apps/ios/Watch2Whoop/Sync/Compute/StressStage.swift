import Foundation

/// M2 (docs/ios-native-2.0.md section 4): stress, resilience, body battery, HR recovery (uploads `stress`); runs after the scores, since it reads their hrv_night_ln.
/// Empty until M2 lands: `implemented` is false, so `LocalCompute` skips it. The package fills in `run` and sets
/// `implemented` in this file only.
struct StressStage: ComputeStage {
  static let name = "stress"
  static let package = "stress"
  static let implemented = false

  func run(_ ctx: ComputeContext) async -> ComputeStageReport {
    ComputeStageReport(stage: Self.name, state: "skipped")
  }
}
