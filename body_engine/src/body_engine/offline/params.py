"""Offline use of the Today page: the flag and the limits of what a device keeps (docs/offline-plan.md).

The page (web/static/offline.js) mirrors the limits; tests/test_offline.py checks the two agree."""
FEATURE = "offline_mode"

CACHE_MAX_ENTRIES = 300          # saved GET responses per device (oldest dropped first)
CACHE_MAX_BYTES = 4_000_000      # ~4 MB of JSON per device; well under the WKWebView / Safari IndexedDB quota
CACHE_MAX_AGE_DAYS = 14          # older copies are not shown (and are dropped)
QUEUE_MAX_ITEMS = 200            # writes waiting for a connection; more are refused with a message
