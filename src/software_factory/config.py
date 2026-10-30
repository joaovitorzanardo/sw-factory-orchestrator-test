import os
from dataclasses import dataclass, field

DEFAULT_TRIGGER_LABELS = frozenset({"feature", "bug"})
DEFAULT_TEMPORAL_ADDRESS = "localhost:7233"
DEFAULT_TASK_QUEUE = "software-factory"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} não definida")
    return value


@dataclass(frozen=True)
class Settings:
    linear_webhook_secret: str
    host: str = "0.0.0.0"
    port: int = 8000
    trigger_labels: frozenset[str] = field(default=DEFAULT_TRIGGER_LABELS)
    temporal_address: str = DEFAULT_TEMPORAL_ADDRESS
    task_queue: str = DEFAULT_TASK_QUEUE

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            linear_webhook_secret=_required("LINEAR_WEBHOOK_SECRET"),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
            temporal_address=os.environ.get("TEMPORAL_ADDRESS", DEFAULT_TEMPORAL_ADDRESS),
            task_queue=os.environ.get("TASK_QUEUE", DEFAULT_TASK_QUEUE),
        )


@dataclass(frozen=True)
class FactorySettings:
    """Configuração do worker: sandbox E2B, Claude Code e repo alvo."""

    e2b_api_key: str
    github_token: str
    target_repo: str
    # Auth do Claude Code no sandbox: exatamente uma das duas (API ou assinatura).
    anthropic_api_key: str | None = None
    claude_code_oauth_token: str | None = None
    temporal_address: str = DEFAULT_TEMPORAL_ADDRESS
    task_queue: str = DEFAULT_TASK_QUEUE
    e2b_template: str = "claude"

    def __post_init__(self) -> None:
        if bool(self.anthropic_api_key) == bool(self.claude_code_oauth_token):
            raise RuntimeError(
                "defina exatamente uma de ANTHROPIC_API_KEY ou CLAUDE_CODE_OAUTH_TOKEN"
            )

    @property
    def claude_auth_env(self) -> dict[str, str]:
        if self.anthropic_api_key:
            return {"ANTHROPIC_API_KEY": self.anthropic_api_key}
        return {"CLAUDE_CODE_OAUTH_TOKEN": self.claude_code_oauth_token or ""}

    @classmethod
    def from_env(cls) -> "FactorySettings":
        return cls(
            e2b_api_key=_required("E2B_API_KEY"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            claude_code_oauth_token=os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or None,
            github_token=_required("GITHUB_TOKEN"),
            target_repo=_required("TARGET_REPO"),
            temporal_address=os.environ.get("TEMPORAL_ADDRESS", DEFAULT_TEMPORAL_ADDRESS),
            task_queue=os.environ.get("TASK_QUEUE", DEFAULT_TASK_QUEUE),
            e2b_template=os.environ.get("E2B_TEMPLATE", "claude"),
        )
