from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Strict base model for the synchronous API contract."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    status: str
    service: str
    deterministic_mode_available: bool


class ReviewRequest(ApiModel):
    project_path: str = Field(min_length=1)
    include_pytest_execution: bool = True


class ReviewSummary(ApiModel):
    total: int
    passed: int
    failed: int
    warning: int
    skipped: int
    blocking: int
    status_counts: dict[str, int]
    risk_counts: dict[str, int]


class CheckResultResponse(ApiModel):
    checker_name: str
    status: str
    risk_level: str
    title: str
    message: str
    evidence: list[str]
    recommendation: str | None
    rule_id: str | None
    rule_source: str | None
    file_path: str | None
    metadata: dict[str, Any]
    should_block_release: bool
    passed: bool
    failed: bool


class ReviewResponse(ApiModel):
    project_path: str
    include_pytest_execution: bool
    release_allowed: bool
    summary: ReviewSummary
    results: list[CheckResultResponse]
    artifacts: dict[str, str]
    retrieval_evidence: list[dict[str, Any]]


class VerificationRequest(ApiModel):
    before_project_path: str = Field(min_length=1)
    after_project_path: str = Field(min_length=1)
    include_pytest_execution: bool = True


class VerificationResponse(ApiModel):
    status: str
    resolved: list[str]
    new: list[str]
    unchanged: list[str]
    before_release_allowed: bool
    release_allowed: bool
    route_history: list[str]


class ApiErrorDetail(ApiModel):
    code: str
    message: str
    details: list[dict[str, Any]] = Field(default_factory=list)


class ApiErrorResponse(ApiModel):
    error: ApiErrorDetail
