import GRDB

/// M6's local tables (docs/ios-native-2.0.md section 3.1): `reminders`.
/// A stub until M6 lands: it registers nothing. The package adds its migrations here (named `v<N>_rules_<what>`),
/// never in `Schema.swift`, which calls the packages in the fixed order derive → workouts → stress → healthspan → rules.
enum RulesSchema {
  static func register(_ m: inout DatabaseMigrator) {}
}
