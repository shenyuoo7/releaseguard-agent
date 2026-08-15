import time
from pathlib import Path
from threading import Lock

import pytest

from releaseguard_agent.agent_tools.release_tools import build_release_tool_registry
from releaseguard_agent.runtime.guardrails import ToolExecutionContext
from releaseguard_agent.runtime.models import RunBudget
from releaseguard_agent.runtime.tools import (
    SensitiveToolArgumentError,
    ToolCall,
    ToolRegistry,
    ToolSpec,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_context(**changes: object) -> ToolExecutionContext:
    values: dict[str, object] = {
        "budget": RunBudget(max_steps=2, max_tool_calls=2, max_retries=1),
        "max_output_bytes": 1_000_000,
    }
    values.update(changes)
    return ToolExecutionContext(**values)  # type: ignore[arg-type]


def make_call(**changes: object) -> ToolCall:
    values: dict[str, object] = {
        "tool_name": "echo",
        "tool_version": "1",
        "args": {"message": "hello"},
        "run_id": "run-1",
        "step_index": 0,
        "idempotency_key": "tool-1",
    }
    values.update(changes)
    return ToolCall.create(**values)  # type: ignore[arg-type]


def echo_spec(**changes: object) -> ToolSpec:
    values: dict[str, object] = {
        "name": "echo",
        "version": "1",
        "input_schema": {"message": str},
        "output_schema": {"message": str},
        "side_effect": "read_only",
        "allowed_roots": (),
        "network_policy": "offline",
        "timeout_ms": 1000,
        "max_retries": 1,
        "budget_cost": 1,
        "required_approval_scope": None,
    }
    values.update(changes)
    return ToolSpec(**values)  # type: ignore[arg-type]


def test_registry_returns_a_blocked_result_for_an_unknown_tool() -> None:
    result = ToolRegistry().execute(make_call(tool_name="missing"), make_context())

    assert result.status == "blocked"
    assert result.error_type == "unknown_tool"


def test_registry_rejects_malformed_arguments_without_invoking_the_handler() -> None:
    invoked = False

    def handler(_: dict[str, object], __: ToolExecutionContext) -> dict[str, object]:
        nonlocal invoked
        invoked = True
        return {"message": "unexpected"}

    registry = ToolRegistry()
    registry.register(echo_spec(), handler)

    result = registry.execute(make_call(args={"message": 7}), make_context())

    assert result.status == "blocked"
    assert result.error_type == "malformed_arguments"
    assert invoked is False


def test_registry_restricts_preparers_to_read_only_offline_tools() -> None:
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="preparer.*read-only offline"):
        registry.register(
            echo_spec(
                side_effect="network",
                network_policy="network",
                required_approval_scope="network.echo",
            ),
            lambda args, _context: args,
            preparer=lambda _args, _context: None,  # type: ignore[arg-type]
        )


def test_registry_rejects_output_that_does_not_match_the_declared_schema() -> None:
    registry = ToolRegistry()
    registry.register(echo_spec(), lambda _args, _context: {"message": 7})

    result = registry.execute(make_call(), make_context())

    assert result.status == "error"
    assert result.error_type == "output_schema_mismatch"


def test_registry_blocks_output_larger_than_the_context_byte_budget() -> None:
    registry = ToolRegistry()
    registry.register(echo_spec(), lambda _args, _context: {"message": "x" * 40})

    result = registry.execute(make_call(), make_context(max_output_bytes=10))

    assert result.status == "blocked"
    assert result.error_type == "output_bytes_exceeded"


def test_registry_returns_the_completed_idempotency_result_without_reinvoking_handler() -> None:
    calls = 0

    def handler(args: dict[str, object], _: ToolExecutionContext) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"message": str(args["message"])}

    registry = ToolRegistry()
    registry.register(echo_spec(), handler)
    context = make_context()

    first = registry.execute(make_call(), context)
    duplicate = registry.execute(make_call(), context)

    assert first.status == "completed"
    assert duplicate == first
    assert calls == 1


