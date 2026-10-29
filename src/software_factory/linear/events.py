from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LinearIssue:
    id: str
    identifier: str
    title: str
    description: str | None
    url: str | None
    team_key: str | None
    labels: tuple[str, ...]


def _trigger_label_ids(data: dict[str, Any], trigger_labels: Iterable[str]) -> set[str]:
    triggers = {name.lower() for name in trigger_labels}
    return {
        label["id"]
        for label in data.get("labels") or []
        if label.get("name", "").lower() in triggers
    }


def should_process(payload: dict[str, Any], trigger_labels: Iterable[str]) -> bool:
    """Issue criada com label de gatilho, ou atualizada recebendo uma agora."""
    if payload.get("type") != "Issue":
        return False
    data = payload.get("data") or {}
    trigger_ids = _trigger_label_ids(data, trigger_labels)
    if not trigger_ids:
        return False

    action = payload.get("action")
    if action == "create":
        return True
    if action == "update":
        updated_from = payload.get("updatedFrom") or {}
        if "labelIds" not in updated_from:
            return False
        previous = set(updated_from["labelIds"] or [])
        return bool(trigger_ids - previous)
    return False


def parse_issue(payload: dict[str, Any]) -> LinearIssue:
    data = payload["data"]
    team = data.get("team") or {}
    return LinearIssue(
        id=data["id"],
        identifier=data.get("identifier", ""),
        title=data.get("title", ""),
        description=data.get("description"),
        url=payload.get("url") or data.get("url"),
        team_key=team.get("key"),
        labels=tuple(label["name"] for label in data.get("labels") or []),
    )
