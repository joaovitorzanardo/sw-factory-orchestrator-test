import json
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from temporalio.exceptions import WorkflowAlreadyStartedError

from software_factory.api import create_app
from software_factory.config import Settings
from tests.payloads import DOCS, FEATURE, issue_payload
from tests.test_signature import SECRET, sign


def now_ms() -> int:
    return int(time.time() * 1000)


class FakeTemporalClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.started: list[tuple[tuple, dict]] = []

    async def start_workflow(self, *args, **kwargs) -> None:
        self.started.append((args, kwargs))
        if self.error:
            raise self.error


class TestLinearWebhook(unittest.TestCase):
    def setUp(self) -> None:
        self.temporal = FakeTemporalClient()
        self.client = self.make_client(self.temporal)

    def make_client(self, temporal: FakeTemporalClient) -> TestClient:
        return TestClient(
            create_app(Settings(linear_webhook_secret=SECRET), temporal_client=temporal)
        )

    def accepted_body(self) -> bytes:
        return json.dumps(issue_payload(labels=[FEATURE], webhook_timestamp=now_ms())).encode()

    def headers(self, body: bytes, signature: str | None = "auto") -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Linear-Delivery": "delivery-1"}
        if signature == "auto":
            signature = sign(body)
        if signature is not None:
            headers["Linear-Signature"] = signature
        return headers

    def post(self, body: bytes, signature: str | None = "auto"):
        return self.client.post(
            "/webhooks/linear", content=body, headers=self.headers(body, signature)
        )

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
        self.assertEqual(self.temporal.started, [])

    def test_accepted_starts_workflow(self) -> None:
        response = self.post(self.accepted_body())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "accepted"})
        ((args, kwargs),) = self.temporal.started
        issue_input = args[1]
        self.assertEqual(issue_input.identifier, "ENG-12")
        self.assertEqual(kwargs["id"], f"plan-issue-{issue_input.issue_id}")
        self.assertEqual(kwargs["task_queue"], "software-factory")

    def test_already_started(self) -> None:
        temporal = FakeTemporalClient(WorkflowAlreadyStartedError("plan-issue-x", "PlanIssueWorkflow"))
        body = self.accepted_body()
        response = self.make_client(temporal).post(
            "/webhooks/linear", content=body, headers=self.headers(body)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "duplicate"})

    def test_temporal_unavailable(self) -> None:
        temporal = FakeTemporalClient(RuntimeError("conexão recusada"))
        body = self.accepted_body()
        response = self.make_client(temporal).post(
            "/webhooks/linear", content=body, headers=self.headers(body)
        )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
