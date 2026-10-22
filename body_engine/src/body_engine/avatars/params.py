"""Avatar upload limits (avatars/api.py). Product choices; keep them configurable."""

UPLOADS_PER_WINDOW = 10        # uploads (also refused ones) per person ...
WINDOW_SECONDS = 3600          # ... in this many seconds → 429
