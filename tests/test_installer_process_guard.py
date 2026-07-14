import base64
import os
import subprocess
from pathlib import Path

import pytest


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


def _invoke_version_policy(
    candidate: str,
    installed: str,
    source: str,
    *,
    auto_update: bool = False,
):
    arguments = [
        "pwsh.exe",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(SCRIPT),
        "-CandidateVersion",
        candidate,
        "-InstalledVersion",
        installed,
        "-InstalledVersionSource",
        source,
    ]
    if auto_update:
        arguments.append("-AutoUpdate")
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )


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


def test_script_runs_under_windows_powershell_51(tmp_path):
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-InstallDir",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="ascii",
        errors="replace",
        timeout=15,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("candidate", "installed", "source", "auto_update", "expected_exit"),
    [
        ("0.2.0", "0.1.0", "Registry", False, 0),
        ("0.2.0", "0.3.0", "Registry", False, 13),
        ("0.2.0", "0.2.0", "Registry", True, 14),
        ("0.2.0", "0.2.0", "Registry", False, 0),
        ("1.2.4", "1.2.3", "Registry", False, 0),
        ("1.2.4", "1.2.3.0", "PeFile", False, 0),
    ],
)
def test_version_policy_acceptance_matrix(
    candidate: str,
    installed: str,
    source: str,
    auto_update: bool,
    expected_exit: int,
):
    result = _invoke_version_policy(
        candidate,
        installed,
        source,
        auto_update=auto_update,
    )

    assert result.returncode == expected_exit, result.stderr


@pytest.mark.parametrize(
    ("candidate", "installed", "source"),
    [
        ("0.2.0", "01.2.3", "Registry"),
        ("0.2.0", "-1.2.3", "Registry"),
        ("0.2.0", "65536.2.3", "Registry"),
        ("01.2.3", "0.2.0", "Registry"),
        ("0.2.0", "1.2.3.1", "PeFile"),
        ("0.2.0", "1.2.3.65535", "PeFile"),
        ("0.2.0", "1.2.3", "PeFile"),
        ("0.2.0", "1.2.3.0", "Registry"),
    ],
)
def test_version_policy_rejects_noncanonical_versions(
    candidate: str,
    installed: str,
    source: str,
):
    result = _invoke_version_policy(candidate, installed, source)

    assert result.returncode == 12, result.stderr
