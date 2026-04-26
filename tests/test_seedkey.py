import unittest

from unified_can_lin_host_tool.e68.seedkey import calc_e68_fbl_key, calc_e68_level1_key


class SeedKeyTests(unittest.TestCase):
    def test_app_level1_first_seed_vector(self):
        self.assertEqual(calc_e68_level1_key(bytes.fromhex("35 79 24 68")), bytes.fromhex("70 C7 71 B5"))

    def test_fbl_first_seed_vector(self):
        self.assertEqual(calc_e68_fbl_key(bytes.fromhex("24 68 35 79")), bytes.fromhex("4D 62 06 0F"))

    def test_algorithms_are_not_interchangeable(self):
        seed = bytes.fromhex("12 34 56 78")

        self.assertEqual(calc_e68_level1_key(seed), bytes.fromhex("70 10 00 B2"))
        self.assertEqual(calc_e68_fbl_key(seed), bytes.fromhex("21 57 00 0F"))
        self.assertNotEqual(calc_e68_level1_key(seed), calc_e68_fbl_key(seed))

