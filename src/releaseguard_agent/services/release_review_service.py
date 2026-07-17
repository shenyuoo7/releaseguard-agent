from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from releaseguard_agent.agents import (
    ReleaseDecisionAdviceArtifacts,
    ReleaseDecisionAdviceResult,
    ReleaseDecisionAgent,
    ReleaseDecisionExplainer,
    ReleaseDecisionWorkflowResult,
    get_default_rule_index_path,
    write_advice_artifacts,
)
from releaseguard_agent.core.checker_runner import CheckerRunner
from releaseguard_agent.core.default_checkers import build_default_python_runner
from releaseguard_agent.models.check_result import CheckResult, CheckStatus, RiskLevel
from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.observability import (
    TraceArtifacts,
    build_trace_payload,
    write_trace_artifact,
)
from releaseguard_agent.reports import (
    ReleaseChecklistArtifacts,
    ReportArtifacts,
    build_release_checklist_payload,
    build_report_payload,
    write_release_checklist_artifact,
    write_report_artifacts,
)
from releaseguard_agent.rag import RuleRetrievalService


RunnerFactory = Callable[..., CheckerRunner]


class ReleaseReviewError(Exception):
    """Base exception for review-service input and artifact failures."""


class InvalidProjectPathError(ReleaseReviewError):
    """Raised when the requested project path cannot be reviewed."""


class ReviewArtifactError(ReleaseReviewError):
    """Raised when one requested review artifact cannot be written."""

    def __init__(
        self,
        *,
        artifact_label: str,
        output_dir: Path,
        cause: Exception,
    ) -> None:
        self.artifact_label = artifact_label
        self.output_dir = output_dir
        self.cause_type = type(cause).__name__
        super().__init__(
            f"Could not write {artifact_label} to {output_dir}: {cause}"
        )


@dataclass(frozen=True)
class ReleaseReviewArtifacts:
    """Optional artifacts produced during one review run."""

    report: ReportArtifacts | None = None
    checklist: ReleaseChecklistArtifacts | None = None
    advice: ReleaseDecisionAdviceArtifacts | None = None
    trace: TraceArtifacts | None = None

    def output_paths(self) -> dict[str, str]:
        """Return stable artifact names and paths for trace/API consumers."""
        paths: dict[str, str] = {}

        if self.report is not None:
            paths["release_report"] = str(self.report.markdown_path)
            paths["check_result"] = str(self.report.json_path)
        if self.checklist is not None:
            paths["release_checklist"] = str(self.checklist.markdown_path)
        if self.advice is not None:
            paths["release_decision_advice_markdown"] = str(
                self.advice.markdown_path
            )
            paths["release_decision_advice_json"] = str(self.advice.json_path)
        if self.trace is not None:
            paths["trace"] = str(self.trace.trace_path)

        return paths


@dataclass(frozen=True)
class ReleaseReviewResult:
    """Complete deterministic result from one repository review."""

    project_path: Path
    include_pytest_execution: bool
    check_results: tuple[CheckResult, ...]
    summary: dict[str, object]
    report_payload: dict[str, Any]
    advice_result: ReleaseDecisionAdviceResult | None
    retrieval_evidence: tuple[RetrievalEvidence, ...]
    artifacts: ReleaseReviewArtifacts

    @property
    def release_allowed(self) -> bool:
        """Return the deterministic blocking-policy outcome."""
        return _summary_count(self.summary, "blocking") == 0

    def to_dict(self) -> dict[str, Any]:
        """Return the stable report payload used by CLI and future APIs."""
        return self.report_payload


