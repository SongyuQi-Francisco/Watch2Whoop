import Foundation

/// Tunables of the watch app (docs/watch-app-plan.md sections 2.1, 2.2, 2.4, 2.6). Kept in one place so they can be
/// changed without touching the screens; none of them is a health threshold (the watch computes no score: recovery
/// zones, verdicts and the stale rule of the phone's own data come from the phone).
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

  /// The phone has not been heard from for this long: its numbers are drawn grey with their age (mockup S3), the same
  /// limit as the phone's own capsule (TodayParams.syncStaleMinutes = 360 min: the design's 「6时前」 example and two
  /// missed rounds of the Shortcut's daytime runs).
  static let staleAfterMin = 360
  /// The phone's 「同步中」 is shown only while the context saying so is this fresh; an older one says nothing about now.
  static let phoneBusyFreshSec: TimeInterval = 120
  /// Quick meals on the quick-log screen: 3 + the water tile = the mockup's four tiles, no scrolling (plan 2.2).
  static let quickMealsMax = 3
  /// The water tile's amount when the phone gives none: the server's first quick button (body_engine
  /// water/params.py `QUICK_ML` = (250, 500)), the same as the phone's water card.
  static let waterQuickMl = 250
  /// Ends of the two readings' bars on the home screen: sleep score 0–100, strain 0–21 (the phone's TodayGlance gauges).
  static let sleepMax = 100.0
  static let strainMax = 21.0
  /// Quick logs the watch remembers for the receipt and undo: the ones of the last day only.
  static let quickLogsKeptHours: TimeInterval = 36
  /// Quick-log results the phone sends back (the newest; a day's logs never come near this).
  static let phoneResultsKept = 40
  /// A saved session older than this is not resumed on launch but ended (its sets were already handed to the phone):
  /// a strength session longer than this is a session someone forgot to end.
  static let resumeWithinHours: TimeInterval = 6
}
