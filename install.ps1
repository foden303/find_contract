# Windows PowerShell 5.1 / PowerShell 7. Works as a file or via irm ... | iex.
# Optional trust pinning: set FINDER_EXPECTED_PUBLISHER (exact certificate Subject)
# and/or FINDER_EXPECTED_THUMBPRINT before invoking, or pass these script parameters.
param(
    [string]$ExpectedPublisher = $env:FINDER_EXPECTED_PUBLISHER,
    [string]$ExpectedThumbprint = $env:FINDER_EXPECTED_THUMBPRINT
)

& {
    param($Publisher, $Thumbprint)
    $ErrorActionPreference = 'Stop'
    $temporary = $null
    $previousTls = [Net.ServicePointManager]::SecurityProtocol

    function Save-HttpsFile([string]$Url, [string]$Destination) {
        $uri = [Uri]$Url
        for ($redirect = 0; $redirect -le 5; $redirect++) {
            if ($uri.Scheme -cne 'https' -or $uri.Port -ne 443 -or $uri.UserInfo -or
                $uri.DnsSafeHost -notin @('api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com')) {
                throw "Untrusted download URL: $uri"
            }
            $request = [Net.HttpWebRequest]::Create($uri)
            $request.AllowAutoRedirect = $false
            $request.UserAgent = 'B2BContactFinder-Installer/1'
            $request.Timeout = 60000
            $request.ReadWriteTimeout = 60000
            $response = $request.GetResponse()
            try {
                $status = [int]$response.StatusCode
                if ($status -in @(301, 302, 303, 307, 308)) {
                    if (-not $response.Headers['Location']) { throw 'Download redirect has no destination.' }
                    $uri = New-Object Uri($uri, $response.Headers['Location'])
                    continue
                }
                if ($status -ne 200) { throw "Download failed with HTTP $status." }
                $inputStream = $response.GetResponseStream()
                $outputStream = [IO.File]::Create($Destination)
                try { $inputStream.CopyTo($outputStream) }
                finally { $outputStream.Dispose(); $inputStream.Dispose() }
                return
            } finally { $response.Dispose() }
        }
        throw 'Too many download redirects.'
    }

    try {
        if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
            throw 'The consumer installer supports Windows 10/11 x64 only. Use the separate developer/Docker instructions on other systems.'
        }
        $nativeArchitecture = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment').PROCESSOR_ARCHITECTURE
        if ($nativeArchitecture -ne 'AMD64') {
            throw "Unsupported native Windows architecture '$nativeArchitecture'. This release requires x64; ARM64 and 32-bit Windows are not supported."
        }
        $windowsBuild = [int](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuildNumber
        if ($windowsBuild -lt 17763) { throw 'Windows 10 version 1809 or newer is required.' }
        if ($Thumbprint) {
            $Thumbprint = $Thumbprint.Replace(' ', '').ToUpperInvariant()
            if ($Thumbprint -cnotmatch '\A[0-9A-F]{40}\z') { throw 'Expected certificate thumbprint must contain exactly 40 hexadecimal characters.' }
        }
        [Net.ServicePointManager]::SecurityProtocol = $previousTls -bor [Net.SecurityProtocolType]::Tls12
        $temporary = Join-Path ([IO.Path]::GetTempPath()) ('B2BContactFinder-' + [Guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($temporary) | Out-Null
        $metadataPath = Join-Path $temporary 'release.json'
        Write-Host 'Finding the latest public B2B Contact Finder release...'
        Save-HttpsFile 'https://api.github.com/repos/foden303/find_contract/releases/latest' $metadataPath
        $release = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
        if ($release.draft -or $release.prerelease -or $release.tag_name -cnotmatch '\Av(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\z') {
            throw 'The latest release does not have a valid stable vMAJOR.MINOR.PATCH tag.'
        }
        $tag = [string]$release.tag_name
        $version = $tag.Substring(1)
        foreach ($part in $version.Split('.')) {
            if ($part.Length -gt 5 -or [int]$part -gt 65535) { throw 'Release version exceeds Windows version limits.' }
        }
        $assetName = "B2BContactFinder-$version-windows-x64-setup.exe"
        $installerAssets = @($release.assets | Where-Object { $_.name -ceq $assetName -and $_.state -eq 'uploaded' })
        $checksumAssets = @($release.assets | Where-Object { $_.name -ceq 'SHA256SUMS.txt' -and $_.state -eq 'uploaded' })
        if ($installerAssets.Count -ne 1 -or $checksumAssets.Count -ne 1) {
            throw "Release $tag is incomplete: exactly one $assetName and SHA256SUMS.txt are required."
        }
        $prefix = "https://github.com/foden303/find_contract/releases/download/$tag/"
        foreach ($asset in @($installerAssets[0], $checksumAssets[0])) {
            if ($asset.browser_download_url -cne ($prefix + $asset.name)) { throw 'Release asset URL does not match the repository, tag and asset name.' }
            if ([long]$asset.size -le 0) { throw 'Release asset is empty.' }
        }
        $checksumsPath = Join-Path $temporary 'SHA256SUMS.txt'
        Save-HttpsFile $checksumAssets[0].browser_download_url $checksumsPath
        if ((Get-Item -LiteralPath $checksumsPath).Length -ne [long]$checksumAssets[0].size) { throw 'Checksum download size mismatch.' }
        $matchingHashes = @()
        foreach ($line in Get-Content -LiteralPath $checksumsPath) {
            if (-not $line.Trim()) { continue }
            if ($line -cnotmatch '\A([0-9a-fA-F]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)\z') { throw 'Malformed SHA256SUMS.txt; refusing to install.' }
            if ($Matches[2] -ceq $assetName) { $matchingHashes += $Matches[1] }
        }
        if ($matchingHashes.Count -ne 1) { throw 'Missing or duplicate installer checksum; refusing to install.' }
        $installerPath = Join-Path $temporary $assetName
        Write-Host "Downloading $assetName..."
        Save-HttpsFile $installerAssets[0].browser_download_url $installerPath
        if ((Get-Item -LiteralPath $installerPath).Length -ne [long]$installerAssets[0].size) { throw 'Installer download size mismatch.' }
        $actualHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash
        if ($actualHash -ine $matchingHashes[0]) { throw 'SHA256 verification FAILED. The installer will not be executed.' }
        $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
        if ($signature.Status -eq 'Valid') {
            if ($Publisher -and $signature.SignerCertificate.Subject -cne $Publisher) { throw 'Authenticode publisher does not match the expected certificate Subject.' }
            if ($Thumbprint -and $signature.SignerCertificate.Thumbprint -ine $Thumbprint) { throw 'Authenticode certificate thumbprint does not match.' }
            Write-Host "Verified signature: $($signature.SignerCertificate.Subject)"
        } elseif ($signature.Status -eq 'NotSigned' -and -not $Publisher -and -not $Thumbprint) {
            Write-Warning 'This release is UNSIGNED. SHA256 verifies the GitHub release download, not publisher identity. Windows may display SmartScreen; no protection is disabled by this installer.'
        } else {
            throw "Authenticode verification failed ($($signature.Status)). A requested publisher pin requires a valid trusted signature."
        }
        $fileVersion = (Get-Item -LiteralPath $installerPath).VersionInfo.ProductVersion
        if ($fileVersion -ne $version -and $fileVersion -ne "$version.0") { throw 'Installer version resource does not match the release tag.' }
        Write-Host "Installing B2B Contact Finder $version for the current Windows user..."
        $process = Start-Process -FilePath $installerPath -ArgumentList '/CURRENTUSER', '/NORESTART' -Wait -PassThru
        if ($process.ExitCode -eq 3010) {
            Write-Host 'Installation succeeded; Windows reports that a restart is required.'
        } elseif ($process.ExitCode -ne 0) {
            throw "Installer exited with code $($process.ExitCode). Installation did not complete successfully."
        } else {
            Write-Host 'Installation complete. Open B2B Contact Finder from the Start menu.'
        }
    } catch {
        throw "B2B Contact Finder installation stopped: $($_.Exception.Message)"
    } finally {
        [Net.ServicePointManager]::SecurityProtocol = $previousTls
        if ($temporary -and (Test-Path -LiteralPath $temporary)) {
            Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction Continue
        }
    }
} $ExpectedPublisher $ExpectedThumbprint
