-- get_briefing size and cache hit rate (issue #78, docs/ai-assistant-plan.md §9.13): one row per user and local day, counts only.
-- Written on every get_briefing of one's own data (assistant/briefing.py); read by `body-engine briefing-stats` to decide whether
-- an incremental briefing is worth building. No content is stored, only how often the cache answered and how many characters
-- the briefing had.
CREATE TABLE briefing_stats (
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day         DATE NOT NULL,
  hits        INTEGER NOT NULL DEFAULT 0,
  misses      INTEGER NOT NULL DEFAULT 0,
  chars_total BIGINT NOT NULL DEFAULT 0,
  chars_max   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
        GRANT SELECT ON briefing_stats TO grafana_ro;
    END IF;
END $$;
