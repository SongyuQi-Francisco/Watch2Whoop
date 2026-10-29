import Foundation

/// M6 (docs/ios-native-2.0.md section 4): reminders, training suggestions, nutrition targets (uploads `reminders`); runs last, since the rules read everything above.
/// Empty until M6 lands: `implemented` is false, so `LocalCompute` skips it. The package fills in `run` and sets
/// `implemented` in this file only.
struct RulesStage: ComputeStage {
  static let name = "rules"
  static let package = "rules"
  static let implemented = false

  func run(_ ctx: ComputeContext) async -> ComputeStageReport {
    ComputeStageReport(stage: Self.name, state: "skipped")
  }
}
