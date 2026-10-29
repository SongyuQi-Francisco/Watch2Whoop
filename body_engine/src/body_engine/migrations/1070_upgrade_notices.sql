-- Forced upgrade (R2, client/notice.py): who was told to upgrade, per stage and block date, so `body-engine upgrade send`
-- sends each message at most once per person (a new block date starts over). The policy itself (dates, minimum versions,
-- store link) is in rollout_settings, its changes in rollout_audit; the kill switch is the flag force_upgrade.
-- Not health data; no grant to grafana_ro.
CREATE TABLE IF NOT EXISTS upgrade_notice_sends (
  user_id         INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  stage           TEXT NOT NULL CHECK (stage IN ('notice', 'last-day')),
  block_from      TEXT NOT NULL DEFAULT '',      -- the policy's block date as set ('' = a notice without a block date)
  notice_id       BIGINT,                        -- admin_notices.id (no FK: kept when the notice goes)
  notification_id BIGINT,                        -- notifications.id (no FK: notifications are pruned after 90 days)
  push_status     TEXT,                          -- notify(): pushed / quiet / off / no_device / flag_off …
  sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, stage, block_from)
);
COMMENT ON TABLE upgrade_notice_sends IS 'Upgrade notices sent per person and stage (client/notice.py, R2)';
