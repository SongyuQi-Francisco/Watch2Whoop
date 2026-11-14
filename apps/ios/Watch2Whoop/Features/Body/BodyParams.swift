import CoreGraphics

/// Display constants of the 2.0.1 身体 tab (docs/design/2.0.1 `05-body`). None of them is a health threshold: the scores,
/// their previous-period means and the series come from `/trends` (trends/series.py); these only decide how they are drawn.
enum BodyParams {
  /// Above this many days the hero's bars become weekly means (90 days → 13 bars, 365 → 53), so a bar never gets thinner
  /// than about 3 pt on a 402 pt phone.
  static let weeklyBarsAbove = 45
  /// Days per bar once the bars are weekly.
  static let barBucket = 7
  /// Height of the hero's bar chart (design: 180 pt, y 232–412).
  static let barsHeight: CGFloat = 180
  /// The range the hero shows when the tab's range is 「今天」 (a one-day range has no period to average).
  static let todayWindow = 30
  /// Days of the 周报 face's dots (the last full week up to the end day).
  static let weekDays = 7
  /// Days of the 心肺 face's HRV line (design: 30 days on the usual band).
  static let hrvDays = 30
  /// A day counts as a training day when it has this many aerobic minutes or any strength volume (`aerobic_min`,
  /// `muscle_load` of the same document). Display rule only: 1 minute = something was logged that day.
  static let trainingMinMinutes = 1.0
  /// The ranges of the header's menu (direction.md 5: 7 / 30 / 90 / 365).
  static let menuRanges: [TrendRange] = [.week, .month, .quarter, .year]
  /// Body age face: the gauge spans the calendar age ± this many years.
  static let ageSpan = 8.0
}
