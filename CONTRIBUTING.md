# Contributing to Watch2Whoop

Thanks for helping! Good first areas: a new data provider (other watches), translations / English UI, validating the
algorithms against published research, UI polish in the iOS app.

**Not accepted for new work** (decided 2026-11-18; development is iOS native only): the Flutter app (`apps/watch2whoop/`,
historical and unmaintained; the only exception is the sync files that `apps/ios/scripts/sync_flutter_sources.sh` copies into
`apps/ios/`, whose originals are still there), an Android client or any other new client platform, and new features on the web Today page
(`body_engine/src/body_engine/web/static/`, frozen, to be replaced by a small landing page; the handbook, join, activate and
OAuth pages stay).

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
- **Today page** (`body_engine/src/body_engine/web/static/`, frozen: only fixes, no new features): every UI string goes through `tr("中文", { … })`, and its
  English goes into `i18n-en.js`. In HTML, use `data-i18n` / `data-i18n-ph` / `data-i18n-aria` / `data-i18n-title`.
  For dates and numbers use the `I18N.*` helpers.
- **Server texts the page shows** (labels, reminders, explain sheets, error details): use `L("中文", "English")` /
  `tl()` from `body_engine/i18n.py`, picked by the request's `X-Body-Lang`. New `HTTPException` details also get
  their English in `errors_en.py`. AI-facing texts (tool results, prompts) stay Chinese.
  **Data the page shows as it is** is the part the scanners cannot see (a name the server sends, a stored value): translate
  it where it is built (`L()` in the builder) or show it through `tr(value)` when it is a fixed vocabulary (catalog labels,
  built-in plan names; the page keeps the stored Chinese value for the AI). Check the new screen in English before you open
  the PR, and say so in the PR.
- **iOS app** (`apps/ios/`): write `tr("中文", "English")` (`Watch2Whoop/Localization/I18n.swift`).
- **Enforced in CI**:
  - `body_engine/tests/test_i18n.py` fails on any Chinese literal outside `tr()`, on a key without English, on unmarked
    Chinese in the HTML, and on a page script or page that is not in its lists (a new `.js` file must be added there);
  - `test_i18n_server.py` fails on an error detail without English, and on a module that raises a Chinese message and is
    in neither `REACHABLE` (its messages reach the page: translate them) nor `AI_OR_ADMIN_ONLY` (the AI or the CLI read them);
  - `test_i18n_remnants.py` checks the data tables the page shows (exercise cues, catalog vocabulary, plan templates);
  - `apps/ios/scripts/check_sources.py` (CI job "iOS native") fails on a Chinese literal outside `tr()` and on Chinese in
    the English half of a `tr()`;
  - `apps/watch2whoop/test/i18n_test.dart` does the same for the historical Flutter app.
- Details: [docs/i18n-plan.md](docs/i18n-plan.md).

每个新功能、每次改动都要同时有中文和英文：今天页的文字走 `tr("中文")` 并在 `i18n-en.js` 加英文；服务器给页面的文字用
`L()` / `tl()`（按 `X-Body-Lang`），报错的英文加进 `errors_en.py`；iOS app 用 `tr("中文", "English")`。CI 里的 i18n 测试会拦住
缺英文或漏掉 `tr()` 的改动。

## Every view has a door / 每个页面都有入口
Rules of the iPhone app's screens (2.1.0, `docs/design/2.0.1/drilldown/README.md` section 9):
1. **No false floor.** A tab's first screen either has nothing below it, or its next block is cut by the fold with at least
   `LevelParams.peek` (48 pt) of it showing above the Jarvis key. No blank spacer taller than `LevelParams.maxBlankGap`
   (56 pt) inside a tab root's scroll content.
2. **Every view has a visible door**, at most two taps from a tab's first screen. A gesture (long press, swipe, double tap,
   context menu) may repeat a visible door, never be the only one.
3. **A number opens its own depth, in its own tab.** Only a face that names another tab's subject (饮食, 圈子) switches tabs.
4. **The inventory is part of the change.** A PR that adds, moves or removes a screen updates `apps/ios/reachability.json`.
5. **Dials and rings**: the digits sit on the axis and the unit hangs outside without shifting them (not checked by a test).

`Watch2WhoopTests/ReachabilityTests.swift` enforces them: every `View` is built somewhere, every destination case is opened
outside `#if DEBUG` and has an inventory entry with a tap door within two taps, no tab root has a spacer above the limit, and
the first screens of 今天 and 身体 fit above the key.

首屏下面要么什么都没有，要么下一块被屏幕底边切开、在 Jarvis 键上方露出至少 48 pt，不放超过 56 pt 的空白；每个页面离某个 tab 的首屏
最多点两下、要有看得见的入口（手势只能是重复的入口）；数字点进去是它自己的详情，不跨 tab；加 / 挪 / 删页面要同时改
`apps/ios/reachability.json`。`ReachabilityTests` 会检查。

