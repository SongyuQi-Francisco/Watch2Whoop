#!/usr/bin/env bash
# Tag the commit a store build (TestFlight / Play / AppGallery) is made from, and zip the app source at that commit.
# Versioning rules: docs/apps.md, section 4.
#
#   tools/app_release.sh <ios|android> [--ref <commit>] [--create | --push] [--allow-unmerged]
#
#   (default)   dry run: run the checks and print the tag, commit and zip it would make; changes nothing
#   --create    create the annotated tag locally and write dist/watch2whoop-<platform>-app-v<ver>-build<N>.zip (+ .sha256)
#   --push      --create, then push the tag to origin and create a GitHub pre-release with the zip (needs gh)
#   --ref       commit to release (default: origin/main after a fetch)
#   --allow-unmerged   allow a commit that is not on origin/main (never for builds given to testers)
#
# The version comes from pubspec.yaml at that commit (version: X.Y.Z+N); the app's CHANGELOG.md at that commit must
# have a section "## X.Y.Z+N". Tag: app-<platform>-vX.Y.Z+N. The zip holds only the app directory, like the v0.1.0
# release asset, and its zip comment is the commit id (`unzip -z`).
set -euo pipefail

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-2}"; }
die() { echo "✗ $*" >&2; exit 1; }

platform="" ref="" mode="dry-run" allow_unmerged=0
while [ $# -gt 0 ]; do
  case "$1" in
    ios|android) platform="$1" ;;
    harmony) die "HarmonyOS app does not exist yet (docs/apps.md); add its version source here when it does" ;;
    --ref) [ $# -ge 2 ] || usage; ref="$2"; shift ;;
    --create) mode="create" ;;
    --push) mode="push" ;;
    --allow-unmerged) allow_unmerged=1 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
  shift
done
[ -n "$platform" ] || usage

root="$(git rev-parse --show-toplevel)"
cd "$root"

git fetch --quiet --tags origin main || die "git fetch origin failed"
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
echo "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\+[0-9]+$' \
  || die "pubspec.yaml version must look like 1.2.3+45, got '$version'"
marketing="${version%+*}"
build="${version#*+}"
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
echo

if [ "$allow_unmerged" = 1 ]; then
  echo "⚠ --allow-unmerged: not checking that $short is on origin/main"
else
  check "commit is on origin/main" 'git merge-base --is-ancestor "$commit" origin/main'
fi
check "tag $tag is not on origin yet" '[ -z "$(git ls-remote --tags origin "refs/tags/$tag")" ]'
reuse_tag=0
if local_tag="$(git rev-parse --verify --quiet "refs/tags/$tag^{commit}")"; then
  # A tag made earlier with --create can be pushed with --push, if it points at the same commit.
  if [ "$local_tag" = "$commit" ] && [ "$mode" = "push" ]; then
    reuse_tag=1; echo "✓ local tag $tag (from --create) points at $short, will push it"
  else
    echo "✗ local tag $tag already exists ($(git rev-parse --short "$local_tag")); delete it with: git tag -d $tag"; ok=0
  fi
fi

# Build numbers only go up per platform (Android versionCode forever; TestFlight per upload).
last_build="$(git tag -l "app-$platform-v*" | { grep -vxF "$tag" || true; } | sed -n 's/.*+\([0-9][0-9]*\)$/\1/p' | sort -n | tail -1)"
if [ -n "$last_build" ]; then
  check "build $build is higher than the last $platform build ($last_build)" '[ "$build" -gt "$last_build" ]'
else
  echo "✓ first $platform tag"
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
if ! has_text "$notes"; then
  # Tagging an older build retroactively (before the changelog existed at that commit): take the notes from main.
  notes="$(changelog_section origin/main | section)" || true
  has_text "$notes" && echo "⚠ CHANGELOG.md at $short has no \"## $version\" section; using the one on origin/main"
fi
check "CHANGELOG.md has a section \"## $version\"" 'has_text "$notes"'

[ "$ok" = 1 ] || die "checks failed; nothing changed"

if [ "$mode" = "dry-run" ]; then
  echo
  echo "Dry run, nothing changed. Would run:"
  echo "  git tag -a $tag $short -m \"Watch2Whoop $label app $marketing (build $build)\""
  echo "  git archive --format=zip --prefix=$name/ -o $zip $short $app_dir"
  echo "  (--push) git push origin refs/tags/$tag && gh release create $tag $zip --prerelease --latest=false"
  echo "Re-run with --create (local only) or --push."
  exit 0
fi

if [ "$reuse_tag" = 0 ]; then
  git tag -a "$tag" "$commit" -m "Watch2Whoop $label app $marketing (build $build)

Store build source: $app_dir at $commit.

$notes"
  echo "✓ tagged $tag → $short"
fi

mkdir -p dist
git archive --format=zip --prefix="$name/" -o "$zip" "$commit" "$app_dir"
(cd dist && shasum -a 256 "$name.zip" > "$name.zip.sha256")
echo "✓ $zip ($(du -h "$zip" | cut -f1 | tr -d ' '), sha256 in $zip.sha256)"

if [ "$mode" = "push" ]; then
  command -v gh >/dev/null || die "gh not installed; the tag is local only (git push origin refs/tags/$tag)"
  git push origin "refs/tags/$tag"
  echo "✓ pushed $tag"
  notes_file="$(mktemp)"
  {
    echo "Source of the $label store build **$marketing ($build)**: \`$app_dir\` at $commit."
    echo "Build from this tag (or the attached zip), not from main — see \`$app_dir/README.md\`."
    echo
    echo "$notes"
  } > "$notes_file"
  gh release create "$tag" "$zip" "$zip.sha256" --verify-tag --prerelease --latest=false \
    --title "$label app $marketing (build $build)" --notes-file "$notes_file"
  rm -f "$notes_file"
  echo "✓ GitHub pre-release $tag"
else
  echo "Local only. Push later with: tools/app_release.sh $platform --ref $short --push"
fi
