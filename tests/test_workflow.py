import unittest
import uuid

from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from software_factory.orchestration.models import (
    CommitPlanRequest,
    MoveIssueRequest,
    PlanIssueInput,
    PlanRequest,
    PlanResult,
)
from software_factory.orchestration.workflow import PlanIssueWorkflow

ISSUE = PlanIssueInput(
    issue_id="issue-1",
    identifier="ENG-12",
    title="Adicionar login",
    description=None,
    url=None,
)


class FakeActivities:
    def __init__(
        self,
        fail_plan: bool = False,
        fail_commit: bool = False,
        skip_planning_move: bool = False,
        fail_planning_move: bool = False,
    ) -> None:
        self.fail_plan = fail_plan
        self.fail_commit = fail_commit
        self.skip_planning_move = skip_planning_move
        self.fail_planning_move = fail_planning_move
        self.calls: list[str] = []
        self.moves: list[MoveIssueRequest] = []

    @activity.defn(name="move_issue")
    async def move_issue(self, request: MoveIssueRequest) -> bool:
        self.calls.append(f"move_issue:{request.to_state}")
        self.moves.append(request)
        if request.to_state == "In Planning":
            if self.fail_planning_move:
                raise ApplicationError("status não existe", non_retryable=True)
            if self.skip_planning_move:
                return False
        return True

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

    @activity.defn(name="commit_plan")
    async def commit_plan(self, request: CommitPlanRequest) -> str:
        self.calls.append("commit_plan")
        if self.fail_commit:
            raise RuntimeError("push falhou")
        return f"factory/{request.identifier}"

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
                    fake.move_issue,
                    fake.create_sandbox,
                    fake.clone_repo,
                    fake.generate_plan,
                    fake.commit_plan,
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
            PlanResult(
                identifier="ENG-12",
                plan="plano para ENG-12 em sbx-1",
                sandbox_id="sbx-1",
                branch="factory/ENG-12",
            ),
        )
        self.assertEqual(
            fake.calls,
            [
                "move_issue:In Planning",
                "create_sandbox",
                "clone_repo",
                "generate_plan",
                "commit_plan",
                "kill_sandbox",
                "move_issue:In Progress",
            ],
        )
        self.assertEqual(
            fake.moves,
            [
                MoveIssueRequest(
                    "issue-1",
                    to_state="In Planning",
                    from_state="To Do",
                    require_states=("In Progress",),
                ),
                MoveIssueRequest("issue-1", to_state="In Progress"),
            ],
        )

    async def test_plans_even_when_planning_move_is_skipped(self) -> None:
        fake = FakeActivities(skip_planning_move=True)
        await self.run_workflow(fake)
        self.assertIn("commit_plan", fake.calls)
        self.assertEqual(fake.calls[-1], "move_issue:In Progress")

    async def test_planning_move_failure_creates_no_sandbox(self) -> None:
        fake = FakeActivities(fail_planning_move=True)
        with self.assertRaises(WorkflowFailureError):
            await self.run_workflow(fake)
        self.assertEqual(fake.calls, ["move_issue:In Planning"])

    async def test_kills_sandbox_on_failure(self) -> None:
        fake = FakeActivities(fail_plan=True)
        with self.assertRaises(WorkflowFailureError):
            await self.run_workflow(fake)
        self.assertEqual(fake.calls[-1], "kill_sandbox")
        self.assertIn("generate_plan", fake.calls)
        self.assertNotIn("move_issue:In Progress", fake.calls)

    async def test_kills_sandbox_on_commit_failure(self) -> None:
        fake = FakeActivities(fail_commit=True)
        with self.assertRaises(WorkflowFailureError):
            await self.run_workflow(fake)
        self.assertEqual(fake.calls[-1], "kill_sandbox")
        self.assertIn("commit_plan", fake.calls)
        self.assertNotIn("move_issue:In Progress", fake.calls)


if __name__ == "__main__":
    unittest.main()
