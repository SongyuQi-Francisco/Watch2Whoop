-- Circle activation codes and circle avatars (#199; friends/codes.py, friends/admin.py, images/).
-- A circle activation code is a readable promo code (e.g. a club's name) the admin binds to one circle: a new person who
-- signs up with it gets an account and joins that circle once they have given the electronic consent; someone who already
-- has an account joins the circle with it on the Friends page. Uses are capped, may expire, can be disabled; every use is
-- one row. Avatars are small square images kept in the database (images/store.py, shared with user avatars later).
-- No grant to grafana_ro (nothing here is health data).

-- Re-encoded square images (EXIF and every other metadata dropped). user_id = who uploaded it (NULL: the account is gone).
CREATE TABLE images (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id) ON DELETE SET NULL,
  kind TEXT NOT NULL CHECK (kind IN ('circle_avatar', 'user_avatar')),
  mime TEXT NOT NULL CHECK (mime IN ('image/webp', 'image/png', 'image/jpeg')),
  width INT NOT NULL,
  height INT NOT NULL,
  data BYTEA NOT NULL,
  sha256 TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX images_user ON images (user_id);

ALTER TABLE circles
  ADD COLUMN avatar_image_id INT REFERENCES images(id) ON DELETE SET NULL,
  -- what a member joining with a circle code shares until they change it; NULL = friends/params.py DEFAULT_SCOPES
  ADD COLUMN default_scopes TEXT[] CHECK (default_scopes IS NULL OR NOT ('ecg' = ANY(default_scopes))),
  -- member cap of this circle; NULL = friends/params.py MAX_MEMBERS (club circles made by the admin are bigger)
  ADD COLUMN max_members INT CHECK (max_members IS NULL OR max_members BETWEEN 2 AND 1000);

-- code: as the admin typed it (shown in listings; it is a promo code, not a secret); code_norm: case-folded without
-- spaces / dashes, what is matched. One live (not disabled) code per normalised text.
CREATE TABLE circle_codes (
  id SERIAL PRIMARY KEY,
  circle_id INT NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
  user_id INT REFERENCES users(id) ON DELETE SET NULL,          -- the circle's owner when the code was made
  code TEXT NOT NULL CHECK (length(code) BETWEEN 4 AND 60),
  code_norm TEXT NOT NULL CHECK (length(code_norm) BETWEEN 4 AND 40),
  label TEXT,
  max_uses INT NOT NULL CHECK (max_uses BETWEEN 1 AND 10000),
  uses INT NOT NULL DEFAULT 0 CHECK (uses >= 0 AND uses <= max_uses),
  expires_at TIMESTAMPTZ,                                       -- NULL = no expiry
  disabled_at TIMESTAMPTZ,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX circle_codes_live ON circle_codes (code_norm) WHERE disabled_at IS NULL;
CREATE INDEX circle_codes_circle ON circle_codes (circle_id);

-- One row per use: who, when, whether it opened a new account, and when they actually joined the circle
-- (joined_at NULL = waiting for that person's consent). user_id NULL = the account was deleted (the use still counts).
CREATE TABLE circle_code_uses (
  id SERIAL PRIMARY KEY,
  code_id INT NOT NULL REFERENCES circle_codes(id) ON DELETE CASCADE,
  user_id INT REFERENCES users(id) ON DELETE SET NULL,
  new_account BOOLEAN NOT NULL,
  used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  joined_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX circle_code_uses_once ON circle_code_uses (code_id, user_id) WHERE user_id IS NOT NULL;
CREATE INDEX circle_code_uses_pending ON circle_code_uses (user_id) WHERE joined_at IS NULL;
