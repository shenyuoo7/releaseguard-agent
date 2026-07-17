import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from releaseguard_agent.agents.release_decision_advisor import (
    ReleaseDecisionAdviceResult,
)
from releaseguard_agent.llm import LLMClient, LLMMessage, LLMResponse
from releaseguard_agent.models.retrieval_evidence import RetrievalEvidence


RELEASE_RISK_ANALYSIS_SCHEMA_VERSION = "2.0"
ReportDetailLevel = Literal["concise", "standard", "detailed"]

_ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}
_DETAIL_GUIDANCE = {
    "concise": "精简：结论清楚，每个问题给出最少但可执行的步骤。",
    "standard": (
        "标准：总结约 300 至 600 个中文字；每个警告或阻断问题都说明现象、"
        "影响、3 至 7 个修复步骤、建议文件、验证方法和规则证据。"
    ),
    "detailed": (
        "详细：在标准要求上补充取舍、示例、边界条件和失败排查，但不要重复凑字数。"
    ),
}


class ReleaseRiskAnalysisParseError(ValueError):
    """Raised when an LLM release-risk response is not valid."""


@dataclass(frozen=True)
class ReleaseRiskAnalysisContext:
    """Grounded input for the LLM release-risk Agent."""

    advice_result: ReleaseDecisionAdviceResult
    release_report_markdown: str | None = None
    release_checklist_markdown: str | None = None
    retrieval_evidence: tuple[RetrievalEvidence, ...] = ()
    check_results: tuple[dict[str, Any], ...] = ()
    trace_payload: Mapping[str, Any] = field(default_factory=dict)
    locale: str = "zh-CN"
    detail_level: ReportDetailLevel = "standard"

    def __post_init__(self) -> None:
        if self.detail_level not in _DETAIL_GUIDANCE:
            raise ValueError(f"Unsupported report detail level: {self.detail_level!r}.")
        object.__setattr__(
            self, "retrieval_evidence", tuple(copy.deepcopy(self.retrieval_evidence))
        )
        object.__setattr__(
            self,
            "check_results",
            tuple(copy.deepcopy(dict(item)) for item in self.check_results),
        )
        object.__setattr__(self, "trace_payload", copy.deepcopy(dict(self.trace_payload)))

    def to_dict(self) -> dict[str, Any]:
        """Convert Agent context to a prompt- and trace-ready dictionary."""
        return {
            "advice_result": self.advice_result.to_dict(),
            "release_report_markdown": self.release_report_markdown,
            "release_checklist_markdown": self.release_checklist_markdown,
            "retrieval_evidence": [item.to_dict() for item in self.retrieval_evidence],
            "check_results": [copy.deepcopy(item) for item in self.check_results],
            "trace_payload": copy.deepcopy(dict(self.trace_payload)),
            "locale": self.locale,
            "detail_level": self.detail_level,
        }


