import GRDB

/// M6's local tables (docs/ios-native-2.0.md section 3.1): `reminders`. Migrations named `v<N>_rules_<what>`, registered
/// from `Schema.swift` in the fixed package order (derive → workouts → stress → healthspan → rules).
enum RulesSchema {
  static func register(_ m: inout DatabaseMigrator) {
    m.registerMigration("v8_rules_reminders") { db in
      // The phone's rule reminders (RulesStage with the bundle's rulesLocal, the twin of evaluate-rules): one row per
      // (date, rule, subject), the row in the server's shape (`payload`: rule_key, subject, level, title, message,
      // title_en, message_en, evidence, sticky). `status` is the user's (new / seen / dismissed); `status_dirty` = changed
      // on this phone and not yet confirmed by the server (it rides along with the next `reminders` upload);
      // `server_id` = the server's row id once the records pull brought it (PATCH /reminders/{id} needs it).
      // Written in shadow and primary; Today draws them only in primary (section 5), else the server's.
      try db.create(table: "reminders") { t in
        t.column("date", .text).notNull()
        t.column("rule_key", .text).notNull()
        t.column("subject", .text).notNull()
        t.column("level", .text).notNull()
        t.column("payload", .blob).notNull()
        t.column("status", .text).notNull().defaults(to: "new")
        t.column("status_dirty", .boolean).notNull().defaults(to: false)
        t.column("server_id", .integer)
        t.column("algo_version", .text).notNull()
        t.column("bundle_version", .text).notNull()
        t.column("computed_at", .double).notNull()
        t.primaryKey(["date", "rule_key", "subject"])
      }
      try db.create(index: "reminders_server_id", on: "reminders", columns: ["server_id"])
    }
  }
}
