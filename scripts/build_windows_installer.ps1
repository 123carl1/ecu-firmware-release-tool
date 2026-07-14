#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$PythonPath = 'D:\software\Python311\python.exe',
    [string]$IsccPath = 'D:\software\InnoSetup\6.7.3\ISCC.exe',
    [switch]$ValidateUsb2xxxRuntimeOnly,
    [string]$Usb2xxxSdkRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Dist = Join-Path $Root 'dist\windows'
$InstallerDist = Join-Path $Root 'dist\installer'
$Build = Join-Path $Root 'build\pyinstaller'
$ReleaseBuild = Join-Path $Root 'build\release'
$Contract = Get-Content -LiteralPath (Join-Path $Root 'release_toolchain.json') -Raw | ConvertFrom-Json
$SourceContract = Join-Path $Root 'third_party\usb2xxx_runtime_source.json'
$Source = Get-Content -LiteralPath $SourceContract -Raw | ConvertFrom-Json
$UsbRuntime = Join-Path $Root "build\third_party\usb2xxx\$($Source.commit)"
$ExpectedUsb2xxxSha256 = '7857f3c43b5f5f41414da0ce04f2914d45af805a7ad0e14a0aa84b6a16a42d1b'
$ExpectedLibusbSha256 = 'a8c91f0ff68fb7802a9f4416728f0eeb4d99af4ceaa4ef7dfe9374e76e375018'
if ($Source.files.'USB2XXX.dll'.sha256 -ne $ExpectedUsb2xxxSha256 -or
    $Source.files.'libusb-1.0.dll'.sha256 -ne $ExpectedLibusbSha256) {
    throw 'USB2XXX source contract hashes do not match the pinned release contract.'
}

function Assert-FileIntegrity {
    param(
        [Parameter(Mandatory)][string]$LiteralPath,
        [Parameter(Mandatory)][long]$ExpectedSize,
        [Parameter(Mandatory)][string]$ExpectedSha256
    )
    $File = Get-Item -LiteralPath $LiteralPath -ErrorAction Stop
    if ($File.Length -ne $ExpectedSize) {
        throw "$($File.Name) size mismatch: expected $ExpectedSize, actual $($File.Length)"
    }
    $ActualSha256 = (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualSha256 -ne $ExpectedSha256) {
        throw "$($File.Name) SHA256 mismatch: expected $ExpectedSha256, actual $ActualSha256"
    }
}

function Get-Usb2xxxRuntimeFileValidationError {
    param(
        [Parameter(Mandatory)][string]$LiteralPath,
        [Parameter(Mandatory)][long]$ExpectedSize,
        [Parameter(Mandatory)][string]$ExpectedSha256
    )
    try {
        Assert-FileIntegrity -LiteralPath $LiteralPath -ExpectedSize $ExpectedSize -ExpectedSha256 $ExpectedSha256
    }
    catch {
        return $_.Exception.Message
    }
}

if ($ValidateUsb2xxxRuntimeOnly) {
    if (-not $Usb2xxxSdkRoot) {
        throw 'ValidateUsb2xxxRuntimeOnly requires Usb2xxxSdkRoot.'
    }
    $DllDir = Join-Path $Usb2xxxSdkRoot $Source.runtimePath
    $Errors = @(
        Get-Usb2xxxRuntimeFileValidationError -LiteralPath (Join-Path $DllDir 'USB2XXX.dll') `
            -ExpectedSize $Source.files.'USB2XXX.dll'.size `
            -ExpectedSha256 $Source.files.'USB2XXX.dll'.sha256
        Get-Usb2xxxRuntimeFileValidationError -LiteralPath (Join-Path $DllDir 'libusb-1.0.dll') `
            -ExpectedSize $Source.files.'libusb-1.0.dll'.size `
            -ExpectedSha256 $Source.files.'libusb-1.0.dll'.sha256
    )
    if ($Errors.Count -gt 0) {
        throw ($Errors -join [Environment]::NewLine)
    }
    Write-Output "USB2XXX runtime validation passed: $DllDir"
    return
}

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Pinned Python not found: $PythonPath"
}
$env:PYTHONNOUSERSITE = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PYTHONPATH = (Join-Path $Root 'src')
$env:PYINSTALLER_CONFIG_DIR = 'D:\Temp\ecu-release-task8\pyinstaller-cache'
$env:PIP_CACHE_DIR = 'D:\software\pip-cache\ecu-release-tool'
$PythonVersion = & $PythonPath -c 'import platform; print(platform.python_version())'
if ($PythonVersion.Trim() -ne '3.11.9') {
    throw "Python version mismatch: expected 3.11.9, actual $PythonVersion"
}
if (-not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw "Pinned Inno Setup not found: $IsccPath"
}
$ActualIsccHash = (Get-FileHash -LiteralPath $IsccPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualIsccHash -ne $Contract.innoSetup.isccSha256) {
    throw "ISCC.exe SHA256 mismatch: expected $($Contract.innoSetup.isccSha256), actual $ActualIsccHash"
}

