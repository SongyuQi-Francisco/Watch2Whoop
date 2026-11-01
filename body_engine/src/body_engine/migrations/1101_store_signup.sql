-- App Store sign-up (flag store_signup, account/open_signup.py, account/params.py): which switch opened an account.
-- open: open sign-up below the account cap (flag open_signup); store: the App Store switch (no cap). Existing rows are open.
ALTER TABLE open_signups ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'open' CHECK (mode IN ('open', 'store'));
