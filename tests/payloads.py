"""Payloads de exemplo no formato dos webhooks de Issue do Linear."""

from typing import Any

FEATURE = {"id": "label-feature", "name": "Feature", "color": "#BB87FC"}
BUG = {"id": "label-bug", "name": "bug", "color": "#EB5757"}
DOCS = {"id": "label-docs", "name": "Docs", "color": "#4EA7FC"}


def issue_payload(
    action: str = "create",
    labels: list[dict[str, str]] | None = None,
    updated_from: dict[str, Any] | None = None,
    webhook_timestamp: int = 0,
    type_: str = "Issue",
) -> dict[str, Any]:
    labels = labels or []
    payload: dict[str, Any] = {
        "action": action,
        "type": type_,
        "url": "https://linear.app/acme/issue/ENG-12/titulo",
        "webhookTimestamp": webhook_timestamp,
        "data": {
            "id": "issue-uuid",
            "identifier": "ENG-12",
            "title": "Título",
            "description": "Descrição",
            "team": {"id": "team-uuid", "key": "ENG", "name": "Engineering"},
            "labelIds": [label["id"] for label in labels],
            "labels": labels,
        },
    }
    if updated_from is not None:
        payload["updatedFrom"] = updated_from
    return payload
