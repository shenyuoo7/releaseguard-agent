from collections.abc import Iterable
from pathlib import Path

from fastapi import FastAPI

from releaseguard_agent.api.errors import ApiError, install_error_handlers
from releaseguard_agent.api.path_policy import (
    ProjectPathNotAllowedError,
    ProjectPathPolicy,
)
from releaseguard_agent.api.schemas import (
    ApiErrorResponse,
    CheckResultResponse,
    HealthResponse,
    ReviewRequest,
    ReviewResponse,
    ReviewSummary,
    VerificationRequest,
    VerificationResponse,
)
from releaseguard_agent.services import (
    InvalidProjectPathError,
    ReleaseReviewResult,
    ReleaseReviewService,
)
from releaseguard_agent.services.verification_service import (
    ReleaseVerificationService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def create_app(
    *,
    review_service: ReleaseReviewService | None = None,
    verification_service: ReleaseVerificationService | None = None,
    allowed_project_roots: Iterable[Path] | None = None,
) -> FastAPI:
    """Build the synchronous ReleaseGuard API with explicit dependencies."""
    application = FastAPI(
        title="ReleaseGuard Agent API",
        version="0.3.0",
        description=(
            "Synchronous local API for deterministic pre-release reviews."
        ),
    )
    service = review_service or ReleaseReviewService()
    verifier = verification_service or ReleaseVerificationService(
        review_service=service
    )
    path_policy = ProjectPathPolicy(
        allowed_project_roots or (PROJECT_ROOT,)
    )
    install_error_handlers(application)

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="releaseguard-agent",
            deterministic_mode_available=True,
        )

    @application.post(
        "/reviews",
        response_model=ReviewResponse,
        responses={
            400: {"model": ApiErrorResponse},
            403: {"model": ApiErrorResponse},
            404: {"model": ApiErrorResponse},
            422: {"model": ApiErrorResponse},
        },
    )
    def create_review(request: ReviewRequest) -> ReviewResponse:
        try:
            project_path = path_policy.resolve_allowed(request.project_path)
        except ProjectPathNotAllowedError as exc:
            raise ApiError(
                status_code=403,
                code="project_path_not_allowed",
                message=str(exc),
            ) from exc

        try:
            result = service.review(
                project_path=project_path,
                include_pytest_execution=request.include_pytest_execution,
            )
        except InvalidProjectPathError as exc:
            status_code = 404 if not project_path.exists() else 400
            code = (
                "project_path_not_found"
                if status_code == 404
                else "project_path_invalid"
            )
            raise ApiError(
                status_code=status_code,
                code=code,
                message=str(exc),
            ) from exc

        return _review_response(result)

    @application.post(
        "/verifications",
        response_model=VerificationResponse,
        responses={
            400: {"model": ApiErrorResponse},
            403: {"model": ApiErrorResponse},
            404: {"model": ApiErrorResponse},
            422: {"model": ApiErrorResponse},
        },
    )
    def create_verification(
        request: VerificationRequest,
    ) -> VerificationResponse:
        before_path = _resolve_api_path(
            path_policy, request.before_project_path
        )
        after_path = _resolve_api_path(path_policy, request.after_project_path)
        try:
            result = verifier.verify(
                before_project_path=before_path,
                after_project_path=after_path,
                include_pytest_execution=request.include_pytest_execution,
            )
        except InvalidProjectPathError as exc:
            missing = not before_path.exists() or not after_path.exists()
            raise ApiError(
                status_code=404 if missing else 400,
                code=(
                    "project_path_not_found" if missing else "project_path_invalid"
                ),
                message=str(exc),
            ) from exc
        return VerificationResponse(
            status=result.delta.status,
            resolved=list(result.delta.resolved),
            new=list(result.delta.new),
            unchanged=list(result.delta.unchanged),
            before_release_allowed=result.delta.before_release_allowed,
            release_allowed=result.delta.release_allowed,
            route_history=list(
                result.after_workflow.state.get("route_history", [])
            ),
        )

    return application


def _resolve_api_path(
    policy: ProjectPathPolicy,
    raw_path: str,
) -> Path:
    try:
        return policy.resolve_allowed(raw_path)
    except ProjectPathNotAllowedError as exc:
        raise ApiError(
            status_code=403,
            code="project_path_not_allowed",
            message=str(exc),
        ) from exc


def _review_response(result: ReleaseReviewResult) -> ReviewResponse:
    summary = ReviewSummary.model_validate(result.summary)
    checks = [
        CheckResultResponse.model_validate(check.to_dict())
        for check in result.check_results
    ]
    return ReviewResponse(
        project_path=str(result.project_path),
        include_pytest_execution=result.include_pytest_execution,
        release_allowed=result.release_allowed,
        summary=summary,
        results=checks,
        artifacts=result.artifacts.output_paths(),
        retrieval_evidence=[
            item.to_dict() for item in result.retrieval_evidence
        ],
    )


app = create_app()
