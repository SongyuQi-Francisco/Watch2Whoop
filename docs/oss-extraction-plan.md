# Open-source extraction plan

Status: step 1 in progress (issue #115). Owner decisions are dated; open questions are at the end.

Watch2Whoop is a complete product, which makes it hard for others to reuse one part of it. On 2026-10-22 the maintainer
decided to extract the reusable infrastructure and publish it piece by piece. Each piece is first split into its own
package **inside this repository**; only when its interface is stable does it move to a repository of its own.

## 1. Pieces, order and licences

| # | Piece | Licence | Where it lives now | Next |
|---|---|---|---|---|
| 1 | **Scoring library** `openbodyscore` (working name; the main focus): sleep, recovery, strain, training load, muscle load, stress, body age, HRV from beat times | **Apache-2.0** | `packages/openbodyscore/` (this step) | methodology and validation (#118), RMSSD (#117), stabilise the API, then its own repository and PyPI |
| 2 | **MCP "personal data layer" framework** (#116) | to be decided with the piece | `body_engine/` (MCP server, tools) | built and run in this project first, then extracted |
| 3 | English design documents and extension-point notes in this repository | AGPL-3.0 (docs of this repository) | `docs/`, component READMEs | ongoing |

Not extracted (maintainer decision, 2026-10-22): the HealthKit sync spec and the iOS Shortcut (`docs/shortcut-sync-spec.md`,
`shortcuts/`, already MIT) — too little value as a separate project.

**Naming and wording.** The package / repository name is a working name until the maintainer picks the final one
(renaming only touches the directory and configuration). Neither may contain the WHOOP trademark. Public wording may
say "open-source, evidence-based WHOOP-style scores" and must state that the project is not affiliated with WHOOP.

The server (`body_engine/`, `deploy/`, …) stays AGPL-3.0. Relicensing the extracted code is possible because every
commit in the history of the moved files is the maintainer's own (checked with `git log` on the moved paths when
the package was created); this must be re-checked before each release (checklist below), since contributions land
under the licence of the directory they change (CONTRIBUTING.md).

## 2. The scoring package (step 1)

### What moved
All pure functions, **no algorithm or number changed**. The old modules stay importable, so no caller and no open
pull request has to change a line.

| Old path (still works) | Package module | How the old path is kept |
|---|---|---|
| `body_engine.scores.params` | `openbodyscore.params` | alias: the old module *is* the package module (`sys.modules`), so `monkeypatch.setattr(P, …)` and private names keep working |
| `body_engine.scores.recovery` / `sleep` / `strain` / `muscle` | `openbodyscore.recovery` / `sleep` / `strain` / `muscle` | alias |
| `body_engine.rawdata.hrv` | `openbodyscore.hrv` | alias |
| `body_engine.insights.healthspan_params` | `openbodyscore.healthspan_params` | re-export (`import *`); `TIPS` (product texts in Chinese, including one person's weight target) stays in Body Engine |
| `body_engine.insights.healthspan` | `openbodyscore.healthspan` | re-export plus the private helpers; `tip()` stays (it picks from `TIPS`) |
| `body_engine.insights.stress` | `openbodyscore.stress` (constants, `band`, `robust_baseline`, `calm_samples`, `score_day`) | the database part (`compute`, `write`, `read`, the Open Wearables readers) and `DEFAULT_DAYS` stay and call the package |

Not moved, because they read the database or the network: `scores/engine.py`, `scores/inputs.py`, `scores/store.py`,
`insights/healthspan_store.py`, the rest of `rawdata/`. They import through the old paths, unchanged.

### Parameters
Every function that reads a parameter takes a keyword-only `params=` argument whose default is the parameter module,
so calls without it behave exactly as before. `params.override(NAME=value, …)` (also `healthspan_params.override`,
`stress.override`, `hrv.override`) returns a copy with some values replaced and rejects unknown names; the module
itself is never modified, so overrides are safe in a multi-threaded server. Arguments that had a parameter as their
default (`window`, `hold`, `cap`, `k`, `weeks`) now default to `None` and read the parameter at call time. The
source of every parameter is in the docstring / comment next to it; `packages/openbodyscore/METHODOLOGY.md` labels each one literature / ported spec / design choice / personal
calibration, with formulas, known limits and the validation status (#118).

### Proof that no number changed
1. `packages/openbodyscore/tests/golden_cases.py` builds 60 days of synthetic data for a made-up person (seeded, the
   same shape as `quickstart/demo_data.py`) and runs every public function on it, including edge cases and error
   messages. `tests/golden/scores.json` was written by running it against the **old** `body_engine` modules in a
   separate commit before anything moved. `tests/test_golden.py` (package) and
   `body_engine/tests/test_openbodyscore_golden.py` (old import paths) compare keys, ints and strings exactly and floats
   to a relative 1e-12 (`golden_cases.mismatch`: libm's exp / log differ in the last bit between macOS and Linux).
2. End to end: `quickstart/demo_data.py`'s export (seed 7, 60 days, fixed date) → the real Apple export importer →
   scoring engine, body age and stress → `body_engine/tests/fixtures/golden_e2e_sdnn.json` (one file per HRV kind; the RMSSD one is added with #117), also
   written before the move.
3. `test_old_import_paths_are_the_package` checks that the old paths give the package's own objects.

### Build and CI
- **Dependency**: `body_engine/pyproject.toml` depends on `openbodyscore` through a uv path source
  (`../packages/openbodyscore`, editable). Chosen over a uv workspace because it adds one line to `pyproject.toml`
  and a dozen to `uv.lock` and leaves `body_engine/` as the project root that CI, Docker and every deploy script use.
- **Docker**: the build context stays `body_engine/` (it must never include `health_data/` or `secrets/` from the
  repository root). The package comes in as the named build context `openbodyscore`: `compose.yaml` and
  `quickstart/compose.yaml` set it with `additional_contexts` (Docker Compose ≥ 2.17); by hand:
  `docker build --build-context openbodyscore=packages/openbodyscore body_engine`. Without it the Dockerfile's empty
  `FROM scratch AS openbodyscore` stage is used (never a registry image of that name) and a check fails the build with
  a message saying what to add. `packages/openbodyscore/.dockerignore` keeps tests and virtual environments out.
- **CI**: the `Body Engine tests` job runs `uv run --locked pytest -q` in `packages/openbodyscore` before the Body
  Engine tests; the path filter already counts everything outside `apps/` (so `packages/`) as server.
- The package has its own `uv.lock` (only pytest) so `cd packages/openbodyscore && uv run pytest` is reproducible.

### Apple Health export parsing (evaluated, not moved)
`importers/apple_export.py` and `importers/apple_zip.py` could become an optional `openbodyscore.io` module (no extra
dependencies: `zipfile`, `xml.etree.iterparse`, `csv`). What has to be separated first:
- **Pure and movable**: `apple_zip.find_xml_member`, `iter_elements` (streaming, clears elements), `parse` (with its
  callbacks), `parse_workout`, `daily_rows`, `first_bytes_ok`, `_Counting`; `apple_export.read_daily` /
  `read_segments`, `STAGE_MAP`; from `importers/common.py` `Segment`, `normalize`, `snake`, `sessionize`; from
  `rawdata/export.py` the beat-series / ECG record parsers (they use `hrv` and `rawdata/ecg.py`, both pure).
- **Stays in Body Engine**: everything that writes (`run`, `save_samples`, `upsert_daily`, `replace_sleep`,
  `wk.upsert`, `refresh_duplicates`, `rx_store`) and `queries.local_today`.
- **Coupling to cut**: `parse_workout` uses training helpers (`training.importer._base_row`, `type_name` — a Chinese
  display name — and `shortcut_workouts.shortcut_type`); the output rows carry Body Engine column names and source
  ranks. The `io` module should return neutral records (samples, sleep segments, workouts with the HealthKit activity
  type) and Body Engine maps them to rows.
- Golden test the same way (a synthetic export in, records out) before moving. A later PR of #115.

## 3. Package and repository plan
1. **In this repository** (now): `packages/openbodyscore/`, version `0.1.0`, classifier `Private :: Do Not Upload`
   (PyPI refuses it) until the release checklist is done. Body Engine is the only user.
2. **API stabilisation**: settle names and the `params=` / `override` design, typed inputs (e.g. dataclasses for a
   night / a day) where the tuple lists are awkward, English error messages; any change of a number bumps that
   module's algorithm version and regenerates the golden file in the same PR.
3. **Own repository** (`openbodyscore`, Apache-2.0), with history of the package directory (`git filter-repo
   --path packages/openbodyscore`), its own CI and releases; this repository then depends on a pinned version from
   PyPI (or a git tag) instead of the path source, and the old import paths keep working.
   Order: #117 (HRV per user, SDNN or RMSSD) comes after this step is merged. Its preparation added an `rmssd` golden file
   next to the unchanged `sdnn` one (`body_engine/tests/test_openbodyscore_golden.py`, `HRV_KINDS`); the per-user choice
   keeps `ALGO_VERSION` (the stored `hrv_kind` tells the two apart).
4. **PyPI**: tagged releases, semantic versions; a score change is at least a minor version and is listed in a CHANGELOG.

## 4. Release checklist (before the first public release)
- [ ] **Name**: maintainer confirms the final name; check PyPI, GitHub and a quick trademark search. Must not contain
      WHOOP or other brand names.
- [ ] **Trademark**: no "WHOOP" in package code, metadata or examples (done for the docstrings that said "matching
      WHOOP …"); the README keeps the not-affiliated statement; "WHOOP-style" wording stays only in this repository.
- [ ] **Licence**: `LICENSE` (Apache-2.0) and `NOTICE` present in the sdist and wheel; decide whether every file gets
      an SPDX header (`# SPDX-License-Identifier: Apache-2.0`); copyright line confirmed by the maintainer.
- [ ] **Relicensing**: re-check that all commits touching the moved files are the maintainer's (or get the other
      authors' agreement) — `git log --format='%an' -- packages/openbodyscore <old paths>`.
- [ ] **Third-party sources**: the sleep score is a port of Open Wearables 0.9.0 (MIT) — its notice is in `NOTICE`;
      the body-age tables are numbers from published papers (cited in `healthspan_params.py`; facts, not code);
      no SparkyFitness code or data; the exercise library (free-exercise-db, Unlicense) is not part of the package.
- [ ] **Language**: package code in English — two `ValueError` messages and the `热身` (warm-up) set type in
      `muscle.WARMUP` are still Chinese (kept so this step changes nothing; the error messages are part of the
      golden file).
- [ ] **One person's data**: no hard-coded personal values; the owner-calibrated constants (strain K, the first-version
      recovery weights) are documented as such.
- [ ] **Methodology and validation (#118) complete**: `METHODOLOGY.md` filled in (inputs, formulas, every parameter with its
      category and a verified source, known limits), the validation studies of #118 done, and the README states only
      validated claims ("v1 value, not yet validated" otherwise). Includes replacing the one-person calibration (strain K).
      Status 2026-10-24: `METHODOLOGY.md` filled in and the synthetic-data studies done; still open are the real-data
      studies (pre-registered in `docs/scoring-validation.md` §5) and replacing K, both waiting for the maintainer's
      decisions (§6 there: research consent, WHOOP comparison, calibration approach).
- [ ] **Docs**: README with install, example; a CHANGELOG; supported Python versions tested in CI.
- [ ] Remove `Private :: Do Not Upload`.

## 5. Merge coordination (step 1)
Open pull requests at the time (#105–#110, #97, #91, #96, `feat/friends-leaderboard`) do not touch the moved files.
Possible conflicts and how to resolve them:
- `body_engine/pyproject.toml` / `body_engine/uv.lock`: keep both sides' dependency lines, then run `uv lock` (uv
  0.12.19, as in CI) — never edit the lock by hand.
- `.github/workflows/ci.yml`: this step only adds one step to the `Body Engine tests` job and a comment line in the
  path filter; keep both sides.
- `compose.yaml` / `quickstart/compose.yaml` / `body_engine/Dockerfile`: only the `build:` block of `body-engine`
  and the top of the Dockerfile changed.
- A branch that edits one of the moved algorithm files after this step conflicts on the old path (now the thin
  re-export): keep the re-export and apply the change to the same function under `packages/openbodyscore/src/openbodyscore/`.
  Git does not follow these renames, because the old paths still exist.

## 6. Open questions for the maintainer
- Final package name and copyright line.
- `uv` workspace instead of the path source, once more packages exist.
- Whether the improvement tips (`TIPS`) should get a neutral English default in the package, with the product texts
  staying in Body Engine.
