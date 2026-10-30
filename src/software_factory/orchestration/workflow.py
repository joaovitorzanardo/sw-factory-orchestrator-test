from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from software_factory.orchestration.models import PlanIssueInput, PlanRequest, PlanResult


@workflow.defn
class PlanIssueWorkflow:
    """Um sandbox por issue: cria, clona o repo, gera o plano e sempre destrói o sandbox."""

    @workflow.run
    async def run(self, issue: PlanIssueInput) -> PlanResult:
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
        finally:
            await workflow.execute_activity(
                "kill_sandbox",
                sandbox_id,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
        return PlanResult(identifier=issue.identifier, plan=plan, sandbox_id=sandbox_id)
