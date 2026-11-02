-- Messages from the admin to one user (notices/): shown once as a sheet on the user's Today page (also inside the app's
-- WebView), with an optional button link; marked seen when shown. Written by `body-engine notice-send` or
-- POST /v1/admin/notices (BODY_ADMIN_USER_IDS only), e.g. "your phone sync stopped, tap to reconnect".
-- Not health data; no grant to grafana_ro.
CREATE TABLE IF NOT EXISTS admin_notices (
  id         BIGSERIAL PRIMARY KEY,
  user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- who sees it
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '',
  link       TEXT,                     -- https://…, /app/… or watch2whoop://… (notices/store.py checks it)
  link_label TEXT,                     -- the button's text (default 「打开」)
  sent_by    INT,                      -- the admin's user id; NULL = the command line (no FK: kept if that account goes)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,              -- not shown after this (NULL = until seen)
  seen_at    TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ               -- withdrawn by the admin before it was seen
);
CREATE INDEX IF NOT EXISTS admin_notices_unseen ON admin_notices (user_id, created_at) WHERE seen_at IS NULL AND revoked_at IS NULL;
COMMENT ON TABLE admin_notices IS 'One-time in-app messages from the admin to one user (notices/)';
