import json
from pathlib import Path

import pytest

from releaseguard_agent.agents.release_decision_advisor import (
    ReleaseDecisionAdvisor,
)
from releaseguard_agent.agents.release_risk_analysis_agent import (
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisContext,
    ReleaseRiskAnalysisParseError,
)
from releaseguard_agent.checkers.base import BaseChecker
from releaseguard_agent.core.checker_runner import CheckerRunner
from releaseguard_agent.llm import FakeLLMClient
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULE_INDEX_PATH = (
    PROJECT_ROOT
    / "knowledge_base"
    / "release_rules"
    / "rule_index.md"
)


class StaticChecker(BaseChecker):
    """Test checker that returns predefined check results."""

    description = "Returns predefined check results."

    def __init__(
        self,
        *,
        name: str,
        results: tuple[CheckResult, ...],
    ) -> None:
        self.name = name
        self._results = results

    def run(self, project_path: Path) -> list[CheckResult]:
        """Return predefined check results."""
        return list(self._results)


def _check_result(
    *,
    rule_id: str = "RG-DOCKER-003",
    status: CheckStatus = CheckStatus.FAILED,
    risk_level: RiskLevel = RiskLevel.HIGH,
    title: str = "Dockerfile release contract failed",
) -> CheckResult:
    return CheckResult(
        checker_name="docker_checker",
        status=status,
        risk_level=risk_level,
        title=title,
        message="Docker release intent exists but Dockerfile is invalid.",
        evidence=["Dockerfile is missing a required release contract."],
        recommendation="Add a valid Dockerfile for the release path.",
        rule_id=rule_id,
        rule_source="ReleaseGuard rule knowledge base",
    )


def _advice_result(
    project_path: Path,
    *check_results: CheckResult,
):
    advisor = ReleaseDecisionAdvisor.from_rule_index(
        runner=CheckerRunner(
            [
                StaticChecker(
                    name="static_checker",
                    results=tuple(check_results),
                )
            ]
        ),
        index_path=RULE_INDEX_PATH,
    )

    return advisor.advise(project_path)


def _model_response(**overrides: object) -> str:
    payload: dict[str, object] = {
        "risk_level": "high",
        "summary": "Release is blocked by a Docker release contract risk.",
        "release_status": "blocked",
        "release_allowed": False,
        "prioritized_risks": [
            {
                "rule_id": "RG-DOCKER-003",
                "title": "Docker release contract failed",
                "severity": "high",
                "reason": (
                    "A release with container intent needs a valid "
                    "Dockerfile."
                ),
                "evidence": [
                    "Deterministic checker reported RG-DOCKER-003."
                ],
            }
        ],
        "fix_plan": [
            {
                "priority": 1,
                "title": "Fix Dockerfile release contract",
                "action": "Create or repair the root Dockerfile.",
                "rule_ids": ["RG-DOCKER-003"],
                "validation": (
                    "Run ReleaseGuard again and confirm the check passes."
                ),
            }
        ],
        "evidence_rule_ids": ["RG-DOCKER-003"],
        "evidence_ids": [],
        "unsupported_claims": [],
        "missing_evidence_notes": [],
    }
    payload.update(overrides)

    return json.dumps(payload)


def _retrieval_evidence() -> RetrievalEvidence:
    return RetrievalEvidence(
        evidence_id="EVID-RG-DOCKER-003:chunk-01",
        rule_id="RG-DOCKER-003",
        source_url="https://docs.docker.com/reference/dockerfile/",
        local_source="knowledge_base/release_rules/sources/dockerfile_reference.md",
        chunk_id="RG-DOCKER-003:chunk-01",
        retrieval_method="exact",
        raw_score=1.0,
        fusion_score=0.0,
        rerank_score=0.0,
        text="Dockerfile release contract",
        metadata={},
    )


def test_agent_calls_llm_with_grounded_context_and_parses_analysis(
    tmp_path: Path,
) -> None:
    client = FakeLLMClient(responses=[_model_response()])
    agent = ReleaseRiskAnalysisAgent(
        llm_client=client,
        model="fake-risk-model",
    )
    context = ReleaseRiskAnalysisContext(
        advice_result=_advice_result(tmp_path, _check_result()),
        release_report_markdown="# Release Report\nBlocked.",
        release_checklist_markdown="# Release Checklist\nFix Dockerfile.",
        trace_payload={
            "run_id": "run-001",
        },
    )

    result = agent.analyze(context)

    assert result.analysis.risk_level == "high"
    assert result.analysis.release_status == "blocked"
    assert result.analysis.release_allowed is False
    assert result.analysis.model_release_status == "blocked"
    assert result.analysis.model_release_allowed is False
    assert result.analysis.evidence_rule_ids == ("RG-DOCKER-003",)
    assert result.analysis.guardrail_notes == ()

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call.model == "fake-risk-model"
    assert call.temperature == 0.0
    assert call.response_format == "json_object"
    assert call.metadata == {
        "agent": "ReleaseRiskAnalysisAgent",
        "schema_version": "2.0",
        "locale": "zh-CN",
        "detail_level": "standard",
    }

    assert call.messages[0].role == "system"
    assert call.messages[1].role == "user"
    assert "RG-DOCKER-003" in call.messages[1].content
    assert "release_report_markdown" in call.messages[1].content
    assert "release_checklist_markdown" in call.messages[1].content


