-- The inbox behind 「我 → 消息」 (inbox/, issue #359 P1, flag inbox): a read-only merge of admin_notices, whats_new_sends,
-- circle_invites addressed to the person and notifications of the category circle; those tables stay as they are. This table
-- holds only what none of them has a place for: which items the person hid, and read state for items without a read column of
-- their own (invites, what's-new weeks whose push was switched off). Not health data; no grant to grafana_ro.
CREATE TABLE inbox_item_state (
  user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_id    TEXT NOT NULL CHECK (length(item_id) <= 60),   -- notice:3, whatsnew:2026-10-25, invite:12, notification:9
  read_at    TIMESTAMPTZ,
  hidden_at  TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),            -- rows older than inbox/params.KEEP_DAYS are removed on the next write
  PRIMARY KEY (user_id, item_id)
);
COMMENT ON TABLE inbox_item_state IS 'What a person read or hid in the inbox, where the source table has no place for it (inbox/store.py)';
