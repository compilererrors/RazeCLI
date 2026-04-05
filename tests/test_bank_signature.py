import unittest

from razecli.ble.bank_signature import bank_signature_from_parsed_stages, match_bank_snapshot_labels


class BankSignatureTest(unittest.TestCase):
    def test_signature_stable_for_two_stages(self):
        sig = bank_signature_from_parsed_stages(
            1,
            3,
            [(1000, 1000), (1600, 1600)],
            [0x11, 0x22],
        )
        self.assertEqual(len(sig), 16)
        again = bank_signature_from_parsed_stages(
            1,
            3,
            [(1000, 1000), (1600, 1600)],
            [0x11, 0x22],
        )
        self.assertEqual(sig, again)

    def test_match_returns_newest_first(self):
        labels = match_bank_snapshot_labels("ffffffffffffffff", path_override="__does_not_exist__")
        self.assertEqual(labels, [])


if __name__ == "__main__":
    unittest.main()
