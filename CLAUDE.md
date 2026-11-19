# Watch2Whoop — notes for AI coding assistants

Your body's data layer for AI: a self-hosted, multi-user scaffold that any AI reads and writes through MCP (or an API key).
The data accumulates here; the AI is swappable. Users log by talking to their AI (no input forms); the algorithm layer
(WHOOP-style recovery, strain, sleep, body age) turns raw watch data into facts the AI and the user can rely on. Start with [README.md](README.md),
then [`docs/README.md`](docs/README.md) (design documents, mostly Chinese) and [`body_engine/README.md`](body_engine/README.md).
A code name in code or docs (N0–N9, M1–M6, "2.0.1", "1.0.9") is translated in the table at the top of [`docs/ios-native-app.md`](docs/ios-native-app.md).

## Components
Status as of 2026-11-19; the release in progress is iOS 2.1.0 (phone UI redesign + the Apple Watch app).

| Path | Status | What |
|---|---|---|
| `body_engine/` | active | FastAPI + PostgreSQL: scores, insights, records, rules, training, AI tools, MCP server. `cd body_engine && uv run pytest` |
| `apps/ios/` | **active, the only client** | Native SwiftUI iPhone app (no WebView): per-account local database, own HealthKit sync engine that uploads to `/v1/ingest/healthkit`, scoring algorithms run on the phone (JavaScriptCore). It embeds the Apple Watch app (`Watch2WhoopWatch/`, shared code in `WatchShared/`). Read [`docs/ios-native-app.md`](docs/ios-native-app.md) and [`apps/ios/README.md`](apps/ios/README.md) first. CI job "iOS native" |
| `packages/openbodyscore/` | active | The scoring algorithms as a standalone Python library (Apache-2.0); Body Engine depends on it |
| `packages/openbodyscore-js/` | active | JS port of the algorithms for the iOS app (JavaScriptCore), served by `/v1/algo`. Change Python first, then port `src/`, `node build.js`, `node --test test/golden.test.js` (README) |
| `quickstart/`, `quickstart.sh` | active | One-command local demo with synthetic data |
| `deploy/` | mixed | Grafana, the AI coach workspace (`deploy/assistant/`), Open Wearables and the optional SparkyFitness (both being retired). `deploy/ops/` is Mac-era tooling (LaunchAgents, local backups) and historical: production runs on one Linux server with its own backups, no Mac fallback |
| `body_engine/src/body_engine/web/static/` | **frozen** | The web Today page (`/app/`): no new features; it will be replaced by a small landing page. The handbook, join, activate and OAuth pages stay |
| `apps/watch2whoop/` | **historical, unmaintained** | The Flutter iOS app (up to 1.0.8: Open Wearables SDK + the Today page in a WebView). Kept for reference; no new work, and Android and HarmonyOS clients are not planned. **One exception**: `ios/Runner/{SyncEngine,RawSync,Backfill}.swift` and `assets/healthkit_types.json` are still the originals of files that `apps/ios/scripts/sync_flutter_sources.sh` copies byte for byte into `apps/ios`: change the original, then run the script (CI only warns on a difference) |
| `vendor/open-wearables` | being retired | Open Wearables (git submodule, pinned). Older app versions synced through it; it still runs and `import-ow` still pulls it. Do not build on it |
| `shortcuts/`, `health_sync/` | being retired | iOS Shortcut upload path and its receiver (format spec: `docs/shortcut-sync-spec.md`). Not maintained; the app has its own sync engine |

## Product principles
- One entry point per device: the native iPhone app (and its Apple Watch app), no WebView. Open Wearables, SparkyFitness and Grafana are back ends only.
- Every table carries `user_id`; every endpoint authorizes per user; viewing someone else's data needs an explicit share.
- The UI shows numbers, charts and short labels; longer texts stay in the API for the AI coach.
- Build on existing open-source projects instead of rewriting them. Food / weight / workout logs live in Body Engine's own
  tables (#252, `body_engine/src/body_engine/ownstore/`); SparkyFitness is optional, only runs unmodified (its licence
  is non-commercial) and is being retired — never copy its code or schema.

## Conventions
- Every change is a pull request from your own branch or fork (never push to `main`). `main` needs the CI checks named in `.github/workflows/ci.yml` and an up-to-date branch; the coordinator's review comment with its verification evidence is the review; nobody bypasses. See `CONTRIBUTING.md`.
- Maintenance rules (CONTRIBUTING.md): never close an issue silently — comment what was fixed, in which PR / commit, how it was verified and what is left open; keep a written record on every PR (what was developed, each later round of commits, test / CI results, deploy status); a session that ends with unfinished work leaves a handoff comment.
- Never commit `health_data/`, `secrets/`, database files or `.env`. Secrets are files under `secrets/`, read at runtime.
- Importers must be idempotent. Dates and times are ISO 8601 with the user's local time zone; weight in kg, energy in kcal.
- **Every new feature and change is bilingual (zh + en)**, a permanent rule (CONTRIBUTING.md "Bilingual UI", [`docs/i18n-plan.md`](docs/i18n-plan.md)): in the iOS app every UI string is `tr("中文", "English")` (`apps/ios/scripts/check_sources.py` fails on Chinese outside it);
  server texts the app shows pick the language from `X-Body-Lang` (`i18n.py` `L()` / `tl()`, error details in `errors_en.py`),
  AI-facing texts stay Chinese; the frozen web page keeps its `tr("中文")` + `i18n-en.js` pairs (`tests/test_i18n.py`). Code comments and developer docs are English.
- New database migration: `body_engine/src/body_engine/migrations/NNN_name.sql`; grant `SELECT` to `grafana_ro` if Grafana reads it.
- New AI tool: a spec in the feature's own module, registered in `body_engine/src/body_engine/dl_app.py`, handler `(ctx, args)`. If it deletes or undoes: `confirm=True` on the spec and its name in the `ask` list of `assistant/.claude/settings.json` (`tests/test_butler.py` checks both).
- Health-related rules and thresholds live in `*/params.py` with their sources; keep them configurable, never hard-code one person's data.
- **Sync code never swallows an error**: every failure path records a status the server can see on the next upload. A new upload channel is registered in the health check with its expected request count.
- **One fact, one source.** When a file must exist in two places (Python and JS algorithms, persona prompts, copied Swift sources, deployment overlays), the copy is generated or checked by CI and the check fails the build. A hand-maintained copy is not allowed.
- **Every migration, rollout flag or platform switch has an exit issue**: its stop condition, a target date and the deletion checklist.
- **An endpoint that issues a token, code or session never grants more than the caller has**, and a negative-permission test covers it.
- **UI**: the screen rules (no false floor, every view has a visible door within two taps, a gesture only repeats a door, the inventory in `apps/ios/reachability.json`, dials and rings) are in CONTRIBUTING.md "Every view has a door" and enforced by `ReachabilityTests`. Interaction and motion changes start as a prototype (CONTRIBUTING.md "Design changes").
