import Foundation

/// Tunables of the watch's set logging (docs/watch-app-plan.md section 2.1). Kept in one place so they can be changed
/// without touching the screens; none of them is a health threshold.
enum WatchParams {
  /// Rest between sets when the plan gives none: the server's own default (body_engine training/params.py
  /// `EST_DEFAULT_REST_SEC` / `ADD_DEFAULT_REST_SEC` = 90 s), so the watch and the plan card agree.
  static let defaultRestSec = 90
  /// Digital Crown step on the rest countdown (plan 2.1, input table: 「休息中：休息时间 ±15 秒」).
  static let restStepSec = 15
  /// Rest never goes below this (a 0 s rest would skip the rest screen without a tap).
  static let minRestSec = 15
  /// Rest never goes above the server's limit for `rest_sec` (training/api.py `le=600`).
  static let maxRestSec = 600
  /// Weight step of one crown detent when the exercise gives none: the smallest plate pair in most gyms
  /// (2 × 1.25 kg; plan 2.1 「每格 2.5 kg」).
  static let defaultWeightStepKg = 2.5
  /// How long the 「记好了」 receipt stays before the rest screen (mockup T5: 1.2 s).
  static let receiptSec: TimeInterval = 1.2
  /// Reps the crown can reach (a set of more than 100 reps is a typing error, not training).
  static let maxReps = 100
  /// Heaviest weight the crown can reach (the server accepts any; this only stops runaway scrolling).
  static let maxWeightKg = 500.0
  /// After this long without hearing from the phone the 「已连接」 mark shows the age in hours instead of minutes.
  static let ageHoursAfterMin = 60
  /// Event ids the phone remembers to drop duplicates (WatchConnectivity may deliver a transfer twice after a restart).
  static let phoneSeenIdsKept = 2000
}
