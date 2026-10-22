-- Circles (Friends page, docs/friends-social-plan.md §3.2 / §4, friends/ module): self-service family / friend groups.
-- Joining a circle shares the scopes the member picked with every other member; the union over all circles two people
-- share is written to shares.circle_scopes, kept apart from the hand-made grants in shares.scopes so leaving a circle
-- never removes an admin-made share. ecg is never a circle scope (friends/params.py CIRCLE_SCOPES, CHECK below).
-- No grant to grafana_ro (nothing here is health data).

ALTER TABLE shares ADD COLUMN circle_scopes TEXT[] NOT NULL DEFAULT '{}' CHECK (NOT ('ecg' = ANY(circle_scopes)));
COMMENT ON COLUMN shares.scopes IS 'Hand-made grant (admin), subject to expires_at';
COMMENT ON COLUMN shares.circle_scopes IS 'Derived from common circles (friends/store.py sync_pair); never expires, recomputed on every change';

CREATE TABLE circles (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  owner_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- creator; may invite, reset invites, remove members
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX circles_owner ON circles (owner_id);

CREATE TABLE circle_members (
  circle_id INT NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scopes TEXT[] NOT NULL DEFAULT '{}' CHECK (NOT ('ecg' = ANY(scopes))),   -- what this member shows the others
  joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (circle_id, user_id)
);
CREATE INDEX circle_members_user ON circle_members (user_id);

-- An invite is either a code (72 h, shared as a link / QR; only its sha256 is kept) or addressed to one user (a friend who
-- signed up with the inviter's referral code, account/invites.py). Resetting revokes every live code of the circle.
CREATE TABLE circle_invites (
  id SERIAL PRIMARY KEY,
  circle_id INT NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
  created_by INT REFERENCES users(id) ON DELETE SET NULL,
  code_sha256 TEXT UNIQUE,
  invitee_user_id INT REFERENCES users(id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  used_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((code_sha256 IS NULL) <> (invitee_user_id IS NULL))
);
CREATE INDEX circle_invites_circle ON circle_invites (circle_id);
CREATE INDEX circle_invites_invitee ON circle_invites (invitee_user_id) WHERE invitee_user_id IS NOT NULL;

ALTER TABLE invite_redemptions ADD CONSTRAINT invite_redemptions_circle
  FOREIGN KEY (circle_id) REFERENCES circles(id) ON DELETE SET NULL;

-- One-tap cheers (👏 / 💤 / 💪): one of each kind per person per day, only between people who share a circle.
CREATE TABLE circle_nudges (
  id SERIAL PRIMARY KEY,
  from_user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  to_user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('clap', 'sleep', 'train')),
  date DATE NOT NULL,                          -- the sender's local date
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (from_user_id, to_user_id, kind, date),
  CHECK (from_user_id <> to_user_id)
);
CREATE INDEX circle_nudges_to ON circle_nudges (to_user_id, date);
