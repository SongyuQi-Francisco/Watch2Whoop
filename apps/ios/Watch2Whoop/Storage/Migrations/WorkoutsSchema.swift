import GRDB

/// M1c's local tables (docs/ios-native-2.0.md section 3.1): `workouts`, `workout_hrr`. Migrations named
/// `v<N>_workouts_<what>`, registered from `Schema.swift` in the fixed package order (derive → workouts → stress →
/// healthspan → rules).
enum WorkoutsSchema {
  static func register(_ m: inout DatabaseMigrator) {
    m.registerMigration("v5_workouts_tables") { db in
      // Every workout of the account, one row per (source, external_id): the server's (pulled `records` kind `workout`,
      // computed_by server: Open Wearables, Shortcut, export.zip, Hevy, and the app's own before the phone computed them)
      // and the phone's (its HKWorkouts through OBS.workoutDays, computed_by phone, replacing the server's row of the same
      // workout). dup_of = the key of the copy kept; pages read `dup_of IS NULL AND excluded IS NULL` like the server.
      try db.create(table: "workouts") { t in
        t.primaryKey("key", .text)                          // "<source>:<external_id>"
        t.column("server_id", .integer)                     // the server's workouts.id (the calendar's "w<id>")
        t.column("source", .text).notNull()                 // app | ow | shortcut | apple_export | hevy
        t.column("external_id", .text).notNull()
        t.column("local_date", .text).notNull().indexed()   // the start's local day
        t.column("type", .text).notNull()
        t.column("start", .double).notNull()                // UTC seconds
        t.column("end", .double).notNull()
        t.column("duration_min", .double).notNull()
        t.column("active_kcal", .double)
        t.column("trimp", .double)
        t.column("hr_recovery_bpm", .double)
        t.column("intensity", .text)
        t.column("dup_of", .text)                           // key of the kept copy
        t.column("excluded", .text)                         // implausible_duration | user_hidden | shadow | …
        t.column("computed_by", .text).notNull()            // phone | server
        t.column("algo_version", .text)
        t.column("bundle_version", .text)
        t.column("row", .blob).notNull()                    // JSON: the row in the server's shape (history.load)
        t.column("server_row", .blob)                       // JSON: the server's own row of this workout, as pulled
        t.column("updated_at", .double).notNull()
      }
      // cardio/hrr.hrr60 of each kept workout (the server's workout_hrr), by workout key.
      try db.create(table: "workout_hrr") { t in
        t.primaryKey("key", .text)
        t.column("local_date", .text).notNull().indexed()
        t.column("end", .double).notNull()
        t.column("peak_hr", .double)
        t.column("hr_60s", .double)
        t.column("hrr60", .double)
        t.column("hr_max_used", .double)
        t.column("valid", .boolean).notNull()
        t.column("reason", .text)
        t.column("computed_by", .text).notNull()
        t.column("algo_version", .text)
      }
      try db.create(index: "hk_workouts_by_start", on: "hk_workouts", columns: ["start"])
    }
  }
}
