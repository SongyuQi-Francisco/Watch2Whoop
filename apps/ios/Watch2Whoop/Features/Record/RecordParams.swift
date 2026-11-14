import CoreGraphics

/// Display constants of the 2.0.1 记录 tab (docs/design/2.0.1 screens 10–15). None of them is a health threshold: the
/// muscle targets, the day strain and the food goals come from the server; these only decide how they are drawn.
enum RecordParams {
  // MARK: set ring (12 / 11)

  /// Gap between two sets of one exercise, as a share of the ring.
  static let setGap = 0.008
  /// Wider gap between two exercises (and at 12 o'clock, where the ring starts and ends).
  static let exerciseGap = 0.024

  // MARK: muscles inside the ring

  /// A secondary muscle of an exercise counts this much of a set (the catalog's primary / secondary split; the same
  /// weighting the small exercise map draws as dark / light).
  static let secondaryWeight = 0.5
  /// Opacity of the least and the most worked muscle of the day (`train`, opacity = share, direction.md 4).
  static let shareOpacity: ClosedRange<Double> = 0.35...1

  /// The body areas of the weekly map's rows (screen 13), in this order; every catalog muscle key belongs to one.
  static let muscleGroups: [(key: String, muscles: [String])] = [
    ("chest", ["chest"]),
    ("back", ["lats", "middle back", "lower back", "traps"]),
    ("shoulders", ["shoulders", "neck"]),
    ("arms", ["biceps", "triceps", "forearms"]),
    ("legs", ["quadriceps", "hamstrings", "glutes", "abductors", "adductors", "calves"]),
    ("core", ["abdominals"]),
  ]

  // MARK: history dots (14)

  /// Day strain that draws the largest dot (WHOOP's 0–21 scale; a day above it is drawn at the largest size).
  static let strainScaleMax = 21.0
  /// Diameter of a trained day's dot, smallest (strain 0) to largest.
  static let dotDiameter: ClosedRange<CGFloat> = 18...38
  /// Opacity of a trained day's dot by intensity (`training/params.py INTENSITY_LEVELS`).
  static let intensityOpacity: [String: Double] = ["light": 0.55, "moderate": 0.75, "hard": 0.92, "max": 1]
  /// Session cards under the history calendar.
  static let historyCards = 12

  // MARK: food dial (10)

  /// The kcal dial's scale reaches this far past the goal, so the target tick sits before the end (design: 2,100 of 2,520).
  static let goalHeadroom = 1.2
  /// Scale of the dial when no goal is set (never a dial that is always full).
  static let noGoalKcal = 2500.0
  /// Scales of the macro readings without a goal (g).
  static let noGoalMacro: [String: Double] = ["protein": 150, "carbs": 300, "fat": 90]
  /// Recipes drawn as ticks on the 食谱 face, at most.
  static let recipeTicks = 24
}
