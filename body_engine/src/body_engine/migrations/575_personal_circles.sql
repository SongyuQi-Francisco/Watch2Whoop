-- Personal circles (owner decision 2026-10-22, plan A; friends/store.py ensure_personal): everyone gets one circle of their
-- own ("Ann 的圈子"), made the first time circles are used (the `friends` flag on for them and the circle consent given),
-- not by a backfill here, because a migration cannot see per-user flags or consent and flag-off users must see no change.
-- users.personal_circle_at marks that this was settled once: set when the circle is made, and also for people who already
-- owned a circle made by hand (they get no second one; their oldest stays their home circle). Never cleared, so a
-- dissolved personal circle does not come back.
ALTER TABLE circles ADD COLUMN personal BOOLEAN NOT NULL DEFAULT false;
COMMENT ON COLUMN circles.personal IS 'Made automatically as the owner''s personal circle (friends/store.py ensure_personal)';
ALTER TABLE users ADD COLUMN personal_circle_at TIMESTAMPTZ;
COMMENT ON COLUMN users.personal_circle_at IS 'When the personal circle was made (or skipped: already owned a circle); NULL = not yet';
