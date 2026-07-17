from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from releaseguard_agent.agent_tools import ScanProjectTool
from releaseguard_agent.agents.role_agents import (
    EvidenceAgentInput,
    EvidenceAgentOutput,
    FixPlannerAgentInput,
    FixPlannerAgentOutput,
    ReleaseRoleAgents,
    RiskAgentInput,
    RiskAgentOutput,
    VerifierAgentInput,
    VerifierAgentOutput,
)
from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.observability import (
    ExecutionTraceArtifacts,
    ExecutionTracer,
)
from releaseguard_agent.services.release_review_service import ReleaseReviewResult


class ReleaseGraphState(TypedDict, total=False):
    project_path: str
    include_pytest_execution: bool
    retrieval_mode: str
    top_k: int
    minimum_evidence: int
    baseline_review: ReleaseReviewResult
    review: ReleaseReviewResult
    evidence: tuple[RetrievalEvidence, ...]
    evidence_output: EvidenceAgentOutput
    risk_output: RiskAgentOutput
    fix_plan_output: FixPlannerAgentOutput
    verification: VerifierAgentOutput
    risk_analysis: dict[str, Any]
    fix_plan: tuple[dict[str, Any], ...]
    route: str
    route_history: list[str]
    degraded_reason: str | None
    supplemental_retrieval: bool
    llm_attempted: bool
    llm_failed: bool
    error_type: str | None
    manual_review_required: bool


class ReleaseAgentWorkflowResult:
    """Serializable result from a compiled role-based LangGraph invocation."""

    def __init__(
        self,
        state: ReleaseGraphState,
        *,
        trace: dict[str, Any] | None = None,
        trace_artifacts: ExecutionTraceArtifacts | None = None,
    ) -> None:
        self.state = state
        self.trace = trace or {}
        self.trace_artifacts = trace_artifacts

    @property
    def review(self) -> ReleaseReviewResult:
        return self.state["review"]

    @property
    def release_allowed(self) -> bool:
        return self.review.release_allowed

    @property
    def verification(self) -> VerifierAgentOutput | None:
        return self.state.get("verification")

    def to_dict(self) -> dict[str, Any]:
        verification = self.verification
        return {
            "review": self.review.to_dict(),
            "evidence": [
                item.to_dict() for item in self.state.get("evidence", ())
            ],
            "risk_analysis": dict(self.state.get("risk_analysis", {})),
            "fix_plan": [dict(step) for step in self.state.get("fix_plan", ())],
            "verification": verification.to_dict() if verification else None,
            "route": self.state.get("route"),
            "route_history": list(self.state.get("route_history", [])),
            "degraded_reason": self.state.get("degraded_reason"),
            "supplemental_retrieval": self.state.get(
                "supplemental_retrieval", False
            ),
            "llm_attempted": self.state.get("llm_attempted", False),
            "llm_failed": self.state.get("llm_failed", False),
            "error_type": self.state.get("error_type"),
            "manual_review_required": self.state.get(
                "manual_review_required", False
            ),
            "trace": dict(self.trace),
            "trace_artifacts": (
                {"execution_trace": str(self.trace_artifacts.trace_path)}
                if self.trace_artifacts
                else {}
            ),
        }


