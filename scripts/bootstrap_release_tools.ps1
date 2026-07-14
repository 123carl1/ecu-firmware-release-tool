#requires -Version 7.0
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Contract = Get-Content -LiteralPath (Join-Path $Root 'release_toolchain.json') -Raw | ConvertFrom-Json
$DownloadRoot = 'D:\Temp\ecu-release-toolchain-bootstrap'
$env:TEMP = $DownloadRoot
$env:TMP = $DownloadRoot
New-Item -ItemType Directory -Force -Path $DownloadRoot | Out-Null

function Assert-FileIntegrity {
    param(
        [Parameter(Mandatory)][string]$LiteralPath,
        [Nullable[long]]$ExpectedSize,
        [Parameter(Mandatory)][string]$ExpectedSha256
    )
    $File = Get-Item -LiteralPath $LiteralPath -ErrorAction Stop
    if ($null -ne $ExpectedSize -and $File.Length -ne $ExpectedSize) {
        throw "$($File.Name) size mismatch: expected $ExpectedSize, actual $($File.Length)"
    }
    $ActualHash = (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualHash -ne $ExpectedSha256) {
        throw "$($File.Name) SHA256 mismatch: expected $ExpectedSha256, actual $ActualHash"
    }
}

function Assert-AuthenticodeValid {
    param([Parameter(Mandatory)][string]$LiteralPath)
    $Signature = Get-AuthenticodeSignature -LiteralPath $LiteralPath
    if ($Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "Authenticode validation failed for ${LiteralPath}: $($Signature.Status)"
    }
}

function Get-VerifiedDownload {
    param(
        [Parameter(Mandatory)]$Item,
        [Parameter(Mandatory)][string]$FileName,
        [switch]$RequireAuthenticode
    )
    $Destination = Join-Path $DownloadRoot $FileName
    if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
        Invoke-WebRequest -Uri $Item.url -OutFile $Destination -UseBasicParsing
    }
    $ExpectedSize = if ($null -eq $Item.PSObject.Properties['size']) { $null } else { [long]$Item.size }
    Assert-FileIntegrity -LiteralPath $Destination -ExpectedSize $ExpectedSize -ExpectedSha256 $Item.sha256
    if ($RequireAuthenticode) {
        Assert-AuthenticodeValid -LiteralPath $Destination
    }
    return $Destination
}

$PythonExe = Join-Path $Contract.python.installDir 'python.exe'
$SkipPythonInstall = $env:GITHUB_ACTIONS -eq 'true'
if (-not $SkipPythonInstall) {
    $PythonInstaller = Get-VerifiedDownload -Item $Contract.python `
        -FileName 'python-3.11.9-amd64.exe' -RequireAuthenticode
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        $PythonInstall = Start-Process -FilePath $PythonInstaller -ArgumentList @(
            '/quiet', 'InstallAllUsers=0', "TargetDir=$($Contract.python.installDir)",
            'Include_launcher=0', 'Include_test=0', 'PrependPath=0', 'Include_pip=1',
            '/log', (Join-Path $DownloadRoot 'python-install.log')
        ) -Wait -PassThru -WindowStyle Hidden
        if ($PythonInstall.ExitCode -ne 0) {
            throw "Python installer failed with exit code $($PythonInstall.ExitCode)"
        }
    }
    $PythonVersion = & $PythonExe -c 'import platform; print(platform.python_version())'
    if ($LASTEXITCODE -ne 0 -or $PythonVersion.Trim() -ne $Contract.python.version) {
        throw "Python version mismatch: expected $($Contract.python.version), actual $PythonVersion"
    }
}

$IsccPath = Join-Path $Contract.innoSetup.installDir 'ISCC.exe'
$InnoInstaller = Get-VerifiedDownload -Item $Contract.innoSetup `
    -FileName 'innosetup-6.7.3.exe' -RequireAuthenticode
if (-not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    $InnoInstall = Start-Process -FilePath $InnoInstaller -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
        "/DIR=$($Contract.innoSetup.installDir)",
        "/LOG=$(Join-Path $DownloadRoot 'inno-install.log')"
    ) -Wait -PassThru -WindowStyle Hidden
    if ($InnoInstall.ExitCode -ne 0) {
        throw "Inno Setup installer failed with exit code $($InnoInstall.ExitCode)"
    }
}
Assert-FileIntegrity -LiteralPath $IsccPath -ExpectedSize $null `
    -ExpectedSha256 $Contract.innoSetup.isccSha256
$InnoUninstaller = Join-Path $Contract.innoSetup.installDir 'unins000.exe'
$InnoVersion = (Get-Item -LiteralPath $InnoUninstaller -ErrorAction Stop).VersionInfo.ProductVersion.Trim()
if ($InnoVersion -ne $Contract.innoSetup.version) {
    throw "Inno Setup version mismatch: expected $($Contract.innoSetup.version), actual $InnoVersion"
}

$GitleaksExe = Join-Path $Contract.gitleaks.installDir 'gitleaks.exe'
$GitleaksArchive = Get-VerifiedDownload -Item $Contract.gitleaks `
    -FileName 'gitleaks_8.30.1_windows_x64.zip'
if (-not (Test-Path -LiteralPath $GitleaksExe -PathType Leaf)) {
    New-Item -ItemType Directory -Force -Path $Contract.gitleaks.installDir | Out-Null
    Expand-Archive -LiteralPath $GitleaksArchive -DestinationPath $Contract.gitleaks.installDir -Force
}
$GitleaksVersion = & $GitleaksExe version
if ($LASTEXITCODE -ne 0 -or $GitleaksVersion.Trim() -ne $Contract.gitleaks.version) {
    throw "Gitleaks version mismatch: expected $($Contract.gitleaks.version), actual $GitleaksVersion"
}

[pscustomobject]@{
    Python = if ($SkipPythonInstall) { 'GitHub runner Python (local install skipped)' } else { $PythonExe }
    InnoSetup = $IsccPath
    Gitleaks = $GitleaksExe
}
