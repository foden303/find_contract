param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = 'Stop'
$setup = (Resolve-Path -LiteralPath $Installer).Path
$app = Join-Path $env:LOCALAPPDATA 'Programs\B2BContactFinder'
$state = Join-Path $env:LOCALAPPDATA 'B2BContactFinder'
if ((Test-Path -LiteralPath $app) -or (Test-Path -LiteralPath $state)) {
    throw 'Installer lifecycle checks require a disposable Windows user with no existing application or data.'
}
$marker = Join-Path $state 'ci-retention-marker.txt'
$token = [Guid]::NewGuid().ToString('N')
function Invoke-Setup([string]$Path, [string[]]$Arguments) {
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Installer lifecycle process exited $($process.ExitCode): $Path" }
}
try {
    Invoke-Setup $setup @('/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    $exe = Join-Path $app 'B2BContactFinder.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw 'Per-user installation did not produce the launcher.' }
    & "$PSScriptRoot\smoke.ps1" -Executable $exe -OutputFile (Join-Path (Split-Path $PSScriptRoot -Parent) 'installed-smoke-result.json')
    [IO.Directory]::CreateDirectory($state) | Out-Null
    [IO.File]::WriteAllText($marker, $token)
    Invoke-Setup $setup @('/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    if ([IO.File]::ReadAllText($marker) -cne $token) { throw 'Upgrade changed existing user data.' }
    Invoke-Setup (Join-Path $app 'unins000.exe') @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    if (Test-Path -LiteralPath $exe) { throw 'Uninstall did not remove the application.' }
    if ([IO.File]::ReadAllText($marker) -cne $token) { throw 'Default uninstall did not retain user data.' }
    Write-Host 'Installer lifecycle passed: per-user install, installed smoke, upgrade retention, default uninstall retention.'
} finally {
    # The disposable-user prerequisite means this marker belongs exclusively to this check.
    if (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker -Force }
    if ((Test-Path -LiteralPath $state) -and -not (Get-ChildItem -LiteralPath $state -Force)) { Remove-Item -LiteralPath $state }
}
