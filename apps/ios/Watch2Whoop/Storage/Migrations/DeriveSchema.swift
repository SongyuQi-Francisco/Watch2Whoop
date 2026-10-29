import GRDB

/// M1's local tables (docs/ios-native-2.0.md section 3.1). Migrations named `v<N>_derive_<what>`, registered from
/// `Schema.swift` in the fixed package order (derive → workouts → stress → healthspan → rules).
enum DeriveSchema {
  static func register(_ m: inout DatabaseMigrator) {
    m.registerMigration("v4_derive_tables") { db in
      // Every source's daily value (the server's `daily_metric_sources`): `origin` server = the pulled `daily.sources`
      // (all sources the server has, its own 'app' rows included), phone = the 'app' rows this phone derived itself
      // (DeriveStage). The phone chooses the day's value among the server's non-app rows and its own (OBS.deriveDays).
      try db.create(table: "daily_metric_sources") { t in
        t.column("date", .text).notNull()
        t.column("metric", .text).notNull()
        t.column("source", .text).notNull()                 // app | ow | apple_export | shortcut | …
        t.column("origin", .text).notNull()                 // server | phone
        t.column("value", .double).notNull()
        t.column("unit", .text)
        t.column("agg", .text).notNull()                    // sum | avg
        t.column("n", .integer)
        t.column("min", .double)
        t.column("max", .double)
        t.column("flags", .blob)                            // JSON object
        t.column("updated_at", .double).notNull()
        t.primaryKey(["date", "metric", "source", "origin"])
      }
      // What the derive stage computed, per (kind, day): the rows of PUT /v1/users/me/derived/app as JSON, the versions
      // that computed them, whether the server has them (the next upload sends the others, settled days only).
      try db.create(table: "derived_rows") { t in
        t.column("kind", .text).notNull()                   // daily | sleep (later packages add theirs)
        t.column("date", .text).notNull()
        t.column("rows", .blob).notNull()                   // JSON array
        t.column("algo_version", .text).notNull()
        t.column("bundle_version", .text).notNull()
        t.column("computed_at", .double).notNull()
        t.column("uploaded", .boolean).notNull().defaults(to: false)
        t.primaryKey(["kind", "date"])
      }
      // score_dirty per kind (section 3.3): `scores` (the existing queue, ScoringSync), `derive` (days whose HealthKit
      // rows or pulled sources changed: DeriveStage derives them again). Rebuilt with (date, kind) as the key.
      try db.create(table: "score_dirty_new") { t in
        t.column("date", .text).notNull()
        t.column("kind", .text).notNull().defaults(to: "scores")
        t.column("at", .double).notNull()
        t.primaryKey(["date", "kind"])
      }
      try db.execute(sql: "INSERT INTO score_dirty_new (date, kind, at) SELECT date, 'scores', at FROM score_dirty")
      try db.drop(table: "score_dirty")
      try db.rename(table: "score_dirty_new", to: "score_dirty")
      try db.create(index: "hk_samples_by_source", on: "hk_samples", columns: ["source_id"])
    }
  }
}
