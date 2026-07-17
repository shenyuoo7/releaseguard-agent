import json
from pathlib import Path

import pytest

from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.services import (
    InvalidProjectPathError,
    ReleaseReviewService,
)


def _passed_result(project_path: Path) -> CheckResult:
    return CheckResult(
        checker_name="fake_checker",
        status=CheckStatus.PASSED,
        risk_level=RiskLevel.INFO,
        title="Dependency declaration exists",
        message="The test project has a dependency declaration.",
        rule_id="RG-DEPS-001",
        rule_source="Offline test rule",
        file_path=str(project_path / "requirements.txt"),
    )


def test_review_runs_checker_once_and_reuses_results_for_all_artifacts(
    tmp_path: Path,
) -> None:
    calls: list[Path] = []

    class FakeRunner:
        def run(self, project_path: Path) -> list[CheckResult]:
            calls.append(project_path)
            return [_passed_result(project_path)]

    def runner_factory(*, include_pytest_execution: bool) -> FakeRunner:
        assert include_pytest_execution is False
        return FakeRunner()

    service = ReleaseReviewService(runner_factory=runner_factory)
    result = service.review(
        project_path=tmp_path,
        include_pytest_execution=False,
        report_output_dir=tmp_path / "report",
        checklist_output_dir=tmp_path / "checklist",
        advice_output_dir=tmp_path / "advice",
        trace_output_dir=tmp_path / "trace",
        command_args=["check", str(tmp_path)],
        output_format="json",
    )

    assert calls == [tmp_path.resolve()]
    assert result.release_allowed is True
    assert result.summary["total"] == 1
    assert result.summary["blocking"] == 0
    assert result.advice_result is not None
    assert result.advice_result.check_results == result.check_results
    assert result.retrieval_evidence
    evidence = result.retrieval_evidence[0]
    assert evidence.rule_id == "RG-DEPS-001"
    assert evidence.chunk_id.startswith("RG-DEPS-001:chunk-")
    assert evidence.retrieval_method == "exact"
    assert result.artifacts.report is not None
    assert result.artifacts.checklist is not None
    assert result.artifacts.advice is not None
    assert result.artifacts.trace is not None

    trace_payload = json.loads(
        result.artifacts.trace.trace_path.read_text(encoding="utf-8")
    )
    assert trace_payload["environment"]["output_format"] == "json"
    assert trace_payload["decision"]["release_allowed"] is True
    assert trace_payload["outputs"]["check_result"].endswith(
        "check_result.json"
    )


def test_review_without_output_directories_performs_no_artifact_writes(
    tmp_path: Path,
) -> None:
    class FakeRunner:
        def run(self, project_path: Path) -> list[CheckResult]:
            return [_passed_result(project_path)]

    service = ReleaseReviewService(
        runner_factory=lambda **_: FakeRunner()
    )
    result = service.review(
        project_path=tmp_path,
        include_pytest_execution=False,
    )

    assert result.artifacts.output_paths() == {}
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_review_rejects_missing_project_before_building_runner(
    tmp_path: Path,
) -> None:
    runner_was_built = False

    def runner_factory(**_: object) -> object:
        nonlocal runner_was_built
        runner_was_built = True
        raise AssertionError("runner must not be built")

    service = ReleaseReviewService(runner_factory=runner_factory)
    missing_path = tmp_path / "missing"

    with pytest.raises(
        InvalidProjectPathError,
        match="Project path does not exist",
    ):
        service.review(project_path=missing_path)

    assert runner_was_built is False


def test_review_result_payload_is_the_cli_report_contract(
    tmp_path: Path,
) -> None:
    class FakeRunner:
        def run(self, project_path: Path) -> list[CheckResult]:
            return [_passed_result(project_path)]

    result = ReleaseReviewService(
        runner_factory=lambda **_: FakeRunner()
    ).review(project_path=tmp_path, include_pytest_execution=False)

    payload = result.to_dict()
    assert payload is result.report_payload
    assert payload["project_path"] == str(tmp_path.resolve())
    assert payload["include_pytest_execution"] is False
    assert payload["summary"]["blocking"] == 0
    assert payload["results"][0]["checker_name"] == "fake_checker"
    assert payload["retrieval_evidence"][0]["rule_id"] == "RG-DEPS-001"
