from temporalio import activity
from temporalio.exceptions import ApplicationError

from software_factory.linear.client import LinearClient
from software_factory.orchestration.models import MoveIssueRequest

ISSUE_STATES_QUERY = """
query($id: String!) {
  issue(id: $id) { state { id name } team { states { nodes { id name } } } }
}
"""

UPDATE_STATE_MUTATION = """
mutation($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: {stateId: $stateId}) { success }
}
"""


class LinearActivities:
    def __init__(self, client: LinearClient) -> None:
        self.client = client

    @activity.defn
    async def move_issue(self, request: MoveIssueRequest) -> bool:
        """True se a issue terminou em `to_state`; False se a guarda `from_state` pulou."""
        data = await self.client.execute(ISSUE_STATES_QUERY, {"id": request.issue_id})
        issue = data.get("issue")
        if issue is None:
            raise ApplicationError(
                f"issue {request.issue_id} não encontrada no Linear", non_retryable=True
            )
        current = issue["state"]["name"]
        states = issue["team"]["states"]["nodes"]
        names = {s["name"].lower() for s in states}
        missing = [
            name for name in (request.to_state, *request.require_states) if name.lower() not in names
        ]
        if missing:
            available = ", ".join(s["name"] for s in states)
            raise ApplicationError(
                f"status {', '.join(map(repr, missing))} não existe no time da issue"
                f" (há: {available})",
                non_retryable=True,
            )
        # Já no destino (inclusive retry após uma mutation aplicada): nada a fazer.
        if current.lower() == request.to_state.lower():
            return True
        if request.from_state and current.lower() != request.from_state.lower():
            activity.logger.info(
                "issue %s está em %r, não em %r; não move para %r",
                request.issue_id,
                current,
                request.from_state,
                request.to_state,
            )
            return False

        target = next(s for s in states if s["name"].lower() == request.to_state.lower())
        result = await self.client.execute(
            UPDATE_STATE_MUTATION, {"id": request.issue_id, "stateId": target["id"]}
        )
        if not result["issueUpdate"]["success"]:
            raise ApplicationError(f"Linear não atualizou o status da issue {request.issue_id}")
        activity.logger.info("issue %s: %r → %r", request.issue_id, current, target["name"])
        return True
