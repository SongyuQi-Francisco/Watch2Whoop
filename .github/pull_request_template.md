## What and why
<!-- One or two sentences; link the issue (Closes #123). -->

## Platform
<!-- Tick every part this PR changes; a maintainer adds the matching `platform:*` labels. -->
- [ ] Server (`body_engine/`, `deploy/`, `health_sync/`, AI / MCP) — `platform:server`
- [ ] Web pages (`body_engine/src/body_engine/web/`: the Today page is frozen, no new features; handbook, join, activate, OAuth) — `platform:web`
- [ ] iOS app (`apps/ios/`, with the watch app) — `platform:ios`
- [ ] Docs / CI only

## Checklist
- [ ] Tests pass locally (`cd body_engine && uv run pytest`; for the iOS app the steps in `apps/ios/README.md`)
- [ ] No secrets, tokens, real health data, database files or `.env` (synthetic / example data only)
- [ ] I license this under the licence of the directories I changed (AGPL-3.0; MIT for `apps/ios/`, `apps/watch2whoop/` and `shortcuts/`), see CONTRIBUTING.md
- [ ] Every new or changed user-facing text is bilingual (zh + en): `tr()` + `i18n-en.js` on the Today page, `L()` / `errors_en.py` on the server, `tr("中文", "English")` in the iOS app (CONTRIBUTING.md "Bilingual UI"; `test_i18n*` enforce it)
- [ ] UI changes: an English screenshot is attached (language set to English under Me → 语言 / Language), next to the Chinese one
- [ ] Docs updated if behaviour, commands or settings changed
- [ ] Something a user can see changed: one entry added to `body_engine/src/body_engine/whatsnew/entries.py` (one short sentence, zh + en; see CONTRIBUTING.md "What's new"), or nothing user-visible
- [ ] App changes: a line in the in-progress version's section of `apps/ios/CHANGELOG.md` (version bumps only in release PRs, see docs/apps.md)
- [ ] Interaction or motion change: a prototype behind a flag was measured on a device and the owner picked one in this PR; this PR lists every number on screen and its API field (CONTRIBUTING.md "Design changes") — or not applicable
- [ ] Migration, rollout flag or platform switch: the exit issue is linked (stop condition, target date, deletion checklist) — or not applicable
- [ ] Third-party terms, deprecation notices and trademarks this work depends on are checked and named in this description — or none
- [ ] I will leave a summary comment on this PR and on every issue it closes (what changed, verification, CI, deploy status; see CONTRIBUTING.md "Maintenance rules"), and a handoff comment (branch, state, next step, what was verified) if I stop with unfinished work
