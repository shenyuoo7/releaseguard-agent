from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, SecretStr

from releaseguard_agent.services.local_ai_settings import (
    AiSettingsError,
    LocalAiSettingsService,
    PROVIDER_PRESETS,
    ProviderSettings,
)
from releaseguard_agent.services.local_project_picker import (
    LocalProjectError,
    WindowsFolderPicker,
    inspect_local_project,
)
from releaseguard_agent.services.local_run_service import (
    LocalReviewRunService,
    LocalRunError,
)
from releaseguard_agent.services.run_history_service import (
    HistoryOperationError,
    HistoryRemovalScope,
    RunHistoryService,
)
from releaseguard_agent.services.storage_audit_service import StorageAuditService


API_DIRECTORY = Path(__file__).resolve().parent


class AiSettingsPayload(BaseModel):
    provider: str
    base_url: str
    model: str
    api_key: SecretStr = Field(default=SecretStr(""))
    remember_device: bool = False
    timeout_seconds: float = 60.0

    def settings(self) -> ProviderSettings:
        return ProviderSettings(
            provider=self.provider,
            base_url=self.base_url,
            model=self.model,
            timeout_seconds=self.timeout_seconds,
            remember_device=self.remember_device,
        )


class ProjectPathPayload(BaseModel):
    project_path: str


class StartRunPayload(BaseModel):
    project_path: str
    mode: Literal["basic", "ai"]


class DemoRunPayload(BaseModel):
    mode: Literal["basic", "ai"]


class HistoryRemovalPayload(BaseModel):
    scope: HistoryRemovalScope


@dataclass
class LocalWebDependencies:
    releaseguard_root: Path
    ai_settings: LocalAiSettingsService
    runs: LocalReviewRunService
    folder_picker: WindowsFolderPicker
    history: RunHistoryService | None = None
    storage_audit: StorageAuditService | None = None
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))


def build_local_web_dependencies(releaseguard_root: Path) -> LocalWebDependencies:
    root = Path(releaseguard_root).resolve()
    ai_settings = LocalAiSettingsService(root / ".runtime")
    runs = LocalReviewRunService(
        releaseguard_root=root,
        ai_settings=ai_settings,
        output_root=root / "outputs" / "runs",
    )
    return LocalWebDependencies(
        releaseguard_root=root,
        ai_settings=ai_settings,
        runs=runs,
        folder_picker=WindowsFolderPicker(),
        history=RunHistoryService(
            runs.store,
            hidden_state_path=root / ".runtime" / "history_hidden.json",
            is_run_active=runs.is_active,
        ),
        storage_audit=StorageAuditService(root),
    )


