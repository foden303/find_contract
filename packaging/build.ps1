param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    if ($env:OS -ne 'Windows_NT' -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -or $env:PROCESSOR_ARCHITEW6432) {
        throw 'Build on native Windows x64 with a 64-bit PowerShell and Python 3.12.10.'
    }
    & $Python -c "import struct,sys; assert sys.version_info[:3] == (3,12,10) and struct.calcsize('P') == 8, 'Python 3.12.10 x64 is required'"
    if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python interpreter.' }
    & $Python -m pip install --require-hashes --only-binary=:all: -r packaging/requirements-windows.lock
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Application regression tests failed.' }
    & $Python packaging/create_assets.py
    if ($LASTEXITCODE -ne 0) { throw 'Asset generation failed.' }
    & $Python -m PyInstaller --noconfirm --clean packaging/B2BContactFinder.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    & $Python packaging/check_package.py dist/B2BContactFinder
    if ($LASTEXITCODE -ne 0) { throw 'Frozen package checks failed.' }
} finally { Pop-Location }
