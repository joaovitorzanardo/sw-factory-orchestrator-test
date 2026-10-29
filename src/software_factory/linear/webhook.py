import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from software_factory.config import Settings
from software_factory.linear.events import LinearIssue, parse_issue, should_process
from software_factory.linear.signature import is_fresh, verify_signature

logger = logging.getLogger(__name__)

router = APIRouter()


def handle_issue(issue: LinearIssue, delivery_id: str | None) -> None:
    # Ponto de extensão: aqui entrará o start do workflow no Temporal.
    logger.info(
        "issue aceita: %s %r labels=%s id=%s delivery=%s",
        issue.identifier,
        issue.title,
        list(issue.labels),
        issue.id,
        delivery_id,
    )


@router.post("/webhooks/linear")
async def linear_webhook(request: Request) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    body = await request.body()

    if not verify_signature(
        body, request.headers.get("Linear-Signature"), settings.linear_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="assinatura inválida")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSON inválido")

    timestamp = payload.get("webhookTimestamp") if isinstance(payload, dict) else None
    if not isinstance(timestamp, int) or not is_fresh(timestamp, int(time.time() * 1000)):
        raise HTTPException(status_code=400, detail="webhookTimestamp ausente ou expirado")

    delivery_id = request.headers.get("Linear-Delivery")
    if not should_process(payload, settings.trigger_labels):
        logger.debug(
            "evento ignorado: %s %s delivery=%s",
            payload.get("type"),
            payload.get("action"),
            delivery_id,
        )
        return {"status": "ignored"}

    handle_issue(parse_issue(payload), delivery_id)
    return {"status": "accepted"}
