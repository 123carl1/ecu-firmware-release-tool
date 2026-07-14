from pathlib import Path


INSTALLER = Path("installer/EcuReleaseTool.iss")


def _installer_text() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_installer_requires_build_supplied_version():
    text = _installer_text()

    assert "#ifndef MyAppVersion" in text
    assert "#error MyAppVersion" in text
    assert '#define MyAppVersion "0.1.0"' not in text
    assert "ignoreversion" not in text
    assert "VersionInfoVersion={#MyAppVersion}.0" in text
    assert "OutputBaseFilename=EcuReleaseTool_Setup_{#MyAppVersion}" in text


def test_setup_disables_automatic_process_termination_and_restart():
    text = _installer_text()

    assert "UninstallDisplayName={#MyAppName}" in text
    assert "CloseApplications=no" in text
    assert "RestartApplications=no" in text


def test_auto_update_run_entry_is_not_skipped_when_silent():
    text = _installer_text()

    assert "Check: not IsAutoUpdate" in text
    assert "Flags: nowait skipifnotsilent; Check: IsAutoUpdate" in text
    assert "CheckForMutexes" in text
    assert "PARENT_PID" in text


def test_prepare_to_install_waits_before_mutex_and_process_checks():
    text = _installer_text()
    body = text.split("function PrepareToInstall", maxsplit=1)[1]

    wait_position = body.index("WaitForParentProcess")
    mutex_position = body.index("CheckForMutexes")
    process_position = body.index("RunProcessGuard")
    version_position = body.index("CheckInstalledVersion")

    assert wait_position < mutex_position < process_position < version_position
    assert "60000" in text
    assert "Local\\EcuFirmwareReleaseTool.Run" in text


def test_auto_update_arguments_are_strictly_validated():
    text = _installer_text()

    assert "AUTO_UPDATE" in text
    assert "PARENT_PID" in text
    assert "ParentPid <= 0" in text
    assert "自动更新缺少有效的父进程 PID" in text


def test_installed_version_policy_rejects_downgrade_and_auto_reinstall():
    text = _installer_text()

    assert "RunVersionPolicy" in text
    assert "InstalledVersionSource" in text
    assert "不允许降级安装" in text
    assert "自动更新不允许重复安装相同版本" in text


def test_uninstall_uses_mutex_and_process_path_guards():
    text = _installer_text()

    assert "InitializeUninstall" in text
    assert "CheckForMutexes" in text
    assert "RunProcessGuard" in text


def test_process_guard_uses_installed_script_during_uninstall():
    text = _installer_text()
    body = text.split("function RunProcessGuard", maxsplit=1)[1]

    assert "InstalledScriptPath" in body
    assert "ExpandConstant('{app}\\check_running_processes.ps1')" in body
    assert "ExtractTemporaryFile('check_running_processes.ps1')" in body
