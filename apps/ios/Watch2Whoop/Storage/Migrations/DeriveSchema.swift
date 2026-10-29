import GRDB

/// M1's local tables (docs/ios-native-2.0.md section 3.1): `daily_metric_sources` (date, metric, source, value, unit, agg, n, min, max, flags); `score_dirty.kind`.
/// A stub until M1 lands: it registers nothing. The package adds its migrations here (named `v<N>_derive_<what>`),
/// never in `Schema.swift`, which calls the packages in the fixed order derive → workouts → stress → healthspan → rules.
enum DeriveSchema {
  static func register(_ m: inout DatabaseMigrator) {}
}
