"""Versioned immutable contracts for durable Agent runtime replay."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping, cast, get_args
from types import MappingProxyType


RUNTIME_SCHEMA_VERSION = 1
AgentRunStatus = Literal[
    "CREATED",
    "RUNNING",
    "WAITING_HITL",
    "PAUSED",
    "RECOVERING",
    "FAILED",
    "COMPLETED",
    "CANCELLED",
]
_RUN_STATUSES = frozenset(get_args(AgentRunStatus))
RunLifecyclePhase = Literal[
    "CREATED",
    "PLANNED",
    "TOOL_REQUESTED",
    "APPROVAL_PENDING",
    "APPROVAL_GRANTED",
    "TOOL_STARTED",
    "TOOL_TERMINAL",
    "EVALUATED",
    "CHECKPOINTED",
    "PAUSED",
    "RECOVERING",
    "TERMINAL",
]
EvaluationOutcome = Literal["CONTINUE", "RETRY", "COMPLETED", "FAILED", "PAUSED"]
_LIFECYCLE_PHASES = frozenset(get_args(RunLifecyclePhase))
_EVALUATION_OUTCOMES = frozenset(get_args(EvaluationOutcome))


def canonical_json(value: object) -> str:
    """Return compact, sorted, UTF-8-safe JSON for durable hashes."""

    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: object) -> str:
    """Hash the UTF-8 bytes of a value's canonical JSON representation."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def run_event_digest(
    *,
    event_id: str,
    run_id: str,
    sequence: int,
    event_kind: str,
    payload: Mapping[str, Any],
    created_at_utc: str,
) -> str:
    """Hash the complete immutable event envelope, including its UTC time."""

    return sha256_json(
        {
            "event_id": event_id,
            "run_id": run_id,
            "sequence": sequence,
            "event_kind": event_kind,
            "payload": payload,
            "created_at_utc": created_at_utc,
        }
    )


def approval_id_for(
    run_id: str,
    step_index: int,
    idempotency_key: str,
    *,
    tool_name: str = "legacy",
    tool_version: str = "legacy",
    args_sha256: str = "legacy",
    allowed_paths: tuple[str, ...] = (),
    scope: str = "legacy_approval",
) -> str:
    """Derive a non-secret approval identity from immutable request identity."""

    return "approval:" + sha256_json(
        {
            "run_id": run_id,
            "step_index": step_index,
            "idempotency_key": idempotency_key,
            "tool_name": tool_name,
            "tool_version": tool_version,
            "args_sha256": args_sha256,
            "allowed_paths": sorted(set(allowed_paths)),
            "scope": scope,
        }
    )


ApprovalDecision = Literal["APPROVED", "REJECTED", "EXPIRED"]
_APPROVAL_DECISIONS = frozenset(get_args(ApprovalDecision))


