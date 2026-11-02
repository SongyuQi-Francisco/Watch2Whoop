-- History top-up reminders (「补全历史数据」, sync/backfill.py): the app's first sync reads only the last 14 days (app 1.0.8); the
-- Today page asks the person to top the history up to 90 days, on a fixed rhythm (1, 3 and 7 days after the first sync, then
-- at most weekly), until they decline ("不再提醒"), the top-up is complete or the phone has no more to give. One row per user,
-- written when the Today page or the briefing first looks at the coverage. Phone-sync state, not health data: no grant to grafana_ro.
CREATE TABLE IF NOT EXISTS history_backfill (
  user_id               INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  anchor_at             TIMESTAMPTZ NOT NULL,   -- first time watch data was seen = the "first sync" the rhythm counts from
  last_shown_at         TIMESTAMPTZ,            -- the card was last shown as a reminder (the Today page reports it)
  shown_count           INT NOT NULL DEFAULT 0,
  muted_at              TIMESTAMPTZ,            -- "不再提醒"
  completed_at          TIMESTAMPTZ,            -- the 90-day target was reached: reminders stop for good
  exhausted_at          TIMESTAMPTZ,            -- a whole-history top-up finished and the target is still not reached: the phone has no more
  backfill_requested_at TIMESTAMPTZ,            -- the last top-up started from the card
  backfill_days         INT,                    -- ... how many days it asked for (NULL = all history)
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE history_backfill IS 'Reminder state of the history top-up card on the Today page (sync/backfill.py)';
