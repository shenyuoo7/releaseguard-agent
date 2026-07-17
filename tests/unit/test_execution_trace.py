import json
from pathlib import Path

from releaseguard_agent.llm import FakeLLMClient, LLMRuntime
from releaseguard_agent.observability import ExecutionTracer
from releaseguard_agent.services.agent_workflow_service import (
    ReleaseAgentWorkflowService,
)
from releaseguard_agent.services.verification_service import (
    ReleaseVerificationService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "sample_projects"


def test_execution_tracer_redacts_sensitive_keys_and_values() -> None:
    tracer = ExecutionTracer(run_id="test-run")
    with tracer.span(
        "llm",
        tool="llm.complete",
        api_key="sk-supersecretvalue",
        nested={"authorization": "Bearer hidden"},
        message="request token-abcdefghijk failed",
    ):
        pass

    event = tracer.to_dict()["events"][0]
    assert event["api_key"] == "[REDACTED]"
    assert event["nested"]["authorization"] == "[REDACTED]"
    assert "abcdefghijk" not in event["message"]


def test_agent_workflow_trace_records_nodes_tools_retrieval_llm_and_artifact(
    tmp_path: Path,
) -> None:
    runtime = LLMRuntime(
        mode="llm",
        provider="fake",
        model="fake-trace-model",
        client=FakeLLMClient(["not-json"]),
    )
    result = ReleaseAgentWorkflowService(llm_runtime=runtime).run(
        project_path=SAMPLES / "fastapi_bad_project",
        include_pytest_execution=False,
        trace_output_dir=tmp_path / "trace",
    )

    events = result.trace["events"]
    assert result.trace["run_id"].startswith("rg-")
    assert {event["node"] for event in events if event["kind"] == "node"} >= {
        "scan",
        "evidence_agent",
        "risk_agent",
        "deterministic_fallback",
        "fix_planner_agent",
    }
    assert {event["tool"] for event in events if event["tool"]} >= {
        "scan_project",
        "search_rule_evidence",
        "analyze_risk",
        "llm.complete",
        "build_fix_plan",
    }
    retrieval = next(event for event in events if event["kind"] == "retrieval")
    assert retrieval["retrieval_candidates"]
    assert retrieval["evidence_ids"]
    llm = next(event for event in events if event["kind"] == "llm")
    assert llm["provider"] == "fake"
    assert llm["model"] == "fake-trace-model"
    assert llm["error_type"] == "ReleaseRiskAnalysisParseError"
    assert any(event["kind"] == "route" for event in events)
    assert result.trace_artifacts is not None
    payload = json.loads(
        result.trace_artifacts.trace_path.read_text(encoding="utf-8")
    )
    assert payload["artifact_paths"]["execution_trace"].endswith(
        "execution_trace.json"
    )


def test_verification_trace_records_before_after_delta(tmp_path: Path) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_project(before, dependency=False)
    _write_project(after, dependency=True)

    result = ReleaseVerificationService().verify(
        before_project_path=before,
        after_project_path=after,
        include_pytest_execution=False,
        trace_output_dir=tmp_path / "verification-trace",
    )

    events = result.after_workflow.trace["events"]
    assert any(event["node"] == "baseline_scan" for event in events)
    verifier = next(event for event in events if event["node"] == "verifier_agent")
    assert verifier["before_after_delta"]["resolved"]
    assert verifier["before_after_delta"]["release_allowed"] is True


def _write_project(path: Path, *, dependency: bool) -> None:
    path.mkdir()
    if dependency:
        (path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (path / ".env.example").write_text("APP_ENV=test\n", encoding="utf-8")
    (path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n", encoding="utf-8"
    )
