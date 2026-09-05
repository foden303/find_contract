#!/usr/bin/env bash
set -euo pipefail

APP="${1:?usage: package_macos.sh APP OUTPUT_ZIP}"
OUTPUT="${2:?usage: package_macos.sh APP OUTPUT_ZIP}"
[ -d "$APP" ] || { printf 'Missing app bundle: %s\n' "$APP" >&2; exit 1; }
[ ! -e "$OUTPUT" ] || { printf 'Refusing to overwrite release archive: %s\n' "$OUTPUT" >&2; exit 1; }
mkdir -p "$(dirname "$OUTPUT")"
/usr/bin/codesign --verify --deep --strict "$APP"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUTPUT"
[ -s "$OUTPUT" ] || { printf 'Release archive is empty.\n' >&2; exit 1; }
