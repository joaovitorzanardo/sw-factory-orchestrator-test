from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from software_factory.orchestration.models import (
        STATE_IN_PLANNING,
        STATE_IN_PROGRESS,
        STATE_TODO,
        CommitPlanRequest,
        MoveIssueRequest,
        PlanIssueInput,
        PlanRequest,
        PlanResult,
    )

MOVE_ISSUE_TIMEOUT = timedelta(seconds=30)
MOVE_ISSUE_RETRY = RetryPolicy(maximum_attempts=5)


@workflow.defn
class PlanIssueWorkflow:
    """Move a issue para In Planning; um sandbox por issue: cria, clona, gera o plano, commita em
    factory/<issue> e sempre destrói; por fim move a issue para In Progress.

    Em falha a issue fica no status em que estava (sem compensação)."""

    @workflow.run
    async def run(self, issue: PlanIssueInput) -> PlanResult:
        # Só sai de To Do: issue que recebeu a label em outro status é planejada sem mover.
        moved: bool = await workflow.execute_activity(
            "move_issue",
            MoveIssueRequest(issue.issue_id, to_state=STATE_IN_PLANNING, from_state=STATE_TODO),
            start_to_close_timeout=MOVE_ISSUE_TIMEOUT,
            retry_policy=MOVE_ISSUE_RETRY,
            result_type=bool,
        )
        if not moved:
            workflow.logger.info("%s fora de %s; segue sem mover", issue.identifier, STATE_TODO)
        sandbox_id: str = await workflow.execute_activity(
            "create_sandbox",
            issue.issue_id,
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        try:
            await workflow.execute_activity(
                "clone_repo",
                sandbox_id,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            plan: str = await workflow.execute_activity(
                "generate_plan",
                PlanRequest(sandbox_id=sandbox_id, issue=issue),
                start_to_close_timeout=timedelta(minutes=45),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=2),
                result_type=str,
            )
            branch: str = await workflow.execute_activity(
                "commit_plan",
                CommitPlanRequest(sandbox_id=sandbox_id, identifier=issue.identifier),
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=RetryPolicy(maximum_attempts=3),
                result_type=str,
            )
        finally:
            await workflow.execute_activity(
                "kill_sandbox",
                sandbox_id,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
        await workflow.execute_activity(
            "move_issue",
            MoveIssueRequest(issue.issue_id, to_state=STATE_IN_PROGRESS),
            start_to_close_timeout=MOVE_ISSUE_TIMEOUT,
            retry_policy=MOVE_ISSUE_RETRY,
            result_type=bool,
        )
        return PlanResult(
            identifier=issue.identifier, plan=plan, sandbox_id=sandbox_id, branch=branch
        )