def test_registry_rejects_a_completed_idempotency_key_collision_with_different_arguments() -> None:
    calls = 0

    def handler(args: dict[str, object], _: ToolExecutionContext) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"message": str(args["message"])}

    registry = ToolRegistry()
    registry.register(echo_spec(), handler)
    context = make_context()

    first = registry.execute(make_call(), context)
    collision = registry.execute(make_call(args={"message": "changed"}), context)

    assert first.status == "completed"
    assert collision.status == "blocked"
    assert collision.error_type == "idempotency_key_collision"
    assert calls == 1


def test_artifact_version_budget_and_fallback_are_part_of_the_call_fingerprint() -> None:
    base = {
        "review_ref": "review:abc",
        "relation_index_version": "ri-" + "1" * 64,
        "memory_version": "pm-" + "2" * 64,
        "relation_budget": {"max_hops": 2, "max_nodes": 12},
        "memory_budget": {"top_k": 1, "max_characters": 100},
        "artifact_context": {"relation_fallback_reason": None},
    }
    original = make_call(tool_name="search_rule_evidence", args=base)

    variants = (
        {**base, "relation_index_version": "ri-" + "3" * 64},
        {**base, "memory_version": "pm-" + "4" * 64},
        {**base, "relation_budget": {"max_hops": 1, "max_nodes": 12}},
        {**base, "artifact_context": {"relation_fallback_reason": "missing"}},
    )

    assert all(
        make_call(tool_name="search_rule_evidence", args=variant).fingerprint
        != original.fingerprint
        for variant in variants
    )


def test_registry_validates_malformed_duplicate_before_reusing_a_completed_result() -> None:
    calls = 0

    def handler(args: dict[str, object], _: ToolExecutionContext) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"message": str(args["message"])}

    registry = ToolRegistry()
    registry.register(echo_spec(), handler)
    context = make_context()
    registry.execute(make_call(), context)

    duplicate = registry.execute(make_call(args={"message": 7}), context)

    assert duplicate.status == "blocked"
    assert duplicate.error_type == "malformed_arguments"
    assert calls == 1


def test_registry_counts_a_failed_attempt_before_a_second_handler_can_run() -> None:
    calls = 0

    def handler(_: dict[str, object], __: ToolExecutionContext) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise RuntimeError("injected failure")

    registry = ToolRegistry()
    registry.register(echo_spec(), handler)
    context = make_context(
        budget=RunBudget(max_steps=2, max_tool_calls=1, max_retries=1)
    )

    first = registry.execute(make_call(idempotency_key="first"), context)
    second = registry.execute(make_call(idempotency_key="second"), context)

    assert first.status == "error"
    assert second.status == "blocked"
    assert second.error_type == "tool_call_budget_exceeded"
    assert calls == 1


def test_registry_times_out_a_slow_read_only_handler_without_waiting_for_completion() -> None:
    def handler(_: dict[str, object], __: ToolExecutionContext) -> dict[str, object]:
        time.sleep(0.2)
        return {"message": "late"}

    registry = ToolRegistry()
    registry.register(echo_spec(timeout_ms=20), handler)

    started = time.monotonic()
    result = registry.execute(make_call(), make_context())
    elapsed = time.monotonic() - started

    assert result.status == "quarantined"
    assert result.error_type == "execution_timeout_ambiguous"
    assert elapsed < 0.15


def test_timed_out_idempotent_call_reserves_one_worker_and_one_budget_charge() -> None:
    calls = 0

    def handler(_: dict[str, object], __: ToolExecutionContext) -> dict[str, object]:
        nonlocal calls
        calls += 1
        time.sleep(0.1)
        return {"message": "late"}

    registry = ToolRegistry()
    registry.register(echo_spec(timeout_ms=10), handler)
    context = make_context()
    call = make_call(idempotency_key="slow-once")

    first = registry.execute(call, context)
    duplicate = registry.execute(call, context)

    assert first.status == "quarantined"
    assert first.error_type == "execution_timeout_ambiguous"
    assert duplicate == first
    assert calls == 1
    assert context.tool_call_count == 1


