import GRDB

/// M4's local tables (docs/ios-native-2.0.md section 3.1): `healthspan_weekly`. Migrations named `v<N>_healthspan_<what>`,
/// registered from `Schema.swift` in the fixed package order (derive → workouts → stress → healthspan → rules).
enum HealthspanSchema {
  static func register(_ m: inout DatabaseMigrator) {
    m.registerMigration("v7_healthspan_weekly") { db in
      // The phone's body-age weeks (the server's healthspan_weekly, computed by HealthspanStage with the bundle's
      // healthspanLocal). Written in shadow and primary; pages read them only in primary (section 5), else the server's
      // documents. One row per week, keyed by its Monday.
      try db.create(table: "healthspan_weekly") { t in
        t.primaryKey("week_start", .text)
        t.column("as_of", .text).notNull()
        t.column("chrono_age", .double)
        t.column("body_age", .double)
        t.column("delta_years", .double)
        t.column("pace_of_aging", .double)
        t.column("pace_raw", .double)
        t.column("pace_window_weeks", .integer)
        t.column("contributors", .blob).notNull()           // JSON [{key, label, category, value, unit, hr, years, …}]
        t.column("insufficient", .blob).notNull()           // JSON [{key, label, category, reason}]
        t.column("algo_version", .text).notNull()
        t.column("bundle_version", .text).notNull()
        t.column("computed_at", .double).notNull()
      }
    }
  }
}