@dataclass(frozen=True)
class ReleaseRiskAnalysis:
    """Structured LLM release-risk analysis with deterministic guardrails."""

    schema_version: str
    risk_level: str
    executive_summary: str
    release_recommendation: str
    positive_findings: tuple[str, ...]
    risk_analysis: tuple[dict[str, Any], ...]
    prioritized_actions: tuple[dict[str, Any], ...]
    final_verification_steps: tuple[str, ...]
    limitations: tuple[str, ...]
    release_status: str
    release_allowed: bool
    model_release_status: str | None
    model_release_allowed: bool | None
    evidence_rule_ids: tuple[str, ...]
    unsupported_claims: tuple[str, ...]
    missing_evidence_notes: tuple[str, ...]
    guardrail_notes: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    locale: str = "zh-CN"
    detail_level: ReportDetailLevel = "standard"

    @property
    def summary(self) -> str:
        """Compatibility alias for the former schema."""
        return self.executive_summary

    @property
    def prioritized_risks(self) -> tuple[dict[str, Any], ...]:
        """Compatibility alias for the former schema."""
        return self.risk_analysis

    @property
    def fix_plan(self) -> tuple[dict[str, Any], ...]:
        """Compatibility alias for the former schema."""
        return self.prioritized_actions

    def to_dict(self) -> dict[str, Any]:
        """Convert analysis to a stable dictionary with compatibility aliases."""
        risks = [copy.deepcopy(item) for item in self.risk_analysis]
        actions = [copy.deepcopy(item) for item in self.prioritized_actions]
        return {
            "schema_version": self.schema_version,
            "locale": self.locale,
            "detail_level": self.detail_level,
            "risk_level": self.risk_level,
            "executive_summary": self.executive_summary,
            "release_recommendation": self.release_recommendation,
            "positive_findings": list(self.positive_findings),
            "risk_analysis": risks,
            "prioritized_actions": actions,
            "final_verification_steps": list(self.final_verification_steps),
            "limitations": list(self.limitations),
            "summary": self.executive_summary,
            "prioritized_risks": copy.deepcopy(risks),
            "fix_plan": copy.deepcopy(actions),
            "release_status": self.release_status,
            "release_allowed": self.release_allowed,
            "model_release_status": self.model_release_status,
            "model_release_allowed": self.model_release_allowed,
            "evidence_rule_ids": list(self.evidence_rule_ids),
            "unsupported_claims": list(self.unsupported_claims),
            "missing_evidence_notes": list(self.missing_evidence_notes),
            "guardrail_notes": list(self.guardrail_notes),
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class ReleaseRiskAnalysisResult:
    context: ReleaseRiskAnalysisContext
    analysis: ReleaseRiskAnalysis
    llm_response: LLMResponse
    prompt_messages: tuple[LLMMessage, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self.context.to_dict(),
            "analysis": self.analysis.to_dict(),
            "llm_response": self.llm_response.to_dict(),
            "prompt_messages": [message.to_dict() for message in self.prompt_messages],
        }


class ReleaseRiskAnalysisAgent:
    """LLM Agent that analyzes release risk from grounded evidence."""

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        self._llm_client = llm_client
        self._model = model
        self._temperature = temperature

    def analyze(self, context: ReleaseRiskAnalysisContext) -> ReleaseRiskAnalysisResult:
        prompt_messages = _build_prompt_messages(context)
        response = self._llm_client.complete(
            prompt_messages,
            model=self._model,
            temperature=self._temperature,
            response_format="json_object",
            metadata={
                "agent": "ReleaseRiskAnalysisAgent",
                "schema_version": RELEASE_RISK_ANALYSIS_SCHEMA_VERSION,
                "locale": context.locale,
                "detail_level": context.detail_level,
            },
        )
        analysis = _parse_analysis(content=response.content, context=context)
        return ReleaseRiskAnalysisResult(context, analysis, response, prompt_messages)


def _build_prompt_messages(context: ReleaseRiskAnalysisContext) -> tuple[LLMMessage, ...]:
    payload = {
        "schema_version": RELEASE_RISK_ANALYSIS_SCHEMA_VERSION,
        "task": "基于确定性检查事实和规则证据，生成可直接指导人工修复的发布准备分析。",
        "output_language": context.locale,
        "detail_level": context.detail_level,
        "detail_guidance": _DETAIL_GUIDANCE[context.detail_level],
        "deterministic_context": context.to_dict(),
        "response_schema": {
            "risk_level": "low | medium | high | critical",
            "executive_summary": "2 至 4 段简体中文，说明总体情况、核心风险和发布建议。",
            "release_recommendation": "简体中文发布建议，不得改变确定性裁决。",
            "positive_findings": [
                "优先列出 2 至 5 条来自实际 PASSED 检查的中文事实；"
                "不足 2 条时只列真实存在的项，不得补造。"
            ],
            "risk_analysis": [
                {
                    "title": "中文风险标题",
                    "severity": "low | medium | high | critical",
                    "what_was_found": "发现了什么",
                    "why_it_matters": "为什么重要",
                    "possible_impact": "可能影响",
                    "related_check_ids": ["必须来自 check_results.check_result_id"],
                    "evidence_ids": ["必须来自 retrieval_evidence.evidence_id"],
                }
            ],
            "prioritized_actions": [
                {
                    "priority": 1,
                    "title": "中文修复标题",
                    "objective": "修复目标",
                    "why": "修复原因",
                    "steps": ["3 至 7 个可执行步骤"],
                    "suggested_files": ["具体相对路径；无法确定时写需要人工确认"],
                    "example": "可复制的配置、代码或明确说明不适用",
                    "verification_command": "真实可运行的命令或页面操作",
                    "success_criteria": "可判断的完成标准",
                    "related_check_ids": ["相关 check_result_id"],
                    "rule_ids": ["相关规则 ID"],
                    "evidence_ids": ["相关 Evidence ID"],
                }
            ],
            "final_verification_steps": ["修复后的检查顺序和真实命令"],
            "limitations": ["本次扫描未覆盖或无法确定的内容"],
            "release_status": "模型看法，仅供记录",
            "release_allowed": "模型看法，仅供记录",
            "evidence_rule_ids": ["本次实际引用的规则 ID"],
            "evidence_ids": ["本次实际引用的 Evidence ID"],
            "unsupported_claims": ["没有依据的主张；通常应为空"],
            "missing_evidence_notes": ["证据不足说明"],
        },
    }
    return (
        LLMMessage.system(_SYSTEM_PROMPT),
        LLMMessage.user(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)),
    )


