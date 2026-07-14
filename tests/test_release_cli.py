from pathlib import Path
from types import SimpleNamespace

import json

from unified_can_lin_host_tool.as5pr.ota_state_machine import OtaProgress, OtaResult, OtaResultStatus
from unified_can_lin_host_tool.adapters.tsmaster import TsmasterAdapter
from unified_can_lin_host_tool.adapters.usb2xxx import Usb2xxxAdapter
from unified_can_lin_host_tool.cli.release import _open_transport, build_parser, emit_progress_json, main


def test_cli_exposes_only_scan_and_native_ota_commands():
    parser = build_parser()
    assert parser.parse_args(["scan", "--project", "AS5PR"]).command == "scan"
    assert parser.parse_args([
        "ota", "app.hex", "--project", "AS5PR",
        "--confirm-project", "AS5PR", "--yes-i-know-this-erases-app",
    ]).command == "ota"
    for removed in ("inspect", "flash", "build-package", "probe"):
        try:
            parser.parse_args([removed])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"legacy command remains exposed: {removed}")


def test_scan_lists_tsmaster_devices_as_machine_readable_event(monkeypatch, capsys):
    class Device:
        device_name = "TC1016"
        product = "TOSUN HS CANFD4.LIN2"
        serial = "ABC123"
        manufacturer = "TOSUN"
        hw_subtype = 11
        device_index = 2
        can_channel_count = 4
        is_can_fd = True

    monkeypatch.setattr(
        "unified_can_lin_host_tool.cli.release.TsmasterAdapter.probe_can_devices",
        lambda **_: [Device()],
    )
    monkeypatch.setattr(
        "unified_can_lin_host_tool.cli.release.Usb2xxxAdapter.probe_can_devices",
        lambda **_: [],
    )

    assert main(["scan", "--project", "AS5PR"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["event"] == "scan_result"
    assert output["devices"][0]["name"] == "TC1016"
    assert output["devices"][0]["product"] == "TOSUN HS CANFD4.LIN2"
    assert output["devices"][0]["deviceIndex"] == 2
    assert output["devices"][0]["hwSubtype"] == 11
    assert output["devices"][0]["isCanFd"] is True
    assert output["devices"][0]["channels"] == [
        {"displayChannel": 1, "hwChannel": 0, "appChannel": 0,
         "canChannelCount": 4, "baseHwChannel": 0},
        {"displayChannel": 2, "hwChannel": 1, "appChannel": 1,
         "canChannelCount": 4, "baseHwChannel": 0},
        {"displayChannel": 3, "hwChannel": 2, "appChannel": 2,
         "canChannelCount": 4, "baseHwChannel": 0},
        {"displayChannel": 4, "hwChannel": 3, "appChannel": 3,
         "canChannelCount": 4, "baseHwChannel": 0},
    ]
    assert len(output["devices"]) == 1


def test_scan_combines_usb2xxx_sdk_devices(monkeypatch, capsys):
    device = type("Device", (), {
        "device_name": "图莫斯 UTA0401",
        "product": "UTA0401",
        "serial": "USB-SERIAL",
        "manufacturer": "TOOMOSS",
        "device_index": 0,
        "can_channel_count": 2,
        "is_can_fd": False,
    })()
    monkeypatch.setattr(
        "unified_can_lin_host_tool.cli.release.TsmasterAdapter.probe_can_devices",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "unified_can_lin_host_tool.cli.release.Usb2xxxAdapter.probe_can_devices",
        lambda **_: [device],
    )

    assert main(["scan", "--project", "AS5PR"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["devices"][0]["adapter"] == "usb2xxx"
    assert output["devices"][0]["product"] == "UTA0401"
    assert output["devices"][0]["channels"] == [
        {"displayChannel": 1, "hwChannel": 0, "canChannelCount": 2},
        {"displayChannel": 2, "hwChannel": 1, "canChannelCount": 2},
    ]


def test_usb2xxx_ota_rebinds_selected_serial(monkeypatch):
    calls = {}
    selected = type("Device", (), {
        "serial": "USB-SERIAL", "device_index": 4,
        "can_channel_count": 2,
    })()

    class Adapter:
        @classmethod
        def probe_can_devices(cls, **kwargs):
            return [selected]

        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def open_can(self):
            calls["opened"] = True

    monkeypatch.setattr("unified_can_lin_host_tool.cli.release.Usb2xxxAdapter", Adapter)
    monkeypatch.setattr(
        "unified_can_lin_host_tool.cli.release.CanIsoTpTransport",
        lambda adapter, config, trace_logger: (adapter, config, trace_logger),
    )
    args = SimpleNamespace(
        adapter="usb2xxx", usb2xxx_dll="USB2XXX.dll",
        hw_serial="USB-SERIAL", hw_channel=1,
    )
    config = SimpleNamespace(bus=SimpleNamespace(baudrate=500000, response_id=0x709))

    _open_transport(args, config, object())

    assert calls["init"]["device_serial"] == "USB-SERIAL"
    assert calls["init"]["device_index"] == 4
    assert calls["init"]["channel"] == 1
    assert calls["init"]["receive_ids"] == (config.bus.response_id,)
    assert calls["opened"] is True


def test_scan_does_not_filter_another_tosun_model(monkeypatch, capsys):
    device = type("Device", (), {
        "device_name": "TC1026",
        "product": "TOSUN OTHER CAN DEVICE",
        "serial": "OTHER",
        "manufacturer": "TOSUN",
        "hw_subtype": 25,
        "device_index": 0,
        "can_channel_count": 1,
        "is_can_fd": False,
    })()
    monkeypatch.setattr(
        "unified_can_lin_host_tool.cli.release.TsmasterAdapter.probe_can_devices",
        lambda **_: [device],
    )
    monkeypatch.setattr(
        "unified_can_lin_host_tool.cli.release.Usb2xxxAdapter.probe_can_devices",
        lambda **_: [],
    )

    assert main(["scan", "--project", "AS5PR"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["devices"][0]["name"] == "TC1026"
    assert len(output["devices"][0]["channels"]) == 1


def test_tsmaster_scan_uses_sdk_device_detail_for_can_capability(monkeypatch):
    class FakeFunction:
        def __init__(self, action=None):
            self.action = action or (lambda *_: 0)
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.action(*args)

    def scan(count):
        count._obj.value = 1
        return 0

    def detail(_index, manufacturer, product, serial, device_type, device_name,
               can_count, is_can_fd, lin_count, fr_count, ethernet_count):
        manufacturer._obj.value = b"TOSUN"
        product._obj.value = b"TOSUN HS CANFD4.LIN2"
        serial._obj.value = b"ABC123"
        device_type._obj.value = 11
        device_name._obj.value = b"TC1016"
        can_count._obj.value = 4
        is_can_fd._obj.value = True
        lin_count._obj.value = 2
        fr_count._obj.value = 0
        ethernet_count._obj.value = 0
        return 0

    fake_dll = type("FakeDll", (), {
        "initialize_lib_tscan": FakeFunction(),
        "tscan_scan_devices": FakeFunction(scan),
        "tscan_get_device_info_detail": FakeFunction(detail),
        "finalize_lib_tscan": FakeFunction(),
    })()
    monkeypatch.setattr(
        "unified_can_lin_host_tool.adapters.tsmaster.WinDLL",
        lambda _path: fake_dll,
    )

    devices = TsmasterAdapter.probe_can_devices(dll_path=r"D:\TSMaster\TSMaster.dll")

    assert len(devices) == 1
    assert devices[0].device_name == "TC1016"
    assert devices[0].product == "TOSUN HS CANFD4.LIN2"
    assert devices[0].hw_subtype == 11
    assert devices[0].device_index == 0
    assert devices[0].can_channel_count == 4
    assert devices[0].is_can_fd is True
    assert len(fake_dll.tscan_get_device_info_detail.argtypes) == 11


def test_ota_rebinds_selected_serial_to_current_tsmaster_device_index(monkeypatch):
    calls = {}
    mapping = type("Mapping", (), {
        "serial": "ABC123", "name": "TC1016", "device_type": 3,
        "device_index": 5,
    })()

    class Adapter:
        @classmethod
        def probe(cls, **kwargs):
            calls["probe"] = kwargs
            return [mapping]

        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def open_can(self):
            calls["opened"] = True

    monkeypatch.setattr("unified_can_lin_host_tool.cli.release.TsmasterAdapter", Adapter)
    monkeypatch.setattr(
        "unified_can_lin_host_tool.cli.release.CanIsoTpTransport",
        lambda adapter, config, trace_logger: (adapter, config, trace_logger),
    )
    args = SimpleNamespace(
        tsmaster_dll="TSMaster.dll", tsmaster_app="OTA", tsmaster_channel=3,
        hw_name="stale-name", hw_device_type=3, hw_serial="ABC123", hw_subtype=11,
        hw_index=0, hw_channel=3, can_channel_count=4, base_hw_channel=0,
    )
    config = SimpleNamespace(bus=SimpleNamespace(baudrate=500000))

    _open_transport(args, config, object())

    assert calls["init"]["hw_name"] == "TC1016"
    assert calls["init"]["hw_device_type"] == 3
    assert calls["init"]["hw_index"] == 5
    assert calls["opened"] is True


def test_progress_is_emitted_as_json_line(capsys):
    emit_progress_json(OtaProgress(67, "下载 App", "block 32/48", 32, 48))

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "event": "progress",
        "percent": 67,
        "stage": "下载 App",
        "message": "block 32/48",
        "current": 32,
        "total": 48,
    }


def test_native_ota_reaches_state_machine_without_legacy_flash_arguments(monkeypatch, tmp_path: Path):
    prepared = type("Prepared", (), {"release_set_id": "a" * 64})()
    calls = []

    class Adapter:
        def close(self):
            calls.append("close")

    class Trace:
        path = tmp_path / "ota.asc"
        def close(self):
            calls.append("trace-close")

    monkeypatch.setattr("unified_can_lin_host_tool.cli.release.prepare_as5pr_app", lambda _: prepared)
    monkeypatch.setattr("unified_can_lin_host_tool.cli.release.TraceLogger", lambda *_args, **_kwargs: Trace())
    monkeypatch.setattr("unified_can_lin_host_tool.cli.release._open_transport", lambda *_: (Adapter(), object()))

    def run(_self, package):
        calls.append(package)
        return OtaResult(OtaResultStatus.COMPLETED, package.release_set_id)

    monkeypatch.setattr("unified_can_lin_host_tool.cli.release.As5prOtaStateMachine.run", run)

    assert main([
        "ota", str(tmp_path / "app.hex"), "--project", "AS5PR",
        "--confirm-project", "AS5PR", "--yes-i-know-this-erases-app",
    ]) == 0
    assert prepared in calls
