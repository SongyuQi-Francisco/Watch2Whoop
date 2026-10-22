# Contributing to Watch2Whoop

Thanks for helping! Good first areas: a new data provider (other watches), translations / English UI, validating the
algorithms against published research, an Android client, UI polish.

## Maintenance rules / 维护规则
- **Issues are never closed silently.** Before closing one (including auto-close by `Fixes #N` on merge), comment what
  was fixed, in which PR / commit, how it was verified, and anything left open.
  关闭 issue（包括合并时 `Fixes #N` 自动关闭）前，先留言：修了什么、哪个 PR / commit、怎么验证的、还剩什么没做。
- **Every PR keeps a written record in comments**: what was developed, what each later round of commits changed (rebase,
  review fixes, version bumps), test / CI results, and the deploy status (merged / deployed / needs a new app build).
  每个 PR 都在评论里留记录：开发了什么、之后每一轮提交改了什么（rebase、review 修改、版本号）、测试 / CI 结果、部署状态
  （已合并 / 已部署 / 需要新的 app 构建）。
- **No real personal health data in the repository.** Docs, comments, tests and examples never carry a real person's
  values (weight, lab results, HRV, sleep times, injuries, supplements…), the maintainers' included: describe results in
  words, use obviously fictional numbers in tests and examples (say 「示例」 / "example"). CI runs
  `tools/export_public.py --scan` with generic rules (lab values in prose, numbers attributed to a real person or to a
  real-data block); maintainers' pre-push hooks add private deny lists kept under `secrets/`.
  仓库里不放任何真人的健康数据（包括维护者本人）：文档用文字描述结果，测试和示例用明显虚构的数值并标「示例」。

## Bilingual UI / 双语界面
**Every new feature and every change ships in both Chinese and English.** A PR that adds or changes user-facing text
without both languages is not merged.
- **Today page** (`body_engine/src/body_engine/web/static/`): every UI string goes through `tr("中文", { … })`, and its
  English goes into `i18n-en.js`. In HTML, use `data-i18n` / `data-i18n-ph` / `data-i18n-aria` / `data-i18n-title`.
  For dates and numbers use the `I18N.*` helpers.
- **Server texts the page shows** (labels, reminders, explain sheets, error details): use `L("中文", "English")` /
  `tl()` from `body_engine/i18n.py`, picked by the request's `X-Body-Lang`. New `HTTPException` details also get
  their English in `errors_en.py`. AI-facing texts (tool results, prompts) stay Chinese.
- **iOS app**: write `tr('中文', 'English')` (`lib/src/i18n.dart`).
- **Enforced in CI**:
  - `body_engine/tests/test_i18n.py` fails on any Chinese literal outside `tr()`, on a key without English, and on
    unmarked Chinese in the HTML;
  - `test_i18n_server.py` fails on an error detail without English;
  - `apps/watch2whoop/test/i18n_test.dart` fails on a Chinese literal outside `tr()`.
- Details: [docs/i18n-plan.md](docs/i18n-plan.md).

每个新功能、每次改动都要同时有中文和英文：今天页的文字走 `tr("中文")` 并在 `i18n-en.js` 加英文；服务器给页面的文字用
`L()` / `tl()`（按 `X-Body-Lang`），报错的英文加进 `errors_en.py`；app 用 `tr('中文', 'English')`。CI 里的 i18n 测试会拦住
缺英文或漏掉 `tr()` 的改动。

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
| `packages/openbodyscore/` (scoring library) | Apache-2.0 — [`packages/openbodyscore/LICENSE`](packages/openbodyscore/LICENSE) |
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
- Conventions (multi-user, idempotent importers, English comments, UI text through `tr()` in both languages): see [CLAUDE.md](CLAUDE.md)
  and [`body_engine/README.md`](body_engine/README.md).
- Never commit `secrets/`, `health_data/`, database files or `.env`.

## Trademark
Watch2Whoop is not affiliated with WHOOP, Inc.; WHOOP is a trademark of WHOOP, Inc. Do not use WHOOP logos or imply
endorsement in contributions.
