import unittest

from unified_can_lin_host_tool.adapters.fake import FakeCanAdapter
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.transport.base import CanFrame
from unified_can_lin_host_tool.transport.can_isotp import CanIsoTpTransport


class CanIsoTpTransportTests(unittest.TestCase):
    def test_single_frame_request_uses_profile_ids_and_padding(self):
        profile = load_profile("profiles/as5pr_can_bootloader.yaml")
        adapter = FakeCanAdapter(responses=[(0x709, bytes.fromhex("02 50 01 AA AA AA AA AA"))])
        transport = CanIsoTpTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(bytes.fromhex("10 01"), expect_prefix=bytes.fromhex("50 01"))

        self.assertEqual(response.payload, bytes.fromhex("50 01"))
        self.assertEqual(adapter.sent_frames[0], (0x701, bytes.fromhex("02 10 01 AA AA AA AA AA")))

    def test_multi_frame_request_waits_for_flow_control_and_sends_consecutive_frames(self):
        profile = load_profile("profiles/as5pr_can_bootloader.yaml")
        adapter = FakeCanAdapter(
            responses=[
                (0x709, bytes.fromhex("30 00 00 AA AA AA AA AA")),
                (0x709, bytes.fromhex("02 76 01 AA AA AA AA AA")),
            ]
        )
        transport = CanIsoTpTransport(adapter, profile, sleep_func=lambda _: None)

        transport.request(bytes.fromhex("36 01 01 02 03 04 05 06 07 08"), expect_prefix=bytes.fromhex("76 01"))

        self.assertEqual(adapter.sent_frames[0], (0x701, bytes.fromhex("10 0A 36 01 01 02 03 04")))
        self.assertEqual(adapter.sent_frames[1], (0x701, bytes.fromhex("21 05 06 07 08 AA AA AA")))

    def test_transfer_data_with_62_byte_payload_uses_full_can_isotp_request(self):
        profile = load_profile("profiles/as5pr_can_bootloader.yaml")
        adapter = FakeCanAdapter(
            responses=[
                (0x709, bytes.fromhex("30 00 00 AA AA AA AA AA")),
                (0x709, bytes.fromhex("02 76 01 AA AA AA AA AA")),
            ]
        )
        transport = CanIsoTpTransport(adapter, profile, sleep_func=lambda _: None)
        payload = bytes([0x36, 0x01]) + bytes(range(62))

        response = transport.request(payload, expect_prefix=bytes.fromhex("76 01"))

        self.assertEqual(response.payload, bytes.fromhex("76 01"))
        self.assertEqual(len(adapter.sent_frames), 10)
        self.assertEqual(adapter.sent_frames[0], (0x701, bytes.fromhex("10 40 36 01 00 01 02 03")))
        self.assertEqual(adapter.sent_frames[1], (0x701, bytes.fromhex("21 04 05 06 07 08 09 0A")))
        self.assertEqual(adapter.sent_frames[-1], (0x701, bytes.fromhex("29 3C 3D AA AA AA AA AA")))

    def test_response_pending_waits_for_final_response(self):
        profile = load_profile("profiles/as5pr_can_bootloader.yaml")
        adapter = FakeCanAdapter(
            responses=[
                (0x709, bytes.fromhex("30 00 00 AA AA AA AA AA")),
                (0x709, bytes.fromhex("03 7F 31 78 AA AA AA AA")),
                (0x709, bytes.fromhex("04 71 01 FF 00 AA AA AA")),
            ]
        )
        transport = CanIsoTpTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(
            bytes.fromhex("31 01 FF 00 00 00 70 00 00 00 02 00"),
            expect_prefix=bytes.fromhex("71 01 FF 00"),
            allow_response_pending=True,
        )

        self.assertEqual(response.payload, bytes.fromhex("71 01 FF 00"))

    def test_multi_frame_response_is_reassembled_and_fc_is_sent(self):
        profile = load_profile("profiles/as5pr_can_bootloader.yaml")
        adapter = FakeCanAdapter(
            responses=[
                (0x709, bytes.fromhex("10 08 62 F1 90 01 02 03")),
                (0x709, bytes.fromhex("21 04 05 AA AA AA AA AA")),
            ]
        )
        transport = CanIsoTpTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(bytes.fromhex("22 F1 90"), expect_prefix=bytes.fromhex("62 F1 90"))

        self.assertEqual(response.payload, bytes.fromhex("62 F1 90 01 02 03 04 05"))
        self.assertEqual(adapter.sent_frames[1], (0x701, bytes.fromhex("30 00 00 AA AA AA AA AA")))

    def test_flow_control_overflow_is_transport_error(self):
        profile = load_profile("profiles/as5pr_can_bootloader.yaml")
        adapter = FakeCanAdapter(responses=[(0x709, bytes.fromhex("32 00 00 AA AA AA AA AA"))])
        transport = CanIsoTpTransport(adapter, profile, sleep_func=lambda _: None)

        with self.assertRaises(HostToolError) as caught:
            transport.request(bytes.fromhex("36 01 01 02 03 04 05 06"), expect_prefix=bytes.fromhex("76 01"))

        self.assertEqual(caught.exception.category, ErrorCategory.TRANSPORT)

    def test_timestamp_boundary_accepts_only_response_generated_after_transmit(self):
        class FreshnessAdapter:
            def __init__(self):
                self.responses = [CanFrame(0x709, bytes.fromhex("02 50 01 AA AA AA AA AA"))]

            def send_can_frame(self, can_id, data):
                self.responses.clear()
                self.responses.append(CanFrame(0x709, bytes.fromhex("02 50 03 AA AA AA AA AA")))

            def receive_can_frame(self, can_id, timeout_ms):
                return self.responses.pop(0) if self.responses else None

        profile = load_profile("profiles/as5pr_can_bootloader.yaml")
        adapter = FreshnessAdapter()
        transport = CanIsoTpTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(bytes.fromhex("10 03"), expect_prefix=bytes.fromhex("50 03"))

        self.assertEqual(response.payload, bytes.fromhex("50 03"))

    def test_trace_failure_never_controls_protocol(self):
        class BrokenTrace:
            def write(self, _event):
                raise OSError("disk full")

        profile = load_profile("profiles/as5pr_can_bootloader.yaml")
        adapter = FakeCanAdapter(responses=[(0x709, bytes.fromhex("02 50 01 AA AA AA AA AA"))])
        transport = CanIsoTpTransport(adapter, profile, sleep_func=lambda _: None, trace_logger=BrokenTrace())

        response = transport.request(bytes.fromhex("10 01"), expect_prefix=bytes.fromhex("50 01"))

        self.assertEqual(response.payload, bytes.fromhex("50 01"))

    def test_initial_flow_control_timeout_retries_only_first_frame(self):
        class RetryAdapter:
            def __init__(self):
                self.sent = []
                self.responses = []
                self.first_frames = 0

            def send_can_frame(self, can_id, data):
                self.sent.append(data)
                if data[0] & 0xF0 == 0x10:
                    self.first_frames += 1
                    if self.first_frames == 2:
                        self.responses.append(CanFrame(0x709, bytes.fromhex("30 00 00 AA AA AA AA AA")))
                elif data[0] & 0xF0 == 0x20:
                    self.responses.append(CanFrame(0x709, bytes.fromhex("02 76 01 AA AA AA AA AA")))

            def receive_can_frame(self, can_id, timeout_ms):
                return self.responses.pop(0) if self.responses else None

        profile = load_profile("profiles/as5pr_can_bootloader.yaml")
        adapter = RetryAdapter()
        transport = CanIsoTpTransport(adapter, profile, sleep_func=lambda _: None)

        response = transport.request(
            bytes.fromhex("36 01 01 02 03 04 05 06 07 08"),
            expect_prefix=bytes.fromhex("76 01"),
        )

        self.assertEqual(response.payload, bytes.fromhex("76 01"))
        self.assertEqual(adapter.first_frames, 2)
        self.assertEqual(sum(1 for item in adapter.sent if item[0] & 0xF0 == 0x20), 1)


if __name__ == "__main__":
    unittest.main()
