# English version (issue #13): plan and checklist

The product is Chinese-first today. English is added in stages, each a separate pull request ("Part of #13"), so that the
many people editing `web/static/app.js` do not collide with one huge diff.

## How it works

- **Language setting**: `users.language` (`zh` / `en`, `NULL` = follow the device; migration `250_user_language.sql`).
  Set in the Today page under Me → Profile; returned by `GET/PATCH /v1/users/{user}/profile` and `GET /v1/web/me`.
- **Today page**: `web/static/i18n.js`, loaded before every other script (no build step).
  - `I18N.t(key, params)` with `{name}` placeholders; page scripts alias it as `tr` (app.js has many locals named `t`).
  - Two tables, `ZH` and `EN`, one `"key": "text",` per line. New languages = another table with the same keys.
  - Static HTML: `data-i18n` (text), `data-i18n-aria`, `data-i18n-ph` (placeholder), `<html data-i18n-title>`.
  - Dates: `I18N.dayLabel / monthDay / yearMonth / monthShort / weekdaysMon / locale()`; never hard-code `"zh-CN"` in new code.
  - Language changes reload the page once (strings computed at startup, such as tab names, are only right after a reload).
  - Key prefixes by area: `tab. hdr. common. login. err. banner. picker. who. me. jarvis. profile. activate.`;
    add a prefix to the list in `body_engine/tests/test_i18n.py` when you start a new area.
- **Tests**: `body_engine/tests/test_i18n.py` (every key in zh and en with the same placeholders, no Chinese in the English
  table, every key used by the scripts / HTML exists, i18n.js loaded first and cached by `sw.js`);
  `apps/watch2whoop/test/i18n_test.dart` (every Chinese literal in the app is the zh half of `tr('中文', 'English')`).
- **AI**: the personas (`ai/system_prompt.md`, `ai/system_prompt_lite.md`, `assistant/CLAUDE.md`) reply in the user's
  language: `get_briefing` → `me.language`, and the in-page chat system prompt lists 语言 under 用户. Tool results stay
  Chinese / structured; the model translates.
- **iOS app**: `tr('中文', 'English')` by the system language (`lib/src/i18n.dart`); the embedded Today page follows the
  same system language unless the profile sets one.

## Stage 1 (done)

- [x] i18n layer, profile `language` field + migration, `/v1/web/me` returns it, switch in Me → Profile
- [x] Shell: tab bar, header (date, yesterday, day navigation, date picker), login screen, offline banner, API error
      fallbacks, Me page (rows, status / notes headings, version line), Jarvis sheet, profile sheet, activation page
- [x] AI personas: reply in the user's language
- [x] iOS app: all onboarding / sync / home strings already bilingual; coverage test added

## Stage 2: remaining Today page (one PR per bullet keeps diffs small)

- [ ] Today tab cards and sheets: recovery / sleep / strain / load / training / nutrition sheets, reminders, meal
      templates (`RENDER_today`, `recoverySheet` … `allRemindersSheet`, `goalSheet`, `mealSheet`), `SHORT` reason labels,
      `ZONE_LABEL`, `CATEGORY`, `LEVEL`, `fmtMin` / `fmtNum` units, sleep chart weekday labels (`WEEK`)
- [ ] Trends tab: metric names, ranges, status cards, `nightSheet`, `RENDER_trends`
- [ ] Log tab: journal, events, training calendar / day / plan sheets (`RENDER_record`, `eventSheet`, `numberSheet`,
      `daySheet`, `planSheet`, `sessionHtml`, `EVENT_CATEGORY`, `SUPP_STATUS`, intensity names)
- [ ] Food tab: recipes, recipe sheet, food entry, shopping (`RENDER_food`, `recipeSheet`, `foodEntrySheet`, `SLOTS`)
- [ ] Me tab details: healthspan, status / lab sheets (`statusSheet`, `labSheet`, `labsSheet`, `observationSheet`,
      `healthspanSheet`), toasts such as 已删除 / 已撤销 / 已记
- [ ] `ai.js` (AI settings, chat panel, fallback AI consent sheet), `history.js` (health history import),
      `profile.js` consent sheet wrapper (buttons, headings; the consent text itself is stage 3), `sw.js` offline message
- [ ] Server error `detail` strings shown in toasts: return a stable `code` next to the Chinese `detail`
      and translate the code in the page (keep `detail` for older clients)

## Stage 3: server texts

- [ ] Explain glossary (`explain/glossary.py`, `glossary_trends.py`, `personal.py`): add English entries, choose by the
      viewer's `language` (fall back to zh)
- [ ] Reminders (`rules/`): titles and bodies by language; keep the thresholds and sources in `params.py` unchanged
- [ ] Status card labels (`assistant/status.py`) and the goal-weight item written by `profile/store.py`
- [ ] Consent form in English: own file and **own version number** (`consent.en.md`, `docs/family-consent.en.md`,
      identical check like the Chinese one); record which language was accepted in `user_consents`
- [ ] Fallback AI consent text (`ai/fallback.py`)

## Stage 4: handbook and app store

- [ ] `docs/tester-handbook.en.md`, rendered to `/app/handbook/en/` by `deploy/ali/handbook.py`; activation page links
      to the handbook in the page language
- [ ] iOS permission prompts: split the bilingual `NS*UsageDescription` strings into `en.lproj` / `zh-Hans.lproj`
      `InfoPlist.strings` (needs the Xcode project's variant group; test on a device)
- [ ] App Store English listing and review notes
