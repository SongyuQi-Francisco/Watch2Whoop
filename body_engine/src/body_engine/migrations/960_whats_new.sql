-- Weekly "what's new" message (whatsnew/): one row per user and send slot (the user's local Sunday), so the hourly
-- `body-engine whats-new send` sends at most one message per person per week and the next one starts where this one ended.
-- Not health data; no grant to grafana_ro.
CREATE TABLE IF NOT EXISTS whats_new_sends (
  user_id         INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week_start      DATE NOT NULL,                 -- the user's local Sunday of the send slot
  through         DATE NOT NULL,                 -- entries dated up to this local day are covered
  items           INT NOT NULL,                  -- how many entries the message named
  outcome         TEXT NOT NULL CHECK (outcome IN ('sent', 'off')),   -- off: the person switched the category off, nothing was written
  notification_id BIGINT,                        -- notifications.id (no FK: notifications are pruned after 90 days)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, week_start)
);
COMMENT ON TABLE whats_new_sends IS 'Which weeks the what''s-new message went to whom (whatsnew/service.py)';
