import base64
import subprocess
from pathlib import Path


SCRIPT = Path("installer/check_running_processes.ps1").resolve()


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        [
            "pwsh.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )


def _invoke_guard(records: str, install_dir: str, excluded_pid: int = 0):
    command = rf"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
. '{SCRIPT}'
$records = @({records})
$result = Test-EcuReleaseProcessPath `
    -ProcessRecords $records `
    -InstallDir '{install_dir}' `
    -ExcludedPid {excluded_pid}
$result.Message
exit $result.ExitCode
"""
    return _run_powershell(command)


def test_process_inside_install_directory_returns_10():
    result = _invoke_guard(
        "[pscustomobject]@{ Id = 123; ProcessName = 'EcuReleaseTool'; "
        "Path = 'C:\\Tools\\EcuReleaseTool\\EcuReleaseTool.exe' }",
        "C:\\Tools\\EcuReleaseTool",
    )

    assert result.returncode == 10
    assert "123" in result.stdout
    assert "EcuReleaseTool.exe" in result.stdout


def test_same_named_process_outside_install_directory_is_allowed():
    result = _invoke_guard(
        "[pscustomobject]@{ Id = 124; ProcessName = 'EcuReleaseTool'; "
        "Path = 'C:\\Other\\EcuReleaseTool.exe' }",
        "C:\\Tools\\EcuReleaseTool",
    )

    assert result.returncode == 0


def test_path_comparison_ignores_case_and_trailing_backslash():
    result = _invoke_guard(
        "[pscustomobject]@{ Id = 125; ProcessName = 'EcuReleaseCLI'; "
        "Path = 'c:\\TOOLS\\ECURELEASETOOL\\EcuReleaseCLI.exe' }",
        "C:\\Tools\\EcuReleaseTool\\",
    )

    assert result.returncode == 10


def test_directory_prefix_without_separator_is_not_a_match():
    result = _invoke_guard(
        "[pscustomobject]@{ Id = 126; ProcessName = 'EcuReleaseTool'; "
        "Path = 'C:\\Tools\\EcuReleaseTool-Old\\EcuReleaseTool.exe' }",
        "C:\\Tools\\EcuReleaseTool",
    )

    assert result.returncode == 0


def test_excluded_pid_is_ignored():
    result = _invoke_guard(
        "[pscustomobject]@{ Id = 127; ProcessName = 'EcuReleaseTool'; "
        "Path = 'C:\\Tools\\EcuReleaseTool\\EcuReleaseTool.exe' }",
        "C:\\Tools\\EcuReleaseTool",
        excluded_pid=127,
    )

    assert result.returncode == 0


def test_missing_process_path_returns_query_failure_11():
    result = _invoke_guard(
        "[pscustomobject]@{ Id = 128; ProcessName = 'EcuReleaseTool'; Path = $null }",
        "C:\\Tools\\EcuReleaseTool",
    )

    assert result.returncode == 11
    assert "128" in result.stdout


def test_unrelated_process_name_is_ignored():
    result = _invoke_guard(
        "[pscustomobject]@{ Id = 129; ProcessName = 'python'; "
        "Path = 'C:\\Tools\\EcuReleaseTool\\python.exe' }",
        "C:\\Tools\\EcuReleaseTool",
    )

    assert result.returncode == 0


def test_script_never_terminates_processes():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "Stop-Process" not in text
