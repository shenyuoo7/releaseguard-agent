from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from releaseguard_agent.agent_tools import ReleaseWorkflowTools
from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence
from releaseguard_agent.services.release_review_service import ReleaseReviewResult


class ReleaseGraphState(TypedDict, total=False):
    project_path: str
    include_pytest_execution: bool
    retrieval_mode: str
    top_k: int
    minimum_evidence: int
    review: ReleaseReviewResult
    evidence: tuple[RetrievalEvidence, ...]
    risk_analysis: dict[str, Any]
    fix_plan: tuple[dict[str, Any], ...]
    route: str
    route_history: list[str]
    degraded_reason: str | None
    llm_attempted: bool
    llm_failed: bool
    error_type: str | None
    manual_review_required: bool


class ReleaseAgentWorkflowResult:
    """Serializable result from a compiled LangGraph invocation."""

    def __init__(self, state: ReleaseGraphState) -> None:
        self.state = state

    @property
    def review(self) -> ReleaseReviewResult:
        return self.state["review"]

    @property
    def release_allowed(self) -> bool:
        return self.review.release_allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "review": self.review.to_dict(),
            "evidence": [
                item.to_dict() for item in self.state.get("evidence", ())
            ],
            "risk_analysis": dict(self.state.get("risk_analysis", {})),
            "fix_plan": [dict(step) for step in self.state.get("fix_plan", ())],
            "route": self.state.get("route"),
            "route_history": list(self.state.get("route_history", [])),
            "degraded_reason": self.state.get("degraded_reason"),
            "llm_attempted": self.state.get("llm_attempted", False),
            "llm_failed": self.state.get("llm_failed", False),
            "error_type": self.state.get("error_type"),
            "manual_review_required": self.state.get(
                "manual_review_required", False
            ),
        }


def build_release_graph(tools: ReleaseWorkflowTools) -> Any:
    """Build and compile the real conditional ReleaseGuard StateGraph."""

    def scan(state: ReleaseGraphState) -> ReleaseGraphState:
        review = tools.scan.invoke(
            Path(state["project_path"]),
            include_pytest_execution=state.get("include_pytest_execution", True),
        )
        return {
            "review": review,
            "route": "scanned",
            "route_history": _append_route(state, "scan"),
        }

    def route_after_scan(
        state: ReleaseGraphState,
    ) -> Literal["clean", "evidence"]:
        return "clean" if state["review"].release_allowed else "evidence"

    def retrieve_evidence(state: ReleaseGraphState) -> ReleaseGraphState:
        review = state["review"]
        query = " ".join(
            part
            for result in review.check_results
            if result.should_block_release
            for part in (result.rule_id or "", result.title, result.message)
        )
        result = tools.evidence.invoke(
            query,
            mode=state.get("retrieval_mode", "hybrid"),
            top_k=state.get("top_k", 5),
        )
        return {
            "evidence": result.evidence,
            "degraded_reason": result.degraded_reason,
            "route": "evidence_retrieved",
            "route_history": _append_route(state, "retrieve_evidence"),
        }

    def route_after_evidence(
        state: ReleaseGraphState,
    ) -> Literal["risk", "supplement"]:
        return "risk" if _has_enough_evidence(state) else "supplement"

    def supplemental_retrieval(state: ReleaseGraphState) -> ReleaseGraphState:
        combined = {item.chunk_id: item for item in state.get("evidence", ())}
        for result in state["review"].check_results:
            if not result.should_block_release or not result.rule_id:
                continue
            exact = tools.evidence.invoke(result.rule_id, mode="exact", top_k=10)
            combined.update({item.chunk_id: item for item in exact.evidence})
        return {
            "evidence": tuple(combined.values()),
            "route": "supplemental_retrieval",
            "route_history": _append_route(state, "supplemental_retrieval"),
        }

    def route_after_supplement(
        state: ReleaseGraphState,
    ) -> Literal["risk", "manual"]:
        return "risk" if _has_enough_evidence(state) else "manual"

    def analyze_risk(state: ReleaseGraphState) -> ReleaseGraphState:
        result = tools.risk.invoke(
            state["review"],
            state.get("evidence", ()),
        )
        return {
            "risk_analysis": result.payload,
            "llm_attempted": result.llm_attempted,
            "llm_failed": result.llm_failed,
            "error_type": result.error_type,
            "route": "risk_analyzed",
            "route_history": _append_route(state, "analyze_risk"),
        }

    def route_after_risk(
        state: ReleaseGraphState,
    ) -> Literal["fallback", "fix"]:
        return "fallback" if state.get("llm_failed", False) else "fix"

    def deterministic_fallback(state: ReleaseGraphState) -> ReleaseGraphState:
        return {
            "route": "deterministic_fallback",
            "route_history": _append_route(state, "deterministic_fallback"),
        }

    def plan_fixes(state: ReleaseGraphState) -> ReleaseGraphState:
        plan = tools.fix_plan.invoke(
            state["review"],
            state.get("risk_analysis", {}),
        )
        return {
            "fix_plan": plan,
            "route": "fix_plan_generated",
            "route_history": _append_route(state, "plan_fixes"),
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

    def manual_review(state: ReleaseGraphState) -> ReleaseGraphState:
        return {
            "manual_review_required": True,
            "fix_plan": (),
            "route": "manual_review_required",
            "route_history": _append_route(state, "manual_review"),
        }

    builder = StateGraph(ReleaseGraphState)
    builder.add_node("scan", scan)
    builder.add_node("retrieve_evidence", retrieve_evidence)
    builder.add_node("supplemental_retrieval", supplemental_retrieval)
    builder.add_node("analyze_risk", analyze_risk)
    builder.add_node("deterministic_fallback", deterministic_fallback)
    builder.add_node("plan_fixes", plan_fixes)
    builder.add_node("finalize_clean", finalize_clean)
    builder.add_node("manual_review", manual_review)
    builder.add_edge(START, "scan")
    builder.add_conditional_edges(
        "scan",
        route_after_scan,
        {"clean": "finalize_clean", "evidence": "retrieve_evidence"},
    )
    builder.add_conditional_edges(
        "retrieve_evidence",
        route_after_evidence,
        {"risk": "analyze_risk", "supplement": "supplemental_retrieval"},
    )
    builder.add_conditional_edges(
        "supplemental_retrieval",
        route_after_supplement,
        {"risk": "analyze_risk", "manual": "manual_review"},
    )
    builder.add_conditional_edges(
        "analyze_risk",
        route_after_risk,
        {"fallback": "deterministic_fallback", "fix": "plan_fixes"},
    )
    builder.add_edge("deterministic_fallback", "plan_fixes")
    builder.add_edge("plan_fixes", END)
    builder.add_edge("finalize_clean", END)
    builder.add_edge("manual_review", END)
    return builder.compile()


def _append_route(state: ReleaseGraphState, node: str) -> list[str]:
    return [*state.get("route_history", []), node]


def _has_enough_evidence(state: ReleaseGraphState) -> bool:
    evidence = state.get("evidence", ())
    minimum = state.get("minimum_evidence", 1)
    return len(evidence) >= minimum and all(
        item.rule_id and item.chunk_id and item.local_source for item in evidence
    )
