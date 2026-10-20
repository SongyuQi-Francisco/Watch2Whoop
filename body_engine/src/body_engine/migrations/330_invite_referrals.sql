-- Multi-use invite codes bound to sign-up identities, and groundwork for user referral codes (account/invites.py).
-- An admin code (kind admin, owner NULL) can open N accounts; each redemption records which sign-in identity used it.
-- A referral code (kind referral) belongs to a user; redeeming it records the inviter and writes a pending reward.
-- No grant to grafana_ro (nothing here is health data).

ALTER TABLE invite_codes
  ADD COLUMN kind TEXT NOT NULL DEFAULT 'admin' CHECK (kind IN ('admin', 'referral')),
  ADD COLUMN owner_user_id INT REFERENCES users(id) ON DELETE SET NULL,   -- referral: the inviter; admin: NULL
  ADD COLUMN display_code TEXT;                 -- referral only: the user's own shareable code, shown back to them
-- one live referral code per user
CREATE UNIQUE INDEX invite_codes_referral ON invite_codes (owner_user_id) WHERE kind = 'referral' AND revoked_at IS NULL;

ALTER TABLE invite_redemptions
  ADD COLUMN inviter_user_id INT REFERENCES users(id) ON DELETE SET NULL,   -- owner of a referral code, NULL for admin codes
  ADD COLUMN circle_id INT;                     -- TODO(referrals): the inviter's circle the new user joins; no circle logic yet

-- The sign-in identities (Apple / e-mail) of the account at the moment it redeemed a code. One identity can redeem
-- at most once, ever: the row outlives the account (only a hash and a masked hint are kept), so deleting the account
-- and signing up again does not consume another use. hint is what the admin sees in `invite-code list`.
CREATE TABLE invite_redemption_identities (
  id SERIAL PRIMARY KEY,
  redemption_id INT NOT NULL REFERENCES invite_redemptions(id) ON DELETE CASCADE,
  provider TEXT NOT NULL CHECK (provider IN ('apple', 'email')),
  identity_sha256 TEXT NOT NULL UNIQUE,         -- sha256("provider:subject")
  hint TEXT NOT NULL                            -- masked e-mail (j***@e***.com) or Apple ID ···last 4
);
CREATE INDEX invite_redemption_identities_redemption ON invite_redemption_identities (redemption_id);

-- Rewards owed to inviters (account/params.py: type and amount). Written as pending on a referral redemption;
-- nothing grants them yet.
CREATE TABLE invite_rewards (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,          -- the inviter who earns it
  redemption_id INT NOT NULL UNIQUE REFERENCES invite_redemptions(id) ON DELETE CASCADE,
  invitee_user_id INT REFERENCES users(id) ON DELETE SET NULL,
  reward_type TEXT NOT NULL,
  amount NUMERIC NOT NULL,
  unit TEXT,
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'granted', 'cancelled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  granted_at TIMESTAMPTZ
);
CREATE INDEX invite_rewards_user ON invite_rewards (user_id, state);
