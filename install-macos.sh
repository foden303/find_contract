#!/usr/bin/env bash
# macOS 11+ consumer bootstrap. Works as a file or via curl ... | bash.
set -euo pipefail

REPOSITORY="foden303/find_contract"
API="https://api.github.com/repos/$REPOSITORY/releases/latest"
EXPECTED_TEAM_ID="${FINDER_EXPECTED_APPLE_TEAM_ID:-}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/B2BContactFinder.XXXXXX")"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT INT TERM
fail() { printf 'B2B Contact Finder installation stopped: %s\n' "$*" >&2; exit 1; }
download() {
  /usr/bin/curl --fail --silent --show-error --location --max-redirs 5 \
    --proto '=https' --proto-redir '=https' --tlsv1.2 --output "$2" "$1"
}

[ "$(uname -s)" = Darwin ] || fail 'this installer supports macOS only.'
ARCH="$(uname -m)"
if [ "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null || true)" = 1 ]; then ARCH=arm64; fi
case "$ARCH" in
  arm64) ASSET_ARCH=arm64 ;;
  x86_64) ASSET_ARCH=x64 ;;
  *) fail "unsupported architecture '$ARCH'; use a 64-bit Apple Silicon or Intel Mac." ;;
esac
OS_MAJOR="$(/usr/bin/sw_vers -productVersion | /usr/bin/cut -d. -f1)"
[ "$OS_MAJOR" -ge 11 ] || fail 'macOS 11 Big Sur or newer is required.'
command -v /usr/bin/curl >/dev/null || fail 'the built-in curl command is unavailable.'

