-- One-tap consent (rollout flag consent_one_tap; profile/consent.py, profile/api.py): the first-run screen is a single
-- 「开始使用」 button with one line naming the data consent (consent.md) and the Jarvis terms (ai/fallback.py CONSENT_POINTS);
-- one tap records both with their current versions. The texts and their versions do not change, only how the agreement
-- is given, so this adds an allowed method and a column with a default; existing rows keep their meaning.

-- users' consent records: one_tap next to app (read the sheet, ticked the box) and paper (signed offline)
ALTER TABLE user_consents DROP CONSTRAINT IF EXISTS user_consents_method_check;
ALTER TABLE user_consents ADD CONSTRAINT user_consents_method_check CHECK (method IN ('app', 'paper', 'one_tap'));

-- How the fallback AI (Jarvis) consent was given: app = its own sheet before the first Jarvis chat (tick + agree; every row
-- before this migration), one_tap = together with the data consent on the one-tap screen, or the one-tap Jarvis sheet.
ALTER TABLE ai_fallback_consents ADD COLUMN method TEXT NOT NULL DEFAULT 'app' CHECK (method IN ('app', 'one_tap'));
