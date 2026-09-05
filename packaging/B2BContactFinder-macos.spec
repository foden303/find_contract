# Build from the repository root with Python 3.12 on native macOS.
from pathlib import Path
import platform
import runpy
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
VERSION = runpy.run_path(str(ROOT / 'core' / 'version.py'))['VERSION']
architecture = platform.machine().lower()
if sys.platform != 'darwin' or architecture not in ('arm64', 'x86_64'):
    raise SystemExit('Build the macOS app natively on Apple Silicon or Intel macOS.')
if sys.version_info[:2] != (3, 12):
    raise SystemExit('The consumer package requires Python 3.12.')

datas = [(str(ROOT / 'web' / 'static'), 'web/static')]
binaries = []
hiddenimports = ['pystray._darwin']
for package in ('ddgs', 'primp', 'selectolax', 'lxml', 'rapidfuzz', 'phonenumbers',
                'tldextract', 'certifi', 'PIL', 'openpyxl', 'pystray'):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
for package in ('uvicorn', 'dns', 'python_multipart', 'objc', 'Quartz'):
    hiddenimports += collect_submodules(package)
datas += collect_data_files('tldextract', includes=['.tld_set_snapshot'])
datas += copy_metadata('pystray')

a = Analysis(
    [str(ROOT / 'desktop.py')], pathex=[str(ROOT)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'IPython', 'pystray._win32', 'pystray._xorg',
              'pystray._gtk', 'pystray._appindicator'], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='B2BContactFinder',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='B2BContactFinder')
app = BUNDLE(
    coll, name='B2B Contact Finder.app', icon=str(ROOT / 'build' / 'assets' / 'finder.icns'),
    bundle_identifier='com.foden.b2bcontactfinder',
    info_plist={
        'CFBundleDisplayName': 'B2B Contact Finder',
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'LSMinimumSystemVersion': '11.0',
        'LSUIElement': True,
        'NSHighResolutionCapable': True,
    },
)
