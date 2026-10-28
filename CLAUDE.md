# Watch2Whoop — notes for AI coding assistants

Your body's data layer for AI: a self-hosted, multi-user scaffold that any AI reads and writes through MCP (or an API key).
The data accumulates here; the AI is swappable. Users log by talking to their AI (no input forms); the algorithm layer
(WHOOP-style recovery, strain, sleep, body age) turns raw watch data into facts the AI and the user can rely on. Start with [README.md](README.md),
then [`docs/README.md`](docs/README.md) (design documents, mostly Chinese) and [`body_engine/README.md`](body_engine/README.md).

## Components
| Path | What |
|---|---|
| `body_engine/` | FastAPI + PostgreSQL: scores, insights, records, rules, training, AI tools, MCP server, Today page (`web/`). `cd body_engine && uv run pytest` |
| `packages/openbodyscore/` | The scoring algorithms as a standalone Python library (Apache-2.0); Body Engine depends on it |
| `packages/openbodyscore-js/` | JS port of the algorithms for the iOS app (JavaScriptCore), served by `/v1/algo`. Change Python first, then port `src/`, `node build.js`, `node --test test/golden.test.js` (README) |
| `apps/watch2whoop/` | Flutter iOS app: HealthKit background sync to Open Wearables + the Today page in a WebView. `flutter test` |
| `quickstart/`, `quickstart.sh` | One-command local demo with synthetic data |
| `deploy/` | Production deployment of Open Wearables, Grafana, the optional SparkyFitness (no longer needed, #252), ops jobs (backups, health checks), the AI coach workspace |
| `shortcuts/` | iOS Shortcut alternative to the app (format spec: `docs/shortcut-sync-spec.md`) |
| `health_sync/` | Receiver for Shortcut uploads |
| `vendor/open-wearables` | Open Wearables (git submodule, pinned) |

## Product principles
- One entry point per device: the Today page (also inside the iOS app). Open Wearables and Grafana are back ends only.
- Every table carries `user_id`; every endpoint authorizes per user; viewing someone else's data needs an explicit share.
- The UI shows numbers, charts and short labels; longer texts stay in the API for the AI coach.
- Build on existing open-source projects instead of rewriting them. Food / weight / workout logs live in Body Engine's own
  tables (#252, `body_engine/src/body_engine/ownstore/`); SparkyFitness is optional and only runs unmodified (its licence
  is non-commercial) — never copy its code or schema.

## Conventions
- Every change is a pull request from your own branch or fork (never push to `main`); CI (`.github/workflows/ci.yml`) must be green and a maintainer reviews before a squash merge. See `CONTRIBUTING.md`.
- Maintenance rules (CONTRIBUTING.md): never close an issue silently — comment what was fixed, in which PR / commit, how it was verified and what is left open; keep a written record on every PR (what was developed, each later round of commits, test / CI results, deploy status).
- Never commit `health_data/`, `secrets/`, database files or `.env`. Secrets are files under `secrets/`, read at runtime.
- Importers must be idempotent. Dates and times are ISO 8601 with the user's local time zone; weight in kg, energy in kcal.
- **Every new feature and change is bilingual (zh + en)**, a permanent rule (CONTRIBUTING.md "Bilingual UI", [`docs/i18n-plan.md`](docs/i18n-plan.md)): in the Today page scripts every UI string goes
  through `tr("中文")` with its English in `web/static/i18n-en.js` (`tests/test_i18n.py` fails on any Chinese literal outside `tr()`);
  server texts the page shows pick the language from `X-Body-Lang` (`i18n.py` `L()` / `tl()`, error details in `errors_en.py`),
  AI-facing texts stay Chinese; the iOS app uses `tr('中文', 'English')`. Code comments and developer docs are English.
- New database migration: `body_engine/src/body_engine/migrations/NNN_name.sql`; grant `SELECT` to `grafana_ro` if Grafana reads it.
- Health-related rules and thresholds live in `*/params.py` with their sources; keep them configurable, never hard-code one person's data.
