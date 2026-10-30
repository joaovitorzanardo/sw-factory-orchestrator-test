import asyncio
import json
import re
from pathlib import Path
from typing import Any

from e2b import AsyncSandbox, FileNotFoundException
from temporalio import activity
from temporalio.exceptions import ApplicationError

from software_factory.config import FactorySettings
from software_factory.orchestration.models import PlanIssueInput, PlanRequest

REPO_DIR = "/home/user/repo"
PROMPT_PATH = "/home/user/prompt.md"
# Skill writing-plans do superpowers 7.0.0, vendorizada (sem instalar plugin via rede).
SKILL_SOURCE = Path(__file__).parent / "skills" / "writing-plans" / "SKILL.md"
SKILL_DEST = "/home/user/.claude/skills/writing-plans/SKILL.md"
PLANS_DIR = "docs/superpowers/plans"
# O identifier vai para caminho e comando shell: só o formato do Linear (ENG-12).
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9]+-[0-9]+")
# Hobby do E2B permite ~1h contínua; o sandbox precisa durar mais que qualquer activity.
SANDBOX_TIMEOUT_S = 55 * 60
PLAN_COMMAND_TIMEOUT_S = 40 * 60
HEARTBEAT_INTERVAL_S = 30



def plan_path(identifier: str) -> str:
    """Caminho do plano relativo ao repo; rejeita identifiers fora do formato do Linear."""
    if not IDENTIFIER_RE.fullmatch(identifier):
        raise ApplicationError(f"identifier inválido: {identifier!r}", non_retryable=True)
    return f"{PLANS_DIR}/{identifier}.md"


def plan_command(identifier: str) -> str:
    # A regra Edit(...) cobre também a ferramenta Write; o Claude só escreve o plano.
    return (
        f"cat {PROMPT_PATH} | claude -p --output-format stream-json --verbose"
        f' --allowedTools "Read,Grep,Glob,Skill,Edit({plan_path(identifier)})"'
    )


def build_plan_prompt(issue: PlanIssueInput) -> str:
    path = plan_path(issue.identifier)
    return f"""Use a skill `writing-plans` para escrever o plano de implementação da issue abaixo.

- A issue é a spec: no campo **Spec:** do cabeçalho, use "Issue {issue.identifier} ({issue.url or "-"})".
- Salve o plano exatamente em `{path}` (substitui o caminho padrão da skill).
- Não altere nenhum outro arquivo.
- Pule a seção "Execution Handoff": não faça perguntas; termine assim que o plano
  estiver salvo e revisado.

## Issue {issue.identifier}: {issue.title}

URL: {issue.url or "-"}

{issue.description or "(sem descrição)"}
"""


def parse_plan(stdout: str) -> str:
    """Extrai o texto do evento `result` do stream-json do Claude Code."""
    result: dict[str, Any] | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
    if result is None:
        raise ApplicationError("Claude Code terminou sem evento result")
    if result.get("subtype") != "success" or result.get("is_error") or not result.get("result"):
        raise ApplicationError(f"Claude Code falhou: {result.get('subtype')}")
    return result["result"]


class SandboxActivities:
    def __init__(self, settings: FactorySettings, sandbox_cls: Any = AsyncSandbox) -> None:
        self.settings = settings
        self.sandbox_cls = sandbox_cls

    async def _connect(self, sandbox_id: str) -> Any:
        return await self.sandbox_cls.connect(sandbox_id, api_key=self.settings.e2b_api_key)

    @activity.defn
    async def create_sandbox(self, issue_id: str) -> str:
        # idempotency_key = workflow_id: um retry reaproveita o mesmo sandbox.
        sandbox = await self.sandbox_cls.create(
            template=self.settings.e2b_template,
            timeout=SANDBOX_TIMEOUT_S,
            envs=self.settings.claude_auth_env,
            metadata={"issue_id": issue_id},
            idempotency_key=activity.info().workflow_id,
            api_key=self.settings.e2b_api_key,
        )
        activity.logger.info("sandbox criado: %s", sandbox.sandbox_id)
        return sandbox.sandbox_id

    @activity.defn
    async def clone_repo(self, sandbox_id: str) -> None:
        sandbox = await self._connect(sandbox_id)
        if await sandbox.files.exists(f"{REPO_DIR}/.git"):
            return
        await sandbox.commands.run(
            "git clone --depth 1 "
            f'"https://x-access-token:$GITHUB_TOKEN@github.com/{self.settings.target_repo}.git" '
            f"{REPO_DIR}",
            envs={"GITHUB_TOKEN": self.settings.github_token},
            timeout=5 * 60,
        )

    @activity.defn
    async def generate_plan(self, request: PlanRequest) -> str:
        path = plan_path(request.issue.identifier)
        sandbox = await self._connect(request.sandbox_id)
        await sandbox.files.write(SKILL_DEST, SKILL_SOURCE.read_text())
        # Plano de uma tentativa anterior: o Write do Claude exigiria Read antes; recomeça limpo.
        if await sandbox.files.exists(f"{REPO_DIR}/{path}"):
            await sandbox.files.remove(f"{REPO_DIR}/{path}")
        await sandbox.files.write(PROMPT_PATH, build_plan_prompt(request.issue))

        # O Claude pode ficar muito tempo sem emitir nada; heartbeat periódico além do stdout.
        async def keep_alive() -> None:
            while True:
                activity.heartbeat()
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)

        keep_alive_task = asyncio.create_task(keep_alive())
        try:
            result = await sandbox.commands.run(
                plan_command(request.issue.identifier),
                cwd=REPO_DIR,
                on_stdout=lambda _chunk: activity.heartbeat(),
                timeout=PLAN_COMMAND_TIMEOUT_S,
            )
        finally:
            keep_alive_task.cancel()
        # O texto final do Claude é só um resumo; o plano é o arquivo salvo.
        parse_plan(result.stdout)
        try:
            return await sandbox.files.read(f"{REPO_DIR}/{path}")
        except FileNotFoundException:
            raise ApplicationError(f"Claude Code não salvou o plano em {path}") from None

    @activity.defn
    async def kill_sandbox(self, sandbox_id: str) -> None:
        # kill retorna False se o sandbox já não existe — idempotente.
        await self.sandbox_cls.kill(sandbox_id, api_key=self.settings.e2b_api_key)
