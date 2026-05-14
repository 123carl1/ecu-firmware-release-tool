import unittest

from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.transport.base import LinFrame
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport


class LinDiagTransportTests(unittest.TestCase):
    def test_single_frame_request_uses_profile_nad_and_ids(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = FakeLinAdapter(responses=[(0x3D, bytes.fromhex("11 02 50 01 FF FF FF FF"))])
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(bytes.fromhex("10 01"), expect_prefix=bytes.fromhex("50 01"))

        self.assertEqual(response.payload, bytes.fromhex("50 01"))
        self.assertEqual(adapter.sent_frames[0], (0x3C, bytes.fromhex("11 02 10 01 FF FF FF FF")))

    def test_multi_frame_request_splits_to_first_and_consecutive_frames(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = FakeLinAdapter(responses=[(0x3D, bytes.fromhex("11 02 76 01 FF FF FF FF"))])
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)

        transport.request(bytes.fromhex("36 01 01 02 03 04 05 06"), expect_prefix=bytes.fromhex("76 01"))

        self.assertEqual(adapter.sent_frames[0], (0x3C, bytes.fromhex("11 10 08 36 01 01 02 03")))
        self.assertEqual(adapter.sent_frames[1], (0x3C, bytes.fromhex("11 21 04 05 06 FF FF FF")))

    def test_transfer_data_request_1026_bytes_splits_to_172_lin_frames(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = FakeLinAdapter(responses=[(0x3D, bytes.fromhex("11 02 76 01 FF FF FF FF"))])
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)
        uds_payload = bytes([0x36, 0x01]) + bytes([0xA5]) * 1024

        transport.request(uds_payload, expect_prefix=bytes.fromhex("76 01"))

        self.assertEqual(len(adapter.sent_frames), 172)
        self.assertEqual(adapter.sent_frames[0], (0x3C, bytes.fromhex("11 14 02 36 01 A5 A5 A5")))
        self.assertEqual(adapter.sent_frames[-1][0], 0x3C)
        self.assertEqual(adapter.sent_frames[-1][1][0], 0x11)
        self.assertEqual(adapter.sent_frames[-1][1][1], 0x2B)

    def test_response_pending_waits_for_final_response(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = FakeLinAdapter(
            responses=[
                (0x3D, bytes.fromhex("11 03 7F 31 78 FF FF FF")),
                (0x3D, bytes.fromhex("11 04 71 01 FF 00 FF FF")),
            ]
        )
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(
            bytes.fromhex("31 01 FF 00 00 00 70 00 00 00 02 00"),
            expect_prefix=bytes.fromhex("71 01 FF 00"),
            allow_response_pending=True,
        )

        self.assertEqual(response.payload[:4], bytes.fromhex("71 01 FF 00"))

    def test_stale_positive_response_is_skipped_until_expected_response(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = FakeLinAdapter(
            responses=[
                (0x3D, bytes.fromhex("11 06 50 02 00 32 13 88")),
                (0x3D, bytes.fromhex("11 06 67 09 24 68 35 79")),
            ]
        )
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(bytes.fromhex("27 09"), expect_prefix=bytes.fromhex("67 09"))

        self.assertEqual(response.payload, bytes.fromhex("67 09 24 68 35 79"))
        self.assertEqual(len(response.raw_frames), 2)

    def test_response_with_wrong_id_is_rejected(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = SequentialRxAdapter(
            [
                LinFrame(0x3F, bytes.fromhex("11 06 50 02 00 32 13 88")),
            ]
        )
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)

        with self.assertRaises(HostToolError) as caught:
            transport.request(bytes.fromhex("27 01"), expect_prefix=bytes.fromhex("67 01"))

        self.assertEqual(caught.exception.category, ErrorCategory.TRANSPORT)

    def test_reset_handoff_noise_is_skipped_until_expected_response(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = SequentialRxAdapter(
            [
                LinFrame(0x3D, bytes.fromhex("00")),
                LinFrame(0x3D, bytes.fromhex("11 06 50 02 00 32 13 88")),
            ]
        )
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(
            bytes.fromhex("10 02"),
            expect_prefix=bytes.fromhex("50 02"),
            ignore_invalid_responses=True,
        )

        self.assertEqual(response.payload, bytes.fromhex("50 02 00 32 13 88"))
        self.assertEqual(len(response.raw_frames), 2)

    def test_empty_single_frame_response_is_classified_error(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = FakeLinAdapter(responses=[(0x3D, bytes.fromhex("11 00 FF FF FF FF FF FF"))])
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)

        with self.assertRaises(HostToolError) as caught:
            transport.request(bytes.fromhex("10 01"), expect_sid=0x50)

        self.assertEqual(caught.exception.category, ErrorCategory.TRANSPORT)


class SequentialRxAdapter:
    def __init__(self, responses: list[LinFrame]) -> None:
        self.sent_frames: list[tuple[int, bytes]] = []
        self._responses = responses

    def send_lin_frame(self, frame_id: int, data: bytes) -> None:
        self.sent_frames.append((frame_id, data))

    def receive_lin_frame(self, frame_id: int, timeout_ms: int) -> LinFrame | None:
        if not self._responses:
            return None
        return self._responses.pop(0)
