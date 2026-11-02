-- Admin notices in two languages (issue #64): the admin may write an English title / text / button label next to the
-- usual one (notices/store.py send(); `notice-send --title-en …`). A user whose page language is English gets the English
-- text when there is one, everyone else (and English users when there is none) the original. Not health data.
ALTER TABLE admin_notices ADD COLUMN IF NOT EXISTS title_en TEXT;
ALTER TABLE admin_notices ADD COLUMN IF NOT EXISTS body_en TEXT;
ALTER TABLE admin_notices ADD COLUMN IF NOT EXISTS link_label_en TEXT;
COMMENT ON COLUMN admin_notices.title_en IS 'English title (optional); shown to users whose page language is English';
