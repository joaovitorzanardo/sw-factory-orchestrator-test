from dataclasses import dataclass

# Status do Linear procurados por nome (case-insensitive) no time da issue.
STATE_TODO = "To Do"
STATE_IN_PLANNING = "In Planning"
STATE_IN_PROGRESS = "In Progress"


@dataclass(frozen=True)
class PlanIssueInput:
    issue_id: str
    identifier: str
    title: str
    description: str | None
    url: str | None


@dataclass(frozen=True)
class PlanRequest:
    sandbox_id: str
    issue: PlanIssueInput


@dataclass(frozen=True)
class CommitPlanRequest:
    sandbox_id: str
    identifier: str


@dataclass(frozen=True)
class PlanResult:
    identifier: str
    plan: str
    sandbox_id: str
    branch: str


@dataclass(frozen=True)
class MoveIssueRequest:
    issue_id: str
    to_state: str
    # Guarda: só move se o status atual for este.
    from_state: str | None = None
