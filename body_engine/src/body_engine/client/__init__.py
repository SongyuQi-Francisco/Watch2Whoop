"""Forced upgrade of the app clients (R2, docs/ios-native-2.0.md §0 D3 and §4 R2).

params.py   defaults: minimum version per platform, notice length, paths never blocked, notification kind
policy.py   the deployer's policy (rollout_settings, audited) + flag `force_upgrade` (kill switch); which client a request
            comes from; the state none / notice / block for one request; the 426 answer
notice.py   `body-engine upgrade send`: admin notice + push to everyone still on an old client (idempotent, --dry-run)
cli.py      `body-engine upgrade status | set | on | off | send`

Only the native app (X-App-Client: ios-native + X-App-Version) and the Today page (web session cookie, also inside the
1.0.8 app's WebView) are ever asked to upgrade. Bearer tokens without X-App-Client (API keys, scripts, the 1.0.8 app's own
uploads), MCP tokens and /mcp, and /v1/ingest/* are never blocked: AI clients keep working and no upload is lost.
"""
