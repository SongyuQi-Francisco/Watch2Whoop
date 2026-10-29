import GRDB

/// M4's local tables (docs/ios-native-2.0.md section 3.1): `healthspan_weekly`.
/// A stub until M4 lands: it registers nothing. The package adds its migrations here (named `v<N>_healthspan_<what>`),
/// never in `Schema.swift`, which calls the packages in the fixed order derive → workouts → stress → healthspan → rules.
enum HealthspanSchema {
  static func register(_ m: inout DatabaseMigrator) {}
}