def test_timed_out_call_never_allows_a_different_key_to_retry_same_handler() -> None:
    calls = 0

    def handler(_: dict[str, object], __: ToolExecutionContext) -> dict[str, object]:
        nonlocal calls
        calls += 1
        time.sleep(0.1)
        return {"message": "late"}

    registry = ToolRegistry()
    registry.register(echo_spec(timeout_ms=10), handler)
    context = make_context()

    first = registry.execute(make_call(idempotency_key="logical-operation"), context)
    automatic_retry = registry.execute(
        make_call(idempotency_key="different-retry-key"), context
    )

    assert first.status == "quarantined"
    assert automatic_retry.status == "blocked"
    assert automatic_retry.error_type == "ambiguous_execution_in_progress"
    assert calls == 1


def test_timed_out_worker_cannot_mutate_visible_context_references_after_return() -> None:
    def handler(_: dict[str, object], context: ToolExecutionContext) -> dict[str, object]:
        context.references["late"] = {"value": "must not escape"}
        time.sleep(0.1)
        return {"message": "late"}

    registry = ToolRegistry()
    registry.register(echo_spec(timeout_ms=10), handler)
    context = make_context()

    result = registry.execute(make_call(idempotency_key="isolated-timeout"), context)
    time.sleep(0.12)

    assert result.error_type == "execution_timeout_ambiguous"
    assert "late" not in context.references


def test_uncopyable_reference_finalization_becomes_a_terminal_idempotency_failure() -> None:
    calls = 0

    def handler(_: dict[str, object], context: ToolExecutionContext) -> dict[str, object]:
        nonlocal calls
        calls += 1
        context.references["uncopyable"] = Lock()
        return {"message": "ready"}

    registry = ToolRegistry()
    registry.register(echo_spec(), handler)
    context = make_context()
    call = make_call(idempotency_key="uncopyable-finalization")

    first = registry.execute(call, context)
    duplicate = registry.execute(call, context)

    assert first.status == "error"
    assert first.error_type == "context_merge_failed"
    assert duplicate == first
    assert calls == 1
    assert context.tool_call_count == 1
    assert "uncopyable" not in context.references


def test_registry_result_and_registered_schemas_are_deeply_immutable() -> None:
    input_schema = {"message": str}
    output_schema = {"message": dict}
    registry = ToolRegistry()
    registry.register(
        echo_spec(input_schema=input_schema, output_schema=output_schema),
        lambda _args, _context: {"message": {"nested": "value"}},
    )
    input_schema["unexpected"] = str
    output_schema["unexpected"] = str

    result = registry.execute(make_call(), make_context())
    registered = registry.get("echo", "1")

    assert result.status == "completed"
    assert registered is not None
    assert set(registered.input_schema) == {"message"}
    assert set(registered.output_schema) == {"message"}
    assert result.output is not None
    with pytest.raises(TypeError):
        result.output["message"] = {}
    with pytest.raises(TypeError):
        result.output["message"]["nested"] = "changed"


def test_offline_release_registry_blocks_a_network_capable_risk_tool_before_invocation() -> None:
    class SentinelRiskTool:
        network_policy = "network"

        def invoke(self, *_: object, **__: object) -> object:
            raise AssertionError("network-capable risk tool must not be invoked offline")

    from releaseguard_agent.agent_tools.release_tools import ReleaseWorkflowTools

    registry = build_release_tool_registry(
        ReleaseWorkflowTools(
            scan=object(),  # type: ignore[arg-type]
            evidence=object(),  # type: ignore[arg-type]
            risk=SentinelRiskTool(),  # type: ignore[arg-type]
            fix_plan=object(),  # type: ignore[arg-type]
        )
    )
    call = ToolCall.create(
        tool_name="analyze_risk",
        tool_version="1",
        args={"review_ref": "review-1", "evidence_ref": "evidence-1"},
        run_id="run-risk",
        step_index=0,
        idempotency_key="risk-1",
    )

    result = registry.execute(call, make_context(offline_mode=True))

    assert result.status == "blocked"
    assert result.error_type == "network_disabled"


