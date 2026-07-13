from pathlib import Path

from unified_can_lin_host_tool.cli.release import build_parser, main


def test_flash_execution_modes_are_mutually_exclusive():
    parser = build_parser()
    try:
        parser.parse_args(["flash", "x.erel", "--project", "AS5PR", "--offline-dry-run", "--real-flash"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("conflicting modes were accepted")


def test_offline_dry_run_does_not_open_hardware(monkeypatch, tmp_path: Path):
    package = object()
    monkeypatch.setattr("unified_can_lin_host_tool.cli.release.load_verified_release_package", lambda *_: package)
    monkeypatch.setattr("unified_can_lin_host_tool.cli.release._summary", lambda _: {"ok": True})
    monkeypatch.setattr("unified_can_lin_host_tool.cli.release._open_transport",
                        lambda *_: (_ for _ in ()).throw(AssertionError("hardware opened")))
    assert main(["flash", str(tmp_path / "x.erel"), "--project", "AS5PR", "--offline-dry-run"]) == 0
