import json
import os
import unittest
from dataclasses import dataclass, field, replace
from typing import Any, Callable
from unittest import mock

from e2b import FileNotFoundException
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from software_factory.config import FactorySettings
from software_factory.orchestration.activities import (
    REPO_DIR,
    SandboxActivities,
    build_plan_prompt,
    plan_path,
)
from software_factory.orchestration.models import CommitPlanRequest, PlanIssueInput, PlanRequest

SETTINGS = FactorySettings(
    e2b_api_key="e2b-key",
    anthropic_api_key="anthropic-key",
    github_token="gh-token",
    target_repo="acme/app",
    linear_api_key="lin-key",
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
    # substring do comando -> stdout; a primeira chave contida no comando vence.
    responses: dict[str, str] = field(default_factory=dict)
    on_run: Callable[[str], None] | None = None
    runs: list[dict[str, Any]] = field(default_factory=list)

    async def run(self, cmd: str, **kwargs: Any) -> FakeResult:
        self.runs.append({"cmd": cmd, **kwargs})
        stdout = next((out for key, out in self.responses.items() if key in cmd), self.stdout)
        on_stdout = kwargs.get("on_stdout")
        if on_stdout and stdout:
            on_stdout(stdout)
        if self.on_run:
            self.on_run(cmd)
        return FakeResult(stdout=stdout)


@dataclass
class FakeFiles:
    existing: set[str] = field(default_factory=set)
    written: dict[str, str] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)

    async def exists(self, path: str, **kwargs: Any) -> bool:
        return path in self.existing or path in self.written

    async def write(self, path: str, data: str, **kwargs: Any) -> None:
        self.written[path] = data

    async def read(self, path: str, **kwargs: Any) -> str:
        try:
            return self.written[path]
        except KeyError:
            raise FileNotFoundException(path) from None

    async def remove(self, path: str, **kwargs: Any) -> None:
        self.written.pop(path, None)
        self.existing.discard(path)
        self.removed.append(path)


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


PLAN_FILE = f"{REPO_DIR}/docs/superpowers/plans/ENG-12.md"
SKILL_DEST = "/home/user/.claude/skills/writing-plans/SKILL.md"


def stream(*events: dict[str, Any]) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


