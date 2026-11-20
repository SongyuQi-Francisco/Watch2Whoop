"""Every route that answers without a login, in one place, each with the reason it may.

Body Engine has no blanket auth middleware: each endpoint asks for the caller itself (api.current_user, the ingest /
MCP / watch token checks). tests/test_auth_boundary.py calls every route of the app with no credentials and with a
revoked token; any route that answers anything but 401 / 403 must be listed here. Adding a public endpoint = adding it
here with its reason, in the same pull request.
"""

# (method, path as the router declares it) -> why it is public
PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    # health check and static pages: no user data
    ("GET", "/healthz"): "liveness probe for the proxy and the doctor; answers {ok: true} only",
    ("GET", "/"): "redirects to the static page /app/",
    ("GET", "/app/"): "the static Today page shell (web/api.py; data comes from /v1 with a login)",
    ("GET", "/app/index.html"): "the same shell under its file name",
    ("GET", "/app/offline-shell.html"): "the shell with its scripts inlined, kept by the app for cold starts (no user data)",
    ("GET", "/app/plugins/manifest.json"): "which static plugin files the page loads (plugins.py); no user data",
    ("GET", "/app/plugins/{name}/{file:path}"): "static files of BODY_PLUGIN_DIR plugins (plugins.py)",
    ("GET", "/legal/{doc}"): "privacy policy and terms pages (App Store requirement)",
    ("GET", "/v1/legal"): "index of the legal pages and their versions",
    # FastAPI's generated API description: the shape of the API, no data
    ("GET", "/openapi.json"): "generated OpenAPI schema (no data)",
    ("GET", "/docs"): "Swagger UI for the schema",
    ("GET", "/docs/oauth2-redirect"): "Swagger UI helper page",
    ("GET", "/redoc"): "ReDoc for the schema",
    # the scoring code the app runs (Apache-2.0, packages/openbodyscore-js)
    ("GET", "/v1/algo/manifest"): "version and sha256 of the public scoring bundle",
    ("GET", "/v1/algo/openbodyscore-{version}.js"): "the public scoring bundle itself",
    ("GET", "/v1/assistant/tools"): "definitions of the AI tools (names, schemas); no user data",
    # OAuth discovery for the remote MCP (RFC 9728 / RFC 8414 require these to be public)
    ("GET", "/.well-known/oauth-authorization-server"): "OAuth server metadata (RFC 8414)",
    ("GET", "/.well-known/oauth-authorization-server/oauth"): "OAuth server metadata, path-suffixed form",
    ("GET", "/.well-known/oauth-protected-resource"): "protected resource metadata (RFC 9728)",
    ("GET", "/.well-known/oauth-protected-resource/mcp"): "protected resource metadata for /mcp",
    # signing in: these take a credential and hand out a login (the negative tests are in test_auth_boundary.py)
    ("GET", "/v1/auth/methods"): "which sign-in methods the server offers",
    ("POST", "/v1/auth/apple"): "Sign in with Apple: a verified Apple identity token -> login",
    ("POST", "/v1/auth/email/start"): "sends an e-mail sign-in code (rate-limited per address and IP)",
    ("POST", "/v1/auth/email/verify"): "e-mail code -> login (attempt-limited)",
    ("POST", "/v1/web/login"): "API token -> Today page cookie (per-IP failure limit)",
    ("POST", "/v1/web/login-code"): "one-time login / watch code -> device token or cookie",
    ("POST", "/v1/web/activate"): "reusable activation code -> one-time setup link (per-IP failure limit)",
    ("POST", "/v1/web/logout"): "ends the cookie session it is sent with (nothing without one)",
    ("POST", "/v1/web/boot"): "authenticates by running /v1/web/me with the caller's own credentials first (web/boot.py)",
}

# Mounted sub-applications, by path prefix -> why. Their own routes are not part of the FastAPI route table.
PUBLIC_MOUNTS: dict[str, str] = {
    "/app": "the static Today page, handbook, join / activate / OAuth consent pages (data comes from /v1 with a login)",
    "/app/media": "exercise demo images (training/media.py); no user data",
    "/oauth": "OAuth 2.1 authorize / token / register / revoke for the remote MCP: public by protocol, each checks its own grant",
}
