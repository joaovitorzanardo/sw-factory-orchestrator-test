from dataclasses import dataclass


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
