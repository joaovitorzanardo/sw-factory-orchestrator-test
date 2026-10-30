import unittest
import uuid

from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from software_factory.orchestration.models import PlanIssueInput, PlanRequest, PlanResult
from software_factory.orchestration.workflow import PlanIssueWorkflow

ISSUE = PlanIssueInput(
    issue_id="issue-1",
    identifier="ENG-12",
    title="Adicionar login",
    description=None,
    url=None,
)


class FakeActivities:
    def __init__(self, fail_plan: bool = False) -> None:
        self.fail_plan = fail_plan
        self.calls: list[str] = []

    @activity.defn(name="create_sandbox")
    async def create_sandbox(self, issue_id: str) -> str:
        self.calls.append("create_sandbox")
        return "sbx-1"

    @activity.defn(name="clone_repo")
    async def clone_repo(self, sandbox_id: str) -> None:
        self.calls.append("clone_repo")

    @activity.defn(name="generate_plan")
    async def generate_plan(self, request: PlanRequest) -> str:
        self.calls.append("generate_plan")
        if self.fail_plan:
            raise RuntimeError("claude falhou")
        return f"plano para {request.issue.identifier} em {request.sandbox_id}"

    @activity.defn(name="kill_sandbox")
    async def kill_sandbox(self, sandbox_id: str) -> None:
        self.calls.append("kill_sandbox")


class TestPlanIssueWorkflow(unittest.IsolatedAsyncioTestCase):
    async def run_workflow(self, fake: FakeActivities) -> PlanResult:
        task_queue = f"test-{uuid.uuid4()}"
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue=task_queue,
                workflows=[PlanIssueWorkflow],
                activities=[
                    fake.create_sandbox,
                    fake.clone_repo,
                    fake.generate_plan,
                    fake.kill_sandbox,
                ],
            ):
                return await env.client.execute_workflow(
                    PlanIssueWorkflow.run,
                    ISSUE,
                    id=f"plan-issue-{uuid.uuid4()}",
                    task_queue=task_queue,
                )

    async def test_happy_path(self) -> None:
        fake = FakeActivities()
        result = await self.run_workflow(fake)
        self.assertEqual(
            result,
            PlanResult(identifier="ENG-12", plan="plano para ENG-12 em sbx-1", sandbox_id="sbx-1"),
        )
        self.assertEqual(
            fake.calls, ["create_sandbox", "clone_repo", "generate_plan", "kill_sandbox"]
        )

    async def test_kills_sandbox_on_failure(self) -> None:
        fake = FakeActivities(fail_plan=True)
        with self.assertRaises(WorkflowFailureError):
            await self.run_workflow(fake)
        self.assertEqual(fake.calls[-1], "kill_sandbox")
        self.assertIn("generate_plan", fake.calls)


if __name__ == "__main__":
    unittest.main()
