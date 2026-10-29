import GRDB

/// Schema of the per-account database (docs/ios-native-app.md section 4.2). Append-only: a page agent adds
/// `vN_<name>` at the end in its own PR; a shipped migration is never edited (`SchemaTests` checks the order).
/// Times are UTC seconds (REAL), days are the user's local `YYYY-MM-DD` (TEXT), units as on the server (kg, kcal, ms).
enum Schema {
  static var migrator: DatabaseMigrator {
    var m = DatabaseMigrator()
    #if DEBUG
    // A changed migration during development wipes the local copy (it is only a cache of the server).
    m.eraseDatabaseOnSchemaChange = true
    #endif

    m.registerMigration("v1_initial") { db in
      // Small facts about this database: schema owner, account, last full restore, algorithm version in use …
      try db.create(table: "meta") { t in
        t.primaryKey("key", .text)
        t.column("value", .text).notNull()
      }

      // ---------------------------------------------------------------- sync bookkeeping
      // Server documents kept as they came (today, trends, circles feed …) until a page has its own tables.
      // key: "<kind>:<arg>", e.g. "today:2026-10-29", "trends:90", "circles:overview".
      try db.create(table: "docs") { t in
        t.primaryKey("key", .text)
        t.column("version", .integer).notNull().defaults(to: 0)   // server version (N2); 0 = from a legacy GET
        t.column("body", .blob).notNull()                          // JSON
        t.column("fetched_at", .double).notNull()
      }
      // One cursor per pull stream (N2 /v1/sync/pull): "scores", "daily", "sleep", "records", "docs" …
      try db.create(table: "sync_cursors") { t in
        t.primaryKey("stream", .text)
        t.column("cursor", .text)
        t.column("pulled_at", .double)
      }
      // Offline queue of writes (section 5.4): FIFO per group, one in flight per group.
      try db.create(table: "outbox") { t in
        t.autoIncrementedPrimaryKey("id")
        t.column("client_id", .text).notNull().unique()     // idempotency key sent to the server
        t.column("kind", .text).notNull()                   // "water", "weight", "journal", "meal_template" …
        t.column("group_key", .text).notNull()              // ordering group, e.g. "water", "training:2026-10-29"
        t.column("method", .text).notNull()
        t.column("path", .text).notNull()
        t.column("body", .blob)
        t.column("created_at", .double).notNull()
        t.column("attempts", .integer).notNull().defaults(to: 0)
        t.column("next_attempt_at", .double).notNull().defaults(to: 0)
        t.column("state", .text).notNull().defaults(to: "pending")   // pending | sending | failed | conflict
        t.column("last_error", .text)
      }
      try db.create(index: "outbox_by_group", on: "outbox", columns: ["group_key", "id"])

      // ---------------------------------------------------------------- raw HealthKit data (the sync engine writes)
      try db.create(table: "hk_sources") { t in
        t.autoIncrementedPrimaryKey("id")
        t.column("key", .text).notNull().unique()           // bundle|product|device name|model|os
        t.column("name", .text)
        t.column("bundle", .text)
        t.column("product", .text)
        t.column("device_type", .text)                      // watch | phone | other
        t.column("device_name", .text)
        t.column("device_model", .text)
        t.column("manufacturer", .text)
        t.column("os_version", .text)
      }
      // Quantity and category samples (category value = HealthKit's integer), registry units.
      try db.create(table: "hk_samples") { t in
        t.primaryKey("uuid", .text)
        t.column("type", .text).notNull()                   // HKQuantityTypeIdentifier… / HKCategoryTypeIdentifier…
        t.column("start", .double).notNull()
        t.column("end", .double).notNull()
        t.column("value", .double).notNull()
        t.column("source_id", .integer).references("hk_sources")
        t.column("tz", .text)
        t.column("uploaded", .boolean).notNull().defaults(to: false)
      }
      try db.create(index: "hk_samples_by_type", on: "hk_samples", columns: ["type", "start"])
      // 900-second statistics buckets of cumulative types (steps, energy …), UTC-aligned.
      try db.create(table: "hk_stat_buckets") { t in
        t.column("type", .text).notNull()
        t.column("start", .integer).notNull()
        t.column("value", .double).notNull()
        t.column("uploaded", .boolean).notNull().defaults(to: false)
        t.primaryKey(["type", "start"])
      }
      try db.create(table: "hk_workouts") { t in
        t.primaryKey("uuid", .text)
        t.column("activity", .integer)
        t.column("activity_name", .text)
        t.column("start", .double).notNull()
        t.column("end", .double).notNull()
        t.column("duration_s", .double)
        t.column("stats", .blob)                            // JSON: active_kcal, basal_kcal, distance_m, avg_hr, max_hr
        t.column("events", .blob)                           // JSON
        t.column("meta", .blob)                             // JSON
        t.column("source_id", .integer).references("hk_sources")
        t.column("uploaded", .boolean).notNull().defaults(to: false)
      }
      try db.create(table: "hk_deleted") { t in
        t.primaryKey("uuid", .text)
        t.column("deleted_at", .double).notNull()
        t.column("uploaded", .boolean).notNull().defaults(to: false)
      }

      // ---------------------------------------------------------------- per-day summaries and scores
      try db.create(table: "daily_metrics") { t in
        t.column("date", .text).notNull()
        t.column("metric", .text).notNull()                 // the server's metric key (steps, hrv_night_ms …)
        t.column("value", .double)
        t.column("unit", .text)
        t.column("source", .text)                           // app | ow | apple_export | shortcut | server
        t.column("flags", .blob)                            // JSON
        t.column("updated_at", .double).notNull()
        t.primaryKey(["date", "metric"])
      }
      try db.create(table: "sleep_sessions") { t in
        t.primaryKey("id", .text)
        t.column("date", .text).notNull().indexed()         // wake-up day (end − 4 h), the server's rule
        t.column("start", .double).notNull()
        t.column("end", .double).notNull()
        t.column("is_main", .boolean).notNull().defaults(to: true)
        t.column("stages", .blob)                           // JSON [[stage, start, end] …]
        t.column("source", .text)
        t.column("updated_at", .double).notNull()
      }
      // One row per day: the ring numbers as columns (fast Today / Trends), the whole score row as JSON.
      try db.create(table: "daily_scores") { t in
        t.primaryKey("date", .text)
        t.column("recovery", .double)
        t.column("sleep_score", .double)
        t.column("strain", .double)
        t.column("hrv_ms", .double)
        t.column("rhr", .double)
        t.column("payload", .blob)                          // JSON: the server's daily_scores row (+ flags)
        t.column("algo_version", .text)                     // algorithm bundle version that computed it
        t.column("origin", .text).notNull()                 // server | app
        t.column("computed_at", .double).notNull()
        t.column("uploaded", .boolean).notNull().defaults(to: false)
      }

      // ---------------------------------------------------------------- the user's own records (N5)
      // One table for every kind (food, weight, water, journal, supplement, lab, training …): the fields pages filter on
      // are columns, the rest is the server's JSON. A kind that needs more gets its own table in a later migration.
      try db.create(table: "records") { t in
        t.column("kind", .text).notNull()
        t.column("id", .text).notNull()                     // server id, or "c:<client_id>" until the server answered
        t.column("date", .text)                             // local day it belongs to
        t.column("server_id", .text)
        t.column("client_id", .text)
        t.column("payload", .blob).notNull()                // JSON
        t.column("version", .integer).notNull().defaults(to: 0)
        t.column("updated_at", .double).notNull()
        t.column("deleted", .boolean).notNull().defaults(to: false)
        t.column("pending", .boolean).notNull().defaults(to: false)   // has an unsent outbox entry
        t.primaryKey(["kind", "id"])
      }
      try db.create(index: "records_by_day", on: "records", columns: ["kind", "date"])

      // ---------------------------------------------------------------- algorithm bundles (N1)
      try db.create(table: "algo_bundles") { t in
        t.primaryKey("version", .text)
        t.column("sha256", .text).notNull()
        t.column("api", .integer).notNull()
        t.column("path", .text).notNull()                   // file under Application Support/W2W/algo/
        t.column("source", .text).notNull()                 // builtin | download
        t.column("installed_at", .double).notNull()
        t.column("active", .boolean).notNull().defaults(to: false)
      }
    }

    // #340 N2b: the phone scores app-mode users itself (docs/ios-native-app.md section 9.1).
    m.registerMigration("v2_local_scoring") { db in
      // Beat-to-beat HRV readings (RMSSD users): the phone's own (beats kept until the bundle computed RMSSD / SDNN, then
      // dropped) and the server's (pulled raw stream, RMSSD only). Pruned with hk_samples (LocalRawStore.keepDays).
      try db.create(table: "heartbeat_series") { t in
        t.primaryKey("uuid", .text)
        t.column("start", .double).notNull().indexed()      // whole seconds, like the server's start_at
        t.column("end", .double).notNull()
        t.column("rmssd", .double)
        t.column("sdnn", .double)
        t.column("beats", .blob)                            // JSON {"t": [s since start], "gaps": [bool]}; nil once analysed
        t.column("origin", .text).notNull()                 // phone | server
      }
      // Per-day heart-rate histogram {bpm: count} (the scores' 365-day HRmax window) for days whose raw samples are gone.
      try db.create(table: "hr_day_hist") { t in
        t.primaryKey("date", .text)
        t.column("hist", .blob).notNull()                   // JSON {"61": 12, …}
        t.column("source", .text).notNull()                 // phone (from hk_samples) | server (GET /v1/sync/scoring)
        t.column("updated_at", .double).notNull()
      }
      // Days whose scoring inputs changed since they were last scored here (new samples, pulled sleep / daily / raw).
      try db.create(table: "score_dirty") { t in
        t.primaryKey("date", .text)
        t.column("at", .double).notNull()
      }
      // The server's whole session as it came from the sleep stream (asleep / stage minutes, device): the scores use them.
      try db.alter(table: "sleep_sessions") { t in t.add(column: "payload", .blob) }
    }

    // #340 local history: the phone keeps its whole history (docs/ios-native-app.md section 9.1, LocalHistoryParams).
    m.registerMigration("v3_local_history") { db in
      // The scores' raw inputs before the hot window, as the server merges them (stream raw_history) or rolled over from
      // hk_samples: one row per (kind, instant), ~25 bytes (WITHOUT ROWID, kind = LocalHistoryParams.Kind code).
      try db.create(table: "hist_samples", options: .withoutRowID) { t in
        t.column("kind", .integer).notNull()
        t.column("t", .double).notNull()                    // UTC seconds
        t.column("value", .double).notNull()                // SpO2 in percent (the server's), the rest in registry units
        t.primaryKey(["kind", "t"])
      }
      // Sleep-stage segments of those nights (the scores' segments); `date` = the day they came with (end − 4 h).
      try db.create(table: "hist_segments", options: .withoutRowID) { t in
        t.column("start", .double).notNull()
        t.column("end", .double).notNull()
        t.column("stage", .text).notNull()
        t.column("date", .text).notNull()
        t.primaryKey(["start", "stage"])
      }
      try db.create(index: "hist_segments_by_date", on: "hist_segments", columns: ["date"])
      // Which days the history holds and where from: server (raw_history; `complete` = every source the server scores
      // from was in it) or phone (rolled over from hk_samples).
      try db.create(table: "hist_days") { t in
        t.primaryKey("date", .text)
        t.column("source", .text).notNull()
        t.column("rows", .integer).notNull()
        t.column("complete", .boolean).notNull()
        t.column("fetched_at", .double).notNull()
      }
      // The beats now stay after the analysis: `analyzed` says the RMSSD / SDNN columns are current.
      try db.alter(table: "heartbeat_series") { t in t.add(column: "analyzed", .boolean).notNull().defaults(to: false) }
      try db.execute(sql: "UPDATE heartbeat_series SET analyzed = 1 WHERE beats IS NULL")
    }
    return m
  }
}