@pytest.mark.parametrize(
    "spec",
    [
        echo_spec(side_effect="write"),
        echo_spec(side_effect="network", network_policy="network"),
    ],
)
def test_registry_rejects_unsafe_capability_specs_without_approval(
    spec: ToolSpec,
) -> None:
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="approval scope"):
        registry.register(spec, lambda args, _context: args)


def test_network_side_effect_rejects_an_offline_network_policy() -> None:
    with pytest.raises(ValueError, match="network side_effect"):
        echo_spec(side_effect="network", network_policy="offline")


def test_network_policy_rejects_a_read_only_side_effect_classification() -> None:
    with pytest.raises(ValueError, match="network_policy"):
        echo_spec(
            side_effect="read_only",
            network_policy="network",
            required_approval_scope="network.use",
        )


def test_network_side_effect_is_offline_blocked_before_handler_invocation() -> None:
    invoked = False

    def handler(_: dict[str, object], __: ToolExecutionContext) -> dict[str, object]:
        nonlocal invoked
        invoked = True
        return {"message": "unexpected"}

    registry = ToolRegistry()
    registry.register(
        echo_spec(
            side_effect="network",
            network_policy="network",
            required_approval_scope="network.use",
        ),
        handler,
    )

    result = registry.execute(make_call(), make_context(offline_mode=True))

    assert result.status == "blocked"
    assert result.error_type == "network_disabled"
    assert invoked is False


def test_tool_call_rejects_nested_secret_keys_before_canonical_serialization() -> None:
    with pytest.raises(SensitiveToolArgumentError, match="sensitive"):
        make_call(args={"message": {"nested": {"api_key": "must-never-persist"}}})


@pytest.mark.parametrize(
    "key",
    [
        "client_secret",
        "refresh_token",
        "auth_token",
        "clientSecret",
        "github_pat",
        "github_token",
    ],
)
def test_tool_call_rejects_extended_secret_key_names(key: str) -> None:
    with pytest.raises(SensitiveToolArgumentError, match="sensitive"):
        make_call(args={"message": {key: "must-never-persist"}})


@pytest.mark.parametrize(
    "secret",
    [
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "sk-test-abcdefghijklmnopqrstuvwxyz",
        "password=hunter2-not-for-storage",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd",
        "ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd",
        "github_pat_11AA0abcdefghijklmnopqrstuv_1234567890ABCDEFGHIJ",
    ],
)
def test_tool_call_rejects_secret_bearing_scalar_values(secret: str) -> None:
    with pytest.raises(SensitiveToolArgumentError, match="sensitive"):
        make_call(args={"message": {"nested": secret}})


def test_tool_call_hash_is_stable_for_equivalent_argument_order() -> None:
    first = make_call(args={"message": "hello", "nested": {"b": 2, "a": 1}})
    second = make_call(args={"nested": {"a": 1, "b": 2}, "message": "hello"})

    assert first.canonical_args == second.canonical_args
    assert first.args_sha256 == second.args_sha256


def test_release_registry_runs_scan_project_read_only_through_the_existing_service() -> None:
    registry = build_release_tool_registry()
    call = ToolCall.create(
        tool_name="scan_project",
        tool_version="1",
        args={
            "project_path": str(PROJECT_ROOT / "sample_projects" / "clean_python_project"),
            "include_pytest_execution": False,
        },
        run_id="run-scan",
        step_index=0,
        idempotency_key="scan-1",
    )
    context = make_context(max_output_bytes=2_000_000)

    result = registry.execute(call, context)

    assert result.status == "completed"
    assert result.output is not None
    assert result.output["release_allowed"] is True
    assert context.tool_call_count == 1
