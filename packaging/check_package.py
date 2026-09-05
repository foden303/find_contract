"""Build-time checks for omitted assets, incompatible native binaries and state leaks."""
from pathlib import Path
import ssl
import sys

import pefile

root = Path(sys.argv[1]).resolve()
runtime = root / '_internal'
required = [
    root / 'B2BContactFinder.exe',
    runtime / 'python312.dll',
    runtime / 'web' / 'static' / 'index.html',
    runtime / 'certifi' / 'cacert.pem',
    runtime / 'tldextract' / '.tld_set_snapshot',
]
for path in required:
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f'Missing or empty packaged resource: {path}')
context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.load_verify_locations(str(runtime / 'certifi' / 'cacert.pem'))
if context.cert_store_stats()['x509_ca'] == 0:
    raise SystemExit('Bundled TLS trust store contains no CA certificates.')
for package in ('selectolax', 'primp', 'lxml', 'rapidfuzz', 'PIL'):
    if not any((runtime / package).rglob('*.pyd')):
        raise SystemExit(f'Missing native extension for {package}')
count = 0
for path in root.rglob('*'):
    if path.name.startswith(('.cache.db', '.runs.db')) or path.name in ('.uploads', '.env', 'settings.json'):
        raise SystemExit(f'User data or secrets leaked into package: {path}')
    if path.suffix.lower() in ('.exe', '.dll', '.pyd'):
        with pefile.PE(str(path), fast_load=True) as binary:
            if binary.FILE_HEADER.Machine != pefile.MACHINE_TYPE['IMAGE_FILE_MACHINE_AMD64']:
                raise SystemExit(f'Non-x64 native binary: {path}')
        count += 1
print(f'Package checks passed: {count} x64 PE binaries, static UI, PSL and TLS certificates.')
