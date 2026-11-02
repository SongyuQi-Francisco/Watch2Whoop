import Foundation

/// Thresholds of the Today tab's 「需要注意」 block and of the direction arrows in Trends' 「今天」 list (#357). Health
/// thresholds live here, each with where it comes from; change them here, never in the views. Every rule compares the
/// user with their own baseline (or a status the algorithms already computed), never with one person's numbers.
///
/// Baselines are the server's (trends/series.py `baseline_band`: mean ± 1 SD of the 60 days before the day, at least 14
/// readings); for a metric without a band the same window is computed on the phone from the cached series (`DayBaseline`).
enum TodayParams {
  // MARK: - which day the documents describe

  /// Days of the `/trends` document behind Trends' 「今天」 and the Today tab's new blocks: the 60 days before the day
  /// (the baseline window above) plus the day itself. One document, shared by both tabs (`TrendsDocs.trends(61, end:)`).
  static let trendsDays = 61
  /// Readings needed in the 60-day window for a phone-side baseline (series.py `BASELINE_MIN_N`).
  static let baselineMinN = 14
  /// Days of the small spark line in Trends' 「今天」 rows.
  static let sparkDays = 7

  // MARK: - direction arrows (Trends 「今天」)

  /// Within this many standard deviations of the baseline the arrow is flat and the row keeps the text colour. 0.5 SD is
  /// the "smallest worthwhile change" used for day-to-day HRV and resting HR (Plews et al. 2013, Buchheit 2014).
  static let flatZ = 0.5
  /// Without a standard deviation (too few readings to spread), a relative change below this is flat.
  static let flatRelative = 0.03

  // MARK: - 需要注意: metrics clearly off the user's own baseline

  struct Watch {
    /// Series key of `/trends`.
    var key: String
    /// The direction that counts as abnormal: −1 below the baseline, +1 above.
    var bad: Int
    /// z-score (vs the 60-day baseline) at or beyond which the metric is flagged (in the bad direction).
    var z: Double
    /// And at least this far from the baseline mean in the metric's own unit, so a very steady baseline (tiny SD) does
    /// not flag noise.
    var minDelta: Double
    /// Instead of a z-score: the value itself (already a deviation) at or beyond this is flagged.
    var absolute: Double? = nil
  }

  /// 1.5 SD: outside the "normal range" band WHOOP / Oura draw (± 1 SD) by a clear margin, about the worst 7 % of days of
  /// a normal distribution. Directions and floors:
  /// - HRV low, resting HR high: the classic recovery / illness signals (WHOOP recovery, Oura readiness contributors).
  /// - Respiratory rate high by ≥ 1 breath/min: WHOOP's illness signal (Miller et al. 2020, respiratory rate during sleep).
  /// - SpO₂ low by ≥ 1.5 points (nightly average; the health monitor separately flags values out of its range).
  /// - Wrist temperature: the series is already the night's deviation from the 60-day mean; ≥ +0.5 °C is the edge of the
  ///   normal band of the Trends card (`TrendCards.conf["wrist_temp"].zone`) and Apple's / Oura's "elevated" deviation.
  static let watch: [Watch] = [
    Watch(key: "hrv", bad: -1, z: 1.5, minDelta: 5),
    Watch(key: "resting_hr", bad: 1, z: 1.5, minDelta: 3),
    Watch(key: "resp_rate", bad: 1, z: 1.5, minDelta: 1),
    Watch(key: "spo2", bad: -1, z: 1.5, minDelta: 1.5),
    Watch(key: "wrist_temp", bad: 1, z: 0, minDelta: 0, absolute: 0.5),
  ]

  // MARK: - 需要注意: status cards and the health monitor

  /// Status cards (trends/calc.py, `TrendStatus.toneMap`) that count when their tone is at least this bad. Resilience,
  /// body battery, lowest-HR timing and heart-rate recovery only when red: their yellow is an ordinary day.
  static let statusMinTone: [String: Tone] = [
    "illness": .mid,           // watch / high (calc.illness_risk)
    "hrv_status": .mid,        // low / poor (calc.hrv_status, Garmin HRV Status)
    "training_status": .mid,   // strained / unproductive / detraining / overreaching (calc.training_status)
    "resilience": .bad,
    "body_battery": .bad,      // < 30 (TrendStatus)
    "recovery_index": .bad,
    "hr_recovery": .bad,
  ]

  // MARK: - ranking

  /// Severity, in z-score units so metric items and status items sort together. A metric's severity is its |z| (capped);
  /// an absolute rule scores `atThreshold` at its threshold and grows with the value.
  static let severityCap = 4.0
  static let atThreshold = 1.5
  /// A status card in red / yellow, a health-monitor vital out of the server's range.
  static let statusBad = 3.0
  static let statusMid = 1.6
  static let monitorOff = 2.0
  /// At most this many items (the block is hidden when there are none).
  static let maxItems = 3

  // MARK: - 今日核心

  /// Stress score bands (0–3): packages/openbodyscore stress.BANDS — < 1 low, 1–2 medium, ≥ 2 high.
  static let stressMedium = 1.0
  static let stressHigh = 2.0
}