def test_agent_keeps_deterministic_release_status_when_model_disagrees(
    tmp_path: Path,
) -> None:
    client = FakeLLMClient(
        responses=[
            _model_response(
                risk_level="low",
                release_status="ready",
                release_allowed=True,
            )
        ],
    )
    agent = ReleaseRiskAnalysisAgent(llm_client=client)
    context = ReleaseRiskAnalysisContext(
        advice_result=_advice_result(tmp_path, _check_result()),
    )

    result = agent.analyze(context)

    assert result.analysis.risk_level == "low"
    assert result.analysis.release_status == "blocked"
    assert result.analysis.release_allowed is False
    assert result.analysis.model_release_status == "ready"
    assert result.analysis.model_release_allowed is True
    assert result.analysis.guardrail_notes == (
        "Model release_status 'ready' did not match deterministic status "
        "'blocked'; deterministic status is retained.",
        "Model release_allowed True did not match deterministic "
        "release_allowed False; deterministic release_allowed is retained.",
    )


def test_agent_rejects_invalid_json_response(tmp_path: Path) -> None:
    client = FakeLLMClient(responses=["not json"])
    agent = ReleaseRiskAnalysisAgent(llm_client=client)
    context = ReleaseRiskAnalysisContext(
        advice_result=_advice_result(tmp_path, _check_result()),
    )

    with pytest.raises(ReleaseRiskAnalysisParseError) as exc_info:
        agent.analyze(context)

    assert str(exc_info.value) == (
        "LLM release-risk response was not valid JSON."
    )


def test_agent_rejects_missing_structured_fields(tmp_path: Path) -> None:
    client = FakeLLMClient(
        responses=[
            json.dumps(
                {
                    "risk_level": "high",
                    "summary": "Missing required lists.",
                    "release_status": "blocked",
                    "release_allowed": False,
                }
            )
        ],
    )
    agent = ReleaseRiskAnalysisAgent(llm_client=client)
    context = ReleaseRiskAnalysisContext(
        advice_result=_advice_result(tmp_path, _check_result()),
    )

    with pytest.raises(ReleaseRiskAnalysisParseError) as exc_info:
        agent.analyze(context)

    assert str(exc_info.value) == "Field 'evidence_ids' must be a list."


def test_result_can_be_converted_to_dict(tmp_path: Path) -> None:
    client = FakeLLMClient(responses=[_model_response()])
    agent = ReleaseRiskAnalysisAgent(llm_client=client)
    context = ReleaseRiskAnalysisContext(
        advice_result=_advice_result(tmp_path, _check_result()),
    )

    data = agent.analyze(context).to_dict()

    assert data["analysis"]["schema_version"] == "2.0"
    assert data["analysis"]["release_status"] == "blocked"
    assert data["llm_response"]["provider"] == "fake"
    assert data["prompt_messages"][0]["role"] == "system"
    assert data["context"]["advice_result"]["workflow_result"][
        "decision"
    ]["status"] == "blocked"


def test_agent_requires_and_validates_evidence_id_citations(
    tmp_path: Path,
) -> None:
    evidence = _retrieval_evidence()
    context = ReleaseRiskAnalysisContext(
        advice_result=_advice_result(tmp_path, _check_result()),
        retrieval_evidence=(evidence,),
    )
    valid = ReleaseRiskAnalysisAgent(
        llm_client=FakeLLMClient(
            [_model_response(evidence_ids=[evidence.evidence_id])]
        )
    ).analyze(context)

    assert valid.analysis.evidence_ids == (evidence.evidence_id,)
    assert valid.context.to_dict()["retrieval_evidence"][0]["chunk_id"] == (
        evidence.chunk_id
    )

    invalid_agent = ReleaseRiskAnalysisAgent(
        llm_client=FakeLLMClient(
            [_model_response(evidence_ids=["EVID-NOT-IN-CONTEXT"])]
        )
    )
    with pytest.raises(
        ReleaseRiskAnalysisParseError,
        match="outside the supplied context",
    ):
        invalid_agent.analyze(context)