def build_release_graph(
    scan_tool: ScanProjectTool,
    agents: ReleaseRoleAgents,
    tracer: ExecutionTracer | None = None,
) -> Any:
    """Build and compile the conditional four-role ReleaseGuard StateGraph."""

    def scan(state: ReleaseGraphState) -> ReleaseGraphState:
        review = scan_tool.invoke(
            Path(state["project_path"]),
            include_pytest_execution=state.get("include_pytest_execution", True),
            tracer=tracer,
        )
        return {
            "review": review,
            "route": "scanned",
            "route_history": _append_route(state, "scan"),
        }

    def route_after_scan(
        state: ReleaseGraphState,
    ) -> Literal["clean", "evidence", "verify"]:
        if "baseline_review" in state:
            return _record_route(tracer, "scan", "verify")
        destination = "clean" if state["review"].release_allowed else "evidence"
        return _record_route(tracer, "scan", destination)

    def verifier_agent(state: ReleaseGraphState) -> ReleaseGraphState:
        output = agents.verifier.run(
            VerifierAgentInput(
                before=state["baseline_review"],
                after=state["review"],
            )
        )
        return {
            "verification": output,
            "route": "verified",
            "route_history": _append_route(state, "verifier_agent"),
        }

    def route_after_verifier(
        state: ReleaseGraphState,
    ) -> Literal["complete", "evidence"]:
        destination = (
            "complete" if state["review"].release_allowed else "evidence"
        )
        return _record_route(tracer, "verifier_agent", destination)

    def evidence_agent(state: ReleaseGraphState) -> ReleaseGraphState:
        output = agents.evidence.run(
            EvidenceAgentInput(
                review=state["review"],
                retrieval_mode=state.get("retrieval_mode", "hybrid"),
                top_k=state.get("top_k", 5),
                minimum_evidence=state.get("minimum_evidence", 1),
            )
        )
        return {
            "evidence_output": output,
            "evidence": output.evidence,
            "degraded_reason": output.degraded_reason,
            "supplemental_retrieval": output.supplemental_attempted,
            "manual_review_required": output.manual_review_required,
            "route": "evidence_complete",
            "route_history": _append_route(state, "evidence_agent"),
        }

    def route_after_evidence(
        state: ReleaseGraphState,
    ) -> Literal["risk", "manual"]:
        destination = "manual" if state["manual_review_required"] else "risk"
        return _record_route(tracer, "evidence_agent", destination)

    def risk_agent(state: ReleaseGraphState) -> ReleaseGraphState:
        output = agents.risk.run(
            RiskAgentInput(
                review=state["review"],
                evidence=state.get("evidence", ()),
            )
        )
        return {
            "risk_output": output,
            "risk_analysis": output.analysis,
            "llm_attempted": output.llm_attempted,
            "llm_failed": output.llm_failed,
            "error_type": output.error_type,
            "route": "risk_complete",
            "route_history": _append_route(state, "risk_agent"),
        }

    def route_after_risk(
        state: ReleaseGraphState,
    ) -> Literal["fallback", "fix"]:
        destination = "fallback" if state.get("llm_failed", False) else "fix"
        return _record_route(tracer, "risk_agent", destination)

    def deterministic_fallback(state: ReleaseGraphState) -> ReleaseGraphState:
        return {
            "route": "deterministic_fallback",
            "route_history": _append_route(state, "deterministic_fallback"),
        }

    def fix_planner_agent(state: ReleaseGraphState) -> ReleaseGraphState:
        output = agents.fix_planner.run(
            FixPlannerAgentInput(
                review=state["review"],
                risk=state["risk_output"],
                evidence=state.get("evidence", ()),
            )
        )
        return {
            "fix_plan_output": output,
            "fix_plan": output.steps,
            "route": "fix_plan_complete",
            "route_history": _append_route(state, "fix_planner_agent"),
        }

    def finalize_clean(state: ReleaseGraphState) -> ReleaseGraphState:
        return {
            "evidence": state["review"].retrieval_evidence,
            "risk_analysis": {
                "analysis_source": "deterministic",
                "summary": "No deterministic blocking findings.",
                "release_allowed": True,
            },
            "fix_plan": (),
            "llm_attempted": False,
            "llm_failed": False,
            "manual_review_required": False,
            "route": "clean_complete",
            "route_history": _append_route(state, "finalize_clean"),
        }

    def verification_complete(state: ReleaseGraphState) -> ReleaseGraphState:
        return {
            "evidence": state["review"].retrieval_evidence,
            "fix_plan": (),
            "manual_review_required": False,
            "route": "verification_complete",
            "route_history": _append_route(state, "verification_complete"),
        }

    def manual_review(state: ReleaseGraphState) -> ReleaseGraphState:
        return {
            "fix_plan": (),
            "route": "manual_review_required",
            "route_history": _append_route(state, "manual_review"),
        }

    builder = StateGraph(ReleaseGraphState)
    builder.add_node("scan", _traced_node(tracer, "scan", scan))
    builder.add_node(
        "evidence_agent",
        _traced_node(tracer, "evidence_agent", evidence_agent),
    )
    builder.add_node(
        "risk_agent", _traced_node(tracer, "risk_agent", risk_agent)
    )
    builder.add_node(
        "fix_planner_agent",
        _traced_node(tracer, "fix_planner_agent", fix_planner_agent),
    )
    builder.add_node(
        "verifier_agent",
        _traced_node(tracer, "verifier_agent", verifier_agent),
    )
    builder.add_node(
        "deterministic_fallback",
        _traced_node(
            tracer, "deterministic_fallback", deterministic_fallback
        ),
    )
    builder.add_node(
        "finalize_clean",
        _traced_node(tracer, "finalize_clean", finalize_clean),
    )
    builder.add_node(
        "verification_complete",
        _traced_node(tracer, "verification_complete", verification_complete),
    )
    builder.add_node(
        "manual_review",
        _traced_node(tracer, "manual_review", manual_review),
    )
    builder.add_edge(START, "scan")
    builder.add_conditional_edges(
        "scan",
        route_after_scan,
        {
            "clean": "finalize_clean",
            "evidence": "evidence_agent",
            "verify": "verifier_agent",
        },
    )
    builder.add_conditional_edges(
        "verifier_agent",
        route_after_verifier,
        {"complete": "verification_complete", "evidence": "evidence_agent"},
    )
    builder.add_conditional_edges(
        "evidence_agent",
        route_after_evidence,
        {"risk": "risk_agent", "manual": "manual_review"},
    )
    builder.add_conditional_edges(
        "risk_agent",
        route_after_risk,
        {"fallback": "deterministic_fallback", "fix": "fix_planner_agent"},
    )
    builder.add_edge("deterministic_fallback", "fix_planner_agent")
    builder.add_edge("fix_planner_agent", END)
    builder.add_edge("finalize_clean", END)
    builder.add_edge("verification_complete", END)
    builder.add_edge("manual_review", END)
    return builder.compile()


def _append_route(state: ReleaseGraphState, node: str) -> list[str]:
    return [*state.get("route_history", []), node]


def _record_route(
    tracer: ExecutionTracer | None,
    source: str,
    destination: str,
) -> Any:
    if tracer is not None:
        tracer.route(source, destination)
    return destination


def _traced_node(
    tracer: ExecutionTracer | None,
    name: str,
    action: Callable[[ReleaseGraphState], ReleaseGraphState],
) -> Any:
    if tracer is None:
        return action

    def invoke(state: ReleaseGraphState) -> ReleaseGraphState:
        with tracer.span("node", node=name) as span:
            result = action(state)
            span.update(route=result.get("route"))
            evidence = result.get("evidence", ())
            if evidence:
                span.update(
                    evidence_ids=[item.evidence_id for item in evidence]
                )
            verification = result.get("verification")
            if verification is not None:
                span.update(before_after_delta=verification.to_dict())
            return result

    return invoke
