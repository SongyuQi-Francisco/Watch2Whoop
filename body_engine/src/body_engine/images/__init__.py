"""Small square images (circle avatars now, user avatars next, #200): validated, re-encoded, kept in the database.

- params.py: accepted types, size limits, output size
- store.py: process() (check type and size, square crop, resize, drop EXIF), save() / get() / delete()
Who may read an image is decided by the feature that points at it (e.g. friends/api.py: circle members only).
"""
