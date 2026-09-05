#!/usr/bin/env bash
set -euo pipefail

APP="${1:?usage: sign_macos.sh APP}"
[ -d "$APP" ] || { printf 'Missing app bundle: %s\n' "$APP" >&2; exit 1; }
values=(
  "${MACOS_SIGNING_P12:-}" "${MACOS_SIGNING_PASSWORD:-}" "${MACOS_SIGNING_IDENTITY:-}"
  "${APPLE_ID:-}" "${APPLE_APP_PASSWORD:-}" "${APPLE_TEAM_ID:-}"
)
present=0
for value in "${values[@]}"; do [ -n "$value" ] && present=$((present + 1)); done
if [ "$present" -eq 0 ]; then
  /usr/bin/codesign --force --deep --sign - "$APP"
  printf 'UNSIGNED: ad-hoc signed for local integrity only; Gatekeeper reputation is not established.\n'
  [ -z "${GITHUB_ENV:-}" ] || printf 'MACOS_SIGNING_STATUS=unsigned\n' >> "$GITHUB_ENV"
  exit 0
fi
[ "$present" -eq 6 ] || { printf 'Signing requires all MACOS_SIGNING_* and APPLE_* variables.\n' >&2; exit 1; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/finder-sign.XXXXXX")"
KEYCHAIN="$WORK/signing.keychain-db"
P12="$WORK/signing.p12"
NOTARY_ZIP="$WORK/notary.zip"
KEYCHAIN_PASSWORD="$(/usr/bin/uuidgen)$(/usr/bin/uuidgen)"
cleanup() {
  /usr/bin/security delete-keychain "$KEYCHAIN" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM
printf '%s' "$MACOS_SIGNING_P12" | /usr/bin/base64 -D > "$P12"
/usr/bin/security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
/usr/bin/security set-keychain-settings -lut 21600 "$KEYCHAIN"
/usr/bin/security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
/usr/bin/security import "$P12" -k "$KEYCHAIN" -P "$MACOS_SIGNING_PASSWORD" -T /usr/bin/codesign
/usr/bin/security set-key-partition-list -S apple-tool:,apple: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN" >/dev/null
/usr/bin/codesign --force --deep --options runtime --timestamp --keychain "$KEYCHAIN" \
  --sign "$MACOS_SIGNING_IDENTITY" "$APP"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$NOTARY_ZIP"
/usr/bin/xcrun notarytool submit "$NOTARY_ZIP" --apple-id "$APPLE_ID" \
  --password "$APPLE_APP_PASSWORD" --team-id "$APPLE_TEAM_ID" --wait
/usr/bin/xcrun stapler staple "$APP"
/usr/bin/xcrun stapler validate "$APP"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"
printf 'SIGNED_AND_NOTARIZED: %s\n' "$MACOS_SIGNING_IDENTITY"
[ -z "${GITHUB_ENV:-}" ] || printf 'MACOS_SIGNING_STATUS=signed-and-notarized\n' >> "$GITHUB_ENV"
