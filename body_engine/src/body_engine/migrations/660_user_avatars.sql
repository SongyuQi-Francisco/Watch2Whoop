-- Personal avatars (#200; avatars/). The picture lives in `images` (kind user_avatar, migration 655: re-encoded square WebP
-- without metadata); the user row points at the current one. Replacing or removing it deletes the old image row.
-- Only the person and the members of a circle they are in can fetch it (avatars/api.py). No grant to grafana_ro.
ALTER TABLE users ADD COLUMN avatar_image_id INT REFERENCES images(id) ON DELETE SET NULL;
