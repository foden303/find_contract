# Build from the repository root with Python 3.12 x64 on Windows.
from pathlib import Path
import platform
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
if sys.platform != 'win32' or platform.machine().upper() not in ('AMD64', 'X86_64'):
    raise SystemExit('The consumer package must be built on Windows x64; ARM64 is unsupported.')
if sys.version_info[:2] != (3, 12):
    raise SystemExit('The consumer package requires Python 3.12.')

# Dynamic search engines, extension modules, DLLs, phone metadata, PSL snapshots,
# and package metadata are runtime inputs, not just statically visible imports.
datas = [(str(ROOT / 'web' / 'static'), 'web/static')]
binaries = []
hiddenimports = ['pystray._win32', 'pystray._util.win32']
for package in ('ddgs', 'primp', 'selectolax', 'lxml', 'rapidfuzz', 'phonenumbers',
                'tldextract', 'certifi', 'PIL', 'openpyxl'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
for package in ('uvicorn', 'dns', 'python_multipart'):
    hiddenimports += collect_submodules(package)
datas += collect_data_files('tldextract', includes=['.tld_set_snapshot'])
datas += copy_metadata('pystray')

a = Analysis(
    [str(ROOT / 'desktop.py')],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'IPython', 'pystray._darwin', 'pystray._xorg',
              'pystray._gtk', 'pystray._appindicator'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='B2BContactFinder', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    icon=str(ROOT / 'build' / 'assets' / 'finder.ico'),
    version=str(ROOT / 'build' / 'assets' / 'version.txt'),
    uac_admin=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='B2BContactFinder')