printf 'Finding the latest public B2B Contact Finder release...\n'
METADATA="$TMP/release.json"
download "$API" "$METADATA"
TAG="$(/usr/bin/plutil -extract tag_name raw "$METADATA")"
DRAFT="$(/usr/bin/plutil -extract draft raw "$METADATA")"
PRERELEASE="$(/usr/bin/plutil -extract prerelease raw "$METADATA")"
[[ "$TAG" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || fail 'latest release tag is not stable MAJOR.MINOR.PATCH.'
[ "$DRAFT" = false ] && [ "$PRERELEASE" = false ] || fail 'latest release is draft or prerelease.'
VERSION="${TAG#v}"
ASSET="B2BContactFinder-$VERSION-macos-$ASSET_ARCH.zip"
ASSET_URL=""; ASSET_SIZE=""; CHECKSUM_URL=""; CHECKSUM_SIZE=""; asset_matches=0; checksum_matches=0
index=0
while name="$(/usr/bin/plutil -extract "assets.$index.name" raw "$METADATA" 2>/dev/null)"; do
  state="$(/usr/bin/plutil -extract "assets.$index.state" raw "$METADATA")"
  url="$(/usr/bin/plutil -extract "assets.$index.browser_download_url" raw "$METADATA")"
  size="$(/usr/bin/plutil -extract "assets.$index.size" raw "$METADATA")"
  if [ "$name" = "$ASSET" ] && [ "$state" = uploaded ]; then
    asset_matches=$((asset_matches + 1)); ASSET_URL="$url"; ASSET_SIZE="$size"
  fi
  if [ "$name" = SHA256SUMS.txt ] && [ "$state" = uploaded ]; then
    checksum_matches=$((checksum_matches + 1)); CHECKSUM_URL="$url"; CHECKSUM_SIZE="$size"
  fi
  index=$((index + 1))
done
[ "$asset_matches" -eq 1 ] && [ "$checksum_matches" -eq 1 ] || fail "release $TAG is incomplete for macOS $ASSET_ARCH."
PREFIX="https://github.com/$REPOSITORY/releases/download/$TAG/"
[ "$ASSET_URL" = "$PREFIX$ASSET" ] && [ "$CHECKSUM_URL" = "${PREFIX}SHA256SUMS.txt" ] || fail 'release asset URL is outside the expected repository/tag.'
[[ "$ASSET_SIZE" =~ ^[1-9][0-9]*$ ]] && [[ "$CHECKSUM_SIZE" =~ ^[1-9][0-9]*$ ]] || fail 'release asset size is invalid.'

CHECKSUMS="$TMP/SHA256SUMS.txt"
download "$CHECKSUM_URL" "$CHECKSUMS"
[ "$(/usr/bin/stat -f%z "$CHECKSUMS")" = "$CHECKSUM_SIZE" ] || fail 'checksum file size mismatch.'
expected=""; matches=0
while read -r hash name extra; do
  [ -n "$hash" ] || continue
  [[ "$hash" =~ ^[0-9a-f]{64}$ ]] && [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] && [ -z "${extra:-}" ] || fail 'malformed checksum manifest.'
  if [ "$name" = "$ASSET" ]; then expected="$hash"; matches=$((matches + 1)); fi
done < "$CHECKSUMS"
[ "$matches" -eq 1 ] || fail 'archive checksum is missing or duplicated.'

ARCHIVE="$TMP/$ASSET"
printf 'Downloading %s...\n' "$ASSET"
download "$ASSET_URL" "$ARCHIVE"
[ "$(/usr/bin/stat -f%z "$ARCHIVE")" = "$ASSET_SIZE" ] || fail 'archive size mismatch.'
actual="$(/usr/bin/shasum -a 256 "$ARCHIVE")"; actual="${actual%% *}"
[ "$actual" = "$expected" ] || fail 'SHA-256 verification failed.'
EXTRACTED="$TMP/extracted"; mkdir "$EXTRACTED"
/usr/bin/ditto -x -k "$ARCHIVE" "$EXTRACTED"
SOURCE_APP="$EXTRACTED/B2B Contact Finder.app"
[ -x "$SOURCE_APP/Contents/MacOS/B2BContactFinder" ] || fail 'archive does not contain the expected app bundle.'
/usr/bin/codesign --verify --deep --strict "$SOURCE_APP" || fail 'app bundle signature is internally invalid.'
if [ -n "$EXPECTED_TEAM_ID" ]; then
  details="$(/usr/bin/codesign -dv --verbose=4 "$SOURCE_APP" 2>&1)"
  printf '%s\n' "$details" | /usr/bin/grep -Fq "TeamIdentifier=$EXPECTED_TEAM_ID" || fail 'Apple Team ID does not match the expected signer.'
  /usr/sbin/spctl -a -t exec -vv "$SOURCE_APP" || fail 'Gatekeeper rejected the signed app.'
elif /usr/sbin/spctl -a -t exec "$SOURCE_APP" >/dev/null 2>&1; then
  printf 'Verified by macOS Gatekeeper.\n'
else
  printf 'WARNING: release is not Developer ID notarized. SHA-256 is valid, but macOS may require Control-click > Open. No security setting is changed.\n' >&2
fi

if /usr/bin/pgrep -f '/B2B Contact Finder.app/Contents/MacOS/B2BContactFinder' >/dev/null 2>&1; then
  fail 'B2B Contact Finder is running. Exit it from the menu bar, then retry.'
fi
APPLICATIONS="$HOME/Applications"; DESTINATION="$APPLICATIONS/B2B Contact Finder.app"
NEW="$APPLICATIONS/.B2B Contact Finder.new.$$"; BACKUP="$APPLICATIONS/.B2B Contact Finder.previous.$$"
mkdir -p "$APPLICATIONS"
rm -rf "$NEW" "$BACKUP"
/usr/bin/ditto "$SOURCE_APP" "$NEW"
/usr/bin/codesign --verify --deep --strict "$NEW" || { rm -rf "$NEW"; fail 'copied app failed signature verification.'; }
if [ -e "$DESTINATION" ]; then mv "$DESTINATION" "$BACKUP"; fi
if ! mv "$NEW" "$DESTINATION"; then
  [ ! -e "$BACKUP" ] || mv "$BACKUP" "$DESTINATION"
  fail 'could not install into ~/Applications.'
fi
rm -rf "$BACKUP"
printf 'Installed B2B Contact Finder %s in %s\n' "$VERSION" "$DESTINATION"
/usr/bin/open "$DESTINATION"
