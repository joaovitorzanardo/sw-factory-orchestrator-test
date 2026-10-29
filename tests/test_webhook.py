import json
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from software_factory.api import create_app
from software_factory.config import Settings
from tests.payloads import DOCS, FEATURE, issue_payload
from tests.test_signature import SECRET, sign


def now_ms() -> int:
    return int(time.time() * 1000)


class TestLinearWebhook(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(Settings(linear_webhook_secret=SECRET)))

    def post(self, body: bytes, signature: str | None = "auto"):
        headers = {"Content-Type": "application/json", "Linear-Delivery": "delivery-1"}
        if signature == "auto":
            signature = sign(body)
        if signature is not None:
            headers["Linear-Signature"] = signature
        return self.client.post("/webhooks/linear", content=body, headers=headers)

    def test_health(self) -> None:
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_missing_signature(self) -> None:
        body = json.dumps(issue_payload(labels=[FEATURE], webhook_timestamp=now_ms())).encode()
        self.assertEqual(self.post(body, signature=None).status_code, 401)

    def test_invalid_signature(self) -> None:
        body = json.dumps(issue_payload(labels=[FEATURE], webhook_timestamp=now_ms())).encode()
        self.assertEqual(self.post(body, signature="deadbeef").status_code, 401)

    def test_invalid_json(self) -> None:
        self.assertEqual(self.post(b"{nope").status_code, 400)

    def test_stale_timestamp(self) -> None:
        body = json.dumps(
            issue_payload(labels=[FEATURE], webhook_timestamp=now_ms() - 120_000)
        ).encode()
        self.assertEqual(self.post(body).status_code, 400)

    def test_ignored(self) -> None:
        body = json.dumps(issue_payload(labels=[DOCS], webhook_timestamp=now_ms())).encode()
        with mock.patch("software_factory.linear.webhook.handle_issue") as handler:
            response = self.post(body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ignored"})
        handler.assert_not_called()

    def test_accepted(self) -> None:
        body = json.dumps(issue_payload(labels=[FEATURE], webhook_timestamp=now_ms())).encode()
        with mock.patch("software_factory.linear.webhook.handle_issue") as handler:
            response = self.post(body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "accepted"})
        handler.assert_called_once()
        issue, delivery_id = handler.call_args.args
        self.assertEqual(issue.identifier, "ENG-12")
        self.assertEqual(delivery_id, "delivery-1")


if __name__ == "__main__":
    unittest.main()
