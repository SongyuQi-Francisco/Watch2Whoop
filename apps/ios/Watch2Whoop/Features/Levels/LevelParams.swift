import CoreGraphics

/// Layout constants of the levels of 今天 and 身体 (2.1.0, docs/design/2.0.1/drilldown option C′): level 1 the first glance,
/// level 2 the day's (or the range's) full list, level 3 one metric. None of them is a health threshold: values, usual
/// ranges and deltas come from `TodayGlanceRules` and the `/trends` series; these only decide how the levels are drawn.
enum LevelParams {
  /// Segments of the depth ladder in the page header (level 1 of 3 … 3 of 3).
  static let levels = 3
  /// One segment of the ladder (drilldown/dd.css `.ladder b`: 14 × 3, 3 apart).
  static let ladderSegment = CGSize(width: 14, height: 3)
  static let ladderGap: CGFloat = 3
  /// The ladder's hit area: a door has to be at least this tall and wide (Apple HIG 44 pt).
  static let hitSide: CGFloat = 44
  /// The small recovery dial a level-2 / level-3 header carries as its way up (dd.css `.mini`: 34 pt, ring 3 pt).
  static let miniDial: CGFloat = 34
  static let miniDialLine: CGFloat = 3

  /// Rows of 今日详情 and of 相关: the label column (dd.css `.rows.wide .row`: 86 · 1fr · 80 · 38 · 12).
  static let rowLabelWidth: CGFloat = 86
  /// Days of a level-2 row's line (the same 14 days as the half sheets of 2.0.1 had).
  static let rowSparkDays = TodayGlanceRules.sparkDays

  /// The chips row of 今日详情 (训练 · 今晚 · 提醒) and the jump rail under it (dd.css `.chips`, `.jump`).
  static let chipHeight: CGFloat = 40
  static let jumpHeight: CGFloat = 34

  /// The metric page: the chart's height (dd.css `.line`: 150) and its range switch (7 / 30 / 90 / 1 year).
  static let metricChartHeight: CGFloat = 150
  static let metricRanges: [TrendRange] = [.week, .month, .quarter, .year]
  /// The range a metric page opens with from 今天 (身体 passes its own).
  static let metricDefaultRange: TrendRange = .month
  /// Related metrics at the end of a metric page: the nearest ones of its own category, at most this many.
  static let relatedCount = 3

  /// Swipe up on a first screen opens level 2 (what a 2.0.0 user tries first): at least this far, mostly vertical.
  static let swipeUpDistance: CGFloat = 90
  /// A level-2 day swipe ignores drags that start this close to the left edge (the system's swipe back lives there).
  static let edgeBack: CGFloat = 28

  /// No false floor (CONTRIBUTING.md, drilldown README section 9): a blank spacer inside a tab root's scroll content is
  /// never taller than this. A larger gap reads as "the page ends here" and hides what comes after it.
  static let maxBlankGap: CGFloat = 56
  /// A tab root that scrolls shows at least this much of its next block above the key band (rule 1, form 2), so the cut
  /// itself says there is more (on iOS the only sign a page scrolls is content cut by the fold).
  static let peek: CGFloat = 48
  /// What a scrolling tab root aims for (a little more than `peek`, so the cut passes through the next block's numbers).
  static let peekTarget: CGFloat = 56
  /// The joint between a first glance and its next block (drilldown `.joint`: 24 pt with one down chevron).
  static let joint: CGFloat = 24
  /// The first glance's gaps never shrink below this share of their value to make room for the peek.
  static let minGapScale: CGFloat = 0.3
  /// Where the gaps are not enough (667 pt phones), the dial or ring of that glance gives up at most this many points.
  static let maxHeroShrink: CGFloat = 64
}
