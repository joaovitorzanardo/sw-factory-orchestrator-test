import hashlib
import hmac
import unittest

from software_factory.linear.signature import is_fresh, verify_signature

SECRET = "s3cr3t"
BODY = b'{"action":"create"}'


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertTrue(verify_signature(BODY, sign(BODY), SECRET))

    def test_wrong_secret(self) -> None:
        self.assertFalse(verify_signature(BODY, sign(BODY, "outro"), SECRET))

    def test_tampered_body(self) -> None:
        self.assertFalse(verify_signature(BODY + b" ", sign(BODY), SECRET))

    def test_missing(self) -> None:
        self.assertFalse(verify_signature(BODY, None, SECRET))
        self.assertFalse(verify_signature(BODY, "", SECRET))


class TestIsFresh(unittest.TestCase):
    def test_within_tolerance(self) -> None:
        self.assertTrue(is_fresh(1_000_000, 1_000_000 + 59_000))

    def test_too_old(self) -> None:
        self.assertFalse(is_fresh(1_000_000, 1_000_000 + 61_000))

    def test_future(self) -> None:
        self.assertFalse(is_fresh(1_000_000 + 61_000, 1_000_000))


if __name__ == "__main__":
    unittest.main()
