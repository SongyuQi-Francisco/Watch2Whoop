"""Limits and lists of the MCP OAuth server (ai/oauth.py, oauth_http.py). Security policy, not health thresholds; the reasons
are next to each value (security review of PR #85)."""

# Redirect hosts of the AI apps we expect (their documented OAuth callbacks). Any other host still works, but the consent page
# shows a prominent warning: a dynamically registered client can call itself "Claude" and send the code anywhere.
#   claude.ai            https://claude.ai/api/mcp/auth_callback (Claude web / desktop / mobile)
#   chatgpt.com          https://chatgpt.com/connector_platform_oauth_redirect, /connector/oauth/{id}
#   chat.openai.com      older ChatGPT callback host
#   antigravity.google   https://antigravity.google/oauth-callback (Antigravity desktop / IDE / CLI)
#   gemini.google.com    Gemini app custom apps
KNOWN_REDIRECT_HOSTS = ("claude.ai", "chatgpt.com", "chat.openai.com", "antigravity.google", "gemini.google.com")

# /oauth/authorize needs no login and stores a request row each time: per-IP limit (in process)
AUTHORIZE_PER_IP = 30
AUTHORIZE_WINDOW_S = 600

# Dynamic client registration: per IP per hour, and clients nobody approved yet across the server per day. Approved clients
# do not count, so a flood of registrations cannot lock out real users for long; unapproved ones are deleted after
# CLIENT_UNUSED_HOURS by the hourly `body-engine oauth-cleanup`.
REGISTER_PER_IP_HOUR = 20
REGISTER_UNAPPROVED_PER_DAY = 500
CLIENT_UNUSED_HOURS = 1

# Approved but the client never exchanged the code (no token ever issued): deleted after this, so they do not fill MAX_GRANTS
GRANT_UNUSED_HOURS = 1

# Per-issuer cache of the SDK route tables (one issuer in practice: BODY_PUBLIC_URL)
APPS_CACHE_MAX = 4
