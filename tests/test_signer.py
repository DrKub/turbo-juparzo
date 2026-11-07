import unittest

from executor.signer import SignerRefusal, sign


class SignerTest(unittest.TestCase):
    def test_sign_always_raises_and_returns_nothing(self):
        with self.assertRaises(SignerRefusal):
            sign(b"transaction")


if __name__ == "__main__":
    unittest.main()