def test_v2_schema_requires_chinese_actionable_analysis(tmp_path: Path) -> None:
    evidence = _retrieval_evidence()
    check_id = "CHECK-001-RG-DOCKER-003"
    payload = {
        "risk_level": "high",
        "executive_summary": (
            "确定性检查发现 Dockerfile 发布契约存在问题，当前不应直接发布。\n\n"
            "建议先修复容器入口并完成验证，再重新执行发布审查。"
        ),
        "release_recommendation": "完成修复和复检后再发布。",
        "positive_findings": [],
        "risk_analysis": [
            {
                "title": "容器发布契约不完整",
                "severity": "high",
                "what_was_found": "Dockerfile 缺少必要发布约束。",
                "why_it_matters": "容器无法形成稳定的发布入口。",
                "possible_impact": "构建或启动过程可能失败。",
                "related_check_ids": [check_id],
                "evidence_ids": [evidence.evidence_id],
            }
        ],
        "prioritized_actions": [
            {
                "priority": 1,
                "title": "修复 Dockerfile",
                "objective": "建立稳定的容器发布入口。",
                "why": "确保构建和启动过程可复现。",
                "steps": ["确认基础镜像。", "补齐启动指令。", "执行容器验证。"],
                "suggested_files": ["Dockerfile"],
                "example": "CMD [\"python\", \"app.py\"]",
                "verification_command": "docker build .",
                "success_criteria": "镜像构建成功且容器健康。",
                "related_check_ids": [check_id],
                "rule_ids": ["RG-DOCKER-003"],
                "evidence_ids": [evidence.evidence_id],
            }
        ],
        "final_verification_steps": ["运行 docker build .。"],
        "limitations": ["未连接生产环境。"],
        "release_status": "blocked",
        "release_allowed": False,
        "evidence_rule_ids": ["RG-DOCKER-003"],
        "evidence_ids": [evidence.evidence_id],
        "unsupported_claims": [],
        "missing_evidence_notes": [],
    }
    context = ReleaseRiskAnalysisContext(
        advice_result=_advice_result(tmp_path, _check_result()),
        retrieval_evidence=(evidence,),
        check_results=(
            {
                "check_result_id": check_id,
                "status": "failed",
                "rule_id": "RG-DOCKER-003",
            },
        ),
    )

    result = ReleaseRiskAnalysisAgent(
        llm_client=FakeLLMClient([json.dumps(payload, ensure_ascii=False)])
    ).analyze(context)

    assert result.analysis.executive_summary.startswith("确定性检查")
    assert result.analysis.prioritized_actions[0]["steps"] == [
        "确认基础镜像。",
        "补齐启动指令。",
        "执行容器验证。",
    ]
    assert result.analysis.locale == "zh-CN"
    assert result.analysis.detail_level == "standard"


def test_v2_schema_rejects_english_natural_language(tmp_path: Path) -> None:
    payload = {
        "risk_level": "low",
        "executive_summary": "This project appears ready.\n\nRun tests before release.",
        "release_recommendation": "Proceed with caution.",
        "positive_findings": [],
        "risk_analysis": [],
        "prioritized_actions": [],
        "final_verification_steps": [],
        "limitations": [],
        "release_status": "release",
        "release_allowed": True,
        "evidence_rule_ids": [],
        "evidence_ids": [],
        "unsupported_claims": [],
        "missing_evidence_notes": [],
    }
    context = ReleaseRiskAnalysisContext(
        advice_result=_advice_result(
            tmp_path,
            _check_result(status=CheckStatus.PASSED, risk_level=RiskLevel.INFO),
        )
    )

    with pytest.raises(ReleaseRiskAnalysisParseError, match="Simplified Chinese"):
        ReleaseRiskAnalysisAgent(
            llm_client=FakeLLMClient([json.dumps(payload)])
        ).analyze(context)


def test_report_detail_level_is_configurable_in_prompt_and_metadata(
    tmp_path: Path,
) -> None:
    client = FakeLLMClient([_model_response()])
    context = ReleaseRiskAnalysisContext(
        advice_result=_advice_result(tmp_path, _check_result()),
        detail_level="detailed",
    )

    ReleaseRiskAnalysisAgent(llm_client=client).analyze(context)

    prompt = json.loads(client.calls[0].messages[-1].content)
    assert prompt["detail_level"] == "detailed"
    assert "边界条件" in prompt["detail_guidance"]
    assert client.calls[0].metadata["detail_level"] == "detailed"
