"""Reject incomplete, cross-architecture or state-bearing macOS app bundles."""

from pathlib import Path
import plistlib
import subprocess
import sys

app = Path(sys.argv[1]).resolve()
expected = sys.argv[2]
contents = app / "Contents"
executable = contents / "MacOS" / "B2BContactFinder"
resources = contents / "Resources"
required = [
    executable,
    resources / "web" / "static" / "index.html",
    resources / "certifi" / "cacert.pem",
    contents / "Info.plist",
]
for path in required:
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"Missing or empty packaged resource: {path}")
info = plistlib.loads((contents / "Info.plist").read_bytes())
if info.get("CFBundleIdentifier") != "com.foden.b2bcontactfinder" or info.get("LSUIElement") is not True:
    raise SystemExit("App identity or menu-bar application metadata is invalid.")
for path in app.rglob("*"):
    if path.name.startswith((".cache.db", ".runs.db")) or path.name in (".uploads", ".env", "settings.json"):
        raise SystemExit(f"User data or secrets leaked into package: {path}")
magic = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
checked = 0
for path in app.rglob("*"):
    if not path.is_file():
        continue
    try:
        with path.open("rb") as stream:
            signature = stream.read(4)
    except OSError:
        continue
    if signature not in magic:
        continue
    architectures = subprocess.check_output(["/usr/bin/lipo", "-archs", str(path)], text=True).split()
    if expected not in architectures:
        raise SystemExit(f"Native binary lacks {expected}: {path} ({architectures})")
    checked += 1
if checked == 0:
    raise SystemExit("No Mach-O binaries found in app bundle.")
print(f"macOS package checks passed: {checked} native binaries, static UI and metadata.")