class TestFactorySettings(unittest.TestCase):
    def test_requires_exactly_one_claude_auth(self) -> None:
        with self.assertRaises(RuntimeError):
            replace(SETTINGS, anthropic_api_key=None)
        with self.assertRaises(RuntimeError):
            replace(SETTINGS, claude_code_oauth_token="oat-token")

    def test_from_env_requires_linear_api_key(self) -> None:
        env = {
            "E2B_API_KEY": "e2b-key",
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GITHUB_TOKEN": "gh-token",
            "TARGET_REPO": "acme/app",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                FactorySettings.from_env()
        with mock.patch.dict(os.environ, {**env, "LINEAR_API_KEY": "lin-key"}, clear=True):
            self.assertEqual(FactorySettings.from_env().linear_api_key, "lin-key")


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

    def test_prompt_uses_skill_and_path(self) -> None:
        prompt = build_plan_prompt(ISSUE)
        self.assertIn("writing-plans", prompt)
        self.assertIn("docs/superpowers/plans/ENG-12.md", prompt)


class TestPlanPath(unittest.TestCase):
    def test_plan_path(self) -> None:
        self.assertEqual(plan_path("ENG-12"), "docs/superpowers/plans/ENG-12.md")

    def test_plan_path_rejects_unsafe_identifier(self) -> None:
        for bad in ["ENG-12; rm -rf /", "../ENG-12", "ENG", ""]:
            with self.subTest(bad=bad), self.assertRaises(ApplicationError) as ctx:
                plan_path(bad)
            self.assertTrue(ctx.exception.non_retryable)


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

    def claude_saves_plan(self, content: str = "# Plano\n1. fazer") -> None:
        files = FakeSandbox.instance.files

        def on_run(cmd: str) -> None:
            if "claude -p" in cmd:
                files.written[PLAN_FILE] = content

        FakeSandbox.instance.commands.on_run = on_run
        FakeSandbox.instance.commands.stdout = stream(
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {}},
            {"type": "result", "subtype": "success", "result": "Plano salvo."},
        )

    async def test_generate_plan(self) -> None:
        self.claude_saves_plan()
        plan = await self.env.run(
            self.activities.generate_plan, PlanRequest(sandbox_id="sbx-1", issue=ISSUE)
        )
        self.assertEqual(plan, "# Plano\n1. fazer")
        files = FakeSandbox.instance.files
        self.assertIn("ENG-12", files.written["/home/user/prompt.md"])
        self.assertTrue(files.written[SKILL_DEST].startswith("---\nname: writing-plans"))
        (run,) = FakeSandbox.instance.commands.runs
        self.assertIn("claude -p", run["cmd"])
        self.assertIn("--output-format stream-json", run["cmd"])
        self.assertIn("Skill", run["cmd"])
        self.assertIn("Edit(docs/superpowers/plans/ENG-12.md)", run["cmd"])
        self.assertNotIn("Bash", run["cmd"])
        self.assertEqual(run["cwd"], REPO_DIR)
        self.assertNotIn("anthropic-key", run["cmd"])
        self.assertTrue(self.heartbeats)

    async def test_generate_plan_removes_stale_plan(self) -> None:
        FakeSandbox.instance.files.written[PLAN_FILE] = "antigo"
        self.claude_saves_plan("novo")
        plan = await self.env.run(
            self.activities.generate_plan, PlanRequest(sandbox_id="sbx-1", issue=ISSUE)
        )
        self.assertEqual(plan, "novo")
        self.assertIn(PLAN_FILE, FakeSandbox.instance.files.removed)

    async def test_generate_plan_missing_file(self) -> None:
        FakeSandbox.instance.commands.stdout = stream(
            {"type": "result", "subtype": "success", "result": "Plano salvo."}
        )
        with self.assertRaises(ApplicationError):
            await self.env.run(
                self.activities.generate_plan, PlanRequest(sandbox_id="sbx-1", issue=ISSUE)
            )

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

    async def run_commit_plan(self, remote: str) -> str:
        FakeSandbox.instance.files.existing.add(PLAN_FILE)
        FakeSandbox.instance.commands.responses = {"rev-parse": "abc123\n", "ls-remote": remote}
        return await self.env.run(
            self.activities.commit_plan, CommitPlanRequest(sandbox_id="sbx-1", identifier="ENG-12")
        )

    def commands(self) -> list[str]:
        return [run["cmd"] for run in FakeSandbox.instance.commands.runs]

    async def test_commit_plan_pushes_new_branch(self) -> None:
        branch = await self.run_commit_plan(remote="")
        self.assertEqual(branch, "factory/ENG-12")
        self.assertEqual(
            self.commands(),
            [
                "git checkout -B factory/ENG-12",
                "git add docs/superpowers/plans/ENG-12.md",
                'git diff --cached --quiet || git -c user.name="software-factory"'
                ' -c user.email="software-factory@users.noreply.github.com"'
                ' commit -m "docs: plano de implementação ENG-12"',
                "git rev-parse HEAD",
                "git ls-remote --heads origin refs/heads/factory/ENG-12",
                "git push -u origin factory/ENG-12",
            ],
        )
        for run in FakeSandbox.instance.commands.runs:
            self.assertEqual(run["cwd"], REPO_DIR)
            self.assertNotIn("gh-token", run["cmd"])

    async def test_commit_plan_idempotent_after_push(self) -> None:
        branch = await self.run_commit_plan(remote="abc123\trefs/heads/factory/ENG-12\n")
        self.assertEqual(branch, "factory/ENG-12")
        self.assertFalse(any("git push" in cmd for cmd in self.commands()))

    async def test_commit_plan_rejects_diverged_remote(self) -> None:
        with self.assertRaises(ApplicationError) as ctx:
            await self.run_commit_plan(remote="zzz999\trefs/heads/factory/ENG-12\n")
        self.assertTrue(ctx.exception.non_retryable)
        self.assertIn("factory/ENG-12", str(ctx.exception))
        self.assertFalse(any("git push" in cmd for cmd in self.commands()))

    async def test_commit_plan_missing_plan_file(self) -> None:
        with self.assertRaises(ApplicationError):
            await self.env.run(
                self.activities.commit_plan,
                CommitPlanRequest(sandbox_id="sbx-1", identifier="ENG-12"),
            )
        self.assertEqual(self.commands(), [])

    async def test_kill_sandbox(self) -> None:
        await self.env.run(self.activities.kill_sandbox, "sbx-1")
        self.assertEqual(FakeSandbox.killed, ["sbx-1"])


if __name__ == "__main__":
    unittest.main()