def build_ui_router(
    dependencies: LocalWebDependencies,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()
    history = dependencies.history or RunHistoryService(
        dependencies.runs.store,
        hidden_state_path=dependencies.runs.store.output_root.parents[1]
        / ".runtime"
        / "history_hidden.json",
        is_run_active=dependencies.runs.is_active,
    )
    storage_audit = dependencies.storage_audit or StorageAuditService(
        dependencies.releaseguard_root
    )

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "ai_status": dependencies.ai_settings.public_status(),
                "latest_run_id": dependencies.runs.store.latest_run_id(),
                "history_available": history.total_size()[0] > 0,
            },
        )

    @router.get(
        "/settings/ai", response_class=HTMLResponse, include_in_schema=False
    )
    def ai_settings_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="ai_settings.html",
            context={
                "ai_status": dependencies.ai_settings.public_status(),
                "presets": PROVIDER_PRESETS,
            },
        )

    @router.get("/api/settings/ai/status", include_in_schema=False)
    def ai_status() -> dict[str, object]:
        return dependencies.ai_settings.public_status()

    @router.post("/api/settings/ai/test", include_in_schema=False)
    def test_ai_connection(payload: AiSettingsPayload) -> dict[str, object]:
        try:
            result = dependencies.ai_settings.test_connection(
                payload.settings(), payload.api_key.get_secret_value()
            )
        except AiSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.to_dict()

    @router.post("/api/settings/ai/save", include_in_schema=False)
    def save_ai_settings(payload: AiSettingsPayload) -> dict[str, object]:
        try:
            return dependencies.ai_settings.save(
                payload.settings(), payload.api_key.get_secret_value()
            )
        except AiSettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/local/select-folder", include_in_schema=False)
    def select_folder() -> dict[str, object]:
        try:
            selected = dependencies.folder_picker.choose()
            if selected is None:
                return {"cancelled": True}
            info = inspect_local_project(
                selected,
                releaseguard_root=dependencies.releaseguard_root,
            )
        except LocalProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"cancelled": False, "project": info.to_dict()}

    @router.post("/api/local/project-info", include_in_schema=False)
    def project_info(payload: ProjectPathPayload) -> dict[str, object]:
        try:
            info = inspect_local_project(
                payload.project_path,
                releaseguard_root=dependencies.releaseguard_root,
            )
        except LocalProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return info.to_dict()

    @router.post("/api/runs", include_in_schema=False)
    def start_run(payload: StartRunPayload) -> dict[str, object]:
        try:
            record = dependencies.runs.start(payload.project_path, payload.mode)
        except (LocalProjectError, LocalRunError, AiSettingsError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "run_id": record.run_id,
            "progress_url": f"/runs/{record.run_id}",
        }

    @router.post("/api/runs/demo", include_in_schema=False)
    def start_demo(payload: DemoRunPayload) -> dict[str, object]:
        sample = (
            dependencies.releaseguard_root
            / "sample_projects"
            / "fastapi_bad_project"
        )
        try:
            record = dependencies.runs.start(str(sample), payload.mode)
        except (LocalProjectError, LocalRunError, AiSettingsError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "run_id": record.run_id,
            "progress_url": f"/runs/{record.run_id}",
        }

    @router.get("/api/runs/{run_id}/status", include_in_schema=False)
    def run_status(run_id: str) -> dict[str, object]:
        record = dependencies.runs.get_record(run_id)
        if record is not None:
            return record.public_status()
        result = dependencies.runs.result(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="审查任务不存在。")
        return {
            "run_id": run_id,
            "status": "completed",
            "result_url": f"/runs/{run_id}",
            "elapsed_seconds": result.get("duration_seconds", 0),
        }

    @router.get("/runs/latest", include_in_schema=False)
    def latest_run() -> RedirectResponse:
        run_id = dependencies.runs.store.latest_run_id()
        if run_id is None:
            return RedirectResponse(url="/?message=no_previous_run", status_code=303)
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @router.get("/history", response_class=HTMLResponse, include_in_schema=False)
    def history_page(
        request: Request,
        page: int = 1,
        search: str = "",
        mode: str = "all",
        decision: str = "all",
        visibility: Literal["visible", "hidden", "all"] = "visible",
    ) -> HTMLResponse:
        result = history.list_runs(
            page=page,
            search=search,
            mode=mode,
            decision=decision,
            visibility=visibility,
        )
        run_count, history_size = history.total_size()
        audit_totals = storage_audit.totals()
        return templates.TemplateResponse(
            request=request,
            name="history.html",
            context={
                "history_page": result,
                "filters": {
                    "search": search,
                    "mode": mode,
                    "decision": decision,
                    "visibility": visibility,
                },
                "csrf_token": dependencies.csrf_token,
                "storage": {
                    "run_count": run_count,
                    "history_size_label": _format_size(history_size),
                    **audit_totals,
                },
            },
        )

    @router.get("/api/history", include_in_schema=False)
    def history_api(
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        mode: str = "all",
        decision: str = "all",
        visibility: Literal["visible", "hidden", "all"] = "visible",
    ) -> dict[str, object]:
        result = history.list_runs(
            page=page,
            page_size=page_size,
            search=search,
            mode=mode,
            decision=decision,
            visibility=visibility,
        )
        return {
            "items": [item.to_dict() for item in result.items],
            "page": result.page,
            "page_size": result.page_size,
            "total": result.total,
            "pages": result.pages,
        }

    @router.post("/api/history/{run_id}/remove", include_in_schema=False)
    async def remove_history(
        request: Request,
        run_id: str,
    ) -> dict[str, object]:
        _require_protected_json_request(request, dependencies.csrf_token)
        try:
            payload = HistoryRemovalPayload.model_validate(await request.json())
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="删除范围无效。") from exc
        try:
            history.remove(run_id, payload.scope)
        except HistoryOperationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "scope": payload.scope.value}

    @router.post("/api/history/{run_id}/restore", include_in_schema=False)
    def restore_history(request: Request, run_id: str) -> dict[str, bool]:
        _require_protected_json_request(request, dependencies.csrf_token)
        try:
            history.restore(run_id)
        except HistoryOperationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @router.get("/storage", response_class=HTMLResponse, include_in_schema=False)
    def storage_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="storage.html",
            context={"entries": storage_audit.scan()},
        )

    @router.get(
        "/runs/{run_id}", response_class=HTMLResponse, include_in_schema=False
    )
    def run_page(request: Request, run_id: str) -> HTMLResponse:
        record = dependencies.runs.get_record(run_id)
        result = dependencies.runs.result_for_page(run_id)
        if record is None and result is None:
            raise HTTPException(status_code=404, detail="审查任务不存在。")
        return templates.TemplateResponse(
            request=request,
            name="run.html",
            context={
                "run_id": run_id,
                "record": record.public_status() if record else None,
                "result": result,
            },
        )

    @router.get(
        "/runs/{run_id}/download/{artifact}", include_in_schema=False
    )
    def download_artifact(run_id: str, artifact: str) -> FileResponse:
        names = {
            "markdown": "release_report.md",
            "json": "result.json",
            "fix-plan": "fix_plan.md",
        }
        filename = names.get(artifact)
        if filename is None:
            raise HTTPException(status_code=404, detail="下载类型不存在。")
        try:
            path = dependencies.runs.store.artifact_path(run_id, filename)
        except LocalRunError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="结果文件不存在。")
        summary = history.summary(run_id)
        suffix = path.suffix
        safe_project = _safe_download_part(summary.project_name)
        safe_time = re.sub(r"[^0-9]", "", summary.reviewed_at or "")[:14]
        artifact_label = {
            "markdown": "report",
            "json": "result",
            "fix-plan": "fix_plan",
        }[artifact]
        download_name = (
            f"ReleaseGuard_{safe_project}_{safe_time}_{run_id}_{artifact_label}{suffix}"
        )
        return FileResponse(path, filename=download_name)

    @router.get("/api/runs/{run_id}/trace", include_in_schema=False)
    def run_trace(run_id: str) -> dict[str, object]:
        trace = dependencies.runs.store.load_trace(run_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="执行轨迹不存在。")
        return trace

    @router.post(
        "/api/runs/{run_id}/open-directory", include_in_schema=False
    )
    def open_directory(run_id: str) -> dict[str, bool]:
        try:
            dependencies.runs.open_result_directory(run_id)
        except LocalRunError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"opened": True}

    return router


def template_directory() -> Path:
    return API_DIRECTORY / "templates"


def static_directory() -> Path:
    return API_DIRECTORY / "static"


def _require_protected_json_request(request: Request, csrf_token: str) -> None:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="该操作只接受 JSON 请求。")
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(status_code=403, detail="缺少同源请求信息。")
    source = urlsplit(origin)
    destination = urlsplit(str(request.base_url))
    if (source.scheme, source.netloc) != (destination.scheme, destination.netloc):
        raise HTTPException(status_code=403, detail="已拒绝非同源请求。")
    if not secrets.compare_digest(
        request.headers.get("x-releaseguard-csrf", ""), csrf_token
    ):
        raise HTTPException(status_code=403, detail="安全令牌无效，请刷新页面后重试。")


def _safe_download_part(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" ._")
    return safe[:80] or "项目"


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"
