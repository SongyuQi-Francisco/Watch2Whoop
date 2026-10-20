#!/usr/bin/env bash
# Release a store build (TestFlight / Play / AppGallery): tag the commit, and let GitHub publish its source.
# Versioning rules: docs/apps.md, section 4. The GitHub Release is made ONLY by the workflow .github/workflows/app-release.yml
# (it runs this script with --publish on every pushed app-<platform>-v* tag), so nothing depends on a maintainer's Mac.
#
#   tools/app_release.sh <ios|android> [--ref <commit>] [--create | --push] [--allow-unmerged]   (maintainer)
#   tools/app_release.sh --publish <tag>                                                       (the workflow)
#
#   (default)   dry run: run the checks and print the tag, commit and zip; changes nothing
#   --create    create the annotated tag locally and write dist/watch2whoop-<platform>-app-v<ver>-build<N>.zip (+ .sha256)
#               to look at; nothing is pushed
#   --push      create the tag (or reuse the one from --create) and push it; the workflow then checks it again and
#               publishes the GitHub pre-release with the zip, its sha256 and the CHANGELOG section
#   --publish   (CI) check an existing tag, build the zip and create / update its GitHub pre-release (needs gh + GH_TOKEN)
#   --ref       commit to release (default: origin/main after a fetch)
#   --allow-unmerged   allow a commit that is not on origin/main (never for builds given to testers)
#
# Versions come from pubspec.yaml at that commit: X.Y.Z+N. Tag: app-<platform>-vX.Y.Z+N. The app's CHANGELOG.md at that
# commit must have a section "## X.Y.Z+N". Test runs of this flow use X.Y.Z-test…+N (e.g. app-ios-v0.0.0-test+0 on a
# throwaway commit): not checked against main or earlier build numbers, titled "TEST", to be deleted right after.
# The zip holds only the app directory, like the v0.1.0 release asset; its zip comment is the commit id (`unzip -z`).
set -euo pipefail

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-2}"; }
die() { echo "✗ $*" >&2; exit 1; }

platform="" ref="" mode="dry-run" allow_unmerged=0 publish_tag=""
while [ $# -gt 0 ]; do
  case "$1" in
    ios|android) platform="$1" ;;
    harmony) die "HarmonyOS app does not exist yet (docs/apps.md); add its version source here when it does" ;;
    --ref) [ $# -ge 2 ] || usage; ref="$2"; shift ;;
    --create) mode="create" ;;
    --push) mode="push" ;;
    --publish) [ $# -ge 2 ] || usage; mode="publish"; publish_tag="$2"; shift ;;
    --allow-unmerged) allow_unmerged=1 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
  shift
done

VERSION_RE='^[0-9]+\.[0-9]+\.[0-9]+(-test[0-9A-Za-z.]*)?\+[0-9]+$'
if [ "$mode" = "publish" ]; then
  [[ "$publish_tag" =~ ^app-(ios|android)-v(.+)$ ]] || die "not an app tag: $publish_tag (app-<ios|android>-vX.Y.Z+N)"
  platform="${BASH_REMATCH[1]}"
  ref="refs/tags/$publish_tag"
fi
[ -n "$platform" ] || usage

root="$(git rev-parse --show-toplevel)"
cd "$root"

# In the workflow, actions/checkout leaves a lightweight copy of the pushed tag: --force replaces it with the real
# annotated tag from origin (origin is authoritative for tags anyway).
force=""; [ "$mode" = "publish" ] && force="--force"
git fetch --quiet $force --tags origin main || die "git fetch origin failed"
[ -n "$ref" ] || ref="origin/main"
commit="$(git rev-parse --verify --quiet "$ref^{commit}")" || die "not a commit: $ref"
short="$(git rev-parse --short "$commit")"

# The Flutter app moves from apps/watch2whoop to apps/flutter (docs/apps.md, section 3); accept either.
app_dir=""
for d in apps/flutter apps/watch2whoop; do
  if git cat-file -e "$commit:$d/pubspec.yaml" 2>/dev/null; then app_dir="$d"; break; fi
