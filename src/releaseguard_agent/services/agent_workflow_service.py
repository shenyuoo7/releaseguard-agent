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
    EvidenceAgentInput,
    EvidenceAgentOutput,
    FixPlannerAgent,
    FixPlannerAgentInput,
    FixPlannerAgentOutput,
    ReleaseRoleAgents,
    RiskAgent,
    RiskAgentInput,
    RiskAgentOutput,
    VerifierAgent,
)
from releaseguard_agent.agents.release_risk_analysis_agent import ReportDetailLevel
from releaseguard_agent.llm import LLMRuntime
from releaseguard_agent.observability import ExecutionTracer
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
        locale: str = "zh-CN",
        report_detail_level: ReportDetailLevel = "standard",
    ) -> None:
        self._tools = tools or ReleaseWorkflowTools(
            scan=ScanProjectTool(review_service),
            evidence=EvidenceSearchTool(
                retrieval_service
                or RuleRetrievalService(get_default_rule_index_path())
            ),
            risk=RiskAnalysisTool(
                llm_runtime,
                locale=locale,
                detail_level=report_detail_level,
            ),
            fix_plan=FixPlanTool(),
        )
    @property
    def graph(self) -> Any:
        return self._build_graph(None)

    def run(
        self,
        *,
        project_path: Path,
        include_pytest_execution: bool = True,
        retrieval_mode: str = "hybrid",
        top_k: int = 5,
        minimum_evidence: int = 1,
        force_ai_review: bool = False,
        baseline_review: ReleaseReviewResult | None = None,
        tracer: ExecutionTracer | None = None,
        trace_output_dir: Path | None = None,
    ) -> ReleaseAgentWorkflowResult:
        active_tracer = tracer or ExecutionTracer()
        review = self._tools.scan.invoke(
            Path(project_path).expanduser().resolve(),
            include_pytest_execution=include_pytest_execution,
            tracer=active_tracer,
        )
        return self.evaluate(
            review=review,
            retrieval_mode=retrieval_mode,
            top_k=top_k,
            minimum_evidence=minimum_evidence,
            force_ai_review=force_ai_review,
            baseline_review=baseline_review,
            tracer=active_tracer,
            trace_output_dir=trace_output_dir,
        )

    def evaluate(
        self,
        *,
        review: ReleaseReviewResult,
        retrieval_mode: str = "hybrid",
        top_k: int = 5,
        minimum_evidence: int = 1,
        force_ai_review: bool = False,
        baseline_review: ReleaseReviewResult | None = None,
        tracer: ExecutionTracer | None = None,
        trace_output_dir: Path | None = None,
    ) -> ReleaseAgentWorkflowResult:
        """Preserve the legacy API by preparing role outputs before pure routing."""

        active_tracer = tracer or ExecutionTracer()
        evidence, risk, fix_plan = self._precompute_role_outputs(
            review=review,
            retrieval_mode=retrieval_mode,
            top_k=top_k,
            minimum_evidence=minimum_evidence,
            force_ai_review=force_ai_review,
            baseline_review=baseline_review,
            tracer=active_tracer,
        )
        return self.evaluate_committed(
            review=review,
            evidence_output=evidence,
            risk_output=risk,
            fix_plan_output=fix_plan,
            retrieval_mode=retrieval_mode,
            top_k=top_k,
            minimum_evidence=minimum_evidence,
            force_ai_review=force_ai_review,
            baseline_review=baseline_review,
            tracer=active_tracer,
            trace_output_dir=trace_output_dir,
        )

    def evaluate_committed(
        self,
        *,
        review: ReleaseReviewResult,
        evidence_output: EvidenceAgentOutput | None = None,
        risk_output: RiskAgentOutput | None = None,
        fix_plan_output: FixPlannerAgentOutput | None = None,
        retrieval_mode: str = "hybrid",
        top_k: int = 5,
        minimum_evidence: int = 1,
        force_ai_review: bool = False,
        baseline_review: ReleaseReviewResult | None = None,
        tracer: ExecutionTracer | None = None,
        trace_output_dir: Path | None = None,
    ) -> ReleaseAgentWorkflowResult:
        """Route only over outputs already committed by the durable controller."""

        initial: ReleaseGraphState = {
            "project_path": str(review.project_path),
            "include_pytest_execution": review.include_pytest_execution,
            "retrieval_mode": retrieval_mode,
            "top_k": top_k,
            "minimum_evidence": minimum_evidence,
            "route_history": [],
            "force_ai_review": force_ai_review,
            "review": review,
        }
        if evidence_output is not None:
            initial["evidence_output"] = evidence_output
        if risk_output is not None:
            initial["risk_output"] = risk_output
        if fix_plan_output is not None:
            initial["fix_plan_output"] = fix_plan_output
        if baseline_review is not None:
            initial["baseline_review"] = baseline_review
        return self._invoke(initial, tracer=tracer, trace_output_dir=trace_output_dir)

    def _precompute_role_outputs(
        self,
        *,
        review: ReleaseReviewResult,
        retrieval_mode: str,
        top_k: int,
        minimum_evidence: int,
        force_ai_review: bool,
        baseline_review: ReleaseReviewResult | None,
        tracer: ExecutionTracer | None,
    ) -> tuple[
        EvidenceAgentOutput | None,
        RiskAgentOutput | None,
        FixPlannerAgentOutput | None,
    ]:
        needs_roles = (
            not review.release_allowed
            if baseline_review is not None
            else force_ai_review or not review.release_allowed
        )
        if not needs_roles:
            return None, None, None
        roles = self._build_roles(tracer)
        evidence = roles.evidence.run(
            EvidenceAgentInput(
                review=review,
                retrieval_mode=retrieval_mode,
                top_k=top_k,
                minimum_evidence=minimum_evidence,
            )
        )
        if evidence.manual_review_required:
            return evidence, None, None
        risk = roles.risk.run(
            RiskAgentInput(review=review, evidence=evidence.evidence)
        )
        fix_plan = roles.fix_planner.run(
            FixPlannerAgentInput(
                review=review,
                risk=risk,
                evidence=evidence.evidence,
            )
        )
        return evidence, risk, fix_plan

    @staticmethod
    def is_retryable_failure(error_type: str | None) -> bool:
        """Classify the narrow transient failures the durable loop may retry."""

        return error_type in {
            "ConnectionError",
            "TimeoutError",
            "connection_reset",
            "timeout_exceeded",
        }

    def _invoke(
        self,
        initial: ReleaseGraphState,
        *,
        tracer: ExecutionTracer | None,
        trace_output_dir: Path | None,
    ) -> ReleaseAgentWorkflowResult:
        active_tracer = tracer or ExecutionTracer()
        final_state = self._build_graph(active_tracer).invoke(initial)
        trace_artifacts = (
            active_tracer.write(trace_output_dir)
            if trace_output_dir is not None
            else None
        )
        artifact_paths = (
            {"execution_trace": str(trace_artifacts.trace_path)}
            if trace_artifacts
            else None
        )
        return ReleaseAgentWorkflowResult(
            final_state,
            trace=active_tracer.to_dict(artifact_paths=artifact_paths),
            trace_artifacts=trace_artifacts,
        )

    def _build_graph(self, tracer: ExecutionTracer | None) -> Any:
        return build_release_graph(
            self._tools.scan,
            self._build_roles(tracer),
            tracer,
        )

    def _build_roles(self, tracer: ExecutionTracer | None) -> ReleaseRoleAgents:
        return ReleaseRoleAgents(
            evidence=EvidenceAgent(self._tools.evidence, tracer),
            risk=RiskAgent(self._tools.risk, tracer),
            fix_planner=FixPlannerAgent(self._tools.fix_plan, tracer),
            verifier=VerifierAgent(),
        )
