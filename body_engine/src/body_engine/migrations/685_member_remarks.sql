-- Private nicknames for circle members (#212, friends/remarks.py): what `viewer_id` calls `target_id`. Only the viewer
-- ever sees it (no notification, nobody else reads it). Set only while the two share a circle; kept after one of them
-- leaves but shown again only once they share a circle again. Deleting either account deletes the row.
-- No grant to grafana_ro (not health data).
CREATE TABLE circle_member_remarks (
  viewer_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  remark TEXT NOT NULL CHECK (length(remark) BETWEEN 1 AND 60),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (viewer_id, target_id),
  CHECK (viewer_id <> target_id)
);
CREATE INDEX circle_member_remarks_target ON circle_member_remarks (target_id);
