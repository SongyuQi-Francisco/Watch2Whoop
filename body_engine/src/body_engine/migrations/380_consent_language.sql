-- Which language of the consent form was read when consenting (issue #64): profile/consent.md (zh) or consent.en.md (en),
-- the same text clause by clause with the same version number. NULL = before this column (always the Chinese text) or unknown.
ALTER TABLE user_consents ADD COLUMN language TEXT CHECK (language IN ('zh', 'en'));
