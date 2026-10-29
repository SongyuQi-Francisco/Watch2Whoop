import Foundation

/// M4 (docs/ios-native-2.0.md section 4): body age and the coaches' inputs (uploads `healthspan`); runs after the scores.
/// Empty until M4 lands: `implemented` is false, so `LocalCompute` skips it. The package fills in `run` and sets
/// `implemented` in this file only.
struct HealthspanStage: ComputeStage {
  static let name = "healthspan"
  static let package = "coach"
  static let implemented = false

  func run(_ ctx: ComputeContext) async -> ComputeStageReport {
    ComputeStageReport(stage: Self.name, state: "skipped")
  }
}
