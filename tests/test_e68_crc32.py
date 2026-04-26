import unittest

from unified_can_lin_host_tool.e68.crc32 import E68_CRC32_INIT, e68_crc32, e68_crc32_update


class E68Crc32Tests(unittest.TestCase):
    def test_known_vector_without_final_xor(self):
        self.assertEqual(e68_crc32(b"123456789"), 0x340BC6D9)

    def test_chunked_equals_single_pass(self):
        single = e68_crc32(b"abcdef")
        crc = e68_crc32_update(E68_CRC32_INIT, b"abc")
        crc = e68_crc32_update(crc, b"def")

        self.assertEqual(crc, single)

    def test_block_sequence_is_not_part_of_crc(self):
        data_crc = e68_crc32(bytes.fromhex("01 02 03 04 05 06"))
        wrong_crc = e68_crc32(bytes.fromhex("01") + bytes.fromhex("01 02 03 04 05 06"))

        self.assertNotEqual(data_crc, wrong_crc)

