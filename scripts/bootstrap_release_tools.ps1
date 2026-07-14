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
$env:PIP_CACHE_DIR = 'D:\software\pip-cache\ecu-release-tool'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONPATH = (Join-Path $Root 'src')
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

function Test-FileHash {
    param(
        [Parameter(Mandatory)][string]$LiteralPath,
        [Parameter(Mandatory)][string]$ExpectedSha256
    )
    if (-not (Test-Path -LiteralPath $LiteralPath -PathType Leaf)) {
        return $false
    }
    return (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $ExpectedSha256
}

function Remove-VerifiedDirectory {
    param([Parameter(Mandatory)][string]$LiteralPath)
    if (-not (Test-Path -LiteralPath $LiteralPath)) {
        return
    }
    $Resolved = (Resolve-Path -LiteralPath $LiteralPath).Path
    if (-not $Resolved.StartsWith('D:\Temp\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove non-temporary directory: $Resolved"
    }
    Remove-Item -LiteralPath $Resolved -Recurse -Force
}

$PythonExe = Join-Path $Contract.python.installDir 'python.exe'
$SkipPythonInstall = $env:GITHUB_ACTIONS -eq 'true'
if (-not $SkipPythonInstall) {
    $PythonInstaller = Get-VerifiedDownload -Item $Contract.python `
        -FileName 'python-3.11.9-amd64.exe' -RequireAuthenticode
    $PythonValid = Test-FileHash -LiteralPath $PythonExe `
        -ExpectedSha256 $Contract.python.executableSha256
    if ($PythonValid) {
        $ExistingPythonVersion = & $PythonExe -I -c 'import platform; print(platform.python_version())'
        $PythonValid = $LASTEXITCODE -eq 0 -and $ExistingPythonVersion.Trim() -eq $Contract.python.version
    }
    if (-not $PythonValid) {
        $InstallMode = if (Test-Path -LiteralPath $PythonExe -PathType Leaf) { '/repair' } else { $null }
        $PythonArguments = @(
            $InstallMode, '/quiet', 'InstallAllUsers=0', "TargetDir=$($Contract.python.installDir)",
            'Include_launcher=0', 'Include_test=0', 'PrependPath=0', 'Include_pip=1',
            '/log', (Join-Path $DownloadRoot 'python-install.log')
        ) | Where-Object { $_ }
        $PythonInstall = Start-Process -FilePath $PythonInstaller `
            -ArgumentList $PythonArguments -Wait -PassThru -WindowStyle Hidden
        if ($PythonInstall.ExitCode -ne 0) {
            throw "Python installer failed with exit code $($PythonInstall.ExitCode)"
        }
    }
    $PythonVersion = & $PythonExe -c 'import platform; print(platform.python_version())'
    if ($LASTEXITCODE -ne 0 -or $PythonVersion.Trim() -ne $Contract.python.version) {
        throw "Python version mismatch: expected $($Contract.python.version), actual $PythonVersion"
    }
    Assert-FileIntegrity -LiteralPath $PythonExe -ExpectedSize $null `
        -ExpectedSha256 $Contract.python.executableSha256
}
$BootstrapPython = if ($SkipPythonInstall) { (Get-Command python.exe -ErrorAction Stop).Source } else { $PythonExe }
$BootstrapPythonVersion = & $BootstrapPython -I -c 'import platform; print(platform.python_version())'
if ($LASTEXITCODE -ne 0 -or $BootstrapPythonVersion.Trim() -ne $Contract.python.version) {
    throw "Bootstrap Python version mismatch: expected $($Contract.python.version), actual $BootstrapPythonVersion"
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
$GitleaksValid = Test-FileHash -LiteralPath $GitleaksExe `
    -ExpectedSha256 $Contract.gitleaks.executableSha256
if (-not $GitleaksValid) {
    New-Item -ItemType Directory -Force -Path $Contract.gitleaks.installDir | Out-Null
    Expand-Archive -LiteralPath $GitleaksArchive -DestinationPath $Contract.gitleaks.installDir -Force
}
Assert-FileIntegrity -LiteralPath $GitleaksExe -ExpectedSize $null `
    -ExpectedSha256 $Contract.gitleaks.executableSha256
$GitleaksVersion = & $GitleaksExe version
if ($LASTEXITCODE -ne 0 -or $GitleaksVersion.Trim() -ne $Contract.gitleaks.version) {
    throw "Gitleaks version mismatch: expected $($Contract.gitleaks.version), actual $GitleaksVersion"
}

$LockFile = Join-Path $Root 'requirements-release.lock'
$LockHash = (Get-FileHash -LiteralPath $LockFile -Algorithm SHA256).Hash.ToLowerInvariant()
$Wheelhouse = "D:\software\EcuReleaseTool\wheelhouse\$LockHash"
$WheelValidationVenv = Join-Path $DownloadRoot 'wheelhouse-validation'

function New-ReleaseWheelhouse {
    if (Test-Path -LiteralPath $Wheelhouse) {
        $ResolvedWheelhouse = (Resolve-Path -LiteralPath $Wheelhouse).Path
        if (-not $ResolvedWheelhouse.StartsWith('D:\software\EcuReleaseTool\wheelhouse\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unexpected wheelhouse path: $ResolvedWheelhouse"
        }
        Remove-Item -LiteralPath $ResolvedWheelhouse -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Wheelhouse | Out-Null
    & $BootstrapPython -I -m pip download --only-binary=:all: --require-hashes `
        --dest $Wheelhouse -r $LockFile
}

function Test-ReleaseWheelhouse {
    try {
        Remove-VerifiedDirectory -LiteralPath $WheelValidationVenv
        & $BootstrapPython -I -m venv $WheelValidationVenv
        $ValidationPython = Join-Path $WheelValidationVenv 'Scripts\python.exe'
        & $ValidationPython -I -m pip install --no-index --find-links $Wheelhouse `
            --require-hashes -r $LockFile | Out-Host
        & $ValidationPython -I -m pip check | Out-Host
        & $BootstrapPython (Join-Path $Root 'scripts\release_build.py') audit-environment `
            --python $ValidationPython --lock $LockFile `
            --output (Join-Path $Wheelhouse 'wheelhouse-audit.json') | Out-Host
        return $true
    }
    catch {
        Write-Warning "Wheelhouse validation failed: $($_.Exception.Message)"
        return $false
    }
    finally {
        Remove-VerifiedDirectory -LiteralPath $WheelValidationVenv
    }
}

if (-not (Test-Path -LiteralPath $Wheelhouse -PathType Container) -or
    -not (Test-ReleaseWheelhouse)) {
    New-ReleaseWheelhouse
    if (-not (Test-ReleaseWheelhouse)) {
        throw 'Wheelhouse remains invalid after a verified re-download.'
    }
}

$UsbSource = Get-Content -LiteralPath (Join-Path $Root 'third_party\usb2xxx_runtime_source.json') -Raw | ConvertFrom-Json
$UsbRuntime = Join-Path $Root "build\third_party\usb2xxx\$($UsbSource.commit)"
& $BootstrapPython (Join-Path $Root 'scripts\release_build.py') fetch-usb2xxx `
    --source-file (Join-Path $Root 'third_party\usb2xxx_runtime_source.json') `
    --output-dir $UsbRuntime

[pscustomobject]@{
    Python = if ($SkipPythonInstall) { 'GitHub runner Python (local install skipped)' } else { $PythonExe }
    InnoSetup = $IsccPath
    Gitleaks = $GitleaksExe
    Wheelhouse = $Wheelhouse
    Usb2xxxRuntime = $UsbRuntime
}
