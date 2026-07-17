import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llama_index.core.embeddings import BaseEmbedding

from releaseguard_agent.agents import (
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisContext,
)
from releaseguard_agent.llm import FakeLLMClient
from releaseguard_agent.rag import RuleRetrievalService, get_default_rule_index_path
from releaseguard_agent.services import (
    ReleaseReviewService,
    build_agent_advice_result,
)
from releaseguard_agent.services.agent_workflow_service import (
    ReleaseAgentWorkflowService,
)
from releaseguard_agent.services.verification_service import (
    ReleaseVerificationService,
)


class DeterministicEvaluationEmbedding(BaseEmbedding):
    """Fixed offline embedding for integration repeatability, not quality claims."""

    def _vector(self, text: str) -> list[float]:
        normalized = text.lower()
        return [
            float(normalized.count("docker")),
            float(normalized.count("pytest") + normalized.count("test")),
            float(normalized.count("fastapi")),
            float(normalized.count("dependency")),
            float(normalized.count("from")),
            float(normalized.count("base image")),
            1.0,
        ]

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._vector(text)


@dataclass(frozen=True)
class EvaluationResult:
    dataset: str
    metrics: dict[str, float]
    details: dict[str, Any]

    @property
    def passed(self) -> bool:
        return all(value == 1.0 for value in self.metrics.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "metrics": dict(self.metrics),
            "passed": self.passed,
            "details": dict(self.details),
            "limitations": [
                "Fixed fake embeddings validate plumbing, not semantic quality.",
                "FakeLLM validates schema handling, not provider answer quality.",
            ],
        }


class EvaluationRunner:
    """Run deterministic golden cases against real product entry services."""

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[3]
        )
        self._review = ReleaseReviewService()

    def run(self, dataset_path: Path) -> EvaluationResult:
        normalized = Path(dataset_path).expanduser().resolve()
        payload = json.loads(normalized.read_text(encoding="utf-8"))
        retrieval = self._evaluate_retrieval(payload["retrieval_cases"])
        decisions = self._evaluate_decisions(payload["decision_cases"])
        paths = self._evaluate_paths(payload["graph_cases"])
        llm_validity = self._evaluate_llm(payload["llm_cases"])
        delta = self._evaluate_verification(payload["verification_cases"])
        metrics = {
            "recall_at_k": retrieval["recall_at_k"],
            "evidence_source_accuracy": retrieval["source_accuracy"],
            "deterministic_decision_consistency": decisions["accuracy"],
            "llm_structured_output_valid_rate": llm_validity["valid_rate"],
            "graph_path_coverage": paths["coverage"],
            "before_after_delta_accuracy": delta["accuracy"],
        }
        return EvaluationResult(
            dataset=str(normalized),
            metrics=metrics,
            details={
                "retrieval": retrieval["cases"],
                "decisions": decisions["cases"],
                "graph_paths": paths["cases"],
                "llm": llm_validity["cases"],
                "verification": delta["cases"],
            },
        )

    def _evaluate_retrieval(
        self, cases: list[dict[str, Any]]
    ) -> dict[str, Any]:
        service = RuleRetrievalService(
            get_default_rule_index_path(),
            embed_model=DeterministicEvaluationEmbedding(),
        )
        expected_total = 0
        hits = 0
        source_hits = 0
        details = []
        for case in cases:
            result = service.retrieve(
                case["query"], mode=case["mode"], top_k=case["top_k"]
            )
            expected = set(case["expected_rule_ids"])
            returned = {item.rule_id for item in result.evidence}
            matched = expected.intersection(returned)
            expected_total += len(expected)
            hits += len(matched)
            source_hits += sum(
                1
                for rule_id in matched
                if any(
                    item.rule_id == rule_id
                    and item.source_url
                    and item.local_source
                    for item in result.evidence
                )
            )
            details.append(
                {
                    "name": case["name"],
                    "mode": result.mode_used,
                    "expected": sorted(expected),
                    "returned": sorted(returned),
                    "matched": sorted(matched),
                }
            )
        denominator = expected_total or 1
        return {
            "recall_at_k": hits / denominator,
            "source_accuracy": source_hits / denominator,
            "cases": details,
        }

    def _evaluate_decisions(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        correct = 0
        details = []
        for case in cases:
            review = self._review.review(
                project_path=self._project_root / case["project"],
                include_pytest_execution=case["include_pytest_execution"],
            )
            matched = review.release_allowed == case["release_allowed"]
            correct += int(matched)
            details.append(
                {
                    "name": case["name"],
                    "expected": case["release_allowed"],
                    "actual": review.release_allowed,
                    "matched": matched,
                }
            )
        return {"accuracy": correct / (len(cases) or 1), "cases": details}

    def _evaluate_paths(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        expected_nodes: set[str] = set()
        observed_nodes: set[str] = set()
        details = []
        service = ReleaseAgentWorkflowService()
        for case in cases:
            result = service.run(
                project_path=self._project_root / case["project"],
                include_pytest_execution=case["include_pytest_execution"],
            )
            expected = set(case["expected_nodes"])
            observed = set(result.state["route_history"])
            expected_nodes.update(expected)
            observed_nodes.update(observed)
            details.append(
                {
                    "name": case["name"],
                    "expected": sorted(expected),
                    "observed": sorted(observed),
                    "covered": expected.issubset(observed),
                }
            )
        return {
            "coverage": len(expected_nodes.intersection(observed_nodes))
            / (len(expected_nodes) or 1),
            "cases": details,
        }

    def _evaluate_llm(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        valid = 0
        details = []
        for case in cases:
            review = self._review.review(
                project_path=self._project_root / case["project"],
                include_pytest_execution=False,
            )
            evidence = review.retrieval_evidence
            advice = build_agent_advice_result(
                project_path=review.project_path,
                results=review.check_results,
            )
            evidence_id = evidence[0].evidence_id
            response = json.dumps(
                {
                    "risk_level": "high",
                    "summary": "Offline golden response.",
                    "release_status": "blocked",
                    "release_allowed": False,
                    "prioritized_risks": [],
                    "fix_plan": [],
                    "evidence_rule_ids": [evidence[0].rule_id],
                    "evidence_ids": [evidence_id],
                    "unsupported_claims": [],
                    "missing_evidence_notes": [],
                }
            )
            result = ReleaseRiskAnalysisAgent(
                llm_client=FakeLLMClient([response])
            ).analyze(
                ReleaseRiskAnalysisContext(
                    advice_result=advice,
                    retrieval_evidence=evidence,
                )
            )
            matched = result.analysis.evidence_ids == (evidence_id,)
            valid += int(matched)
            details.append({"name": case["name"], "valid": matched})
        return {"valid_rate": valid / (len(cases) or 1), "cases": details}

    def _evaluate_verification(
        self, cases: list[dict[str, Any]]
    ) -> dict[str, Any]:
        correct = 0
        details = []
        service = ReleaseVerificationService()
        for case in cases:
            result = service.verify(
                before_project_path=self._project_root / case["before_project"],
                after_project_path=self._project_root / case["after_project"],
                include_pytest_execution=False,
            )
            resolved_rules = {
                item.split("::", 1)[0] for item in result.delta.resolved
            }
            expected = set(case["resolved_rule_ids"])
            matched = expected == resolved_rules and (
                result.release_allowed == case["release_allowed"]
            )
            correct += int(matched)
            details.append(
                {
                    "name": case["name"],
                    "expected_resolved": sorted(expected),
                    "actual_resolved": sorted(resolved_rules),
                    "matched": matched,
                }
            )
        return {"accuracy": correct / (len(cases) or 1), "cases": details}
