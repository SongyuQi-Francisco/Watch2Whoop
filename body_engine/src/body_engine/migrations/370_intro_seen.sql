-- First-run intro on the Today page (issue #60): when the user finished or skipped it. Kept per user on the server so it
-- follows the account across devices; NULL = not seen yet (shown once after login), reset by "Me -> 新手引导 / Getting started".
ALTER TABLE users ADD COLUMN IF NOT EXISTS intro_seen_at TIMESTAMPTZ;
COMMENT ON COLUMN users.intro_seen_at IS 'When the Today page first-run intro was finished or skipped; NULL = not seen yet';
