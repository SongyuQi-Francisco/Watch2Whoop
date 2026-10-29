import GRDB

/// M2's local tables (docs/ios-native-2.0.md section 3.1): `stress_daily`.
/// A stub until M2 lands: it registers nothing. The package adds its migrations here (named `v<N>_stress_<what>`),
/// never in `Schema.swift`, which calls the packages in the fixed order derive → workouts → stress → healthspan → rules.
enum StressSchema {
  static func register(_ m: inout DatabaseMigrator) {}
}
