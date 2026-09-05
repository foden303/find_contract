#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64) LOCK=packaging/requirements-macos-arm64.lock ;;
  x86_64) LOCK=packaging/requirements-macos-x64.lock ;;
  *) printf 'Unsupported macOS architecture: %s\n' "$ARCH" >&2; exit 1 ;;
esac

"$PYTHON" -c 'import platform,struct,sys; assert sys.version_info[:3] == (3,12,10), "Python 3.12.10 required"; assert struct.calcsize("P") == 8; assert platform.machine() in ("arm64", "x86_64")'
"$PYTHON" -m pip install --require-hashes --only-binary=:all: -r "$LOCK"
"$PYTHON" -m unittest discover -s tests -v
"$PYTHON" packaging/create_assets.py
"$PYTHON" -m PyInstaller --noconfirm --clean packaging/B2BContactFinder-macos.spec
"$PYTHON" packaging/check_macos_package.py "dist/B2B Contact Finder.app" "$ARCH"
