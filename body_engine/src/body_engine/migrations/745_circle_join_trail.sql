-- How each member came into a circle (#269, docs/circles-redesign.md §5; friends/trail.py). Written on every join from now on;
-- shown to the circle's owner only (flag circle_join_trail), never to the other members.
--   join_via: created      made the circle (or their personal circle was made for them)
--             invite_code  a circle invite code / link (circle_invites with code_sha256); invited_by = who made the code
--             referral     signed up with someone's referral code and accepted the invite to their home circle
--                          (circle_invites addressed to the user); invited_by = the referral code's owner
--             circle_code  a circle activation code made by the admin (circle_codes); invited_by stays NULL
--             NULL         not recorded (joined before this migration and the backfill below found nothing)
-- join_inferred: backfilled invite_code rows where several codes of the circle were live when the person joined; the
-- inviter is still right (only the owner makes codes), only which code is a guess (the latest one made before joining).
-- Written so that running the file again only fills rows that are still empty (tests rerun the backfill).
-- No grant to grafana_ro (not health data).

ALTER TABLE circle_members
  ADD COLUMN IF NOT EXISTS invited_by INT REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS join_via TEXT CHECK (join_via IN ('created', 'invite_code', 'referral', 'circle_code')),
  ADD COLUMN IF NOT EXISTS invite_id INT REFERENCES circle_invites(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS circle_code_id INT REFERENCES circle_codes(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS join_inferred BOOLEAN NOT NULL DEFAULT false;

-- Backfill. Every join wrote its rows in one transaction, so now() is the same instant on both sides; that is what is
-- matched on below (exact, not a time window).

-- 1. circle activation codes: circle_code_uses.joined_at is set in the transaction that adds the member
UPDATE circle_members m SET join_via = 'circle_code', circle_code_id = u.code_id
FROM circle_code_uses u JOIN circle_codes k ON k.id = u.code_id
WHERE m.join_via IS NULL AND k.circle_id = m.circle_id AND u.user_id = m.user_id AND u.joined_at = m.joined_at;

-- 2. accepted referral invites: used up (used_count + 1, revoked) in the joining transaction. Joining with a code also
-- revokes open addressed invites at that instant, but leaves their used_count at 0.
UPDATE circle_members m SET join_via = 'referral', invite_id = i.id, invited_by = i.created_by
FROM circle_invites i
WHERE m.join_via IS NULL AND i.circle_id = m.circle_id AND i.invitee_user_id = m.user_id
  AND i.used_count > 0 AND i.revoked_at = m.joined_at;

-- 3. the circle's creator: inserted in the transaction that made the circle
UPDATE circle_members m SET join_via = 'created'
FROM circles c
WHERE m.join_via IS NULL AND c.id = m.circle_id AND m.joined_at = c.created_at;

-- 4. the rest of the circle's members came in with one of its codes (the only other way in); which one is not recorded.
-- Candidates: codes of the circle that were used, made before the person joined and still live at that moment.
WITH cand AS (
  SELECT m.circle_id, m.user_id, i.id AS invite_id, i.created_by,
         count(*) OVER (PARTITION BY m.circle_id, m.user_id) AS n,
         row_number() OVER (PARTITION BY m.circle_id, m.user_id ORDER BY i.created_at DESC, i.id DESC) AS rk
  FROM circle_members m JOIN circle_invites i ON i.circle_id = m.circle_id
  WHERE m.join_via IS NULL AND i.code_sha256 IS NOT NULL AND i.used_count > 0 AND i.created_at <= m.joined_at
    AND (i.revoked_at IS NULL OR i.revoked_at >= m.joined_at)
    AND (i.expires_at IS NULL OR i.expires_at >= m.joined_at))
UPDATE circle_members m SET join_via = 'invite_code', invite_id = cand.invite_id, invited_by = cand.created_by,
                            join_inferred = cand.n > 1
FROM cand
WHERE m.join_via IS NULL AND cand.rk = 1 AND cand.circle_id = m.circle_id AND cand.user_id = m.user_id;
