import os
from dataclasses import dataclass, field

DEFAULT_TRIGGER_LABELS = frozenset({"feature", "bug"})


@dataclass(frozen=True)
class Settings:
    linear_webhook_secret: str
    host: str = "0.0.0.0"
    port: int = 8000
    trigger_labels: frozenset[str] = field(default=DEFAULT_TRIGGER_LABELS)

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.environ.get("LINEAR_WEBHOOK_SECRET")
        if not secret:
            raise RuntimeError("LINEAR_WEBHOOK_SECRET não definida")
        return cls(
            linear_webhook_secret=secret,
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
        )
