import GRDB

/// M2's local tables (docs/ios-native-2.0.md section 3.1): `stress_daily`. Migrations named `v<N>_stress_<what>`,
/// registered from `Schema.swift` in the fixed package order (derive → workouts → stress → healthspan → rules).
enum StressSchema {
  static func register(_ m: inout DatabaseMigrator) {
    m.registerMigration("v6_stress_daily") { db in
      // The phone's stress rows (the server's stress_daily, computed by StressStage with the bundle's stressLocal). Written
      // in shadow and primary; the pages read them only in primary (section 5), else the server's documents.
      try db.create(table: "stress_daily") { t in
        t.primaryKey("date", .text)
        t.column("score", .double)                          // 0–3, null without scored hours
        t.column("level", .text)                            // low | medium | high
        t.column("low_h", .integer).notNull().defaults(to: 0)
        t.column("medium_h", .integer).notNull().defaults(to: 0)
        t.column("high_h", .integer).notNull().defaults(to: 0)
        t.column("peak_hour", .integer)
        t.column("hours", .blob).notNull()                  // JSON [{hour, score, level, hr, samples, hrv_z}]
        t.column("baseline", .blob)                         // JSON {calm_hr, spread, hrv_ln}
        t.column("flags", .blob).notNull()                  // JSON {key: text}
        t.column("n_hr", .integer).notNull().defaults(to: 0)   // heart-rate samples of the day it was computed from
        t.column("algo_version", .text).notNull()
        t.column("bundle_version", .text).notNull()
        t.column("computed_at", .double).notNull()
      }
    }
  }
}
