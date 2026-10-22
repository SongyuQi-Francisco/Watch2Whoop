-- Notifications (notify/, issue #175): in-app notifications, the iPhones registered for APNs push, per-user settings.
-- One public entry point writes them: notify.notify(conn, user_id, kind, title, body, url, data).
-- Not health data; no grant to grafana_ro.

-- In-app notifications (the Today page's list). title / body in Chinese, *_en in English when the caller gave both;
-- push_status = what happened to the push when it was written (pushed / merged / quiet / off / flag_off / no_device / no_key).
CREATE TABLE notifications (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                          -- "<category>.<event>", e.g. circle.like (notify/params.py CATEGORIES)
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  title_en TEXT,
  body_en TEXT,
  url TEXT,                                    -- path inside the Today page, e.g. /app/#tab=friends
  data JSONB NOT NULL DEFAULT '{}',            -- small caller data (e.g. actor_id for merging); never sent to Apple
  push_status TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  read_at TIMESTAMPTZ
);
CREATE INDEX notifications_user_created ON notifications (user_id, created_at DESC);
CREATE INDEX notifications_unread ON notifications (user_id, kind, created_at) WHERE read_at IS NULL;

-- APNs device tokens. A token belongs to one app install, so it belongs to whoever registered it last; api_token_id is
-- the app's Bearer token that registered it (sign-out revokes that token and removes its devices).
CREATE TABLE push_devices (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token TEXT NOT NULL UNIQUE,                  -- hex, as the app got it from iOS
  platform TEXT NOT NULL DEFAULT 'ios' CHECK (platform IN ('ios')),
  environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
  bundle_id TEXT,
  app_version TEXT,
  lang TEXT CHECK (lang IN ('zh', 'en')),
  api_token_id INT REFERENCES api_tokens(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_push_at TIMESTAMPTZ,
  last_status TEXT                             -- 200 / reason Apple gave for the last push
);
CREATE INDEX push_devices_user ON push_devices (user_id);

-- Per-user settings; no row = the defaults in notify/params.py. categories: {"circle": false} turns that push off.
CREATE TABLE notification_settings (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  categories JSONB NOT NULL DEFAULT '{}',
  quiet_enabled BOOLEAN NOT NULL DEFAULT true,
  quiet_start TIME NOT NULL DEFAULT '23:00',
  quiet_end TIME NOT NULL DEFAULT '07:00',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
