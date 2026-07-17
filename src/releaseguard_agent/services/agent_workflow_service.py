from pathlib import Path
from typing import Any

from releaseguard_agent.agent_tools import (
    EvidenceSearchTool,
    FixPlanTool,
    ReleaseWorkflowTools,
    RiskAnalysisTool,
    ScanProjectTool,
)
from releaseguard_agent.agents.role_agents import (
    EvidenceAgent,
    FixPlannerAgent,
    ReleaseRoleAgents,
    RiskAgent,
    VerifierAgent,
)
from releaseguard_agent.llm import LLMRuntime
from releaseguard_agent.rag import RuleRetrievalService, get_default_rule_index_path
from releaseguard_agent.services.release_review_service import (
    ReleaseReviewResult,
    ReleaseReviewService,
)
from releaseguard_agent.workflows import (
    ReleaseAgentWorkflowResult,
    ReleaseGraphState,
    build_release_graph,
)


class ReleaseAgentWorkflowService:
    """Compile and invoke the conditional Agent workflow."""

    def __init__(
        self,
        *,
        review_service: ReleaseReviewService | None = None,
        retrieval_service: RuleRetrievalService | None = None,
        llm_runtime: LLMRuntime | None = None,
        tools: ReleaseWorkflowTools | None = None,
    ) -> None:
        self._tools = tools or ReleaseWorkflowTools(
            scan=ScanProjectTool(review_service),
            evidence=EvidenceSearchTool(
                retrieval_service
                or RuleRetrievalService(get_default_rule_index_path())
            ),
            risk=RiskAnalysisTool(llm_runtime),
            fix_plan=FixPlanTool(),
        )
        roles = ReleaseRoleAgents(
            evidence=EvidenceAgent(self._tools.evidence),
            risk=RiskAgent(self._tools.risk),
            fix_planner=FixPlannerAgent(self._tools.fix_plan),
            verifier=VerifierAgent(),
        )
        self._graph = build_release_graph(self._tools.scan, roles)

    @property
    def graph(self) -> Any:
        return self._graph

    def run(
        self,
        *,
        project_path: Path,
        include_pytest_execution: bool = True,
        retrieval_mode: str = "hybrid",
        top_k: int = 5,
        minimum_evidence: int = 1,
        baseline_review: ReleaseReviewResult | None = None,
    ) -> ReleaseAgentWorkflowResult:
        initial: ReleaseGraphState = {
            "project_path": str(Path(project_path).expanduser().resolve()),
            "include_pytest_execution": include_pytest_execution,
            "retrieval_mode": retrieval_mode,
            "top_k": top_k,
            "minimum_evidence": minimum_evidence,
            "route_history": [],
        }
        if baseline_review is not None:
            initial["baseline_review"] = baseline_review
        final_state = self._graph.invoke(initial)
        return ReleaseAgentWorkflowResult(final_state)
