## What and why
<!-- One or two sentences; link the issue (Closes #123). -->

## Platform
<!-- Tick every part this PR changes; a maintainer adds the matching `platform:*` labels. -->
- [ ] Server (`body_engine/`, `deploy/`, `health_sync/`, AI / MCP) — `platform:server`
- [ ] Today page (`body_engine/src/body_engine/web/`) — `platform:web`
- [ ] iOS app — `platform:ios`
- [ ] Android app — `platform:android`
- [ ] HarmonyOS app — `platform:harmony`
- [ ] Docs / CI only

## Checklist
- [ ] Tests pass locally (`cd body_engine && uv run pytest`, `cd apps/watch2whoop && flutter test` if the app changed)
- [ ] No secrets, tokens, real health data, database files or `.env` (synthetic / example data only)
- [ ] I license this under the licence of the directories I changed (AGPL-3.0; MIT for `apps/watch2whoop/` and `shortcuts/`), see CONTRIBUTING.md
- [ ] Every new or changed user-facing text is bilingual (zh + en): `tr()` + `i18n-en.js` on the Today page, `L()` / `errors_en.py` on the server, `tr('中文', 'English')` in the app (CONTRIBUTING.md "Bilingual UI"; `test_i18n*` enforce it)
- [ ] Docs updated if behaviour, commands or settings changed
- [ ] App changes: `CHANGELOG.md` of the app has a line under "Unreleased" (version bumps only in release PRs, see docs/apps.md)
- [ ] I will leave a summary comment on this PR and on every issue it closes (what changed, verification, CI, deploy status; see CONTRIBUTING.md "Maintenance rules")
