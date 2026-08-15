import json
from pathlib import Path

from releaseguard_agent.llm import FakeLLMClient, LLMRuntime
from releaseguard_agent.observability import ExecutionTracer
from releaseguard_agent.observability.execution_trace import _redact
from releaseguard_agent.services.agent_workflow_service import (
    ReleaseAgentWorkflowService,
)
from releaseguard_agent.services.verification_service import (
    ReleaseVerificationService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "sample_projects"


def test_execution_tracer_redacts_sensitive_keys_and_values() -> None:
    source_secrets = (
        "ghp_1234567890abcdefghijklmnopqrstuv",
        "github_pat_11AA0abcdefghijklmnopqrstuv_1234567890ABCDEFGHIJ",
        "AKIAIOSFODNN7EXAMPLE",
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        ),
        "postgresql://release:supersecret@db.internal:5432/app",
        (
            "-----BEGIN PRIVATE KEY-----\n"
            "c291cmNlLWRlcml2ZWQtcHJpdmF0ZS1rZXk=\n"
            "-----END PRIVATE KEY-----"
        ),
    )
    tracer = ExecutionTracer(run_id="test-run")
    with tracer.span(
        "llm",
        tool="llm.complete",
        api_key="sk-supersecretvalue",
        nested={"authorization": "Bearer hidden"},
        message="request token-abcdefghijk failed",
        source_text="\n".join(source_secrets),
    ):
        pass

    event = tracer.to_dict()["events"][0]
    assert "api_key" not in event
    assert "authorization" not in event["nested"]
    assert "[REDACTED]" in event.values()
    assert "[REDACTED]" in event["nested"].values()
    assert "abcdefghijk" not in event["message"]
    for secret in source_secrets:
        assert secret not in event["source_text"]


def test_redactor_neutralizes_secret_bearing_mapping_keys() -> None:
    secret_keys = (
        "api_key",
        "ghp_1234567890abcdefghijklmnopqrstuv",
        "github_pat_11AA0abcdefghijklmnopqrstuv_1234567890ABCDEFGHIJ",
        "AKIAIOSFODNN7EXAMPLE",
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        ),
        "postgresql://release:supersecret@db.internal:5432/app",
        (
            "-----BEGIN PRIVATE KEY-----\n"
            "c291cmNlLWRlcml2ZWQtcHJpdmF0ZS1rZXk=\n"
            "-----END PRIVATE KEY-----"
        ),
    )

    redacted = _redact({secret: "benign" for secret in secret_keys})

    durable_text = repr(redacted)
    for secret in secret_keys:
        assert secret not in durable_text
    assert "[REDACTED_KEY" in durable_text


def test_redacted_key_placeholders_are_collision_free_and_deterministic() -> None:
    first = {
        "api_key": "secret-value",
        "[REDACTED_KEY_1]": "legitimate",
        "ghp_1234567890abcdefghijklmnopqrstuv": "observed",
    }
    second = dict(reversed(tuple(first.items())))

    redacted_first = _redact(first)
    redacted_second = _redact(second)

    assert redacted_first == redacted_second
    assert len(redacted_first) == 3
    assert redacted_first["[REDACTED_KEY_1]"] == "legitimate"
    assert sorted(redacted_first.values()) == [
        "[REDACTED]",
        "legitimate",
        "observed",
    ]


def test_runtime_failure_and_pause_events_have_semantic_trace_status() -> None:
    failed = ExecutionTracer(run_id="failed-run")
    failed.runtime_event(
        run_id="failed-run",
        event_kind="TOOL_FAILED",
        event_sequence=1,
    )
    paused = ExecutionTracer(run_id="paused-run")
    paused.runtime_event(
        run_id="paused-run",
        event_kind="RUN_PAUSED",
        event_sequence=1,
    )

    failed_payload = failed.to_dict()
    paused_payload = paused.to_dict()
    assert failed_payload["events"][0]["status"] == "error"
    assert failed_payload["events"][0]["error_type"] == "tool_failed"
    assert failed_payload["status"] == "error"
    assert paused_payload["events"][0]["status"] == "paused"
    assert paused_payload["status"] == "paused"


def test_execution_trace_uses_the_final_durable_run_status_over_history() -> None:
    tracer = ExecutionTracer(run_id="recovered-run")
    tracer.runtime_event(
        run_id="recovered-run",
        event_kind="TOOL_FAILED",
        event_sequence=3,
    )
    tracer.runtime_event(
        run_id="recovered-run",
        event_kind="RUN_COMPLETED",
        event_sequence=9,
    )

    payload = tracer.to_dict()

    assert payload["events"][0]["status"] == "error"
    assert payload["status"] == "success"


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
