---
name: w2w-ios-release-prep
description: Prepare an iOS (iPhone + Apple Watch) store release as a draft PR plus a gap list — version and build number in the xcconfig, tidy the CHANGELOG section without changing meaning, bump and rebuild the algorithm bundle but leave signing to the owner, dry-run tools/app_release.sh, write the handover from the template, list what was never verified on a real device. No tag, no Release, no deploy, no merge. Triggers: 发布准备, release prep, 发版, 版本号, 构建号, build number, CHANGELOG, 交接单, handover, TestFlight, app_release.sh, 算法包升版本, 缺口清单, 真机清单.
---

# iOS release prep

Distilled from #475 (2.1.0 release prep) and the rules in `docs/apps.md` §4, `tools/app_release.sh`,
`packages/openbodyscore-js/README.md` ("Changing an algorithm") and `docs/ios-native-*-handover*.md`.

Release flow: release PR merged → the coordinator runs `tools/app_release.sh ios --push` → `app-release.yml` publishes
the pre-release → the uploader builds **only from that Release's zip**. This skill covers the first step only.

## 0. Not in this session

No `--create` / `--push`, no tag, no GitHub Release, no deploy, no merge, no production. Never read, use or print
anything under `secrets/` (the algorithm signing key lives there and only the owner uses it).

## 1. The release PR (draft)

1. **Version**: `apps/ios/Config/Watch2Whoop.xcconfig` — `W2W_MARKETING_VERSION = X.Y.Z`, `W2W_BUILD_NUMBER = N`
   (higher than every earlier iOS tag's build; iPhone and watch targets share it, never set them separately).
2. **CHANGELOG** (`apps/ios/CHANGELOG.md`): add the row to the version table; tidy the `## X.Y.Z` section — merge
   duplicates, order by what users notice most, bilingual — **without changing meaning and without adding anything
   that was not done**. Check each line against a merged PR.
3. **Algorithm bundle** (`packages/openbodyscore-js`): bump `version` in `package.json` (the bundled copy must be newer
   than any bundle phones already downloaded, or they keep the old one), `node build.js`, commit `dist/`. **Do not
   sign.** Put the owner's exact signing command in the PR body (`body-engine algo-sign --key <main checkout>/secrets/
   algo_signing_key`, run in this branch's checkout), and say that only `dist/manifest.json` changes and gets
   committed.
4. **CI paths**: make sure changes under `packages/openbodyscore-js/dist/` and its goldens trigger `iOS native`.
5. **Dry run**: `tools/app_release.sh ios --ref <head> --allow-unmerged` and paste its output in the PR. (`--allow-unmerged`
   only for this dry run, never for a build given to testers.)

## 2. Handover and gap list (same PR)

- `docs/ios-native-X.Y.Z-handover.md` from the latest handover template: concrete uploader steps for this version
  (new targets / App IDs / capabilities / platforms since last time), with placeholders `<...>` for every value. The
  public file names no person, Apple ID, Team ID, bundle ID or domain; the coordinator fills a private copy.
- **Gap list** (G1, G2, …): everything not verified on a real device or real account — HealthKit background delivery,
  push, watch hardware, complications on a real face, sign-in, upgrade from the previous build — each with how to
  check it and whether it blocks. Rule: if the device acceptance is not done, the build stays in the **internal**
  TestFlight group; external testers only after the owner says so.
- A device checklist the owner can tick on the phone.

## 3. Keeping it current

If `main` moves before merge: check the remote branch for the owner's signing commit first (never overwrite it), merge
`origin/main`, re-run the dry run, update the PR. A PR touching the same CHANGELOG as one just merged is updated first.

## 4. Report

Draft PR link + head SHA, dry-run result, the signing command waiting for the owner, the gap list summary, what blocks
external testers. Everything else stays for the coordinator.
