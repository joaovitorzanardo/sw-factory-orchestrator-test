import json
import logging
import time

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from temporalio.exceptions import WorkflowAlreadyStartedError

from software_factory.config import Settings
from software_factory.linear.events import LinearIssue, parse_issue, should_process
from software_factory.linear.signature import is_fresh, verify_signature
from software_factory.orchestration.models import PlanIssueInput
from software_factory.orchestration.workflow import PlanIssueWorkflow

logger = logging.getLogger(__name__)

router = APIRouter()


async def handle_issue(
    client: Any, task_queue: str, issue: LinearIssue, delivery_id: str | None
) -> bool:
    """Inicia o PlanIssueWorkflow; retorna False se já há um rodando para a issue."""
    workflow_id = f"plan-issue-{issue.id}"
    try:
        await client.start_workflow(
            PlanIssueWorkflow.run,
            PlanIssueInput(
                issue_id=issue.id,
                identifier=issue.identifier,
                title=issue.title,
                description=issue.description,
                url=issue.url,
            ),
            id=workflow_id,
            task_queue=task_queue,
        )
    except WorkflowAlreadyStartedError:
        logger.info("workflow %s já em execução; delivery=%s", workflow_id, delivery_id)
        return False
    logger.info(
        "workflow %s iniciado: %s %r delivery=%s",
        workflow_id,
        issue.identifier,
        issue.title,
        delivery_id,
    )
    return True


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

    try:
        started = await handle_issue(
            request.app.state.temporal, settings.task_queue, parse_issue(payload), delivery_id
        )
    except Exception:
        # 503 faz o Linear reenviar quando o Temporal voltar.
        logger.exception("falha ao iniciar workflow; delivery=%s", delivery_id)
        raise HTTPException(status_code=503, detail="orquestrador indisponível")
    return {"status": "accepted" if started else "duplicate"}
