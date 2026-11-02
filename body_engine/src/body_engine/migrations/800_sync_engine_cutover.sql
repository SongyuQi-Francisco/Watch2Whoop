-- Apple sync engine, steps S4–S6 (docs/apple-sync-engine.md §4, §5; issue #219): the shadow comparison, the per-user
-- cut-over to the app's own sync engine, the frozen copy of Open Wearables' history and the upload failures the sync
-- status panel shows. Every table carries user_id; nothing here changes what a user sees until `sync-mode switch`.

-- 1. Shadow comparison (rawdata/shadow.py, `body-engine sync-shadow-compare`): one row per user, closed day and item.
--    kind = daily / sleep / workout / score; key = the metric, 'night', the workout pairing key or the score name.
--    verdict: ok / explained / mismatch / missing_app / new (only the app has it: types OW does not carry, never judged).
CREATE TABLE IF NOT EXISTS sync_shadow_diffs (
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date        DATE NOT NULL,
  kind        TEXT NOT NULL,
  key         TEXT NOT NULL,
  ow_value    DOUBLE PRECISION,
  app_value   DOUBLE PRECISION,
  diff        DOUBLE PRECISION,
  rel         DOUBLE PRECISION,
  verdict     TEXT NOT NULL,
  reason      TEXT,                          -- reason code (rawdata/params.SHADOW_REASONS), NULL when ok
  detail      JSONB,                         -- the compared fields (minutes, counts ...): health values, own data only
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, date, kind, key)
);
CREATE INDEX IF NOT EXISTS sync_shadow_diffs_bad_idx ON sync_shadow_diffs (user_id, date) WHERE verdict IN ('mismatch', 'missing_app');

-- Per user: the result of the last comparison (counts only, no health values: doctor and the private ops plugin read it)
CREATE TABLE IF NOT EXISTS sync_shadow_summary (
  user_id      INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  first_day    DATE,
  last_day     DATE,
  counts       JSONB NOT NULL DEFAULT '{}',  -- verdict -> number of items over the compared days
  clean_streak INT NOT NULL DEFAULT 0,       -- consecutive closed days (newest first) without mismatch / missing_app
  coverage     DOUBLE PRECISION,             -- share of OW's daily items the app also has
  ready        BOOLEAN NOT NULL DEFAULT false,
  blockers     TEXT[] NOT NULL DEFAULT '{}'  -- why not ready (codes)
);

-- 2. Cut-over (rawdata/cutover.py, `body-engine sync-mode`): the applied sync mode per user. The effective mode is
--    `app` only while this row says app AND the flags still want it (apple_sync_engine + apple_sync_primary, no pin):
--    turning a flag off or pinning the user to ow takes effect on the next read; `sync-mode apply` then cleans up.
CREATE TABLE IF NOT EXISTS sync_mode_state (
  user_id     INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  mode        TEXT NOT NULL DEFAULT 'shadow' CHECK (mode IN ('shadow', 'app')),
  pin         TEXT CHECK (pin IN ('ow')),    -- per-user rollback: stays on OW whatever the flags say
  switched_at TIMESTAMPTZ,                   -- last switch to app
  rolled_back_at TIMESTAMPTZ,
  checks      JSONB NOT NULL DEFAULT '{}',   -- the gates of the last attempt and the score consistency check
  pre_scores  JSONB,                         -- daily_scores of the check window right before the switch (date -> scores)
  post_check  JSONB,                         -- the same days after the first recompute in the app mode, compared
  note        TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Open Wearables' history kept locally (rawdata/ow_archive.py, `body-engine ow-archive`): the raw samples stay in
--    ow_sample_cache (migration 770) frozen at covered_to; the sleep stages, which scoring read live from OW, are stored
--    here. Once a user is archived, nothing in the app mode asks OW any more.
CREATE TABLE IF NOT EXISTS ow_archive (
  user_id      INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  ow_user_id   TEXT NOT NULL,
  covered_from TIMESTAMPTZ NOT NULL,
  covered_to   TIMESTAMPTZ NOT NULL,
  samples      JSONB NOT NULL DEFAULT '{}',  -- OW series code -> samples in the copy (counts only)
  nights       INT NOT NULL DEFAULT 0,
  segments     INT NOT NULL DEFAULT 0,
  archived_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ow_archive_segments (
  user_id  INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  start_at TIMESTAMPTZ NOT NULL,
  end_at   TIMESTAMPTZ NOT NULL,
  stage    TEXT NOT NULL,                    -- importers/ow.sleep_segments' stage (core / deep / rem / awake / unspecified)
  source   TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (user_id, start_at, stage, source)
);

-- 4. Uploads the server refused (rawdata/sync_status.py): the sync status panel shows the last one and counts by reason.
--    reason: auth / format / limit / refused / server. No request body is stored (health data), only codes.
--    Repeats of the same reason and code within a few minutes (a phone retrying in a loop) update one row (n, at).
CREATE TABLE IF NOT EXISTS ingest_failures (
  id          BIGSERIAL PRIMARY KEY,
  user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  first_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  at          TIMESTAMPTZ NOT NULL DEFAULT now(),   -- the latest occurrence
  n           INT NOT NULL DEFAULT 1,
  reason      TEXT NOT NULL,
  status      INT NOT NULL,
  code        TEXT,                          -- e.g. consent_required, rate_limited, token_revoked, validation
  batch_id    UUID,
  phase       TEXT,
  app_version TEXT
);
CREATE INDEX IF NOT EXISTS ingest_failures_user_idx ON ingest_failures (user_id, at DESC);

COMMENT ON TABLE sync_shadow_diffs IS 'Per closed day: Open Wearables vs the app sync engine, item by item, with a verdict';
COMMENT ON TABLE sync_shadow_summary IS 'Per user: the last shadow comparison (counts only) and whether the cut-over gates pass';
COMMENT ON TABLE sync_mode_state IS 'Per user: the applied sync mode of the app sync engine cut-over, with the checks';
COMMENT ON TABLE ow_archive IS 'Per user: the Open Wearables history kept locally so the app mode never asks OW again';
COMMENT ON TABLE ingest_failures IS 'Refused app uploads (codes only) for the sync status panel';

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
        GRANT SELECT ON sync_shadow_summary, sync_mode_state, ingest_failures TO grafana_ro;
    END IF;
END $$;
