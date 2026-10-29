"""Forced upgrade (R2): defaults. Dates and the store link are never set here: the deployer sets them with
`body-engine upgrade set` (rollout_settings, audited) and turns the flag on with `body-engine upgrade on`."""

FLAG = "force_upgrade"          # rollout/registry.py; off (the default) = no notice, no block, within the flag cache (≤ 30 s)
NOTICE_DAYS = 7                 # without an explicit notice date the notice starts this many days before the block (owner, D3)

# Platforms and their default minimum. ios-native: the native app's X-App-Version ("2.0.0", or "2.0.0+57" = version
# 2.0.0 from build 57 on, header X-App-Build). web: the Today page has no version of its own (it also runs inside the
# retired 1.0.8 app), so its minimum is "all" (every web session counts as old) or "none" (web is not asked to upgrade).
PLATFORMS = ("ios-native", "web")
DEFAULT_MIN = {"ios-native": "2.0.0", "web": "all"}
WEB_VALUES = ("all", "none")
NONE = "none"

# rollout_settings keys (one row each; removed = the default above, or unset for dates and the link)
SETTING_NOTICE_FROM = "upgrade.notice_from"
SETTING_BLOCK_FROM = "upgrade.block_from"
SETTING_URL = "upgrade.url"
SETTING_MIN = "upgrade.min."    # + platform
URL_ENV = "BODY_UPGRADE_URL"    # fallback for the App Store / TestFlight link when the setting is not there

CACHE_TTL_S = 30.0              # policy rows per process; `upgrade set` clears this process's cache

# Paths an old client still reaches after the block: the page's start (/v1/web/me carries the upgrade state; login,
# logout), uploads (no data loss). /mcp and everything outside /v1/ never go through the check.
EXEMPT_PREFIXES = ("/v1/web/", "/v1/ingest/")

NOTIFY_KIND = "system.upgrade"  # notify/params.py category `system`
NOTIFY_URL = "/app/"
STAGES = ("notice", "last-day")  # notice: from the start of the window; last-day: the day before the block