done
[ -n "$app_dir" ] || die "no Flutter app (apps/flutter or apps/watch2whoop) at $short"

version="$(git show "$commit:$app_dir/pubspec.yaml" | sed -n 's/^version:[[:space:]]*\([^[:space:]#]*\).*/\1/p' | head -1)"
echo "$version" | grep -Eq "$VERSION_RE" || die "pubspec.yaml version must look like 1.2.3+45, got '$version'"
marketing="${version%+*}"
build="${version#*+}"
test_run=0
case "$marketing" in *-test*) test_run=1 ;; esac
tag="app-$platform-v$version"
name="watch2whoop-$platform-app-v$marketing-build$build"
zip="dist/$name.zip"
label="$(echo "$platform" | sed 's/ios/iOS/; s/android/Android/')"

ok=1
check() { if eval "$2"; then echo "✓ $1"; else echo "✗ $1"; ok=0; fi; }

echo "App:      $app_dir ($label)"
echo "Commit:   $commit ($(git log -1 --format='%cs %s' "$commit"))"
echo "Version:  $marketing, build $build"
echo "Tag:      $tag"
echo "Zip:      $zip"
if [ "$test_run" = 1 ]; then echo "⚠ test version ($marketing): a throwaway run of the release flow, delete it afterwards"; fi
echo

if [ "$mode" = "publish" ]; then
  check "tag $publish_tag matches pubspec.yaml version $version" '[ "$publish_tag" = "$tag" ]'
  check "tag $publish_tag is an annotated tag" '[ "$(git cat-file -t "refs/tags/$publish_tag")" = tag ]'
fi
if [ "$test_run" = 1 ] || [ "$allow_unmerged" = 1 ]; then
  echo "⚠ not checking that $short is on origin/main"
else
  check "commit is on origin/main" 'git merge-base --is-ancestor "$commit" origin/main'
fi
reuse_tag=0
if [ "$mode" != "publish" ]; then
  check "tag $tag is not on origin yet" '[ -z "$(git ls-remote --tags origin "refs/tags/$tag")" ]'
  if local_tag="$(git rev-parse --verify --quiet "refs/tags/$tag^{commit}")"; then
    # A tag made earlier with --create can be pushed with --push, if it points at the same commit.
    if [ "$local_tag" = "$commit" ] && [ "$mode" = "push" ]; then
      reuse_tag=1; echo "✓ local tag $tag (from --create) points at $short, will push it"
    else
      echo "✗ local tag $tag already exists ($(git rev-parse --short "$local_tag")); delete it with: git tag -d $tag"; ok=0
    fi
  fi
fi

# Build numbers only go up per platform (Android versionCode forever; TestFlight per upload). Test tags do not count.
if [ "$test_run" = 0 ]; then
  last_build="$(git tag -l "app-$platform-v*" | { grep -vxF "$tag" || true; } | { grep -v -- '-test' || true; } \
    | sed -n 's/.*+\([0-9][0-9]*\)$/\1/p' | sort -n | tail -1)"
  if [ -n "$last_build" ]; then
    check "build $build is higher than the last $platform build ($last_build)" '[ "$build" -gt "$last_build" ]'
  else
    echo "✓ first $platform tag"
  fi
fi

changelog_section() {  # <rev> → the app's CHANGELOG.md at that revision
  local d
  for d in apps/flutter apps/watch2whoop; do
    git show "$1:$d/CHANGELOG.md" 2>/dev/null && return
  done
}
section() { awk -v v="## $version" 'index($0, v) == 1 {on = 1; next} on && /^## / {exit} on {print}'; }
has_text() { [ -n "$(echo "$1" | tr -d '[:space:]')" ]; }
notes="$(changelog_section "$commit" | section)" || true
if ! has_text "$notes" && [ "$mode" != "publish" ]; then
  # Tagging an older build retroactively (before the changelog existed at that commit): take the notes from main.
  notes="$(changelog_section origin/main | section)" || true
  if has_text "$notes"; then echo "⚠ CHANGELOG.md at $short has no \"## $version\" section; using the one on origin/main"; fi
