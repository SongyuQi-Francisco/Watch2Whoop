import GRDB

/// M1c's local tables (docs/ios-native-2.0.md section 3.1): `workouts`, `workout_hrr`.
/// A stub until M1c lands: it registers nothing. The package adds its migrations here (named `v<N>_workouts_<what>`),
/// never in `Schema.swift`, which calls the packages in the fixed order derive → workouts → stress → healthspan → rules.
enum WorkoutsSchema {
  static func register(_ m: inout DatabaseMigrator) {}
}
