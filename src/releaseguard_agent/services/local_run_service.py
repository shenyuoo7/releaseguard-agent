from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from releaseguard_agent.llm import LLMRuntime
from releaseguard_agent.observability import ExecutionTracer
from releaseguard_agent.services.agent_workflow_service import (
    ReleaseAgentWorkflowService,
)
from releaseguard_agent.services.local_ai_settings import (
    AiNotConnectedError,
    LocalAiSettingsService,
)
from releaseguard_agent.services.local_project_picker import (
    LocalProjectInfo,
    inspect_local_project,
)
from releaseguard_agent.services.release_review_service import (
    ReleaseReviewResult,
    ReleaseReviewService,
)


RunMode = Literal["basic", "ai"]
RunStatus = Literal["queued", "running", "completed", "failed"]

STEP_DEFINITIONS = (
    ("read_project", "正在读取项目"),
    ("deterministic", "正在执行确定性检查"),
    ("evidence", "正在检索规则证据"),
    ("ai_risk", "正在调用 AI 风险分析"),
    ("fix_plan", "正在生成修复计划"),
    ("report", "正在整理报告"),
    ("complete", "审查完成"),
)


class LocalRunError(ValueError):
    """Safe error raised by the local Web run service."""


@dataclass
class RunStep:
    key: str
    label: str
    status: str = "pending"

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label, "status": self.status}


@dataclass
class LocalRunRecord:
    run_id: str
    project: LocalProjectInfo
    mode: RunMode
    status: RunStatus = "queued"
    created_at: str = field(default_factory=lambda: _utc_now())
    started_monotonic: float = field(default_factory=time.perf_counter)
    finished_at: str | None = None
    current_step: str = "read_project"
    waiting_for_model: bool = False
    steps: list[RunStep] = field(
        default_factory=lambda: [RunStep(key, label) for key, label in STEP_DEFINITIONS]
    )
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    def public_status(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "mode": self.mode,
            "project": self.project.to_dict(),
            "current_step": self.current_step,
            "waiting_for_model": self.waiting_for_model,
            "elapsed_seconds": round(time.perf_counter() - self.started_monotonic, 1),
            "steps": [step.to_dict() for step in self.steps],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "result_url": f"/runs/{self.run_id}" if self.status == "completed" else None,
        }


