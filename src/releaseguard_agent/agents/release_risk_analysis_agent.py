import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from releaseguard_agent.agents.release_decision_advisor import (
    ReleaseDecisionAdviceResult,
)
from releaseguard_agent.llm import LLMClient, LLMMessage, LLMResponse


RELEASE_RISK_ANALYSIS_SCHEMA_VERSION = "1.0"

_ALLOWED_RISK_LEVELS = {
    "low",
    "medium",
    "high",
    "critical",
}


class ReleaseRiskAnalysisParseError(ValueError):
    """Raised when an LLM release-risk response is not valid."""


@dataclass(frozen=True)
class ReleaseRiskAnalysisContext:
    """Grounded input for the first LLM release-risk Agent."""

    advice_result: ReleaseDecisionAdviceResult
    release_report_markdown: str | None = None
    release_checklist_markdown: str | None = None
    trace_payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trace_payload",
            copy.deepcopy(dict(self.trace_payload)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert Agent context to a prompt- and trace-ready dictionary."""
        return {
            "advice_result": self.advice_result.to_dict(),
            "release_report_markdown": self.release_report_markdown,
            "release_checklist_markdown": self.release_checklist_markdown,
            "trace_payload": copy.deepcopy(dict(self.trace_payload)),
        }


@dataclass(frozen=True)
class ReleaseRiskAnalysis:
    """Structured LLM release-risk analysis with deterministic guardrails."""

    schema_version: str
    risk_level: str
    summary: str
    release_status: str
    release_allowed: bool
    model_release_status: str | None
    model_release_allowed: bool | None
    prioritized_risks: tuple[dict[str, Any], ...]
    fix_plan: tuple[dict[str, Any], ...]
    evidence_rule_ids: tuple[str, ...]
    unsupported_claims: tuple[str, ...]
    missing_evidence_notes: tuple[str, ...]
    guardrail_notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Convert analysis to a stable dictionary."""
        return {
            "schema_version": self.schema_version,
            "risk_level": self.risk_level,
            "summary": self.summary,
            "release_status": self.release_status,
            "release_allowed": self.release_allowed,
            "model_release_status": self.model_release_status,
            "model_release_allowed": self.model_release_allowed,
            "prioritized_risks": [
                copy.deepcopy(risk)
                for risk in self.prioritized_risks
            ],
            "fix_plan": [
                copy.deepcopy(step)
                for step in self.fix_plan
            ],
            "evidence_rule_ids": list(self.evidence_rule_ids),
            "unsupported_claims": list(self.unsupported_claims),
            "missing_evidence_notes": list(self.missing_evidence_notes),
            "guardrail_notes": list(self.guardrail_notes),
        }


@dataclass(frozen=True)
class ReleaseRiskAnalysisResult:
    """Full result returned by ReleaseRiskAnalysisAgent."""

    context: ReleaseRiskAnalysisContext
    analysis: ReleaseRiskAnalysis
    llm_response: LLMResponse
    prompt_messages: tuple[LLMMessage, ...]

    def to_dict(self) -> dict[str, Any]:
        """Convert the result to a plain dictionary."""
        return {
            "context": self.context.to_dict(),
            "analysis": self.analysis.to_dict(),
            "llm_response": self.llm_response.to_dict(),
            "prompt_messages": [
                message.to_dict()
                for message in self.prompt_messages
            ],
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

    def analyze(
        self,
        context: ReleaseRiskAnalysisContext,
    ) -> ReleaseRiskAnalysisResult:
        """Ask the configured LLM for structured release-risk analysis."""
        prompt_messages = _build_prompt_messages(context)
        response = self._llm_client.complete(
            prompt_messages,
            model=self._model,
            temperature=self._temperature,
            response_format="json_object",
            metadata={
                "agent": "ReleaseRiskAnalysisAgent",
                "schema_version": RELEASE_RISK_ANALYSIS_SCHEMA_VERSION,
            },
        )
        analysis = _parse_analysis(
            content=response.content,
            context=context,
        )

        return ReleaseRiskAnalysisResult(
            context=context,
            analysis=analysis,
            llm_response=response,
            prompt_messages=prompt_messages,
        )


def _build_prompt_messages(
    context: ReleaseRiskAnalysisContext,
) -> tuple[LLMMessage, ...]:
    payload = {
        "schema_version": RELEASE_RISK_ANALYSIS_SCHEMA_VERSION,
        "task": "Analyze release risk and produce a prioritized fix plan.",
        "deterministic_context": context.to_dict(),
        "response_schema": {
            "risk_level": "low | medium | high | critical",
            "summary": "Short risk summary.",
            "release_status": "The model's view of release status.",
            "release_allowed": "The model's view of release permission.",
            "prioritized_risks": [
                {
                    "rule_id": "Related rule ID or null.",
                    "title": "Risk title.",
                    "severity": "low | medium | high | critical",
                    "reason": "Why this risk matters.",
                    "evidence": ["Evidence from deterministic context."],
                }
            ],
            "fix_plan": [
                {
                    "priority": 1,
                    "title": "Fix title.",
                    "action": "Concrete remediation action.",
                    "rule_ids": ["Related rule IDs."],
                    "validation": "How to verify the fix.",
                }
            ],
            "evidence_rule_ids": ["Rule IDs cited by the analysis."],
            "unsupported_claims": ["Claims without evidence, if any."],
            "missing_evidence_notes": ["Evidence gaps, if any."],
        },
    }

    return (
        LLMMessage.system(_SYSTEM_PROMPT),
        LLMMessage.user(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
                default=str,
            )
        ),
    )


_SYSTEM_PROMPT = """You are ReleaseRiskAnalysisAgent.

Analyze software release risk using only the supplied deterministic context.

Rules:
- Return only valid JSON.
- Do not invent checks, files, commands, rule IDs, or source citations.
- Do not hide missing evidence.
- Do not silently override deterministic release status.
- If your release_status or release_allowed differs from the deterministic
  decision, the deterministic decision remains authoritative.
- Prioritize fixes that unblock release first.
"""


def _parse_analysis(
    *,
    content: str,
    context: ReleaseRiskAnalysisContext,
) -> ReleaseRiskAnalysis:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReleaseRiskAnalysisParseError(
            "LLM release-risk response was not valid JSON."
        ) from exc

    if not isinstance(payload, Mapping):
        raise ReleaseRiskAnalysisParseError(
            "LLM release-risk response must be a JSON object."
        )

    risk_level = _require_string(payload, "risk_level").lower()
    if risk_level not in _ALLOWED_RISK_LEVELS:
        raise ReleaseRiskAnalysisParseError(
            f"Unsupported risk_level: {risk_level!r}."
        )

    deterministic_status = context.advice_result.decision.status.value
    deterministic_release_allowed = (
        context.advice_result.decision.release_allowed
    )
    model_release_status = _optional_string(payload, "release_status")
    model_release_allowed = _optional_bool(payload, "release_allowed")

    return ReleaseRiskAnalysis(
        schema_version=RELEASE_RISK_ANALYSIS_SCHEMA_VERSION,
        risk_level=risk_level,
        summary=_require_string(payload, "summary"),
        release_status=deterministic_status,
        release_allowed=deterministic_release_allowed,
        model_release_status=model_release_status,
        model_release_allowed=model_release_allowed,
        prioritized_risks=_require_dict_list(
            payload,
            "prioritized_risks",
        ),
        fix_plan=_require_dict_list(payload, "fix_plan"),
        evidence_rule_ids=_require_string_list(
            payload,
            "evidence_rule_ids",
        ),
        unsupported_claims=_require_string_list(
            payload,
            "unsupported_claims",
        ),
        missing_evidence_notes=_require_string_list(
            payload,
            "missing_evidence_notes",
        ),
        guardrail_notes=_build_guardrail_notes(
            deterministic_status=deterministic_status,
            deterministic_release_allowed=deterministic_release_allowed,
            model_release_status=model_release_status,
            model_release_allowed=model_release_allowed,
        ),
    )


def _require_string(
    payload: Mapping[str, Any],
    field_name: str,
) -> str:
    value = payload.get(field_name)

    if not isinstance(value, str) or not value.strip():
        raise ReleaseRiskAnalysisParseError(
            f"Field {field_name!r} must be a non-empty string."
        )

    return value.strip()


def _optional_string(
    payload: Mapping[str, Any],
    field_name: str,
) -> str | None:
    value = payload.get(field_name)

    if value is None:
        return None

    if not isinstance(value, str) or not value.strip():
        raise ReleaseRiskAnalysisParseError(
            f"Field {field_name!r} must be a non-empty string when present."
        )

    return value.strip()


def _optional_bool(
    payload: Mapping[str, Any],
    field_name: str,
) -> bool | None:
    value = payload.get(field_name)

    if value is None:
        return None

    if not isinstance(value, bool):
        raise ReleaseRiskAnalysisParseError(
            f"Field {field_name!r} must be a boolean when present."
        )

    return value


def _require_dict_list(
    payload: Mapping[str, Any],
    field_name: str,
) -> tuple[dict[str, Any], ...]:
    value = payload.get(field_name)

    if not isinstance(value, list):
        raise ReleaseRiskAnalysisParseError(
            f"Field {field_name!r} must be a list."
        )

    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ReleaseRiskAnalysisParseError(
                f"Field {field_name!r} must contain only JSON objects."
            )
        items.append(copy.deepcopy(dict(item)))

    return tuple(items)


def _require_string_list(
    payload: Mapping[str, Any],
    field_name: str,
) -> tuple[str, ...]:
    value = payload.get(field_name)

    if not isinstance(value, list):
        raise ReleaseRiskAnalysisParseError(
            f"Field {field_name!r} must be a list."
        )

    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ReleaseRiskAnalysisParseError(
                f"Field {field_name!r} must contain only strings."
            )
        strings.append(item)

    return tuple(strings)


def _build_guardrail_notes(
    *,
    deterministic_status: str,
    deterministic_release_allowed: bool,
    model_release_status: str | None,
    model_release_allowed: bool | None,
) -> tuple[str, ...]:
    notes: list[str] = []

    if (
        model_release_status is not None
        and model_release_status != deterministic_status
    ):
        notes.append(
            "Model release_status "
            f"{model_release_status!r} did not match deterministic status "
            f"{deterministic_status!r}; deterministic status is retained."
        )

    if (
        model_release_allowed is not None
        and model_release_allowed != deterministic_release_allowed
    ):
        notes.append(
            "Model release_allowed "
            f"{model_release_allowed!r} did not match deterministic "
            f"release_allowed {deterministic_release_allowed!r}; "
            "deterministic release_allowed is retained."
        )

    return tuple(notes)