class ReleaseReviewService:
    """Run one scan and orchestrate all current ReleaseGuard artifacts."""

    def __init__(
        self,
        *,
        runner_factory: RunnerFactory | None = None,
        rule_index_path: Path | None = None,
        source_directory: Path | None = None,
    ) -> None:
        self._runner_factory = runner_factory or build_default_python_runner
        self._rule_index_path = Path(
            rule_index_path or get_default_rule_index_path()
        )
        self._source_directory = (
            Path(source_directory) if source_directory is not None else None
        )

    def review(
        self,
        *,
        project_path: Path,
        include_pytest_execution: bool = True,
        report_output_dir: Path | None = None,
        checklist_output_dir: Path | None = None,
        advice_output_dir: Path | None = None,
        trace_output_dir: Path | None = None,
        command_args: list[str] | None = None,
        output_format: str = "text",
    ) -> ReleaseReviewResult:
        """Review a project once and optionally persist requested artifacts."""
        normalized_project_path = Path(project_path).expanduser().resolve()
        _validate_project_path(normalized_project_path)

        runner = self._runner_factory(
            include_pytest_execution=include_pytest_execution
        )
        check_results = tuple(runner.run(normalized_project_path))
        summary = build_result_summary(check_results)
        retrieval_evidence = self._retrieve_exact_evidence(check_results)
        report_payload = build_report_payload(
            project_path=normalized_project_path,
            include_pytest_execution=include_pytest_execution,
            results=list(check_results),
            summary=summary,
        )
        report_payload["retrieval_evidence"] = [
            item.to_dict() for item in retrieval_evidence
        ]

        report_artifacts = self._write_report(
            output_dir=report_output_dir,
            payload=report_payload,
        )
        checklist_artifacts = self._write_checklist(
            output_dir=checklist_output_dir,
            project_path=normalized_project_path,
            include_pytest_execution=include_pytest_execution,
            check_results=check_results,
            summary=summary,
        )
        advice_result, advice_artifacts = self._write_advice(
            output_dir=advice_output_dir,
            project_path=normalized_project_path,
            check_results=check_results,
        )
        artifacts = ReleaseReviewArtifacts(
            report=report_artifacts,
            checklist=checklist_artifacts,
            advice=advice_artifacts,
        )

        trace_artifacts = self._write_trace(
            output_dir=trace_output_dir,
            project_path=normalized_project_path,
            include_pytest_execution=include_pytest_execution,
            output_format=output_format,
            command_args=command_args or [],
            summary=summary,
            advice_result=advice_result,
            artifacts=artifacts,
        )

        artifacts = ReleaseReviewArtifacts(
            report=report_artifacts,
            checklist=checklist_artifacts,
            advice=advice_artifacts,
            trace=trace_artifacts,
        )

        return ReleaseReviewResult(
            project_path=normalized_project_path,
            include_pytest_execution=include_pytest_execution,
            check_results=check_results,
            summary=summary,
            report_payload=report_payload,
            advice_result=advice_result,
            retrieval_evidence=retrieval_evidence,
            artifacts=artifacts,
        )

    def _retrieve_exact_evidence(
        self,
        check_results: tuple[CheckResult, ...],
    ) -> tuple[RetrievalEvidence, ...]:
        service = RuleRetrievalService(self._rule_index_path)
        evidence: dict[str, RetrievalEvidence] = {}
        for result in check_results:
            if not result.rule_id:
                continue
            retrieved = service.retrieve(result.rule_id, mode="exact", top_k=10)
            for item in retrieved.evidence:
                evidence.setdefault(item.evidence_id, item)
        return tuple(evidence.values())

    def _write_report(
        self,
        *,
        output_dir: Path | None,
        payload: dict[str, Any],
    ) -> ReportArtifacts | None:
        if output_dir is None:
            return None
        normalized = Path(output_dir).expanduser().resolve()
        try:
            return write_report_artifacts(output_dir=normalized, payload=payload)
        except (OSError, TypeError, ValueError) as exc:
            raise ReviewArtifactError(
                artifact_label="report artifacts",
                output_dir=normalized,
                cause=exc,
            ) from exc

    def _write_checklist(
        self,
        *,
        output_dir: Path | None,
        project_path: Path,
        include_pytest_execution: bool,
        check_results: tuple[CheckResult, ...],
        summary: dict[str, object],
    ) -> ReleaseChecklistArtifacts | None:
        if output_dir is None:
            return None
        normalized = Path(output_dir).expanduser().resolve()
        try:
            payload = build_release_checklist_payload(
                project_path=project_path,
                include_pytest_execution=include_pytest_execution,
                results=list(check_results),
                summary=summary,
            )
            return write_release_checklist_artifact(
                output_dir=normalized,
                payload=payload,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ReviewArtifactError(
                artifact_label="release checklist artifact",
                output_dir=normalized,
                cause=exc,
            ) from exc

    def _write_advice(
        self,
        *,
        output_dir: Path | None,
        project_path: Path,
        check_results: tuple[CheckResult, ...],
    ) -> tuple[
        ReleaseDecisionAdviceResult | None,
        ReleaseDecisionAdviceArtifacts | None,
    ]:
        if output_dir is None:
            return None, None
        normalized = Path(output_dir).expanduser().resolve()
        try:
            advice_result = build_agent_advice_result(
                project_path=project_path,
                results=check_results,
                rule_index_path=self._rule_index_path,
                source_directory=self._source_directory,
            )
            artifacts = write_advice_artifacts(
                output_dir=normalized,
                advice_result=advice_result,
            )
            return advice_result, artifacts
        except (OSError, TypeError, ValueError) as exc:
            raise ReviewArtifactError(
                artifact_label="Agent advice artifacts",
                output_dir=normalized,
                cause=exc,
            ) from exc

    def _write_trace(
        self,
        *,
        output_dir: Path | None,
        project_path: Path,
        include_pytest_execution: bool,
        output_format: str,
        command_args: list[str],
        summary: dict[str, object],
        advice_result: ReleaseDecisionAdviceResult | None,
        artifacts: ReleaseReviewArtifacts,
    ) -> TraceArtifacts | None:
        if output_dir is None:
            return None
        normalized = Path(output_dir).expanduser().resolve()
        created_at = _utc_now_iso()
        try:
            payload = build_trace_payload(
                run_id=f"releaseguard-check-{created_at}",
                created_at=created_at,
                project_path=project_path,
                command_args=command_args,
                environment_summary={
                    "include_pytest_execution": include_pytest_execution,
                    "output_format": output_format,
                },
                input_artifacts={"rule_index": self._rule_index_path},
                output_artifacts={
                    name: path
                    for name, path in artifacts.output_paths().items()
                },
                decision_summary=_build_trace_decision_summary(
                    summary=summary,
                    advice_result=advice_result,
                ),
            )
            return write_trace_artifact(output_dir=normalized, payload=payload)
        except (OSError, TypeError, ValueError) as exc:
            raise ReviewArtifactError(
                artifact_label="trace artifact",
                output_dir=normalized,
                cause=exc,
            ) from exc


def build_result_summary(
    results: tuple[CheckResult, ...] | list[CheckResult],
) -> dict[str, object]:
    """Aggregate stable status, risk, and blocking counts."""
    status_counts = {status.value: 0 for status in CheckStatus}
    risk_counts = {risk.value: 0 for risk in RiskLevel}

    for result in results:
        status_counts[result.status.value] += 1
        risk_counts[result.risk_level.value] += 1

    return {
        "total": len(results),
        "passed": status_counts[CheckStatus.PASSED.value],
        "failed": status_counts[CheckStatus.FAILED.value],
        "warning": status_counts[CheckStatus.WARNING.value],
        "skipped": status_counts[CheckStatus.SKIPPED.value],
        "blocking": sum(result.should_block_release for result in results),
        "status_counts": status_counts,
        "risk_counts": risk_counts,
    }


def build_agent_advice_result(
    *,
    project_path: Path,
    results: tuple[CheckResult, ...] | list[CheckResult],
    rule_index_path: Path | None = None,
    source_directory: Path | None = None,
) -> ReleaseDecisionAdviceResult:
    """Build deterministic advice from an already-computed scan."""
    check_results = tuple(results)
    agent = ReleaseDecisionAgent.from_rule_index(
        rule_index_path or get_default_rule_index_path(),
        source_directory=source_directory,
    )
    decision = agent.decide(check_results)
    explanation = ReleaseDecisionExplainer().explain(decision)
    return ReleaseDecisionAdviceResult(
        workflow_result=ReleaseDecisionWorkflowResult(
            project_path=project_path,
            check_results=check_results,
            decision=decision,
        ),
        explanation=explanation,
    )


def _validate_project_path(project_path: Path) -> None:
    if not project_path.exists():
        raise InvalidProjectPathError(
            f"Project path does not exist: {project_path}"
        )
    if not project_path.is_dir():
        raise InvalidProjectPathError(
            f"Project path is not a directory: {project_path}"
        )


def _build_trace_decision_summary(
    *,
    summary: dict[str, object],
    advice_result: ReleaseDecisionAdviceResult | None,
) -> dict[str, object]:
    blocking_count = _summary_count(summary, "blocking")
    decision_summary: dict[str, object] = {
        "status": "blocked" if blocking_count > 0 else "ready",
        "release_allowed": blocking_count == 0,
        "blocking_count": blocking_count,
    }
    if advice_result is not None:
        decision = advice_result.workflow_result.decision
        decision_summary.update(
            {
                "status": decision.status.value,
                "release_allowed": decision.release_allowed,
                "blocking_rule_ids": list(decision.blocking_rule_ids),
                "warning_rule_ids": list(decision.warning_rule_ids),
                "missing_rule_evidence_count": (
                    decision.missing_rule_evidence_count
                ),
            }
        )
    return decision_summary


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _summary_count(summary: dict[str, object], key: str) -> int:
    value = summary.get(key)
    if not isinstance(value, int):
        raise TypeError(f"Summary field {key!r} must be an integer.")
    return value