fi
check "CHANGELOG.md has a section \"## $version\"" 'has_text "$notes"'

[ "$ok" = 1 ] || die "checks failed; nothing changed"

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
make_zip() {
  mkdir -p dist
  rm -f "$zip" "$zip.sha256"
  git archive --format=zip --prefix="$name/" -o "$zip" "$commit" "$app_dir"
  (cd dist && sha256 "$name.zip" > "$name.zip.sha256")
  echo "✓ $zip ($(du -h "$zip" | cut -f1 | tr -d ' '), sha256 $(cut -d' ' -f1 "$zip.sha256"))"
}

if [ "$mode" = "dry-run" ]; then
  echo
  echo "Dry run, nothing changed. Would run:"
  echo "  git tag -a $tag $short -m \"Watch2Whoop $label app $marketing (build $build)\""
  echo "  (--push) git push origin refs/tags/$tag   → workflow \"App release\" publishes the GitHub pre-release"
  echo "Re-run with --create (local tag + zip to look at) or --push."
  exit 0
fi

if [ "$mode" = "publish" ]; then
  command -v gh >/dev/null || die "gh not installed"
  make_zip
  repo="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
  notes_file="$(mktemp)"
  {
    if [ "$test_run" = 1 ]; then echo "**Test run of the release flow, not a build to upload; it will be deleted.**"; echo; fi
    echo "Source of the $label store build **$marketing (build $build)**: \`$app_dir\` at commit \`$commit\`."
    echo
    echo "Build only from this release: the attached zip, or \`git clone --branch $tag --depth 1 https://github.com/$repo\`."
    echo "Never from \`main\` or a zip sent by hand. Before building check:"
    echo "- \`shasum -a 256 -c $name.zip.sha256\` prints OK, and \`unzip -z $name.zip\` prints \`$commit\`"
    echo "  (in a clone: \`git describe --tags --exact-match\` prints \`$tag\`);"
    echo "- \`grep '^version:' $app_dir/pubspec.yaml\` prints \`version: $version\`. Do not change it."
    echo
    echo "Steps: \`$app_dir/README.md\`, \"Building and shipping via TestFlight\"."
    echo
    echo "## Changes"
    echo "$notes"
  } > "$notes_file"
  title="$label app $marketing (build $build)"
  if [ "$test_run" = 1 ]; then title="TEST: $title"; fi
  if gh release view "$tag" >/dev/null 2>&1; then
    # Re-run of the workflow: refresh the assets and notes of the existing release.
    gh release upload "$tag" "$zip" "$zip.sha256" --clobber
    gh release edit "$tag" --prerelease --latest=false --title "$title" --notes-file "$notes_file"
    echo "✓ updated GitHub pre-release $tag"
  else
    gh release create "$tag" "$zip" "$zip.sha256" --verify-tag --prerelease --latest=false \
      --title "$title" --notes-file "$notes_file"
    echo "✓ GitHub pre-release $tag"
  fi
  rm -f "$notes_file"
  exit 0
fi

if [ "$reuse_tag" = 0 ]; then
  git tag -a "$tag" "$commit" -m "Watch2Whoop $label app $marketing (build $build)

Store build source: $app_dir at $commit.

$notes"
  echo "✓ tagged $tag → $short"
fi

if [ "$mode" = "create" ]; then
  make_zip
  echo "Local only. Push later with: tools/app_release.sh $platform --ref $short --push"
  exit 0
fi

git push origin "refs/tags/$tag"
echo "✓ pushed $tag"
echo "GitHub Actions now checks the tag again and publishes the release (workflow \"App release\"):"
if command -v gh >/dev/null; then
  repo="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
  if [ -n "$repo" ]; then
    echo "  https://github.com/$repo/actions/workflows/app-release.yml"
    echo "  https://github.com/$repo/releases/tag/$(printf %s "$tag" | sed 's/+/%2B/g')"
  fi
  echo "  gh run watch \$(gh run list --workflow app-release.yml --limit 1 --json databaseId -q '.[0].databaseId')"
fi
