import hashlib
import hmac
import struct

from unified_can_lin_host_tool.release.package_builder import authenticate_image


def test_authentication_covers_payload_followed_by_little_endian_header():
    payload = b"app"
    key = bytes(range(32))
    result = authenticate_image(payload, 0x41503541, 1, key)
    header = struct.pack("<IIII", 0xA5A5A5A5, len(payload), 0x41503541, 1)
    assert result == payload + header + hmac.new(key, payload + header, hashlib.sha256).digest()


def test_authentication_rejects_non_32_byte_key():
    try:
        authenticate_image(b"x", 1, 1, b"short")
    except ValueError as exc:
        assert "32 bytes" in str(exc)
    else:
        raise AssertionError("short key was accepted")
