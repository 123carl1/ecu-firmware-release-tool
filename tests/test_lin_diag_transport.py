import unittest

from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport


class LinDiagTransportTests(unittest.TestCase):
    def test_single_frame_request_uses_profile_nad_and_ids(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = FakeLinAdapter(responses=[(0x3D, bytes.fromhex("02 02 50 01 FF FF FF FF"))])
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(bytes.fromhex("10 01"), expect_prefix=bytes.fromhex("50 01"))

        self.assertEqual(response.payload, bytes.fromhex("50 01"))
        self.assertEqual(adapter.sent_frames[0], (0x3C, bytes.fromhex("02 02 10 01 FF FF FF FF")))

    def test_multi_frame_request_splits_to_first_and_consecutive_frames(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = FakeLinAdapter(responses=[(0x3D, bytes.fromhex("02 02 76 01 FF FF FF FF"))])
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)

        transport.request(bytes.fromhex("36 01 01 02 03 04 05 06"), expect_prefix=bytes.fromhex("76 01"))

        self.assertEqual(adapter.sent_frames[0], (0x3C, bytes.fromhex("02 10 08 36 01 01 02 03")))
        self.assertEqual(adapter.sent_frames[1], (0x3C, bytes.fromhex("02 21 04 05 06 FF FF FF")))

    def test_response_pending_waits_for_final_response(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = FakeLinAdapter(
            responses=[
                (0x3D, bytes.fromhex("02 03 7F 31 78 FF FF FF")),
                (0x3D, bytes.fromhex("02 04 71 01 FF 00 FF FF")),
            ]
        )
        transport = LinDiagTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(
            bytes.fromhex("31 01 FF 00 00 00 70 00 00 00 02 00"),
            expect_prefix=bytes.fromhex("71 01 FF 00"),
            allow_response_pending=True,
        )

        self.assertEqual(response.payload[:4], bytes.fromhex("71 01 FF 00"))

