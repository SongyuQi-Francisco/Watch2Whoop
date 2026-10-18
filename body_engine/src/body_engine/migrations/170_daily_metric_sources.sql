-- Per-source daily values (importers/common.py upsert_daily / resolve_daily, docs/shortcut-sync-spec.md §5).
-- Every importer (ow / apple_export / shortcut) writes its own value for (date, metric) here; daily_metrics then holds the value chosen from these:
-- - cumulative metrics (agg = 'sum': steps, active / basal energy, exercise minutes ...): among the complete, de-duplicated sources the largest
--   single-source total; a source whose day is incomplete (e.g. the OW iOS SDK 0.14 partial syncs of 2026-10-12..16) or a Shortcut total that
--   was summed by hour across devices (flag hourly_sum, double-counts iPhone + Watch steps) is used only when no clean source exists;
-- - everything else: source priority ow > apple_export > shortcut, as before.
-- Seeded with the rows already chosen in daily_metrics (the values that lost to a higher-priority source were never stored; the Shortcut ones
-- are rebuilt from shortcut_samples with `body-engine import-shortcut --rebuild-since`).
CREATE TABLE daily_metric_sources (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  metric TEXT NOT NULL,
  source TEXT NOT NULL,                 -- ow / apple_export / shortcut
  value DOUBLE PRECISION NOT NULL,
  unit TEXT,
  agg TEXT NOT NULL,                    -- sum / avg
  n INT,
  min_value DOUBLE PRECISION,
  max_value DOUBLE PRECISION,
  flags JSONB NOT NULL DEFAULT '{}',    -- set by the importer, e.g. {"hourly_sum": "..."}
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, date, metric, source)
);

INSERT INTO daily_metric_sources (user_id, date, metric, source, value, unit, agg, n, min_value, max_value, updated_at)
SELECT user_id, date, metric, source, value, unit, agg, n, min_value, max_value, updated_at FROM daily_metrics;

-- Why the chosen value is less trustworthy: {"hourly_sum": "..."} (summed across devices) / {"incomplete": "..."} (that source's day is partial)
ALTER TABLE daily_metrics ADD COLUMN flags JSONB NOT NULL DEFAULT '{}';

GRANT SELECT ON daily_metric_sources TO grafana_ro;
