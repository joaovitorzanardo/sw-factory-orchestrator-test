import json
import unittest
from dataclasses import dataclass, field, replace
from typing import Any

from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from software_factory.config import FactorySettings
from software_factory.orchestration.activities import (
    REPO_DIR,
    SandboxActivities,
    build_plan_prompt,
)
from software_factory.orchestration.models import PlanIssueInput, PlanRequest

SETTINGS = FactorySettings(
    e2b_api_key="e2b-key",
    anthropic_api_key="anthropic-key",
    github_token="gh-token",
    target_repo="acme/app",
)

ISSUE = PlanIssueInput(
    issue_id="issue-1",
    identifier="ENG-12",
    title="Adicionar login",
    description="Login com e-mail e senha.",
    url="https://linear.app/acme/issue/ENG-12",
)


@dataclass
class FakeResult:
    stdout: str
    exit_code: int = 0


@dataclass
class FakeCommands:
    stdout: str = ""
    runs: list[dict[str, Any]] = field(default_factory=list)

    async def run(self, cmd: str, **kwargs: Any) -> FakeResult:
        self.runs.append({"cmd": cmd, **kwargs})
        on_stdout = kwargs.get("on_stdout")
        if on_stdout and self.stdout:
            on_stdout(self.stdout)
        return FakeResult(stdout=self.stdout)


@dataclass
class FakeFiles:
    existing: set[str] = field(default_factory=set)
    written: dict[str, str] = field(default_factory=dict)

    async def exists(self, path: str, **kwargs: Any) -> bool:
        return path in self.existing

    async def write(self, path: str, data: str, **kwargs: Any) -> None:
        self.written[path] = data


class FakeSandbox:
    """Imita o AsyncSandbox do E2B; o estado fica na classe para o teste inspecionar."""

    instance: "FakeSandbox"
    create_calls: list[dict[str, Any]]
    killed: list[str]

    def __init__(self, sandbox_id: str = "sbx-1") -> None:
        self.sandbox_id = sandbox_id
        self.commands = FakeCommands()
        self.files = FakeFiles()

    @classmethod
    def reset(cls) -> None:
        cls.instance = cls()
        cls.create_calls = []
        cls.killed = []

    @classmethod
    async def create(cls, **kwargs: Any) -> "FakeSandbox":
        cls.create_calls.append(kwargs)
        return cls.instance

    @classmethod
    async def connect(cls, sandbox_id: str, **kwargs: Any) -> "FakeSandbox":
        assert sandbox_id == cls.instance.sandbox_id
        return cls.instance

    @classmethod
    async def kill(cls, sandbox_id: str, **kwargs: Any) -> bool:
        cls.killed.append(sandbox_id)
        return True


def stream(*events: dict[str, Any]) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


class TestFactorySettings(unittest.TestCase):
    def test_requires_exactly_one_claude_auth(self) -> None:
        with self.assertRaises(RuntimeError):
            replace(SETTINGS, anthropic_api_key=None)
        with self.assertRaises(RuntimeError):
            replace(SETTINGS, claude_code_oauth_token="oat-token")


class TestBuildPlanPrompt(unittest.TestCase):
    def test_includes_issue_fields(self) -> None:
        prompt = build_plan_prompt(ISSUE)
        self.assertIn("ENG-12", prompt)
        self.assertIn("Adicionar login", prompt)
        self.assertIn("Login com e-mail e senha.", prompt)
        self.assertIn(ISSUE.url, prompt)

    def test_missing_description(self) -> None:
        issue = PlanIssueInput("issue-1", "ENG-12", "Título", None, None)
        self.assertIn("sem descrição", build_plan_prompt(issue))


class TestSandboxActivities(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeSandbox.reset()
        self.activities = SandboxActivities(SETTINGS, sandbox_cls=FakeSandbox)
        self.env = ActivityEnvironment()
        self.heartbeats: list[Any] = []
        self.env.on_heartbeat = lambda *details: self.heartbeats.append(details)

    async def test_create_sandbox(self) -> None:
        sandbox_id = await self.env.run(self.activities.create_sandbox, "issue-1")
        self.assertEqual(sandbox_id, "sbx-1")
        (call,) = FakeSandbox.create_calls
        self.assertEqual(call["template"], "claude")
        self.assertEqual(call["envs"], {"ANTHROPIC_API_KEY": "anthropic-key"})
        self.assertEqual(call["metadata"], {"issue_id": "issue-1"})
        self.assertEqual(call["idempotency_key"], self.env.info.workflow_id)
        self.assertEqual(call["api_key"], "e2b-key")

    async def test_create_sandbox_with_oauth_token(self) -> None:
        settings = replace(SETTINGS, anthropic_api_key=None, claude_code_oauth_token="oat-token")
        activities = SandboxActivities(settings, sandbox_cls=FakeSandbox)
        await self.env.run(activities.create_sandbox, "issue-1")
        (call,) = FakeSandbox.create_calls
        self.assertEqual(call["envs"], {"CLAUDE_CODE_OAUTH_TOKEN": "oat-token"})

    async def test_clone_repo(self) -> None:
        await self.env.run(self.activities.clone_repo, "sbx-1")
        (run,) = FakeSandbox.instance.commands.runs
        self.assertIn("git clone --depth 1", run["cmd"])
        self.assertIn("github.com/acme/app.git", run["cmd"])
        self.assertNotIn("gh-token", run["cmd"])
        self.assertEqual(run["envs"], {"GITHUB_TOKEN": "gh-token"})

    async def test_clone_repo_skips_existing(self) -> None:
        FakeSandbox.instance.files.existing.add(f"{REPO_DIR}/.git")
        await self.env.run(self.activities.clone_repo, "sbx-1")
        self.assertEqual(FakeSandbox.instance.commands.runs, [])

    async def test_generate_plan(self) -> None:
        FakeSandbox.instance.commands.stdout = stream(
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {}},
            {"type": "result", "subtype": "success", "result": "# Plano\n1. fazer"},
        )
        plan = await self.env.run(
            self.activities.generate_plan, PlanRequest(sandbox_id="sbx-1", issue=ISSUE)
        )
        self.assertEqual(plan, "# Plano\n1. fazer")
        self.assertIn("ENG-12", FakeSandbox.instance.files.written["/home/user/prompt.md"])
        (run,) = FakeSandbox.instance.commands.runs
        self.assertIn("claude -p", run["cmd"])
        self.assertIn("--output-format stream-json", run["cmd"])
        self.assertEqual(run["cwd"], REPO_DIR)
        self.assertNotIn("anthropic-key", run["cmd"])
        self.assertTrue(self.heartbeats)

    async def test_generate_plan_error_result(self) -> None:
        FakeSandbox.instance.commands.stdout = stream(
            {"type": "result", "subtype": "error_max_turns", "is_error": True}
        )
        with self.assertRaises(ApplicationError):
            await self.env.run(
                self.activities.generate_plan, PlanRequest(sandbox_id="sbx-1", issue=ISSUE)
            )

    async def test_generate_plan_without_result(self) -> None:
        FakeSandbox.instance.commands.stdout = "não é json\n"
        with self.assertRaises(ApplicationError):
            await self.env.run(
                self.activities.generate_plan, PlanRequest(sandbox_id="sbx-1", issue=ISSUE)
            )

    async def test_kill_sandbox(self) -> None:
        await self.env.run(self.activities.kill_sandbox, "sbx-1")
        self.assertEqual(FakeSandbox.killed, ["sbx-1"])


if __name__ == "__main__":
    unittest.main()
