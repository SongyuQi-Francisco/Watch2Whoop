-- Circle training feed (rollout flag circle_feed, friends/feed.py): one post per person per local day, made automatically when
-- the person has training that day AND ticked the `training` scope for the circle. The row only anchors likes and comments;
-- what the post shows is read live from training_session_logs / workouts, so it follows the day's new sets. Posts have no
-- delete: who may see one is decided on every read from circle membership (leaving a circle hides it at once).
-- No grant to grafana_ro (circle social data).

CREATE TABLE circle_feed_posts (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,                              -- the author's local date
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, date)
);
CREATE INDEX circle_feed_posts_order ON circle_feed_posts (date DESC, id DESC);

-- ❤️ (separate from the one-tap cheers in circle_nudges). Unliking keeps the row (active = false) so liking again
-- does not notify the author a second time.
CREATE TABLE circle_feed_likes (
  post_id BIGINT NOT NULL REFERENCES circle_feed_posts(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (post_id, user_id)
);

-- Text-only comments. The author deletes their own (soft delete: kept for reports, never shown).
CREATE TABLE circle_feed_comments (
  id BIGSERIAL PRIMARY KEY,
  post_id BIGINT NOT NULL REFERENCES circle_feed_posts(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);
CREATE INDEX circle_feed_comments_post ON circle_feed_comments (post_id, id);
CREATE INDEX circle_feed_comments_user ON circle_feed_comments (user_id, created_at);

CREATE TABLE circle_feed_reports (
  comment_id BIGINT NOT NULL REFERENCES circle_feed_comments(id) ON DELETE CASCADE,
  reporter_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  reason TEXT CHECK (length(reason) <= 200),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (comment_id, reporter_id)
);

-- When a person last opened the feed: ❤️ and comments on their posts after this make the red dot.
CREATE TABLE circle_feed_seen (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