_SYSTEM_PROMPT = """你是 ReleaseGuard 的风险分析智能体（Risk Agent）。

你只能根据提供的确定性检查结果和规则证据进行分析。

硬性规则：
- 只返回合法 JSON，不要使用 Markdown 代码围栏。
- output_language 为 zh-CN 时，所有自然语言必须使用简体中文；代码、路径、命令、规则 ID、Evidence ID、Provider 和 Model 名称保持原样。
- 不得虚构检查、文件、命令、规则或来源；positive_findings 只能来自 PASSED 检查。
- 每个风险必须引用存在的 check_result_id；有规则证据时必须引用存在的 evidence_id。
- 每个警告或阻断问题都要有对应的风险分析和优先修复动作。
- 不得修改、隐藏或推翻确定性发布结论。
- 证据不足时必须在 limitations 或 missing_evidence_notes 中明确说明。
- 修复只能由用户手动实施，不要声称已经修改目标仓库。
- 标准详细度下，总结写 2 至 4 段，每个修复动作给出 3 至 7 个不重复的步骤、具体建议文件、示例、验证命令和成功标准。
"""


def _parse_analysis(*, content: str, context: ReleaseRiskAnalysisContext) -> ReleaseRiskAnalysis:
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReleaseRiskAnalysisParseError("LLM release-risk response was not valid JSON.") from exc
    if not isinstance(raw, Mapping):
        raise ReleaseRiskAnalysisParseError("LLM release-risk response must be a JSON object.")

    payload = dict(raw)
    is_v2 = "executive_summary" in payload
    normalized = _normalize_payload(payload)
    risk_level = _require_string(normalized, "risk_level").lower()
    if risk_level not in _ALLOWED_RISK_LEVELS:
        raise ReleaseRiskAnalysisParseError(f"Unsupported risk_level: {risk_level!r}.")

    risks = _require_dict_list(normalized, "risk_analysis")
    actions = _require_dict_list(normalized, "prioritized_actions")
    if is_v2:
        _validate_v2_items(risks, actions, context)
        if context.locale == "zh-CN":
            _validate_simplified_chinese(normalized)

    deterministic_status = context.advice_result.decision.status.value
    deterministic_allowed = context.advice_result.decision.release_allowed
    model_status = _optional_string(normalized, "release_status")
    model_allowed = _optional_bool(normalized, "release_allowed")
    evidence_ids = _validated_evidence_ids(
        normalized,
        context,
        risks,
        actions,
        require_citation=is_v2,
    )

    return ReleaseRiskAnalysis(
        schema_version=RELEASE_RISK_ANALYSIS_SCHEMA_VERSION,
        locale=context.locale,
        detail_level=context.detail_level,
        risk_level=risk_level,
        executive_summary=_require_string(normalized, "executive_summary"),
        release_recommendation=_require_string(normalized, "release_recommendation"),
        positive_findings=_require_string_list(normalized, "positive_findings"),
        risk_analysis=risks,
        prioritized_actions=actions,
        final_verification_steps=_require_string_list(normalized, "final_verification_steps"),
        limitations=_require_string_list(normalized, "limitations"),
        release_status=deterministic_status,
        release_allowed=deterministic_allowed,
        model_release_status=model_status,
        model_release_allowed=model_allowed,
        evidence_rule_ids=_require_string_list(normalized, "evidence_rule_ids"),
        unsupported_claims=_require_string_list(normalized, "unsupported_claims"),
        missing_evidence_notes=_require_string_list(normalized, "missing_evidence_notes"),
        guardrail_notes=_build_guardrail_notes(
            deterministic_status, deterministic_allowed, model_status, model_allowed
        ),
        evidence_ids=evidence_ids,
    )


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept the former public fields while making v2 the product contract."""
    normalized = copy.deepcopy(payload)
    summary = normalized.get("executive_summary", normalized.get("summary"))
    risks = normalized.get("risk_analysis", normalized.get("prioritized_risks", []))
    actions = normalized.get("prioritized_actions", normalized.get("fix_plan", []))
    normalized.setdefault("executive_summary", summary)
    normalized.setdefault("release_recommendation", str(summary or "请以确定性检查结论为准。"))
    normalized.setdefault("positive_findings", [])
    normalized.setdefault("risk_analysis", risks)
    normalized.setdefault("prioritized_actions", actions)
    normalized.setdefault(
        "final_verification_steps",
        [
            str(item.get("validation"))
            for item in actions
            if isinstance(item, Mapping) and item.get("validation")
        ],
    )
    normalized.setdefault("limitations", normalized.get("missing_evidence_notes", []))
    return normalized


def _validate_v2_items(
    risks: tuple[dict[str, Any], ...],
    actions: tuple[dict[str, Any], ...],
    context: ReleaseRiskAnalysisContext,
) -> None:
    risk_fields = {
        "title", "severity", "what_was_found", "why_it_matters",
        "possible_impact", "related_check_ids", "evidence_ids",
    }
    action_fields = {
        "priority", "title", "objective", "why", "steps", "suggested_files",
        "example", "verification_command", "success_criteria",
        "related_check_ids", "rule_ids", "evidence_ids",
    }
    for item in risks:
        _require_item_fields(item, risk_fields, "risk_analysis")
        severity = _require_string(item, "severity").lower()
        if severity not in _ALLOWED_RISK_LEVELS:
            raise ReleaseRiskAnalysisParseError(f"Unsupported risk severity: {severity!r}.")
        _require_string_list(item, "related_check_ids")
        _require_string_list(item, "evidence_ids")
    for item in actions:
        _require_item_fields(item, action_fields, "prioritized_actions")
        steps = _require_string_list(item, "steps")
        if context.detail_level in {"standard", "detailed"} and not 3 <= len(steps) <= 7:
            raise ReleaseRiskAnalysisParseError(
                "Each standard or detailed prioritized action must contain 3 to 7 steps."
            )
        _require_string_list(item, "suggested_files")
        _require_string_list(item, "related_check_ids")
        _require_string_list(item, "rule_ids")
        _require_string_list(item, "evidence_ids")
    actionable_ids = {
        str(item.get("check_result_id"))
        for item in context.check_results
        if item.get("status") in {"warning", "failed", "error"}
        and item.get("check_result_id")
    }
    risk_check_ids = {
        check_id
        for item in risks
        for check_id in item.get("related_check_ids", [])
        if isinstance(check_id, str)
    }
    action_check_ids = {
        check_id
        for item in actions
        for check_id in item.get("related_check_ids", [])
        if isinstance(check_id, str)
    }
    if not actionable_ids.issubset(risk_check_ids):
        raise ReleaseRiskAnalysisParseError(
            "Every warning or failed check must appear in risk_analysis."
        )
    if not actionable_ids.issubset(action_check_ids):
        raise ReleaseRiskAnalysisParseError(
            "Every warning or failed check must appear in prioritized_actions."
        )


def _require_item_fields(item: Mapping[str, Any], fields: set[str], section: str) -> None:
    missing = sorted(field for field in fields if field not in item)
    if missing:
        raise ReleaseRiskAnalysisParseError(
            f"Section {section!r} item is missing fields: {', '.join(missing)}."
        )


def _validated_evidence_ids(
    payload: Mapping[str, Any],
    context: ReleaseRiskAnalysisContext,
    risks: tuple[dict[str, Any], ...],
    actions: tuple[dict[str, Any], ...],
    *,
    require_citation: bool,
) -> tuple[str, ...]:
    top_level = _require_string_list(payload, "evidence_ids")
    nested = {
        evidence_id
        for item in (*risks, *actions)
        for evidence_id in item.get("evidence_ids", [])
        if isinstance(evidence_id, str)
    }
    cited = set(top_level).union(nested)
    available = {item.evidence_id for item in context.retrieval_evidence}
    unknown = sorted(cited.difference(available))
    if unknown:
        raise ReleaseRiskAnalysisParseError(
            "Field 'evidence_ids' contains IDs outside the supplied context."
        )
    if available and not cited and require_citation:
        raise ReleaseRiskAnalysisParseError(
            "Field 'evidence_ids' must cite at least one supplied Evidence ID."
        )
    valid_checks = {
        str(item.get("check_result_id"))
        for item in context.check_results
        if item.get("check_result_id")
    }
    if valid_checks:
        cited_checks = {
            check_id
            for item in (*risks, *actions)
            for check_id in item.get("related_check_ids", [])
            if isinstance(check_id, str)
        }
        if cited_checks.difference(valid_checks):
            raise ReleaseRiskAnalysisParseError(
                "Field 'related_check_ids' contains IDs outside the supplied context."
            )
    return tuple(dict.fromkeys([*top_level, *sorted(nested)]))


def _validate_simplified_chinese(payload: Mapping[str, Any]) -> None:
    fields: list[str] = [
        str(payload.get("executive_summary", "")),
        str(payload.get("release_recommendation", "")),
    ]
    fields.extend(str(item) for item in payload.get("positive_findings", []))
    for item in payload.get("risk_analysis", []):
        if isinstance(item, Mapping):
            fields.extend(
                str(item.get(key, ""))
                for key in (
                    "title",
                    "what_was_found",
                    "why_it_matters",
                    "possible_impact",
                )
            )
    for item in payload.get("prioritized_actions", []):
        if isinstance(item, Mapping):
            fields.extend(
                str(item.get(key, ""))
                for key in ("title", "objective", "why", "success_criteria")
            )
            fields.extend(str(step) for step in item.get("steps", []))
    fields.extend(str(item) for item in payload.get("limitations", []))
    natural_fields = [value.strip() for value in fields if value.strip()]
    cjk_count = sum(len(re.findall(r"[\u4e00-\u9fff]", item)) for item in natural_fields)
    english_only = [
        value
        for value in natural_fields
        if not re.search(r"[\u4e00-\u9fff]", value)
        and re.search(r"[A-Za-z]{3,}", value)
    ]
    if cjk_count < 12 or english_only:
        raise ReleaseRiskAnalysisParseError(
            "Model response did not provide the required Simplified Chinese analysis."
        )


def _require_string(payload: Mapping[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ReleaseRiskAnalysisParseError(f"Field {field_name!r} must be a non-empty string.")
    return value.strip()


def _optional_string(payload: Mapping[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReleaseRiskAnalysisParseError(
            f"Field {field_name!r} must be a non-empty string when present."
        )
    return value.strip()


def _optional_bool(payload: Mapping[str, Any], field_name: str) -> bool | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ReleaseRiskAnalysisParseError(f"Field {field_name!r} must be a boolean when present.")
    return value


def _require_dict_list(payload: Mapping[str, Any], field_name: str) -> tuple[dict[str, Any], ...]:
    value = payload.get(field_name)
    if not isinstance(value, list):
        raise ReleaseRiskAnalysisParseError(f"Field {field_name!r} must be a list.")
    if not all(isinstance(item, Mapping) for item in value):
        raise ReleaseRiskAnalysisParseError(
            f"Field {field_name!r} must contain only JSON objects."
        )
    return tuple(copy.deepcopy(dict(item)) for item in value)


def _require_string_list(payload: Mapping[str, Any], field_name: str) -> tuple[str, ...]:
    value = payload.get(field_name)
    if not isinstance(value, list):
        raise ReleaseRiskAnalysisParseError(f"Field {field_name!r} must be a list.")
    if not all(isinstance(item, str) for item in value):
        raise ReleaseRiskAnalysisParseError(f"Field {field_name!r} must contain only strings.")
    return tuple(item.strip() for item in value if item.strip())


def _build_guardrail_notes(
    deterministic_status: str,
    deterministic_release_allowed: bool,
    model_release_status: str | None,
    model_release_allowed: bool | None,
) -> tuple[str, ...]:
    notes: list[str] = []
    if model_release_status is not None and model_release_status != deterministic_status:
        notes.append(
            "Model release_status "
            f"{model_release_status!r} did not match deterministic status "
            f"{deterministic_status!r}; deterministic status is retained."
        )
    if model_release_allowed is not None and model_release_allowed != deterministic_release_allowed:
        notes.append(
            "Model release_allowed "
            f"{model_release_allowed!r} did not match deterministic release_allowed "
            f"{deterministic_release_allowed!r}; deterministic release_allowed is retained."
        )
    return tuple(notes)