if ($env:GITHUB_ACTIONS -eq 'true') {
    if (-not $env:GITHUB_REPOSITORY -or -not $env:GITHUB_TOKEN) {
        throw 'Official build requires GITHUB_REPOSITORY and GITHUB_TOKEN.'
    }
    git -C $Root fetch --force --tags origin '+refs/heads/*:refs/remotes/origin/*'
    $Headers = @{ Authorization = "Bearer $env:GITHUB_TOKEN"; Accept = 'application/vnd.github+json' }
    $RepositoryInfo = Invoke-RestMethod -Headers $Headers `
        -Uri "https://api.github.com/repos/$env:GITHUB_REPOSITORY"
    $DefaultBranchRef = "refs/remotes/origin/$($RepositoryInfo.default_branch)"
    & $PythonPath (Join-Path $Root 'scripts\release_build.py') prepare-build `
        --repo $Root --output-dir $ReleaseBuild --default-branch-ref $DefaultBranchRef
}
else {
    & $PythonPath (Join-Path $Root 'scripts\release_build.py') prepare-build `
        --repo $Root --output-dir $ReleaseBuild
}

& $PythonPath -m pip install --require-hashes -r (Join-Path $Root 'requirements-release.lock')
& $PythonPath -m pip check
& $PythonPath -m pip install --no-deps --no-build-isolation (Join-Path $Root '.')
& $PythonPath -m pip check

& $PythonPath (Join-Path $Root 'scripts\release_build.py') fetch-usb2xxx `
    --source-file $SourceContract --output-dir $UsbRuntime

$Identity = Join-Path $ReleaseBuild '_tool_build_identity.json'
$GuiVersion = Join-Path $ReleaseBuild 'EcuReleaseTool.version.txt'
$CliVersion = Join-Path $ReleaseBuild 'EcuReleaseCLI.version.txt'
$PublicKeys = Join-Path $Root 'src\unified_can_lin_host_tool\update\release_public_keys.json'
$UsbDll = Join-Path $UsbRuntime 'USB2XXX.dll'
$LibusbDll = Join-Path $UsbRuntime 'libusb-1.0.dll'
$Version = (& $PythonPath -c "import tomllib; print(tomllib.load(open(r'$($Root.Replace("'", "''"))\pyproject.toml','rb'))['project']['version'])").Trim()

Remove-Item -LiteralPath $Dist, $Build, $InstallerDist -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Dist, $Build, $InstallerDist | Out-Null

& $PythonPath -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name EcuReleaseTool --paths (Join-Path $Root 'src') `
    --version-file $GuiVersion `
    --add-data "$Identity;unified_can_lin_host_tool" `
    --add-data "$PublicKeys;unified_can_lin_host_tool\update" `
    --copy-metadata unified-can-lin-host-tool `
    --distpath $Dist --workpath (Join-Path $Build 'gui') `
    --specpath $Build (Join-Path $Root 'src\unified_can_lin_host_tool\cli\ui.py')
& $PythonPath -m PyInstaller --noconfirm --clean --onefile --console `
    --name EcuReleaseCLI --paths (Join-Path $Root 'src') `
    --version-file $CliVersion `
    --add-data "$Identity;unified_can_lin_host_tool" `
    --add-data "$PublicKeys;unified_can_lin_host_tool\update" `
    --add-binary "$UsbDll;." --add-binary "$LibusbDll;." `
    --copy-metadata unified-can-lin-host-tool `
    --distpath $Dist --workpath (Join-Path $Build 'cli') `
    --specpath $Build (Join-Path $Root 'src\unified_can_lin_host_tool\cli\release.py')

Copy-Item -LiteralPath $Identity -Destination (Join-Path $Dist '_tool_build_identity.json')
Copy-Item -LiteralPath (Join-Path $Root 'THIRD_PARTY_NOTICES.txt') -Destination $Dist

foreach ($Executable in @('EcuReleaseTool.exe', 'EcuReleaseCLI.exe')) {
    $VersionInfo = (Get-Item -LiteralPath (Join-Path $Dist $Executable)).VersionInfo
    if ($VersionInfo.FileVersion -ne "$Version.0" -or $VersionInfo.ProductVersion -ne $Version) {
        throw "$Executable PE version mismatch: FileVersion=$($VersionInfo.FileVersion), ProductVersion=$($VersionInfo.ProductVersion)"
    }
}

& $IsccPath "/DMyAppVersion=$Version" (Join-Path $Root 'installer\EcuReleaseTool.iss')
$Installer = Join-Path $InstallerDist "EcuReleaseTool_Setup_$Version.exe"
& $PythonPath (Join-Path $Root 'scripts\release_build.py') audit-build `
    --dist-dir $Dist --installer $Installer --identity $Identity `
    --output (Join-Path $Root 'dist\release-audit.json')

Get-Item -LiteralPath (Join-Path $Dist 'EcuReleaseTool.exe'), `
    (Join-Path $Dist 'EcuReleaseCLI.exe'), $Installer, `
    (Join-Path $Root 'dist\release-audit.json') |
    Select-Object FullName, Length, LastWriteTime
