-- Native app sync (#340 N2, docs/ios-native-app.md section 5 and 7.2, nativesync/).
--
-- sync_changes is the per-user change log the app pulls from (POST /v1/sync/pull). Statement-level triggers on every table
-- the app mirrors append one row per (user, stream, kind, day) a statement touched, so every writer counts without
-- remembering to call anything: importers, compute-scores, the record endpoints, the AI's write tools, the CLI.
-- An UPDATE that changes nothing but a timestamp is not a change. Rows are append-only (no locks between writers);
-- nativesync.store.compact() keeps only the newest row per key. The bodies are read from the source tables at pull time,
-- so a change row carries no health values.
--
-- Ordering: a pull returns rows of transactions older than the oldest still-running one (xid < snapshot xmin), ordered
-- by (xid, seq), so a transaction that commits late can never fall behind a cursor already handed out.
-- The user's data version (data_version) is max(seq) of their rows: it grows with every change.
CREATE SEQUENCE IF NOT EXISTS sync_change_seq;

CREATE TABLE IF NOT EXISTS sync_changes (
  seq        BIGINT NOT NULL DEFAULT nextval('sync_change_seq') PRIMARY KEY,
  xid        XID8 NOT NULL DEFAULT pg_current_xact_id(),
  user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  stream     TEXT NOT NULL,                 -- scores / daily / sleep / records / raw
  kind       TEXT NOT NULL DEFAULT '',      -- records: water / journal / food / ...; '' for the other streams
  key        TEXT NOT NULL,                 -- the local day (YYYY-MM-DD), or 'all' for undated kinds
  day        DATE,                          -- NULL for undated kinds
  changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS sync_changes_pull_idx ON sync_changes (user_id, stream, xid, seq);
CREATE INDEX IF NOT EXISTS sync_changes_key_idx ON sync_changes (user_id, stream, kind, key, seq);
CREATE INDEX IF NOT EXISTS sync_changes_user_seq_idx ON sync_changes (user_id, seq);
CREATE INDEX IF NOT EXISTS sync_changes_day_idx ON sync_changes (user_id, day) WHERE day IS NOT NULL;
CREATE INDEX IF NOT EXISTS sync_changes_time_idx ON sync_changes (changed_at);
COMMENT ON TABLE sync_changes IS 'Per-user change log of the data the native app mirrors (no values; bodies are read at pull time)';

-- Columns that change on every rewrite without the data changing.
CREATE OR REPLACE FUNCTION sync_row_json(r JSONB) RETURNS JSONB LANGUAGE sql IMMUTABLE AS $$
  SELECT r - ARRAY['updated_at', 'computed_at', 'received_at', 'synced_at', 'last_seen_at']
$$;

-- Generic trigger: TG_ARGV = (stream, kind, day expression over the row, e.g. 'date' / 'local_date' / 'NULL').
CREATE OR REPLACE FUNCTION sync_log_changes() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  s TEXT := TG_ARGV[0];
  k TEXT := TG_ARGV[1];
  d TEXT := TG_ARGV[2];
  q TEXT;
BEGIN
  IF TG_OP = 'INSERT' THEN
    q := format('SELECT user_id, (%s)::date AS day FROM new_rows', d);
  ELSIF TG_OP = 'DELETE' THEN
    q := format('SELECT user_id, (%s)::date AS day FROM old_rows', d);
  ELSE   -- UPDATE: only rows whose values (timestamps aside) differ, on either side
    q := format('SELECT user_id, day FROM ((SELECT user_id, (%1$s)::date AS day, sync_row_json(to_jsonb(sync_n)) AS j FROM new_rows sync_n
                   EXCEPT SELECT user_id, (%1$s)::date, sync_row_json(to_jsonb(sync_o)) FROM old_rows sync_o)
                 UNION ALL (SELECT user_id, (%1$s)::date, sync_row_json(to_jsonb(sync_o)) FROM old_rows sync_o
                   EXCEPT SELECT user_id, (%1$s)::date, sync_row_json(to_jsonb(sync_n)) FROM new_rows sync_n)) x', d);
  END IF;
  EXECUTE format('INSERT INTO sync_changes (user_id, stream, kind, key, day)
                  SELECT DISTINCT user_id, %L, %L, coalesce(day::text, ''all''), day FROM (%s) c
                  WHERE EXISTS (SELECT 1 FROM users u WHERE u.id = c.user_id)', s, k, q);   -- not while the user is being deleted
  RETURN NULL;
END $$;

-- Children of logged_workouts (exercises, sets) have no date: they count for their workout's day.
CREATE OR REPLACE FUNCTION sync_log_workout_children() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  q TEXT;
  src TEXT := CASE WHEN TG_OP = 'DELETE' THEN 'old_rows' ELSE 'new_rows' END;
BEGIN
  IF TG_TABLE_NAME = 'logged_workout_exercises' THEN
    q := format('SELECT w.user_id, w.date FROM %s r JOIN logged_workouts w ON w.id = r.workout_id', src);
  ELSE
    q := format('SELECT w.user_id, w.date FROM %s r JOIN logged_workout_exercises e ON e.id = r.exercise_entry_id
                 JOIN logged_workouts w ON w.id = e.workout_id', src);
  END IF;
  EXECUTE format('INSERT INTO sync_changes (user_id, stream, kind, key, day)
                  SELECT DISTINCT user_id, ''records'', ''strength'', date::text, date FROM (%s) c
                  WHERE EXISTS (SELECT 1 FROM users u WHERE u.id = c.user_id)', q);
  RETURN NULL;
END $$;

-- Three statement triggers per table (transition tables need one event each).
CREATE OR REPLACE FUNCTION sync_attach(tbl TEXT, s TEXT, k TEXT, d TEXT, fn TEXT DEFAULT 'sync_log_changes') RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  args TEXT := CASE WHEN fn = 'sync_log_changes' THEN format('%L, %L, %L', s, k, d) ELSE '' END;
BEGIN
  EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', tbl || '_sync_ins', tbl);
  EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', tbl || '_sync_upd', tbl);
  EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', tbl || '_sync_del', tbl);
  EXECUTE format('CREATE TRIGGER %I AFTER INSERT ON %I REFERENCING NEW TABLE AS new_rows
                  FOR EACH STATEMENT EXECUTE FUNCTION %I(%s)', tbl || '_sync_ins', tbl, fn, args);
  IF fn = 'sync_log_changes' THEN
    EXECUTE format('CREATE TRIGGER %I AFTER UPDATE ON %I REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
                    FOR EACH STATEMENT EXECUTE FUNCTION %I(%s)', tbl || '_sync_upd', tbl, fn, args);
  ELSE
    EXECUTE format('CREATE TRIGGER %I AFTER UPDATE ON %I REFERENCING NEW TABLE AS new_rows
                    FOR EACH STATEMENT EXECUTE FUNCTION %I(%s)', tbl || '_sync_upd', tbl, fn, args);
  END IF;
  EXECUTE format('CREATE TRIGGER %I AFTER DELETE ON %I REFERENCING OLD TABLE AS old_rows
                  FOR EACH STATEMENT EXECUTE FUNCTION %I(%s)', tbl || '_sync_del', tbl, fn, args);
END $$;

SELECT sync_attach('daily_scores', 'scores', '', 'date');
SELECT sync_attach('daily_metrics', 'daily', '', 'date');
SELECT sync_attach('sleep_sessions', 'sleep', '', 'wake_date');
SELECT sync_attach('water_intake', 'records', 'water', 'local_date');
SELECT sync_attach('journal_entries', 'records', 'journal', 'date');
SELECT sync_attach('food_entries', 'records', 'food', 'date');
SELECT sync_attach('body_measurements', 'records', 'weight', 'date');
SELECT sync_attach('logged_workouts', 'records', 'strength', 'date');
SELECT sync_attach('logged_workout_exercises', '', '', '', 'sync_log_workout_children');
SELECT sync_attach('logged_workout_sets', '', '', '', 'sync_log_workout_children');
SELECT sync_attach('training_session_logs', 'records', 'training_session', 'date');
SELECT sync_attach('meal_logs', 'records', 'meal_log', 'date');
SELECT sync_attach('lab_results', 'records', 'lab', 'collected_on');
SELECT sync_attach('workouts', 'records', 'workout', 'local_date');
SELECT sync_attach('supplements', 'records', 'supplements', 'NULL');
SELECT sync_attach('journal_items', 'records', 'journal_items', 'NULL');
SELECT sync_attach('meal_templates', 'records', 'meal_templates', 'NULL');
-- raw HealthKit data: the engine queues every changed (user, day) here (rawdata/engine.mark_dirty); derive deletes them
SELECT sync_attach('hk_dirty_days', 'raw', '', 'day');
DROP TRIGGER IF EXISTS hk_dirty_days_sync_del ON hk_dirty_days;

-- Scores the app computed (PUT /v1/users/me/scores/app) are stored in daily_scores like the server's, marked by origin.
ALTER TABLE daily_scores ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'server' CHECK (origin IN ('server', 'app'));
ALTER TABLE daily_scores ADD COLUMN IF NOT EXISTS bundle_version TEXT;

-- Per user: is the app scoring? The hourly job leaves a user's scores to the app while this says so (nativesync/appscores.py).
CREATE TABLE IF NOT EXISTS app_score_state (
  user_id          INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  algo_version     TEXT NOT NULL,
  bundle_version   TEXT,
  last_upload_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  uploads          INT NOT NULL DEFAULT 0,
  days_stored      BIGINT NOT NULL DEFAULT 0,
  trusted          BOOLEAN NOT NULL DEFAULT TRUE,   -- false after a failed spot check, until one passes again
  server_forced    BOOLEAN NOT NULL DEFAULT FALSE,  -- admin: the server scores this user whatever the app uploads
  last_check_at    TIMESTAMPTZ,
  last_check_ok    BOOLEAN,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Spot checks of uploaded app scores against the server's Python (one row per checked day).
CREATE TABLE IF NOT EXISTS app_score_checks (
  id             BIGSERIAL PRIMARY KEY,
  user_id        INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date           DATE NOT NULL,
  algo_version   TEXT NOT NULL,
  bundle_version TEXT,
  ok             BOOLEAN NOT NULL,
  diffs          JSONB NOT NULL DEFAULT '{}',   -- field -> {app, server} for fields beyond the threshold
  checked_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS app_score_checks_user_idx ON app_score_checks (user_id, checked_at DESC);

-- Idempotency of the app's writes: client_id -> the answer it got (POST /v1/sync/push and the record endpoints that take
-- a client_id). A resend returns the stored answer instead of writing again. Kept CLIENT_WRITE_KEEP_DAYS.
CREATE TABLE IF NOT EXISTS client_writes (
  user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  client_id  TEXT NOT NULL,
  scope      TEXT NOT NULL,                   -- e.g. push:water, journal.put
  status     INT NOT NULL,
  response   JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, client_id)
);
CREATE INDEX IF NOT EXISTS client_writes_time_idx ON client_writes (created_at);

-- Silent pushes (content-available) sent on data changes: the last change seq each user was told about.
CREATE TABLE IF NOT EXISTS sync_push_state (
  user_id   INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  seq       BIGINT NOT NULL,
  pushed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Only the native app registers for silent pushes (the Flutter app would just wake up for nothing).
ALTER TABLE push_devices ADD COLUMN IF NOT EXISTS silent BOOLEAN NOT NULL DEFAULT FALSE;
