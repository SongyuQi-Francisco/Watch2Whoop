-- Invites addressed to someone (issue #359 section 3, friends/direct.py): where they came from, how the member got in, and who
-- a person no longer wants invites from. Not health data; Grafana does not read these, so no grant to grafana_ro.
--   origin:  referral  made when someone signed up with the inviter's referral code (store.referral_invites)
--            member    a circle's owner invited someone they already share a circle with (friends/direct.py)
--            admin     the deployer's `body-engine circle-admin invite` (friends/admin.py); created_by = the circle's owner
ALTER TABLE circle_invites ADD COLUMN origin TEXT CHECK (origin IN ('referral', 'member', 'admin'));
UPDATE circle_invites SET origin = 'referral' WHERE invitee_user_id IS NOT NULL;
CREATE INDEX circle_invites_sender_day ON circle_invites (created_by, created_at) WHERE origin = 'member';

ALTER TABLE circle_members DROP CONSTRAINT IF EXISTS circle_members_join_via_check;
ALTER TABLE circle_members ADD CONSTRAINT circle_members_join_via_check
  CHECK (join_via IN ('created', 'invite_code', 'referral', 'circle_code', 'member_invite', 'admin_invite'));

-- 「不再接收此人的邀请」: invites from muted_user_id never reach user_id. The sender is not told (friends/direct.py: their invite
-- simply looks pending until it expires).
CREATE TABLE circle_invite_mutes (
  user_id       INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  muted_user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, muted_user_id),
  CHECK (user_id <> muted_user_id)
);
CREATE INDEX circle_invite_mutes_muted ON circle_invite_mutes (muted_user_id);
