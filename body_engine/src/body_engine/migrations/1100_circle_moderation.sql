-- Circle moderation (App Store guideline 1.2: user-generated content): block, report, review queue, bans and the
-- community-rules acceptance (friends/moderation.py, friends/rules.py, docs/legal/community-rules.md). No health data here:
-- ids, report reasons, short notes and states. No grant to grafana_ro (circle social data).

-- Who blocked whom. Blocking hides the other person's posts, comments, ❤️, member card and leaderboard row from the blocker
-- (and the blocker's from them), stops notifications and cheers between the two, and pauses the circle sharing between them
-- (shares.circle_scopes, friends/store.py) until unblocked. source = report: added by reporting the person's profile.
CREATE TABLE circle_blocks (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,            -- the blocker
  blocked_user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source TEXT NOT NULL DEFAULT 'block' CHECK (source IN ('block', 'report')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, blocked_user_id),
  CHECK (user_id <> blocked_user_id)
);
CREATE INDEX circle_blocks_blocked ON circle_blocks (blocked_user_id);

-- One row per reported item (a post, a comment, a person's profile = name + picture): its review state.
-- open: reported, still shown to everyone but the reporters; hidden: enough distinct reporters (friends/params.py
-- REPORT_HIDE_AFTER), hidden for everyone but its author until an admin looks; kept / removed: decided by an admin.
CREATE TABLE circle_moderation (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('post', 'comment', 'profile')),
  target_id BIGINT NOT NULL,                                              -- post id / comment id / user id
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,            -- whose content it is
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'hidden', 'kept', 'removed')),
  first_reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  hidden_at TIMESTAMPTZ,
  kept_at TIMESTAMPTZ,                                                    -- reports before this no longer count to hide it
  resolved_at TIMESTAMPTZ,
  resolved_by INT REFERENCES users(id) ON DELETE SET NULL,                -- NULL: the command line
  action TEXT CHECK (action IN ('keep', 'remove', 'ban-user')),
  UNIQUE (kind, target_id)
);
CREATE INDEX circle_moderation_queue ON circle_moderation (status, first_reported_at);
CREATE INDEX circle_moderation_user ON circle_moderation (user_id);

-- Each report: one per reporter and item (reporting again changes nothing). Hides the item for the reporter at once.
CREATE TABLE circle_reports (
  id BIGSERIAL PRIMARY KEY,
  item_id BIGINT NOT NULL REFERENCES circle_moderation(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,            -- the reporter
  reason TEXT NOT NULL CHECK (reason IN ('spam', 'harassment', 'hate', 'sexual', 'violence', 'self_harm', 'other')),
  note TEXT CHECK (length(note) <= 200),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (item_id, user_id)
);
CREATE INDEX circle_reports_user ON circle_reports (user_id);

-- Audit trail of the review: every automatic hide and every admin decision.
CREATE TABLE circle_moderation_log (
  id BIGSERIAL PRIMARY KEY,
  item_id BIGINT REFERENCES circle_moderation(id) ON DELETE SET NULL,
  user_id INT REFERENCES users(id) ON DELETE SET NULL,                    -- whose content
  admin_id INT REFERENCES users(id) ON DELETE SET NULL,                   -- NULL: automatic, or the command line
  actor TEXT NOT NULL,                                                    -- 'auto', 'cli', 'api:<admin id>'
  action TEXT NOT NULL CHECK (action IN ('auto_hide', 'keep', 'remove', 'ban-user', 'unban')),
  note TEXT CHECK (length(note) <= 500),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- People an admin banned from circles (action ban-user): taken out of every circle, cannot create / join / comment / ❤️ / cheer.
CREATE TABLE circle_bans (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  banned_by INT REFERENCES users(id) ON DELETE SET NULL,
  item_id BIGINT REFERENCES circle_moderation(id) ON DELETE SET NULL,
  note TEXT CHECK (length(note) <= 500),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Community rules accepted before creating / joining a circle or commenting (flag circle_rules, friends/rules.py).
CREATE TABLE circle_rules_acceptances (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  version TEXT NOT NULL,
  accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, version)
);

-- The comment reports made before this migration move into the new tables (the old table stays, no longer written).
INSERT INTO circle_moderation (kind, target_id, user_id, first_reported_at, last_reported_at)
SELECT 'comment', c.id, c.user_id, min(r.created_at), max(r.created_at)
FROM circle_feed_reports r JOIN circle_feed_comments c ON c.id = r.comment_id
GROUP BY c.id, c.user_id;
INSERT INTO circle_reports (item_id, user_id, reason, note, created_at)
SELECT m.id, r.reporter_id, 'other', left(r.reason, 200), r.created_at
FROM circle_feed_reports r JOIN circle_moderation m ON m.kind = 'comment' AND m.target_id = r.comment_id;
