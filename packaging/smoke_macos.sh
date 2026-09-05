#!/usr/bin/env bash
set -euo pipefail

APP="${1:?usage: smoke_macos.sh APP [OUTPUT]}"
OUTPUT="${2:-$PWD/macos-smoke-result.json}"
BINARY="$APP/Contents/MacOS/B2BContactFinder"
[ -x "$BINARY" ] || { printf 'Missing app executable: %s\n' "$BINARY" >&2; exit 1; }
[ ! -e "$OUTPUT" ] || { printf 'Refusing stale smoke evidence: %s\n' "$OUTPUT" >&2; exit 1; }
STATE="$(mktemp -d "${TMPDIR:-/tmp}/finder-smoke.XXXXXX")"
cleanup() { rm -rf "$STATE"; }
trap cleanup EXIT INT TERM
FINDER_HOME="$STATE" FINDER_CACHE_DIR= FINDER_UPLOAD_DIR= \
  "$BINARY" --smoke-test --smoke-output "$OUTPUT"
python3 - "$OUTPUT" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding='utf-8'))
if result.get('ok') is not True or result.get('shutdown_complete') is not True:
    raise SystemExit(f'Incomplete frozen smoke result: {result}')
print(json.dumps(result, ensure_ascii=False))
PY