class LocalRunStore:
    """Persist Web-facing run artifacts without a database."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = Path(output_root)

    def run_directory(self, run_id: str) -> Path:
        if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in run_id):
            raise LocalRunError("无效的 run_id。")
        return self.output_root / run_id

    def save(self, run_id: str, payload: dict[str, Any]) -> dict[str, str]:
        directory = self.run_directory(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        paths = {
            "result_json": directory / "result.json",
            "release_report": directory / "release_report.md",
            "fix_plan": directory / "fix_plan.md",
            "trace": directory / "trace.json",
        }
        payload["artifacts"] = {name: str(path) for name, path in paths.items()}
        paths["result_json"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        paths["release_report"].write_text(
            _release_report_markdown(payload), encoding="utf-8"
        )
        paths["fix_plan"].write_text(_fix_plan_markdown(payload), encoding="utf-8")
        paths["trace"].write_text(
            json.dumps(payload.get("trace", {}), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {name: str(path) for name, path in paths.items()}

    def load(self, run_id: str) -> dict[str, Any] | None:
        path = self.run_directory(run_id) / "result.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def latest_run_id(self) -> str | None:
        if not self.output_root.exists():
            return None
        candidates = [
            item
            for item in self.output_root.iterdir()
            if item.is_dir() and (item / "result.json").is_file()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item / "result.json").stat().st_mtime).name


class LocalReviewRunService:
    """Run basic or real-model reviews in background threads for the local UI."""

    def __init__(
        self,
        *,
        releaseguard_root: Path,
        ai_settings: LocalAiSettingsService,
        output_root: Path,
        review_service: ReleaseReviewService | None = None,
    ) -> None:
        self.releaseguard_root = Path(releaseguard_root).resolve()
        self.ai_settings = ai_settings
        self.store = LocalRunStore(output_root)
        self.review_service = review_service or ReleaseReviewService()
        self._records: dict[str, LocalRunRecord] = {}
        self._lock = threading.RLock()

    def start(self, project_path: str, mode: RunMode) -> LocalRunRecord:
        if mode not in {"basic", "ai"}:
            raise LocalRunError("审查模式必须是 basic 或 ai。")
        project = inspect_local_project(
            project_path, releaseguard_root=self.releaseguard_root
        )
        if mode == "ai":
            self.ai_settings.require_runtime()
        run_id = "rg-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        record = LocalRunRecord(run_id=run_id, project=project, mode=mode)
        with self._lock:
            self._records[run_id] = record
        thread = threading.Thread(
            target=self._execute,
            args=(record,),
            name=f"releaseguard-{run_id}",
            daemon=True,
        )
        thread.start()
        return record

    def get_record(self, run_id: str) -> LocalRunRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def result(self, run_id: str) -> dict[str, Any] | None:
        record = self.get_record(run_id)
        if record and record.result is not None:
            return record.result
        return self.store.load(run_id)

    def wait(self, run_id: str, timeout: float = 30.0) -> LocalRunRecord:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.get_record(run_id)
            if record is None:
                raise LocalRunError("审查任务不存在。")
            if record.status in {"completed", "failed"}:
                return record
            time.sleep(0.02)
        raise TimeoutError("审查任务等待超时。")

    def open_result_directory(self, run_id: str) -> None:
        directory = self.store.run_directory(run_id)
        if not (directory / "result.json").exists():
            raise LocalRunError("结果目录不存在。")
        if os.name != "nt":
            raise LocalRunError("自动打开结果目录仅支持 Windows。")
        subprocess.Popen(["explorer.exe", str(directory)])

    def _execute(self, record: LocalRunRecord) -> None:
        record.status = "running"
        self._set_step(record, "read_project", "completed")
        self._set_step(record, "deterministic", "running")
        try:
            if record.mode == "basic":
                payload = self._run_basic(record)
            else:
                payload = self._run_ai(record)
            self._set_step(record, "report", "running")
            self.store.save(record.run_id, payload)
            self._set_step(record, "report", "completed")
            self._set_step(record, "complete", "completed")
            record.current_step = "complete"
            record.finished_at = _utc_now()
            record.result = payload
            record.status = "completed"
        except Exception as exc:
            record.waiting_for_model = False
            record.status = "failed"
            record.error_code, record.error_message = _safe_run_error(exc)
            self._mark_current_failed(record)

    def _run_basic(self, record: LocalRunRecord) -> dict[str, Any]:
        tracer = ExecutionTracer(run_id=record.run_id)
        with tracer.span("node", node="scan"):
            review = self.review_service.review(
                project_path=Path(record.project.path),
                include_pytest_execution=False,
            )
        self._set_step(record, "deterministic", "completed")
        self._set_step(record, "evidence", "completed")
        self._set_step(record, "ai_risk", "skipped")
        self._set_step(record, "fix_plan", "skipped")
        return _build_result_payload(
            record,
            review,
            evidence=[item.to_dict() for item in review.retrieval_evidence],
            risk_analysis={
                "analysis_source": "deterministic",
                "summary": "本次仅运行确定性基础扫描，未调用大模型。",
            },
            fix_plan=[],
            route_history=["scan", "deterministic_complete"],
            trace=tracer.to_dict(),
            runtime=None,
            llm_attempted=False,
            llm_failed=False,
            error_type=None,
        )

    def _run_ai(self, record: LocalRunRecord) -> dict[str, Any]:
        runtime = self.ai_settings.require_runtime()

        def on_event(event: dict[str, Any]) -> None:
            node = event.get("node")
            if node == "scan":
                self._set_step(record, "deterministic", "completed")
                self._set_step(record, "evidence", "running")
            elif node == "evidence_agent":
                self._set_step(record, "evidence", "completed")
                self._set_step(record, "ai_risk", "running")
                record.waiting_for_model = True
            elif node == "risk_agent":
                record.waiting_for_model = False
                self._set_step(record, "ai_risk", "completed")
                self._set_step(record, "fix_plan", "running")
            elif node == "fix_planner_agent":
                self._set_step(record, "fix_plan", "completed")

        tracer = ExecutionTracer(run_id=record.run_id, event_callback=on_event)
        workflow = ReleaseAgentWorkflowService(
            review_service=self.review_service,
            llm_runtime=runtime,
        )
        result = workflow.run(
            project_path=Path(record.project.path),
            include_pytest_execution=False,
            retrieval_mode="hybrid",
            force_ai_review=True,
            tracer=tracer,
        )
        self._set_step(record, "deterministic", "completed")
        self._set_step(record, "evidence", "completed")
        self._set_step(record, "ai_risk", "completed")
        self._set_step(record, "fix_plan", "completed")
        record.waiting_for_model = False
        return _build_result_payload(
            record,
            result.review,
            evidence=[item.to_dict() for item in result.state.get("evidence", ())],
            risk_analysis=dict(result.state.get("risk_analysis", {})),
            fix_plan=[dict(item) for item in result.state.get("fix_plan", ())],
            route_history=list(result.state.get("route_history", [])),
            trace=result.trace,
            runtime=runtime,
            llm_attempted=bool(result.state.get("llm_attempted", False)),
            llm_failed=bool(result.state.get("llm_failed", False)),
            error_type=result.state.get("error_type"),
        )

    @staticmethod
    def _set_step(record: LocalRunRecord, key: str, status: str) -> None:
        for step in record.steps:
            if step.key == key:
                step.status = status
                break
        if status == "running":
            record.current_step = key

    @staticmethod
    def _mark_current_failed(record: LocalRunRecord) -> None:
        for step in record.steps:
            if step.key == record.current_step:
                step.status = "failed"
                return


def _build_result_payload(
    record: LocalRunRecord,
    review: ReleaseReviewResult,
    *,
    evidence: list[dict[str, object]],
    risk_analysis: dict[str, Any],
    fix_plan: list[dict[str, Any]],
    route_history: list[str],
    trace: dict[str, Any],
    runtime: LLMRuntime | None,
    llm_attempted: bool,
    llm_failed: bool,
    error_type: str | None,
) -> dict[str, Any]:
    finished = time.perf_counter()
    checks = [item.to_dict() for item in review.check_results]
    for item in evidence:
        item["related_findings"] = [
            str(check["title"])
            for check in checks
            if check.get("rule_id") == item.get("rule_id")
        ]
    for step in fix_plan:
        rule_ids = set(step.get("rule_ids", []))
        related = [item for item in checks if item.get("rule_id") in rule_ids]
        if "suggested_files" not in step:
            step["suggested_files"] = sorted(
                {str(item["file_path"]) for item in related if item.get("file_path")}
            )
    ai_invoked = runtime is not None and llm_attempted
    fallback_used = runtime is not None and (not llm_attempted or llm_failed)
    return {
        "run_id": record.run_id,
        "project": record.project.to_dict(),
        "reviewed_at": _utc_now(),
        "mode": record.mode,
        "mode_label": "AI 智能审查" if record.mode == "ai" else "基础扫描",
        "duration_seconds": round(finished - record.started_monotonic, 2),
        "decision": {
            "release_allowed": review.release_allowed,
            "label": "允许发布" if review.release_allowed else "阻止发布",
            "authority": "deterministic_decision_policy",
        },
        "summary": dict(review.summary),
        "checks": checks,
        "evidence": evidence,
        "risk_analysis": risk_analysis,
        "fix_plan": fix_plan,
        "route_history": route_history,
        "ai": {
            "ai_invoked": ai_invoked,
            "provider": runtime.provider if runtime else None,
            "model": runtime.model if runtime else None,
            "latency_ms": _llm_latency(trace),
            "fallback_used": fallback_used,
            "evidence_count": len(evidence),
            "error_type": error_type,
            "error_message": _ai_run_error_message(error_type),
            "message": (
                "AI 调用失败，本次已保留基础扫描结果。"
                if fallback_used
                else "真实 AI 已调用并返回结构化结果。"
                if ai_invoked
                else "本次仅运行确定性基础扫描，未调用大模型。"
            ),
        },
        "trace": trace,
        "artifacts": {},
    }


def _llm_latency(trace: dict[str, Any]) -> float | None:
    events = trace.get("events", [])
    if not isinstance(events, list):
        return None
    values = [
        event.get("latency_ms")
        for event in events
        if isinstance(event, dict) and event.get("kind") == "llm"
    ]
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    return round(sum(numeric), 1) if numeric else None


def _release_report_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    ai = payload["ai"]
    lines = [
        "# ReleaseGuard 发布审查报告",
        "",
        f"- 项目：{payload['project']['name']}",
        f"- 模式：{payload['mode_label']}",
        f"- 结论：{payload['decision']['label']}",
        f"- 真实 AI：{'是' if ai['ai_invoked'] else '否'}",
        f"- 问题：{summary['failed']} 失败 / {summary['warning']} 警告",
        "",
        "## 风险总结",
        "",
        str(payload["risk_analysis"].get("summary", "暂无总结。")),
        "",
        "## 确定性检查结果",
        "",
    ]
    for check in payload["checks"]:
        lines.append(
            f"- [{check['status']}] {check['title']} — {check['message']}"
        )
    lines.extend(["", "## 优先修复计划", ""])
    if not payload["fix_plan"]:
        lines.append("当前没有需要执行的阻断修复项。")
    for step in payload["fix_plan"]:
        lines.append(
            f"- P{step.get('priority', '-')} {step.get('title', '修复项')}："
            f"{step.get('action', '')}"
        )
    lines.extend(["", "## 规则证据", ""])
    for item in payload["evidence"]:
        lines.append(
            f"- {item.get('evidence_id')} / {item.get('rule_id')}："
            f"{item.get('text', '')}（{item.get('source_url', '')}）"
        )
    return "\n".join(lines) + "\n"


def _fix_plan_markdown(payload: dict[str, Any]) -> str:
    lines = ["# ReleaseGuard 修复计划", ""]
    if not payload["fix_plan"]:
        lines.append("当前没有需要执行的阻断修复项。")
    for step in payload["fix_plan"]:
        lines.extend(
            [
                f"## P{step.get('priority', '-')} {step.get('title', '修复项')}",
                "",
                str(step.get("action", "")),
                "",
                f"验证：{step.get('validation', '重新运行 ReleaseGuard。')}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _safe_run_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, AiNotConnectedError):
        return "ai_not_connected", str(exc)
    if isinstance(exc, (LocalRunError, ValueError)):
        return "invalid_request", str(exc)
    return "review_failed", "审查执行失败，请重新选择项目后再试。"


def _ai_run_error_message(error_type: str | None) -> str | None:
    messages = {
        "authentication_failed": "API Key 无效或权限不足。",
        "model_or_url_not_found": "Base URL 或 Model 不存在。",
        "rate_limited": "Provider 限流、余额不足或配额受限。",
        "timeout": "模型请求超时。",
        "provider_error": "Provider 返回异常。",
        "ReleaseRiskAnalysisParseError": "模型响应不符合结构化结果要求。",
    }
    return messages.get(error_type, "模型调用失败。") if error_type else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
