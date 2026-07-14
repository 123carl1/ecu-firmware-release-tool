#requires -Version 5.1

param(
    [string]$InstallDir,
    [int]$ExcludedPid = 0
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
    if ([string]::IsNullOrWhiteSpace($InstallDir)) {
        Write-Output '缺少 -InstallDir 参数。'
        exit 11
    }

    $guardResult = Invoke-EcuReleaseProcessGuard `
        -InstallDir $InstallDir `
        -ExcludedPid $ExcludedPid
    Write-Output $guardResult.Message
    exit $guardResult.ExitCode
}
