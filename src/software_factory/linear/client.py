from typing import Any

import httpx
from temporalio.exceptions import ApplicationError

LINEAR_API_URL = "https://api.linear.app/graphql"
REQUEST_TIMEOUT_S = 30


class LinearClient:
    """Cliente GraphQL mínimo da API do Linear (só GraphQL; não há REST)."""

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.api_key = api_key
        self.transport = transport

    async def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Erros de rede propagam: o Temporal retenta a activity.
        async with httpx.AsyncClient(transport=self.transport, timeout=REQUEST_TIMEOUT_S) as http:
            response = await http.post(
                LINEAR_API_URL,
                json={"query": query, "variables": variables},
                # Personal API key vai crua, sem "Bearer".
                headers={"Authorization": self.api_key},
            )
        try:
            body = response.json()
        except ValueError:
            body = {}

        errors = body.get("errors") if isinstance(body, dict) else None
        if errors:
            messages = "; ".join(str(error.get("message")) for error in errors)
            rate_limited = any(
                (error.get("extensions") or {}).get("code") == "RATELIMITED" for error in errors
            )
            raise ApplicationError(
                f"Linear respondeu com erro: {messages}", non_retryable=not rate_limited
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ApplicationError(f"Linear indisponível: HTTP {response.status_code}")
        if not response.is_success:
            # 401/403: chave inválida ou sem permissão — retentar não resolve.
            raise ApplicationError(
                f"Linear recusou a requisição: HTTP {response.status_code}", non_retryable=True
            )
        return body["data"]
