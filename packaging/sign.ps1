param([Parameter(Mandatory = $true)][string]$File)
$ErrorActionPreference = 'Stop'
$path = (Resolve-Path -LiteralPath $File).Path
$present = @($env:WINDOWS_SIGNING_PFX, $env:WINDOWS_SIGNING_PASSWORD, $env:WINDOWS_SIGNING_PUBLISHER) | Where-Object { $_ }
if ($present.Count -eq 0) {
    Write-Warning "UNSIGNED: $path. No signing credentials configured; SmartScreen reputation is not established."
    return
}
if ($present.Count -ne 3) { throw 'Signing requires WINDOWS_SIGNING_PFX (base64), WINDOWS_SIGNING_PASSWORD and WINDOWS_SIGNING_PUBLISHER (exact Subject).' }
$pfx = Join-Path ([IO.Path]::GetTempPath()) ('finder-sign-' + [Guid]::NewGuid().ToString('N') + '.pfx')
$imported = @()
$previousThumbprints = @(Get-ChildItem Cert:\CurrentUser\My | ForEach-Object Thumbprint)
try {
    [IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WINDOWS_SIGNING_PFX))
    $password = ConvertTo-SecureString $env:WINDOWS_SIGNING_PASSWORD -AsPlainText -Force
    $imported = @(Import-PfxCertificate -FilePath $pfx -CertStoreLocation Cert:\CurrentUser\My -Password $password)
    $signers = @($imported | Where-Object { $_.HasPrivateKey -and $_.Subject -ceq $env:WINDOWS_SIGNING_PUBLISHER })
    if ($signers.Count -ne 1) { throw 'PFX must contain exactly one private-key certificate matching the expected publisher Subject.' }
    $sdk = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $signtool = Get-ChildItem -Path "$sdk\*\x64\signtool.exe" | Sort-Object { [version]$_.Directory.Parent.Name } -Descending | Select-Object -First 1
    if (-not $signtool) { throw 'Windows SDK x64 signtool.exe is required for signing.' }
    & $signtool.FullName sign /sha1 $signers[0].Thumbprint /fd SHA256 /tr https://timestamp.digicert.com /td SHA256 $path
    if ($LASTEXITCODE -ne 0) { throw 'Authenticode signing or timestamping failed.' }
    & $signtool.FullName verify /pa /all $path
    if ($LASTEXITCODE -ne 0) { throw 'Signed file did not pass Authenticode verification.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -cne $env:WINDOWS_SIGNING_PUBLISHER) {
        throw 'Signed file has an invalid signature or unexpected publisher.'
    }
    Write-Host "SIGNED: $($signature.SignerCertificate.Subject)"
} finally {
    if (Test-Path -LiteralPath $pfx) { Remove-Item -LiteralPath $pfx -Force }
    foreach ($certificate in $imported) {
        if ($certificate.Thumbprint -notin $previousThumbprints) {
            Remove-Item -LiteralPath ("Cert:\CurrentUser\My\" + $certificate.Thumbprint) -Force
        }
    }
}
