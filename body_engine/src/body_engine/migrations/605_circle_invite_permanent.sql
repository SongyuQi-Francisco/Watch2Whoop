-- A circle invite code may never expire (#156): expires_at NULL = valid until revoked. Invites addressed to a user
-- (referral, #57) still always expire.
ALTER TABLE circle_invites ALTER COLUMN expires_at DROP NOT NULL;
ALTER TABLE circle_invites ADD CONSTRAINT circle_invites_expiry
  CHECK (expires_at IS NOT NULL OR code_sha256 IS NOT NULL);
