-- Interface / AI reply language chosen by the user in "Me -> Profile" (issue #13). NULL = follow the device (navigator.language / iOS system language).
ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT CHECK (language IN ('zh', 'en'));
COMMENT ON COLUMN users.language IS 'UI and AI reply language: zh / en; NULL = follow the device language';
