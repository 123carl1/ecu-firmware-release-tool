#requires -Version 5.1

param(
    [string]$InstallDir,
    [int]$ExcludedPid = 0,
    [string]$CandidateVersion,
    [string]$InstalledVersion,
    [ValidateSet('Registry', 'PeFile')]
    [string]$InstalledVersionSource = 'Registry',
    [switch]$AutoUpdate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-ProcessGuardResult {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ExitCode,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    [pscustomobject]@{
        ExitCode = $ExitCode
        Message = $Message
    }
}

function Test-EcuReleaseProcessPath {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$ProcessRecords,

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$InstallDir,

        [int]$ExcludedPid = 0
    )

    try {
        $normalizedInstallDir = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')
        if ([string]::IsNullOrWhiteSpace($normalizedInstallDir)) {
            throw '安装目录为空。'
        }
        $installPrefix = $normalizedInstallDir + [System.IO.Path]::DirectorySeparatorChar
    }
    catch {
        return New-ProcessGuardResult -ExitCode 11 -Message "无法规范化安装目录：$($_.Exception.Message)"
    }

    foreach ($process in $ProcessRecords) {
        $processName = [string]$process.ProcessName
        if (($processName -ine 'EcuReleaseTool') -and ($processName -ine 'EcuReleaseCLI')) {
            continue
        }

        $processId = [int]$process.Id
        if (($ExcludedPid -gt 0) -and ($processId -eq $ExcludedPid)) {
            continue
        }

        try {
            $processPath = [string]$process.Path
            if ([string]::IsNullOrWhiteSpace($processPath)) {
                throw '进程路径为空。'
            }
            $normalizedProcessPath = [System.IO.Path]::GetFullPath($processPath)
        }
        catch {
            return New-ProcessGuardResult -ExitCode 11 -Message "无法查询进程 PID=$processId 的可执行文件路径：$($_.Exception.Message)"
        }

        if ($normalizedProcessPath.StartsWith(
                $installPrefix,
                [System.StringComparison]::OrdinalIgnoreCase)) {
            $fileName = [System.IO.Path]::GetFileName($normalizedProcessPath)
            return New-ProcessGuardResult -ExitCode 10 -Message "安装目录内仍有进程运行：PID=$processId，文件=$fileName"
        }
    }

    return New-ProcessGuardResult -ExitCode 0 -Message '未发现安装目录内运行中的 ECU 发布工具进程。'
}

function ConvertTo-EcuReleaseVersionComponents {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Version,

        [Parameter(Mandatory = $true)]
        [ValidateSet('Registry', 'PeFile')]
        [string]$Source
    )

    $componentPattern = '(0|[1-9][0-9]{0,4})'
    if ($Source -eq 'Registry') {
        $pattern = "^(?<major>$componentPattern)\.(?<minor>$componentPattern)\.(?<patch>$componentPattern)$"
    }
    else {
        $pattern = "^(?<major>$componentPattern)\.(?<minor>$componentPattern)\.(?<patch>$componentPattern)\.0$"
    }

    $match = [regex]::Match($Version, $pattern)
    if (-not $match.Success) {
        return $null
    }

    $major = [uint32]$match.Groups['major'].Value
    $minor = [uint32]$match.Groups['minor'].Value
    $patch = [uint32]$match.Groups['patch'].Value
    if (($major -gt 65535) -or ($minor -gt 65535) -or ($patch -gt 65535)) {
        return $null
    }

    return [pscustomobject]@{
        Major = $major
        Minor = $minor
        Patch = $patch
    }
}

function Test-EcuReleaseVersionPolicy {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CandidateVersion,

        [Parameter(Mandatory = $true)]
        [string]$InstalledVersion,

        [Parameter(Mandatory = $true)]
        [ValidateSet('Registry', 'PeFile')]
        [string]$InstalledVersionSource,

        [switch]$AutoUpdate
    )

    $candidate = ConvertTo-EcuReleaseVersionComponents `
        -Version $CandidateVersion `
        -Source 'Registry'
    $installed = ConvertTo-EcuReleaseVersionComponents `
        -Version $InstalledVersion `
        -Source $InstalledVersionSource
    if (($null -eq $candidate) -or ($null -eq $installed)) {
        return New-ProcessGuardResult -ExitCode 12 -Message '安装包版本或已安装版本格式无效。'
    }

    $comparison = 0
    if ($installed.Major -ne $candidate.Major) {
        $comparison = $installed.Major.CompareTo($candidate.Major)
    }
    elseif ($installed.Minor -ne $candidate.Minor) {
        $comparison = $installed.Minor.CompareTo($candidate.Minor)
    }
    elseif ($installed.Patch -ne $candidate.Patch) {
        $comparison = $installed.Patch.CompareTo($candidate.Patch)
    }

    if ($comparison -gt 0) {
        return New-ProcessGuardResult -ExitCode 13 -Message '已安装版本高于当前安装包。'
    }
    if (($comparison -eq 0) -and $AutoUpdate) {
        return New-ProcessGuardResult -ExitCode 14 -Message '自动更新不允许重复安装相同版本。'
    }

    return New-ProcessGuardResult -ExitCode 0 -Message '允许安装。'
}

function Invoke-EcuReleaseProcessGuard {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$InstallDir,

        [int]$ExcludedPid = 0
    )

    try {
        $processes = @(
            Get-Process -Name 'EcuReleaseTool', 'EcuReleaseCLI' -ErrorAction SilentlyContinue |
                ForEach-Object {
                    try {
                        $path = $_.Path
                    }
                    catch {
                        $path = $null
                    }
                    [pscustomobject]@{
                        Id = $_.Id
                        ProcessName = $_.ProcessName
                        Path = $path
                    }
                }
        )
    }
    catch {
        return New-ProcessGuardResult -ExitCode 11 -Message "无法枚举 ECU 发布工具进程：$($_.Exception.Message)"
    }

    return Test-EcuReleaseProcessPath `
        -ProcessRecords $processes `
        -InstallDir $InstallDir `
        -ExcludedPid $ExcludedPid
}

if ($MyInvocation.InvocationName -ne '.') {
    if (-not [string]::IsNullOrWhiteSpace($CandidateVersion)) {
        if ([string]::IsNullOrWhiteSpace($InstalledVersion)) {
            Write-Output '缺少 -InstalledVersion 参数。'
            exit 12
        }
        $guardResult = Test-EcuReleaseVersionPolicy `
            -CandidateVersion $CandidateVersion `
            -InstalledVersion $InstalledVersion `
            -InstalledVersionSource $InstalledVersionSource `
            -AutoUpdate:$AutoUpdate
    }
    else {
        if ([string]::IsNullOrWhiteSpace($InstallDir)) {
            Write-Output '缺少 -InstallDir 参数。'
            exit 11
        }

        $guardResult = Invoke-EcuReleaseProcessGuard `
            -InstallDir $InstallDir `
            -ExcludedPid $ExcludedPid
    }
    Write-Output $guardResult.Message
    exit $guardResult.ExitCode
}
