-- Each member's own handling of a circle (#269 P2, flag circle_prefs, docs/circles-redesign.md §3; friends/prefs.py):
-- pin it (first in the switcher, at most params.MAX_PINNED), mute it (no ❤️ / comment notifications or red dot through it,
-- its posts not in the 「全部」 feed), archive it (hidden from the switcher and from 「全部」, also muted). None of this
-- changes what anyone shares: an archived circle still shares as before (the page says so); leaving is what stops it.
-- Only the member themselves reads or writes their row. No row = none of these. No grant to grafana_ro (not health data).
CREATE TABLE circle_member_prefs (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  circle_id INT NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
  pinned_at TIMESTAMPTZ,
  muted_at TIMESTAMPTZ,
  archived_at TIMESTAMPTZ,
  PRIMARY KEY (user_id, circle_id)
);
CREATE INDEX circle_member_prefs_circle ON circle_member_prefs (circle_id);
