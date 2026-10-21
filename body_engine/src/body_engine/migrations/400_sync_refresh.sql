-- On-demand refresh (app opened / pull to refresh on the Today page): one row per user, requests collapse into it (sync/refresh.py).
-- The API pulls the user's new data from Open Wearables in the background (cheap); the score recompute (≈1–2 min of CPU) is
-- queued for `body-engine sync-refresh-run`, run outside the API process (Ali: the per-minute imports timer).
CREATE TABLE IF NOT EXISTS sync_refresh (
    user_id      BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'idle' CHECK (status IN ('idle', 'importing', 'scores_queued', 'scoring', 'error')),
    requested_at TIMESTAMPTZ,               -- last accepted request (the rate limit counts from here)
    imported_at  TIMESTAMPTZ,               -- last successful pull from OW by a refresh
    scored_at    TIMESTAMPTZ,               -- last score recompute by a refresh
    error        TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE sync_refresh IS 'On-demand data refresh per user: OW import in the API background, score recompute queued for sync-refresh-run';
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN GRANT SELECT ON sync_refresh TO grafana_ro; END IF;
END $$;
