param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$OutputFile
)
$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path -LiteralPath $Executable).Path
$output = [IO.Path]::GetFullPath($OutputFile)
if (Test-Path -LiteralPath $output) { throw "Refusing stale smoke evidence: $output" }
$state = Join-Path ([IO.Path]::GetTempPath()) ('finder-smoke-' + [Guid]::NewGuid().ToString('N'))
$previousHome = $env:FINDER_HOME
$previousCache = $env:FINDER_CACHE_DIR
$previousUploads = $env:FINDER_UPLOAD_DIR
$process = $null
try {
    $env:FINDER_HOME = $state
    $env:FINDER_CACHE_DIR = $null
    $env:FINDER_UPLOAD_DIR = $null
    $process = Start-Process -FilePath $exe -ArgumentList '--smoke-test', '--smoke-output', ('"' + $output + '"') -PassThru
    if (-not $process.WaitForExit(120000)) {
        Stop-Process -Id $process.Id -Force
        throw 'Frozen smoke test timed out after 120 seconds.'
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "Frozen smoke test failed: exit $($process.ExitCode). See $output" }
    if (-not (Test-Path -LiteralPath $output)) { throw 'Frozen smoke test did not write evidence.' }
    $result = Get-Content -LiteralPath $output -Raw | ConvertFrom-Json
    if ($result.ok -ne $true -or $result.shutdown_complete -ne $true) { throw "Incomplete frozen smoke test: $(Get-Content -LiteralPath $output -Raw)" }
    Write-Host (Get-Content -LiteralPath $output -Raw)
} finally {
    $env:FINDER_HOME = $previousHome
    $env:FINDER_CACHE_DIR = $previousCache
    $env:FINDER_UPLOAD_DIR = $previousUploads
    if (Test-Path -LiteralPath $state) { Remove-Item -LiteralPath $state -Recurse -Force }
}
