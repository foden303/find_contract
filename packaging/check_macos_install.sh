#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:?usage: check_macos_install.sh ARCHIVE}"
[ -s "$ARCHIVE" ] || { printf 'Missing archive: %s\n' "$ARCHIVE" >&2; exit 1; }
WORK="$(mktemp -d "${TMPDIR:-/tmp}/finder-install.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT INT TERM
export HOME="$WORK/home"
APPLICATIONS="$HOME/Applications"; STATE="$HOME/Library/Application Support/B2BContactFinder"
DESTINATION="$APPLICATIONS/B2B Contact Finder.app"
mkdir -p "$APPLICATIONS" "$STATE"
install_archive() {
  extracted="$WORK/extracted"; rm -rf "$extracted"; mkdir "$extracted"
  /usr/bin/ditto -x -k "$ARCHIVE" "$extracted"
  [ -x "$extracted/B2B Contact Finder.app/Contents/MacOS/B2BContactFinder" ] || { echo 'Archive app missing.' >&2; exit 1; }
  rm -rf "$DESTINATION"
  /usr/bin/ditto "$extracted/B2B Contact Finder.app" "$DESTINATION"
  /usr/bin/codesign --verify --deep --strict "$DESTINATION"
}
install_archive
packaging/smoke_macos.sh "$DESTINATION" "$WORK/installed-smoke.json"
printf 'retain-me' > "$STATE/upgrade-retention-marker"
install_archive
[ "$(cat "$STATE/upgrade-retention-marker")" = retain-me ] || { echo 'Upgrade lost user data.' >&2; exit 1; }
rm -rf "$DESTINATION"
[ "$(cat "$STATE/upgrade-retention-marker")" = retain-me ] || { echo 'Uninstall lost user data.' >&2; exit 1; }
printf 'macOS install lifecycle passed: install, frozen smoke, upgrade retention, app removal retention.\n'
