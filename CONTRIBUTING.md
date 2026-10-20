# Contributing to Watch2Whoop

Thanks for helping! Good first areas: a new data provider (other watches), translations / English UI, validating the
algorithms against published research, an Android client, UI polish.

## Workflow
Nobody pushes to `main`, maintainers included; every change is a pull request.

1. **Issue** — open one (or comment on an existing one) before larger changes, so we agree on the approach. Issues and
   PRs carry a `platform:*` label (`ios`, `android`, `harmony`, `server`, `web`).
2. **Branch** — fork the repository, or (maintainers) create a feature branch; one topic per branch.
3. **Pull request** — against `main`; fill in the [checklist](.github/pull_request_template.md).
4. **CI green** — [`ci.yml`](.github/workflows/ci.yml) runs Body Engine tests, syntax checks and Flutter tests (each only
   when its paths changed; skipped ones count as passed); all must pass.
5. **Review** — at least one maintainer approves.
6. **Squash merge** — by a maintainer.

## Licensing of contributions (inbound = outbound)
There is no CLA. By submitting a contribution you agree that it is licensed under the licence of the directory it changes:

| Directory | Licence |
|---|---|
| `apps/watch2whoop/` (iOS app) | MIT — [`apps/watch2whoop/LICENSE`](apps/watch2whoop/LICENSE) |
| `shortcuts/` (iOS Shortcut) | MIT — [`shortcuts/LICENSE`](shortcuts/LICENSE) |
| everything else (server, deployment, tools, docs) | GNU AGPL-3.0 — [`LICENSE`](LICENSE) |

Only submit work you have the right to license this way. Do not copy code from SparkyFitness (non-commercial licence) or
any other project whose licence is incompatible with the target directory. Third-party services (Open Wearables,
SparkyFitness, Grafana) are used as separate, unmodified services; changes to Open Wearables go upstream. Details:
[docs/LICENSES.md](docs/LICENSES.md).

## Development
- Try everything locally with `./quickstart.sh` (synthetic data only — never commit real health data).
- Body Engine: `cd body_engine && uv run pytest` (database tests use their own `body_test_*` databases and are skipped
  without Postgres; point them at a server with `BODY_DB_HOST` / `BODY_DB_PORT` / `BODY_DB_USER` / `BODY_DB_PASSWORD_FILE`).
  iOS app: `cd apps/watch2whoop && flutter test`.
- Client apps (iOS, Android, HarmonyOS): platform plan, the contract every client implements, directory layout and
  app versioning / release tags in [docs/apps.md](docs/apps.md). App changes add a line to the app's `CHANGELOG.md`.
- Conventions (multi-user, idempotent importers, English comments, Chinese-first UI text for now): see [CLAUDE.md](CLAUDE.md)
  and [`body_engine/README.md`](body_engine/README.md).
- Never commit `secrets/`, `health_data/`, database files or `.env`.

## Trademark
Watch2Whoop is not affiliated with WHOOP, Inc.; WHOOP is a trademark of WHOOP, Inc. Do not use WHOOP logos or imply
endorsement in contributions.
