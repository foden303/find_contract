"""Generate build-only Windows and macOS icons plus version resources."""
from pathlib import Path
import re
import runpy

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
VERSION = runpy.run_path(str(ROOT / 'core' / 'version.py'))['VERSION']
if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', VERSION):
    raise SystemExit('VERSION must be a stable major.minor.patch version.')
parts = tuple(map(int, VERSION.split('.'))) + (0,)
if any(part > 65535 for part in parts):
    raise SystemExit('Windows version components must be at most 65535.')
out = ROOT / 'build' / 'assets'
out.mkdir(parents=True, exist_ok=True)
image = Image.new('RGBA', (1024, 1024), '#16324f')
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((152, 152, 872, 872), radius=120, fill='#24b6a6')
draw.ellipse((304, 264, 640, 600), outline='white', width=60)
draw.line((600, 560, 752, 736), fill='white', width=72)
image.save(out / 'finder.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
image.save(out / 'finder.icns', format='ICNS', sizes=[
    (16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)
])
(out / 'version.txt').write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={parts!r}, prodvers={parts!r}, mask=0x3f,
    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'B2B Contact Finder'),
    StringStruct('FileDescription', 'B2B Contact Finder'),
    StringStruct('FileVersion', '{VERSION}'),
    StringStruct('InternalName', 'B2BContactFinder'),
    StringStruct('OriginalFilename', 'B2BContactFinder.exe'),
    StringStruct('ProductName', 'B2B Contact Finder'),
    StringStruct('ProductVersion', '{VERSION}')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
''', encoding='utf-8')
print(VERSION)
