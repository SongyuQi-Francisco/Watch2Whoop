# English version (issues #13, #42): plan and checklist

The product is Chinese-first. Stage 1 (#20) added the i18n layer and the profile language; stage 2 (#42, same PR) puts the
whole Today page, the server texts it shows and the app's native screens through it.

## How it works

- **Language setting**: `users.language` (`zh` / `en`, `NULL` = follow the device; migration `250_user_language.sql`).
  Set in the Today page under Me → Profile; returned by `GET/PATCH /v1/users/{user}/profile` and `GET /v1/web/me`.
- **Today page**: `web/static/i18n.js`, loaded before every other script (no build step).
  - `I18N.t(key, params)` with `{name}` placeholders; page scripts alias it as `tr` (app.js has many locals named `t`).
  - **New text: the Chinese source is the key** (gettext style): `tr("恢复")`, `tr("最近 {n} 天", { n })`. The English is one
    `"中文": "English",` line in `web/static/i18n-en.js`. The code stays readable for the Chinese-first maintainers, and a change
    made in parallel only has to wrap its new strings in `tr()` and add their English.
  - The stage-1 shell keeps its dotted keys (`tr("tab.today")`, tables `ZH` / `EN` in `i18n.js`).
  - Chinese that is data, not UI (a regex on a server reason, a value stored in Chinese, the language names 中文 / English):
    mark the line `// i18n-ok`; show stored values with `tr(value)` when they are a fixed vocabulary.
  - Static HTML: `data-i18n` (text), `data-i18n-aria`, `data-i18n-ph` (placeholder), `<html data-i18n-title>`.
  - Dates: `I18N.dayLabel / monthDay / yearMonth / monthShort / weekdaysMon / locale()`; never hard-code `"zh-CN"` in new code.
  - Language changes reload the page once (strings computed at startup, such as tab names, are only right after a reload).
  - Switches: Me page 「语言 / Language」 (saved to the profile); login page 中文 / English (before login: from
    `navigator.language`, remembered in localStorage by `I18N.choose`, written to the profile after login by `boot()`).
  - Every API call sends `X-Body-Lang` (`I18N.headers()`).
- **Server**: `body_engine/i18n.py` — `LangMiddleware` reads `X-Body-Lang` into a context variable (default zh, so MCP / AI
  clients, scripts and older pages see no change); `L("中文", "English")`, `tl(label, TABLE)`, `is_en()`. AI tool runs
  (`assistant.tools.run_tool`) always run in zh, also inside the in-page chat. HTTP error details are translated once in the
  exception handler (`errors_en.py`). Reminders store `title_en` / `message_en` next to the Chinese.
  - Key prefixes by area: `tab. hdr. common. login. err. banner. picker. who. me. jarvis. profile. activate.`;
    add a prefix to the list in `body_engine/tests/test_i18n.py` when you start a new area.
- **Tests**: `body_engine/tests/test_i18n.py` (every key in zh and en with the same placeholders, no Chinese in the English
  tables, every key used by the scripts / HTML exists, **no Chinese literal outside `tr()` in any page script** (scanner in
  `tests/i18n_scan.py`), every Chinese text in the HTML marked `data-i18n*`, i18n files loaded first and cached by `sw.js`);
  `apps/watch2whoop/test/i18n_test.dart` (every Chinese literal in the app is the zh half of `tr('中文', 'English')`).
- **AI**: the personas (`ai/system_prompt.md`, `ai/system_prompt_lite.md`, `assistant/CLAUDE.md`) reply in the user's
  language: `get_briefing` → `me.language`, and the in-page chat system prompt lists 语言 under 用户. Tool results stay
  Chinese / structured; the model translates.
- **iOS app**: `tr('中文', 'English')` (`lib/src/i18n.dart`): the system language by default; the Today page posts
  `lang:zh|en|` on the `Watch2Whoop` JS channel after login and on the login switch, and the native screens follow it.

## Stage 1 (done)

- [x] i18n layer, profile `language` field + migration, `/v1/web/me` returns it, switch in Me → Profile
- [x] Shell: tab bar, header (date, yesterday, day navigation, date picker), login screen, offline banner, API error
      fallbacks, Me page (rows, status / notes headings, version line), Jarvis sheet, profile sheet, activation page
- [x] AI personas: reply in the user's language
- [x] iOS app: all onboarding / sync / home strings already bilingual; coverage test added

## Stage 2 (#42, done in the same PR)

- [x] Every Today page script through `tr()` (app.js, ai.js, account.js incl. the step-by-step sign-in, grocery.js,
      history.js, profile.js, sw.js offline message), index.html marked; enforced by `test_no_chinese_outside_tr`
- [x] Me page language switch, login page language switch (remembered, saved to the profile after login)
- [x] Server texts the page shows: explain glossary + personal advice, reminders (`title_en` / `message_en`), status card
      labels, trends / today / training labels, error details (`errors_en.py`)
- [x] iOS app native screens follow the profile language

## Stage 3: still Chinese

- [x] Consent form in English (#64): `profile/consent.en.md` = `docs/family-consent.en.md`, clause by clause the Chinese
      text with the **same version number** (tests check both pairs, the version and the clause count); `GET /v1/consent`
      answers in the request's language; `user_consents.language` (migration 380) records which text was read.
      Deploy: the ops overlay replaces the example domains in the English files too.
- [x] What the literal scanners could not see (#64): data the page shows as it is. The exercise library's one-line cues and
      step lists have English (`training/catalog_en.py`, used by `catalog.format_exercise` in an English request); the
      catalog's group / pattern / joint-stress labels and the built-in plan templates' names, summaries and session names
      are `i18n-en.js` entries that the page shows through `tr()` (`sname()`, `exTerms()`), because the stored plan keeps the
      Chinese names the AI and the training log use (and the page posts session names back, so they must not be rewritten);
      the training readiness text, prescription text (`3 sets × 30 s`), plan error messages and the sign-in mail subject follow
      the request's language (`L()`); the back end's name no longer comes out as 「底层数据库」 in an English error
      (`ai/naming.py`); `avatars.js`, `remarks.js`, `md.js`, `muscle-body-data.js` are now in the scanner's list, and the tests
      fail on a page script or a module with Chinese raise messages nobody has classified (`test_i18n.py`,
      `test_i18n_server.py`, `test_i18n_remnants.py`). The app's test also covers double-quoted literals and the English half
      of `tr()`.
- [ ] User data stays as stored (food names, recipes, journal items, notes and status cards the AI wrote); the exercise
      detail still shows the other-language name under the title on purpose (both names, page language first)

## Stage 4: handbook and app store

- [x] `docs/tester-handbook.en.md`, rendered to `/app/handbook/en/` by `deploy/ali/handbook.py`; the activation page
      (`activate.html` / `install.html`, `data-i18n-href`) and the Today page's 「我 → 使用手册」 open the handbook in the page
      language, and the two editions link to each other (#50). Screenshots in the English edition still show the Chinese UI
- [x] iOS permission prompts (#64): `ios/Runner/en.lproj` / `zh-Hans.lproj` `InfoPlist.strings` (variant group in the Xcode
      project), English is the development language (`CFBundleDevelopmentRegion = en`, Info.plist holds the English)
- [ ] App Store English listing and review notes
