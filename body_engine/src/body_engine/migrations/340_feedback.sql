-- AI self-reported feedback (docs/ai-feedback-plan.md, body_engine/feedback/): bugs and feature requests the user's AI files
-- through the tool report_feedback. body_raw stays on this server (the reporter and the admin only); what may leave the server
-- (the admin's daily digest e-mail, later a private GitHub repository) is the sanitised title / body_public.
CREATE TABLE IF NOT EXISTS feedback_reports (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('bug', 'feature')),
  title TEXT NOT NULL,                    -- sanitised
  body_public TEXT NOT NULL,              -- sanitised Markdown: what happened, expected, steps, error, area
  body_raw JSONB NOT NULL,                -- the fields exactly as the AI sent them (never leaves the server)
  area TEXT,                              -- tool name or page, sanitised
  fingerprint TEXT NOT NULL,              -- dedupe key (feedback/store.fingerprint)
  status TEXT NOT NULL DEFAULT 'new'
    CHECK (status IN ('new', 'triaged', 'filed', 'public', 'fixed', 'declined', 'duplicate')),
  duplicate_of BIGINT REFERENCES feedback_reports(id) ON DELETE SET NULL,
  needs_review BOOLEAN NOT NULL DEFAULT false,   -- heavily redacted: stays in the queue, only its title goes into the digest
  redactions INT NOT NULL DEFAULT 0,
  channel TEXT,                           -- audit channel of the AI (mcp:<client> / api:<provider> / butler)
  model TEXT,
  rules_version TEXT,                     -- root_rules.md version the AI was given
  server_version TEXT,
  diagnostics JSONB,                      -- server-side: tools that failed for this user shortly before (names and counts only)
  note TEXT,                              -- short note from the maintainer, shown to the reporter
  public_url TEXT,                        -- public issue once the maintainer promoted it
  private_url TEXT,                       -- phase 2: mirror in a private repository
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  notified_at TIMESTAMPTZ,                -- the reporter's AI told them about the latest status change (briefing feedback_updates)
  digest_at TIMESTAMPTZ                   -- included in the admin's daily digest
);
CREATE INDEX IF NOT EXISTS feedback_reports_user_idx ON feedback_reports (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS feedback_reports_open_fp_idx ON feedback_reports (fingerprint)
  WHERE status IN ('new', 'triaged', 'filed', 'public');
CREATE INDEX IF NOT EXISTS feedback_reports_created_idx ON feedback_reports (created_at);

-- "+1": another user's AI reported the same open problem
CREATE TABLE IF NOT EXISTS feedback_votes (
  report_id BIGINT NOT NULL REFERENCES feedback_reports(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (report_id, user_id)
);

-- auto = the AI reports and tells the user; ask = it asks first; off = it does not report (the server refuses too).
-- feedback_blocked_at: set by the admin for abuse; the user cannot lift it.
ALTER TABLE users ADD COLUMN IF NOT EXISTS feedback_mode TEXT NOT NULL DEFAULT 'auto'
  CHECK (feedback_mode IN ('auto', 'ask', 'off'));
ALTER TABLE users ADD COLUMN IF NOT EXISTS feedback_blocked_at TIMESTAMPTZ;
