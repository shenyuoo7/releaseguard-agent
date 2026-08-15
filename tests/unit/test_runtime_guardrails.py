from pathlib import Path

from releaseguard_agent.runtime.guardrails import (
    GuardrailDecision,
    GuardrailEngine,
    ToolExecutionContext,
)
from releaseguard_agent.runtime.models import RunBudget
from releaseguard_agent.runtime.tools import ToolCall, ToolSpec


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_context(**changes: object) -> ToolExecutionContext:
    values: dict[str, object] = {
        "budget": RunBudget(max_steps=2, max_tool_calls=2, max_retries=1),
        "max_output_bytes": 256,
    }
    values.update(changes)
    return ToolExecutionContext(**values)  # type: ignore[arg-type]


def make_spec(**changes: object) -> ToolSpec:
    values: dict[str, object] = {
        "name": "scan_project",
        "version": "1",
        "input_schema": {"project_path": str},
        "output_schema": {"release_allowed": bool},
        "side_effect": "read_only",
        "allowed_roots": (PROJECT_ROOT,),
        "network_policy": "offline",
        "timeout_ms": 1000,
        "max_retries": 1,
        "budget_cost": 1,
        "required_approval_scope": None,
    }
    values.update(changes)
    return ToolSpec(**values)  # type: ignore[arg-type]


def make_call(**changes: object) -> ToolCall:
    values: dict[str, object] = {
        "tool_name": "scan_project",
        "tool_version": "1",
        "args": {"project_path": str(PROJECT_ROOT / "sample_projects" / "clean_python_project")},
        "run_id": "run-1",
        "step_index": 0,
        "idempotency_key": "tool-1",
    }
    values.update(changes)
    return ToolCall.create(**values)  # type: ignore[arg-type]


def test_guardrails_block_path_escaping_the_tool_allowed_root() -> None:
    decision = GuardrailEngine().check(
        make_call(args={"project_path": str(PROJECT_ROOT / "..")}),
        make_spec(),
        make_context(),
    )

    assert decision is GuardrailDecision.BLOCK


def test_guardrails_quarantine_dotenv_arguments_before_a_handler_can_see_them() -> None:
    decision = GuardrailEngine().check(
        make_call(args={"project_path": str(PROJECT_ROOT / ".env")}),
        make_spec(),
        make_context(),
    )

    assert decision is GuardrailDecision.QUARANTINE


def test_guardrails_block_network_tool_when_the_context_is_offline() -> None:
    decision = GuardrailEngine().check(
        make_call(),
        make_spec(
            side_effect="network",
            network_policy="network",
            required_approval_scope="network.use",
        ),
        make_context(offline_mode=True),
    )

    assert decision is GuardrailDecision.BLOCK


def test_guardrails_require_hitl_for_a_side_effect_that_declares_approval() -> None:
    decision = GuardrailEngine().check(
        make_call(),
        make_spec(
            side_effect="write",
            required_approval_scope="project.write",
        ),
        make_context(),
    )

    assert decision is GuardrailDecision.REQUIRE_HITL


def test_guardrails_derive_hitl_from_write_classification_not_optional_scope_only() -> None:
    spec = make_spec(
        side_effect="project_write",
        required_approval_scope="project.write",
    )

    assert GuardrailEngine().check(
        make_call(), spec, make_context()
    ) is GuardrailDecision.REQUIRE_HITL


def test_guardrails_derive_network_denial_from_capability_classification() -> None:
    spec = make_spec(
        side_effect="network",
        network_policy="network",
        required_approval_scope="network.use",
    )

    assert GuardrailEngine().check(
        make_call(), spec, make_context(offline_mode=True)
    ) is GuardrailDecision.BLOCK


def test_guardrails_fail_closed_for_a_tampered_network_capability_pair() -> None:
    spec = make_spec(required_approval_scope="network.use")
    object.__setattr__(spec, "network_policy", "network")

    engine = GuardrailEngine()

    assert engine.check(
        make_call(),
        spec,
        make_context(
            offline_mode=False,
            approved_scopes=frozenset({"network.use"}),
        ),
    ) is GuardrailDecision.BLOCK
    assert engine.error_type(
        make_call(),
        spec,
        make_context(
            offline_mode=False,
            approved_scopes=frozenset({"network.use"}),
        ),
    ) == "capability_inconsistent"


def test_guardrails_block_exceeded_step_retry_and_tool_call_budgets() -> None:
    engine = GuardrailEngine()
    spec = make_spec()

    assert engine.check(
        make_call(step_index=2), spec, make_context()
    ) is GuardrailDecision.BLOCK
    assert engine.check(
        make_call(), spec, make_context(retry_count=2)
    ) is GuardrailDecision.BLOCK
    assert engine.check(
        make_call(), spec, make_context(tool_call_count=2)
    ) is GuardrailDecision.BLOCK
