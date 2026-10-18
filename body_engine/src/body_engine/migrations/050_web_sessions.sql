-- Login sessions for the Today page (PWA): the first time, the user enters an API token on the web page, which is exchanged for an httpOnly cookie (web/session.py).
-- Only the sha256 of the session ID is stored; a session hangs off the token that created it and dies with it when the token is revoked; logging out = deleting this row.
CREATE TABLE web_sessions (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_id INT NOT NULL REFERENCES api_tokens(id) ON DELETE CASCADE,
  session_sha256 TEXT NOT NULL UNIQUE,
  user_agent TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX web_sessions_user ON web_sessions (user_id);
