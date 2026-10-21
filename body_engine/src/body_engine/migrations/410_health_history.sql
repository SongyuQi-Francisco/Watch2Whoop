-- How much Apple Health history the app reads for each user (issue: health range): 'days90' (default) or 'all'.
-- The app reads HealthKit directly (OW SDK) and reports its progress here; once it has uploaded the whole history,
-- `body-engine history-backfill-run` (outside the API) pulls it from Open Wearables window by window and recomputes the
-- scores for the whole history (sync/history.py). Replaces the manual export.zip upload on the Today page.
CREATE TABLE IF NOT EXISTS health_history (
    user_id        BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    scope          TEXT NOT NULL DEFAULT 'days90' CHECK (scope IN ('days90', 'all')),
    status         TEXT NOT NULL DEFAULT 'idle'
                   CHECK (status IN ('idle', 'uploading', 'queued', 'importing', 'scoring', 'done', 'error')),
    upload_percent SMALLINT NOT NULL DEFAULT 0 CHECK (upload_percent BETWEEN 0 AND 100),
    sent_count     BIGINT NOT NULL DEFAULT 0,
    import_from    DATE,                 -- earliest day in Open Wearables
    import_until   DATE,                 -- last day the backfill covers (newer days are kept fresh by the regular import)
    import_cursor  DATE,                 -- next day to import (the backfill resumes here)
    requested_at   TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ,
    error          TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE health_history IS 'Apple Health read range per user and the progress of the full-history backfill';
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN GRANT SELECT ON health_history TO grafana_ro; END IF;
END $$;
