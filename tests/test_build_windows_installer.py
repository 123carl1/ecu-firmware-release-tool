from pathlib import Path


SCRIPT = Path("scripts/build_windows_installer.ps1")
BOOTSTRAP = Path("scripts/bootstrap_release_tools.ps1")


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_windows_build_uses_only_explicit_pinned_tools():
    text = _script_text()

    assert "[string]$PythonPath = 'D:\\software\\Python311\\python.exe'" in text
    assert "[string]$IsccPath = 'D:\\software\\InnoSetup\\6.7.3\\ISCC.exe'" in text
    assert "3.11.9" in text
    assert "pip check" in text
    assert "--require-hashes" in text
    assert "--no-deps" in text
    assert "--no-build-isolation" in text


def test_windows_build_embeds_version_identity_keys_and_metadata():
    text = _script_text()

    assert "prepare-build" in text
    assert "fetch-usb2xxx" in text
    assert "audit-build" in text
    assert text.count("--version-file") >= 2
    assert "_tool_build_identity.json" in text
    assert "release_public_keys.json" in text
    assert "--copy-metadata" in text
    assert "unified-can-lin-host-tool" in text
    assert text.index("prepare-build") < text.index("pip install --no-deps")


def test_windows_build_supplies_source_version_to_inno():
    text = _script_text()

    assert "/DMyAppVersion=$Version" in text
    assert "EcuReleaseTool_Setup_$Version.exe" in text
    assert "release-audit.json" in text
    assert "PYINSTALLER_CONFIG_DIR" in text
    assert "D:\\Temp\\ecu-release-task8" in text
    assert "PIP_CACHE_DIR" in text
    assert "D:\\software\\pip-cache\\ecu-release-tool" in text


def test_bootstrap_waits_for_installers_and_passes_expanded_target_directories():
    text = BOOTSTRAP.read_text(encoding="utf-8")

    assert text.count("Start-Process") >= 2
    assert "-Wait -PassThru -WindowStyle Hidden" in text
    assert '"TargetDir=$($Contract.python.installDir)"' in text
    assert '"/DIR=$($Contract.innoSetup.installDir)"' in text
    assert "unins000.exe" in text
    assert "PSObject.Properties['size']" in text
    assert text.index("$PythonInstaller = Get-VerifiedDownload") < text.index(
        "if (-not (Test-Path -LiteralPath $PythonExe"
    )
    assert text.index("$InnoInstaller = Get-VerifiedDownload") < text.index(
        "if (-not (Test-Path -LiteralPath $IsccPath"
    )
    assert text.index("$GitleaksArchive = Get-VerifiedDownload") < text.index(
        "if (-not (Test-Path -LiteralPath $GitleaksExe"
    )