@dataclass(frozen=True)
class ApprovalRequest:
    """Durable approval request bound to one exact versioned tool call."""

    approval_id: str
    run_id: str
    tool_name: str
    tool_version: str
    args_sha256: str
    allowed_paths: tuple[str, ...]
    scope: str
    step_index: int
    idempotency_key: str
    issued_at_utc: str
    expires_at_utc: str
    requester_identity: str
    identity_evidence: str
    request_signature: str

    def __post_init__(self) -> None:
        paths = _canonical_string_set(self.allowed_paths, "allowed_paths")
        object.__setattr__(self, "allowed_paths", paths)
        for value, name in (
            (self.approval_id, "approval_id"),
            (self.run_id, "run_id"),
            (self.tool_name, "tool_name"),
            (self.tool_version, "tool_version"),
            (self.args_sha256, "args_sha256"),
            (self.scope, "scope"),
            (self.idempotency_key, "idempotency_key"),
            (self.requester_identity, "requester_identity"),
            (self.identity_evidence, "identity_evidence"),
            (self.request_signature, "request_signature"),
        ):
            _nonempty(value, name)
        _int(self.step_index, "step_index")
        issued = _utc_timestamp(self.issued_at_utc, "issued_at_utc")
        expires = _utc_timestamp(self.expires_at_utc, "expires_at_utc")
        if expires <= issued:
            raise ValueError("approval expiry must be after issuance")
        if self.request_signature != _approval_request_signature(self):
            raise ValueError("approval request signature does not match its binding")
        if self.approval_id != approval_id_for(
            self.run_id,
            self.step_index,
            self.idempotency_key,
            tool_name=self.tool_name,
            tool_version=self.tool_version,
            args_sha256=self.args_sha256,
            allowed_paths=self.allowed_paths,
            scope=self.scope,
        ):
            raise ValueError("approval_id does not match its exact request binding")

    @classmethod
    def issue(
        cls,
        *,
        run_id: str,
        tool_name: str,
        tool_version: str,
        args_sha256: str,
        allowed_paths: tuple[str, ...],
        scope: str,
        step_index: int,
        idempotency_key: str,
        issued_at_utc: str,
        expires_at_utc: str,
        requester_identity: str,
        identity_evidence: str,
    ) -> "ApprovalRequest":
        paths = _canonical_string_set(allowed_paths, "allowed_paths")
        approval_id = approval_id_for(
            run_id,
            step_index,
            idempotency_key,
            tool_name=tool_name,
            tool_version=tool_version,
            args_sha256=args_sha256,
            allowed_paths=paths,
            scope=scope,
        )
        unsigned = cls.__new__(cls)
        values = {
            "approval_id": approval_id,
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_version": tool_version,
            "args_sha256": args_sha256,
            "allowed_paths": paths,
            "scope": scope,
            "step_index": step_index,
            "idempotency_key": idempotency_key,
            "issued_at_utc": issued_at_utc,
            "expires_at_utc": expires_at_utc,
            "requester_identity": requester_identity,
            "identity_evidence": identity_evidence,
        }
        for name, value in values.items():
            object.__setattr__(unsigned, name, value)
        object.__setattr__(unsigned, "request_signature", "")
        return cls(
            approval_id=approval_id,
            run_id=run_id,
            tool_name=tool_name,
            tool_version=tool_version,
            args_sha256=args_sha256,
            allowed_paths=paths,
            scope=scope,
            step_index=step_index,
            idempotency_key=idempotency_key,
            issued_at_utc=issued_at_utc,
            expires_at_utc=expires_at_utc,
            requester_identity=requester_identity,
            identity_evidence=identity_evidence,
            request_signature=_approval_request_signature(unsigned),
        )

    def binding_dict(self) -> dict[str, object]:
        return {
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "args_sha256": self.args_sha256,
            "allowed_paths": list(self.allowed_paths),
            "scope": self.scope,
            "step_index": self.step_index,
            "idempotency_key": self.idempotency_key,
            "issued_at_utc": self.issued_at_utc,
            "expires_at_utc": self.expires_at_utc,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.binding_dict(),
            "requester_identity": self.requester_identity,
            "identity_evidence": self.identity_evidence,
            "request_signature": self.request_signature,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ApprovalRequest":
        _exact_keys(value, set(cls.__dataclass_fields__), "approval request")
        raw_paths = value["allowed_paths"]
        if not isinstance(raw_paths, (list, tuple)):
            raise ValueError("allowed_paths must be a list or tuple")
        return cls(
            approval_id=_nonempty_value(value["approval_id"], "approval_id"),
            run_id=_nonempty_value(value["run_id"], "run_id"),
            tool_name=_nonempty_value(value["tool_name"], "tool_name"),
            tool_version=_nonempty_value(value["tool_version"], "tool_version"),
            args_sha256=_nonempty_value(value["args_sha256"], "args_sha256"),
            allowed_paths=tuple(raw_paths),
            scope=_nonempty_value(value["scope"], "scope"),
            step_index=_int(value["step_index"], "step_index"),
            idempotency_key=_nonempty_value(value["idempotency_key"], "idempotency_key"),
            issued_at_utc=_nonempty_value(value["issued_at_utc"], "issued_at_utc"),
            expires_at_utc=_nonempty_value(value["expires_at_utc"], "expires_at_utc"),
            requester_identity=_nonempty_value(
                value["requester_identity"], "requester_identity"
            ),
            identity_evidence=_nonempty_value(
                value["identity_evidence"], "identity_evidence"
            ),
            request_signature=_nonempty_value(
                value["request_signature"], "request_signature"
            ),
        )


@dataclass(frozen=True)
class ApprovalGrant:
    """Immutable signed decision consumable by one exact tool start."""

    approval_id: str
    run_id: str
    tool_name: str
    tool_version: str
    args_sha256: str
    allowed_paths: tuple[str, ...]
    scope: str
    step_index: int
    idempotency_key: str
    actor: str
    decision: ApprovalDecision
    issued_at_utc: str
    expires_at_utc: str
    decided_at_utc: str
    identity_evidence: str
    signature: str

    def __post_init__(self) -> None:
        paths = _canonical_string_set(self.allowed_paths, "allowed_paths")
        object.__setattr__(self, "allowed_paths", paths)
        for value, name in (
            (self.approval_id, "approval_id"),
            (self.run_id, "run_id"),
            (self.tool_name, "tool_name"),
            (self.tool_version, "tool_version"),
            (self.args_sha256, "args_sha256"),
            (self.scope, "scope"),
            (self.idempotency_key, "idempotency_key"),
            (self.actor, "actor"),
            (self.identity_evidence, "identity_evidence"),
            (self.signature, "signature"),
        ):
            _nonempty(value, name)
        _int(self.step_index, "step_index")
        if self.decision not in _APPROVAL_DECISIONS:
            raise ValueError("approval decision is invalid")
        issued = _utc_timestamp(self.issued_at_utc, "issued_at_utc")
        expires = _utc_timestamp(self.expires_at_utc, "expires_at_utc")
        decided = _utc_timestamp(self.decided_at_utc, "decided_at_utc")
        if expires <= issued or decided < issued or decided > expires:
            raise ValueError("approval decision times are invalid")
        if self.signature != _approval_grant_signature(self):
            raise ValueError("approval grant signature does not match its binding")
        if self.approval_id != approval_id_for(
            self.run_id,
            self.step_index,
            self.idempotency_key,
            tool_name=self.tool_name,
            tool_version=self.tool_version,
            args_sha256=self.args_sha256,
            allowed_paths=self.allowed_paths,
            scope=self.scope,
        ):
            raise ValueError("approval_id does not match its exact grant binding")

    @classmethod
    def issue(
        cls,
        request: ApprovalRequest,
        *,
        actor: str,
        decision: ApprovalDecision,
        decided_at_utc: str,
        identity_evidence: str,
    ) -> "ApprovalGrant":
        values = {
            **request.binding_dict(),
            "actor": actor,
            "decision": decision,
            "decided_at_utc": decided_at_utc,
            "identity_evidence": identity_evidence,
        }
        unsigned = cls.__new__(cls)
        for name, value in values.items():
            object.__setattr__(unsigned, name, value)
        object.__setattr__(unsigned, "signature", "")
        return cls(
            approval_id=request.approval_id,
            run_id=request.run_id,
            tool_name=request.tool_name,
            tool_version=request.tool_version,
            args_sha256=request.args_sha256,
            allowed_paths=request.allowed_paths,
            scope=request.scope,
            step_index=request.step_index,
            idempotency_key=request.idempotency_key,
            actor=actor,
            decision=decision,
            issued_at_utc=request.issued_at_utc,
            expires_at_utc=request.expires_at_utc,
            decided_at_utc=decided_at_utc,
            identity_evidence=identity_evidence,
            signature=_approval_grant_signature(unsigned),
        )

    def verify_for(self, request: ApprovalRequest, *, at_utc: str) -> None:
        expected = request.binding_dict()
        actual = {key: self.to_dict()[key] for key in expected}
        if actual != expected:
            raise ValueError("approval grant does not match the exact request")
        if self.decision != "APPROVED":
            raise ValueError("approval grant is not approved")
        at = _utc_timestamp(at_utc, "at_utc")
        if at > _utc_timestamp(self.expires_at_utc, "expires_at_utc"):
            raise ValueError("approval grant is expired")

    def to_dict(self) -> dict[str, object]:
        return {
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "args_sha256": self.args_sha256,
            "allowed_paths": list(self.allowed_paths),
            "scope": self.scope,
            "step_index": self.step_index,
            "idempotency_key": self.idempotency_key,
            "actor": self.actor,
            "decision": self.decision,
            "issued_at_utc": self.issued_at_utc,
            "expires_at_utc": self.expires_at_utc,
            "decided_at_utc": self.decided_at_utc,
            "identity_evidence": self.identity_evidence,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ApprovalGrant":
        _exact_keys(value, set(cls.__dataclass_fields__), "approval grant")
        raw_paths = value["allowed_paths"]
        if not isinstance(raw_paths, (list, tuple)):
            raise ValueError("allowed_paths must be a list or tuple")
        return cls(
            approval_id=_nonempty_value(value["approval_id"], "approval_id"),
            run_id=_nonempty_value(value["run_id"], "run_id"),
            tool_name=_nonempty_value(value["tool_name"], "tool_name"),
            tool_version=_nonempty_value(value["tool_version"], "tool_version"),
            args_sha256=_nonempty_value(value["args_sha256"], "args_sha256"),
            allowed_paths=tuple(raw_paths),
            scope=_nonempty_value(value["scope"], "scope"),
            step_index=_int(value["step_index"], "step_index"),
            idempotency_key=_nonempty_value(value["idempotency_key"], "idempotency_key"),
            actor=_nonempty_value(value["actor"], "actor"),
            decision=value["decision"],
            issued_at_utc=_nonempty_value(value["issued_at_utc"], "issued_at_utc"),
            expires_at_utc=_nonempty_value(value["expires_at_utc"], "expires_at_utc"),
            decided_at_utc=_nonempty_value(value["decided_at_utc"], "decided_at_utc"),
            identity_evidence=_nonempty_value(
                value["identity_evidence"], "identity_evidence"
            ),
            signature=_nonempty_value(value["signature"], "signature"),
        )


def _approval_request_signature(request: ApprovalRequest) -> str:
    return "request-signature:" + sha256_json(
        {
            **request.binding_dict(),
            "requester_identity": request.requester_identity,
            "identity_evidence": request.identity_evidence,
        }
    )


def _approval_grant_signature(grant: ApprovalGrant) -> str:
    return "grant-signature:" + sha256_json(
        {
            "approval_id": grant.approval_id,
            "run_id": grant.run_id,
            "tool_name": grant.tool_name,
            "tool_version": grant.tool_version,
            "args_sha256": grant.args_sha256,
            "allowed_paths": list(grant.allowed_paths),
            "scope": grant.scope,
            "step_index": grant.step_index,
            "idempotency_key": grant.idempotency_key,
            "actor": grant.actor,
            "decision": grant.decision,
            "issued_at_utc": grant.issued_at_utc,
            "expires_at_utc": grant.expires_at_utc,
            "decided_at_utc": grant.decided_at_utc,
            "identity_evidence": grant.identity_evidence,
        }
    )


@dataclass(frozen=True)
class RunBudget:
    """Deterministic upper bounds for one run."""

    max_steps: int
    max_tool_calls: int
    max_retries: int

    def __post_init__(self) -> None:
        if _int(self.max_steps, "max_steps") <= 0:
            raise ValueError("max_steps must be greater than zero")
        if _int(self.max_tool_calls, "max_tool_calls") <= 0:
            raise ValueError("max_tool_calls must be greater than zero")
        if _int(self.max_retries, "max_retries") < 0:
            raise ValueError("max_retries must not be negative")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunBudget":
        _exact_keys(value, {"max_steps", "max_tool_calls", "max_retries"}, "budget")
        return cls(
            max_steps=_int(value["max_steps"], "budget.max_steps"),
            max_tool_calls=_int(value["max_tool_calls"], "budget.max_tool_calls"),
            max_retries=_int(value["max_retries"], "budget.max_retries"),
        )


@dataclass(frozen=True)
class AgentRunState:
    """Materialized state from an append-only run event stream."""

    run_id: str
    schema_version: int
    task_kind: str
    project_root: Path
    status: AgentRunStatus
    step_index: int
    plan_digest: str | None
    checkpoint_event_id: str | None
    budget: RunBudget
    pending_approval_id: str | None
    pending_approval_scope: str | None = None
    pending_approval_request_digest: str | None = None
    approved_grant_digest: str | None = None
    pending_tool_name: str | None = None
    pending_tool_version: str | None = None
    pending_tool_args_sha256: str | None = None
    pending_tool_allowed_paths: tuple[str, ...] = field(default_factory=tuple)
    memory_refs: tuple[str, ...] = field(default_factory=tuple)
    decision_digest: str | None = None
    pending_tool_idempotency_key: str | None = None
    tool_started: bool = False
    tool_call_count: int = 0
    retry_count: int = 0
    last_event_sequence: int = -1
    lifecycle_phase: RunLifecyclePhase = "CREATED"
    evaluation_outcome: EvaluationOutcome | None = None

    def __post_init__(self) -> None:
        _nonempty(self.run_id, "run_id")
        if _int(self.schema_version, "schema_version") != RUNTIME_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        _nonempty(self.task_kind, "task_kind")
        if not isinstance(self.project_root, Path) or not self.project_root.is_absolute():
            raise ValueError("project_root must be absolute")
        if self.status not in _RUN_STATUSES:
            raise ValueError(f"unknown status: {self.status}")
        if self.lifecycle_phase not in _LIFECYCLE_PHASES:
            raise ValueError(f"unknown lifecycle_phase: {self.lifecycle_phase}")
        if (
            self.evaluation_outcome is not None
            and self.evaluation_outcome not in _EVALUATION_OUTCOMES
        ):
            raise ValueError(
                f"unknown evaluation_outcome: {self.evaluation_outcome}"
            )
        if _int(self.step_index, "step_index") < 0:
            raise ValueError("step_index must not be negative")
        if not isinstance(self.budget, RunBudget):
            raise ValueError("budget must be a RunBudget")
        for value, name in (
            (self.plan_digest, "plan_digest"),
            (self.checkpoint_event_id, "checkpoint_event_id"),
            (self.pending_approval_id, "pending_approval_id"),
            (self.pending_approval_scope, "pending_approval_scope"),
            (
                self.pending_approval_request_digest,
                "pending_approval_request_digest",
            ),
            (self.approved_grant_digest, "approved_grant_digest"),
            (self.pending_tool_name, "pending_tool_name"),
            (self.pending_tool_version, "pending_tool_version"),
            (self.pending_tool_args_sha256, "pending_tool_args_sha256"),
            (self.decision_digest, "decision_digest"),
            (self.pending_tool_idempotency_key, "pending_tool_idempotency_key"),
        ):
            _optional_nonempty(value, name)
        if not isinstance(self.tool_started, bool):
            raise ValueError("tool_started must be boolean")
        if not isinstance(self.memory_refs, (list, tuple)):
            raise ValueError("memory_refs must be a list or tuple")
        object.__setattr__(self, "memory_refs", tuple(self.memory_refs))
        if not all(isinstance(reference, str) and reference for reference in self.memory_refs):
            raise ValueError("memory_refs must contain non-empty strings")
        paths = _canonical_string_set(
            self.pending_tool_allowed_paths,
            "pending_tool_allowed_paths",
        )
        object.__setattr__(self, "pending_tool_allowed_paths", paths)
        if _int(self.tool_call_count, "tool_call_count") < 0:
            raise ValueError("tool_call_count must not be negative")
        if _int(self.retry_count, "retry_count") < 0:
            raise ValueError("retry_count must not be negative")
        _int(self.last_event_sequence, "last_event_sequence")

    @classmethod
    def created(
        cls, *, run_id: str, task_kind: str, project_root: Path, budget: RunBudget
    ) -> "AgentRunState":
        return cls(
            run_id=run_id,
            schema_version=RUNTIME_SCHEMA_VERSION,
            task_kind=task_kind,
            project_root=Path(project_root),
            status="CREATED",
            step_index=0,
            plan_digest=None,
            checkpoint_event_id=None,
            budget=budget,
            pending_approval_id=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "task_kind": self.task_kind,
            "project_root": str(self.project_root),
            "status": self.status,
            "step_index": self.step_index,
            "plan_digest": self.plan_digest,
            "checkpoint_event_id": self.checkpoint_event_id,
            "budget": self.budget.to_dict(),
            "pending_approval_id": self.pending_approval_id,
            "pending_approval_scope": self.pending_approval_scope,
            "pending_approval_request_digest": self.pending_approval_request_digest,
            "approved_grant_digest": self.approved_grant_digest,
            "pending_tool_name": self.pending_tool_name,
            "pending_tool_version": self.pending_tool_version,
            "pending_tool_args_sha256": self.pending_tool_args_sha256,
            "pending_tool_allowed_paths": list(self.pending_tool_allowed_paths),
            "memory_refs": list(self.memory_refs),
            "decision_digest": self.decision_digest,
            "pending_tool_idempotency_key": self.pending_tool_idempotency_key,
            "tool_started": self.tool_started,
            "tool_call_count": self.tool_call_count,
            "retry_count": self.retry_count,
            "last_event_sequence": self.last_event_sequence,
            "lifecycle_phase": self.lifecycle_phase,
            "evaluation_outcome": self.evaluation_outcome,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentRunState":
        _exact_keys(value, set(cls.__dataclass_fields__), "state")
        memory_refs = value["memory_refs"]
        if not isinstance(memory_refs, (list, tuple)):
            raise ValueError("memory_refs must be a list or tuple")
        return cls(
            run_id=_nonempty_value(value["run_id"], "run_id"),
            schema_version=_int(value["schema_version"], "schema_version"),
            task_kind=_nonempty_value(value["task_kind"], "task_kind"),
            project_root=Path(_nonempty_value(value["project_root"], "project_root")),
            status=value["status"],
            step_index=_int(value["step_index"], "step_index"),
            plan_digest=_optional_value(value["plan_digest"], "plan_digest"),
            checkpoint_event_id=_optional_value(value["checkpoint_event_id"], "checkpoint_event_id"),
            budget=RunBudget.from_dict(_mapping(value["budget"], "budget")),
            pending_approval_id=_optional_value(value["pending_approval_id"], "pending_approval_id"),
            pending_approval_scope=_optional_value(value["pending_approval_scope"], "pending_approval_scope"),
            pending_approval_request_digest=_optional_value(
                value["pending_approval_request_digest"],
                "pending_approval_request_digest",
            ),
            approved_grant_digest=_optional_value(
                value["approved_grant_digest"], "approved_grant_digest"
            ),
            pending_tool_name=_optional_value(
                value["pending_tool_name"], "pending_tool_name"
            ),
            pending_tool_version=_optional_value(
                value["pending_tool_version"], "pending_tool_version"
            ),
            pending_tool_args_sha256=_optional_value(
                value["pending_tool_args_sha256"], "pending_tool_args_sha256"
            ),
            pending_tool_allowed_paths=tuple(value["pending_tool_allowed_paths"]),
            memory_refs=tuple(memory_refs),
            decision_digest=_optional_value(value["decision_digest"], "decision_digest"),
            pending_tool_idempotency_key=_optional_value(value["pending_tool_idempotency_key"], "pending_tool_idempotency_key"),
            tool_started=value["tool_started"],
            tool_call_count=_int(value["tool_call_count"], "tool_call_count"),
            retry_count=_int(value["retry_count"], "retry_count"),
            last_event_sequence=_int(value["last_event_sequence"], "last_event_sequence"),
            lifecycle_phase=value["lifecycle_phase"],
            evaluation_outcome=_optional_evaluation_outcome(
                value["evaluation_outcome"]
            ),
        )


@dataclass(frozen=True)
class RunEvent:
    """An immutable, content-addressed record in a run stream."""

    event_id: str
    run_id: str
    sequence: int
    event_kind: str
    payload: Mapping[str, Any]
    payload_sha256: str
    created_at_utc: str

    def __post_init__(self) -> None:
        _nonempty(self.event_id, "event_id")
        _nonempty(self.run_id, "run_id")
        if _int(self.sequence, "sequence") < 0:
            raise ValueError("sequence must not be negative")
        _nonempty(self.event_kind, "event_kind")
        _canonical_utc(self.created_at_utc)
        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be an object")
        object.__setattr__(self, "payload", _freeze(copy.deepcopy(dict(self.payload))))
        expected_digest = run_event_digest(
            event_id=self.event_id,
            run_id=self.run_id,
            sequence=self.sequence,
            event_kind=self.event_kind,
            payload=self.payload,
            created_at_utc=self.created_at_utc,
        )
        if self.payload_sha256 != expected_digest:
            raise ValueError("payload_sha256 does not match event envelope")

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        run_id: str,
        sequence: int,
        event_kind: str,
        payload: Mapping[str, Any],
        created_at_utc: str,
    ) -> "RunEvent":
        copied_payload = copy.deepcopy(dict(payload))
        digest = run_event_digest(
            event_id=event_id,
            run_id=run_id,
            sequence=sequence,
            event_kind=event_kind,
            payload=copied_payload,
            created_at_utc=created_at_utc,
        )
        return cls(
            event_id,
            run_id,
            sequence,
            event_kind,
            copied_payload,
            digest,
            created_at_utc,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_kind": self.event_kind,
            "payload": _thaw(self.payload),
            "payload_sha256": self.payload_sha256,
            "created_at_utc": self.created_at_utc,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunEvent":
        _exact_keys(value, {"event_id", "run_id", "sequence", "event_kind", "payload", "payload_sha256", "created_at_utc"}, "event")
        return cls(
            event_id=_nonempty_value(value["event_id"], "event_id"),
            run_id=_nonempty_value(value["run_id"], "run_id"),
            sequence=_int(value["sequence"], "sequence"),
            event_kind=_nonempty_value(value["event_kind"], "event_kind"),
            payload=copy.deepcopy(dict(_mapping(value["payload"], "payload"))),
            payload_sha256=_nonempty_value(value["payload_sha256"], "payload_sha256"),
            created_at_utc=_nonempty_value(value["created_at_utc"], "created_at_utc"),
        )


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _canonical_utc(value: object) -> str:
    timestamp = _nonempty_value(value, "created_at_utc")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at_utc must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("created_at_utc must be canonical UTC")
    canonical = parsed.isoformat()
    if timestamp not in {canonical, canonical.removesuffix("+00:00") + "Z"}:
        raise ValueError("created_at_utc must be canonical UTC")
    return timestamp


def _utc_timestamp(value: object, name: str) -> datetime:
    timestamp = _nonempty_value(value, name)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be canonical UTC")
    canonical = parsed.isoformat()
    if timestamp not in {canonical, canonical.removesuffix("+00:00") + "Z"}:
        raise ValueError(f"{name} must be canonical UTC")
    return parsed


def _canonical_string_set(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list or tuple")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{name} must contain non-empty strings")
    return tuple(sorted(set(value)))


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        details = []
        if missing := expected - actual:
            details.append(f"missing={sorted(missing)}")
        if unknown := actual - expected:
            details.append(f"unknown={sorted(unknown)}")
        raise ValueError(f"invalid {name} fields ({', '.join(details)})")


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _nonempty_value(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_nonempty(value: object, name: str) -> None:
    if value is not None:
        _nonempty(value, name)


def _optional_value(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _nonempty_value(value, name)


def _optional_evaluation_outcome(value: object) -> EvaluationOutcome | None:
    if value is None:
        return None
    if value not in _EVALUATION_OUTCOMES:
        raise ValueError("evaluation_outcome is invalid")
    return cast(EvaluationOutcome, value)


def _int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value