## What's new for users / 面向用户的更新
**A pull request that changes something a user can see adds one entry** to `body_engine/src/body_engine/whatsnew/entries.py`:
the day it ships, one short spoken sentence in Chinese and in English, and optionally a link into the Today page. Every user gets
the new entries once a week (Sunday evening, their own time zone, notification category `updates`, they can switch it off) and
can read all of them under 我 → 帮助与关于 → 更新记录. No PR or issue numbers, flag names or technical words (`tests/test_whats_new.py`
checks length and wording). A change only some people can see yet (a feature switch not on for everyone) gets its entry when it
reaches everyone. Pure refactors, tests, docs and CI need none.

每个会让用户看到变化的 PR 都在 `whatsnew/entries.py` 加一条：上线日期、中文一句、英文一句，可选一个跳转链接。用户每周日晚（按自己的时区）收到
上周以来的新条目，也可以在「我 → 帮助与关于 → 更新记录」里看全部。条目里不写 PR 号、开关名和技术词（测试会检查）；只开给部分人的功能等对所有人开放时再写。
纯重构、测试、文档、CI 不用写。

## Workflow
Nobody pushes to `main`, maintainers included; every change is a pull request.

1. **Issue** — open one (or comment on an existing one) before larger changes, so we agree on the approach. Issues and
   PRs carry a `platform:*` label (`ios`, `server`, `web`; `android` and `harmony` are not in use). A migration, rollout
   flag or platform switch also gets an exit issue (stop condition, target date, deletion checklist, see
   [CLAUDE.md](CLAUDE.md) Conventions). If the work depends on third-party terms, a deprecation notice or a trademark
   (an API's terms of service, a store policy, a name or logo), say so in the issue.
2. **Branch** — fork the repository, or (maintainers) create a feature branch; one topic per branch.
3. **Pull request** — against `main`; fill in the [checklist](.github/pull_request_template.md).
4. **CI green** — [`ci.yml`](.github/workflows/ci.yml) runs the Body Engine tests, syntax checks, the iOS app's build and
   tests, the algorithm JS bundle and the other jobs it lists (each only when its paths changed; skipped ones count as
   passed). The required checks are named in that file's comments; all must be green, on a branch that is up to date with
   `main`. A macOS job that never got a runner is re-run automatically (at most twice, `ci-rerun.yml`); a job that ran and
   failed is a real failure.
5. **Review** — `main` has no approving-review count and administrators are not exempt, so nobody bypasses it. This is a
   one-maintainer project: the maintainer's (coordinator's) review comment on the PR, with its verification evidence (what was run, the
   results, screenshots or CI links), is the review. Merging without it, with a failing or pending required check, or by
   overriding the rules is not done.
6. **Squash merge** — by a maintainer. Never write "merged" by hand: the merge record (merge commit, CI verdict,
   migration numbers) is posted on the PR by `merge-record.yml`.

### Design changes and direction changes
- **Prototype first.** A change to interaction or motion starts as a prototype behind a flag and is measured on a
  device. The owner picks one prototype in the PR; the production implementation starts only after that.
- **A design PR lists every number on screen and the API field it comes from.**
- **Freeze issue.** When the owner decides to change platform or direction, the session that records the decision opens
  a freeze issue listing the paths, flags and open PRs that stop. Nothing on the list gets new work until the issue says
  otherwise.

### Handoff
A person or session that stops with unfinished work leaves a handoff comment on the PR or issue: the branch, its state,
the next step and what was verified.

## Licensing of contributions (inbound = outbound)
There is no CLA. By submitting a contribution you agree that it is licensed under the licence of the directory it changes:

| Directory | Licence |
|---|---|
| `apps/watch2whoop/` (historical Flutter iOS app) | MIT — [`apps/watch2whoop/LICENSE`](apps/watch2whoop/LICENSE) |
| `apps/ios/` (native iOS app, SwiftUI) | MIT — [`apps/ios/LICENSE`](apps/ios/LICENSE) |
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
  iOS app: XcodeGen + `xcodebuild test` on a simulator and `python3 apps/ios/scripts/check_sources.py`, see
  [apps/ios/README.md](apps/ios/README.md).
- iOS app versioning and release tags (`app-ios-v*`): [docs/apps.md](docs/apps.md) section 4 (the rest of that file
  predates the iOS-only decision). App changes add a line to [`apps/ios/CHANGELOG.md`](apps/ios/CHANGELOG.md).
- New AI tools always go through the registration interface: a spec in the feature's own module, registered in
  `body_engine/src/body_engine/dl_app.py`, handler `(ctx, args)`, `confirm=True` if it deletes or undoes
  ([docs/datalayer-design.md](docs/datalayer-design.md) section 3.2). 新的 AI 工具一律通过注册接口（`dl_app.py`）接入。
- Conventions (multi-user, idempotent importers, English comments, UI text through `tr()` in both languages, and the rules
  every change follows: sync errors are never swallowed, one fact one source, exit issues, token-issuing endpoints, the UI
  entry rule): see [CLAUDE.md](CLAUDE.md) and [`body_engine/README.md`](body_engine/README.md).
- Never commit `secrets/`, `health_data/`, database files or `.env`.

## Trademark
Watch2Whoop is not affiliated with WHOOP, Inc.; WHOOP is a trademark of WHOOP, Inc. Do not use WHOOP logos or imply
endorsement in contributions.
