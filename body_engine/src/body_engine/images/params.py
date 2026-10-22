"""Image upload limits (images/store.py). Product choices, not health thresholds; keep them configurable."""

ACCEPTED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}   # Pillow format name -> type
MAX_UPLOAD_BYTES = 5 * 1024 * 1024      # the file as uploaded
MAX_PIXELS = 40_000_000                 # width x height before decoding (decompression bombs)
MIN_SIDE = 32                           # smaller pictures are refused (nothing to show)
SIZE = 256                              # output: SIZE x SIZE pixels, centre crop
OUTPUT_FORMAT, OUTPUT_MIME = "WEBP", "image/webp"
OUTPUT_QUALITY = 85
KINDS = ("circle_avatar", "user_avatar")
