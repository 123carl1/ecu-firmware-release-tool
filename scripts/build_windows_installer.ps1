#requires -Version 7.0
[CmdletBinding()]
param(
    [switch]$ValidateUsb2xxxRuntimeOnly,
    [string]$Usb2xxxSdkRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

function Assert-Usb2xxxRuntimeFile {
    param(
        [Parameter(Mandatory)]
        [string]$LiteralPath,
        [Parameter(Mandatory)]
        [long]$ExpectedSize,
        [Parameter(Mandatory)]
        [string]$ExpectedSha256
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

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Dist = Join-Path $Root 'dist\windows'
$Build = Join-Path $Root 'build\pyinstaller'
$UsbSdkCandidates = @(
    $Usb2xxxSdkRoot,
    $env:USB2XXX_SDK_ROOT,
    (Join-Path (Split-Path $Root -Parent) '_ext\usb2can_lin_pwm_example'),
    (Join-Path (Split-Path (Split-Path (Split-Path $Root -Parent) -Parent) -Parent) '_ext\usb2can_lin_pwm_example')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) }
$UsbSdkRoot = $UsbSdkCandidates | Select-Object -First 1
if ($null -eq $UsbSdkRoot) {
    throw 'USB2XXX SDK not found. Set USB2XXX_SDK_ROOT before building.'
}
$UsbDllDir = Join-Path $UsbSdkRoot 'sdk\libs\windows\x86_64'
$UsbDll = Join-Path $UsbDllDir 'USB2XXX.dll'
$LibusbDll = Join-Path $UsbDllDir 'libusb-1.0.dll'
if (-not (Test-Path -LiteralPath $UsbDll -PathType Leaf) -or
    -not (Test-Path -LiteralPath $LibusbDll -PathType Leaf)) {
    throw "USB2XXX runtime DLLs not found under $UsbDllDir"
}
Assert-Usb2xxxRuntimeFile -LiteralPath $UsbDll `
    -ExpectedSize 538112 `
    -ExpectedSha256 '7857f3c43b5f5f41414da0ce04f2914d45af805a7ad0e14a0aa84b6a16a42d1b'
Assert-Usb2xxxRuntimeFile -LiteralPath $LibusbDll `
    -ExpectedSize 157696 `
    -ExpectedSha256 'a8c91f0ff68fb7802a9f4416728f0eeb4d99af4ceaa4ef7dfe9374e76e375018'
if ($ValidateUsb2xxxRuntimeOnly) {
    Write-Output "USB2XXX runtime validation passed: $UsbDllDir"
    return
}
$Iscc = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if ($null -eq $Iscc) {
    throw 'Inno Setup 6 ISCC.exe not found.'
}

New-Item -ItemType Directory -Force -Path $Dist, $Build | Out-Null
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name EcuReleaseTool --paths (Join-Path $Root 'src') `
    --distpath $Dist --workpath (Join-Path $Build 'gui') `
    --specpath $Build (Join-Path $Root 'src\unified_can_lin_host_tool\cli\ui.py')
python -m PyInstaller --noconfirm --clean --onefile --console `
    --name EcuReleaseCLI --paths (Join-Path $Root 'src') `
    --add-binary "$UsbDll;." --add-binary "$LibusbDll;." `
    --distpath $Dist --workpath (Join-Path $Build 'cli') `
    --specpath $Build (Join-Path $Root 'src\unified_can_lin_host_tool\cli\release.py')
& $Iscc (Join-Path $Root 'installer\EcuReleaseTool.iss')

Get-Item (Join-Path $Dist 'EcuReleaseTool.exe'),
         (Join-Path $Dist 'EcuReleaseCLI.exe'),
         (Join-Path $Root 'dist\installer\EcuReleaseTool_Setup_0.1.0.exe') |
    Select-Object FullName, Length, LastWriteTime
