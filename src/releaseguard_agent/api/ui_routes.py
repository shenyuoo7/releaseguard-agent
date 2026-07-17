from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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


@dataclass
class LocalWebDependencies:
    releaseguard_root: Path
    ai_settings: LocalAiSettingsService
    runs: LocalReviewRunService
    folder_picker: WindowsFolderPicker


def build_local_web_dependencies(releaseguard_root: Path) -> LocalWebDependencies:
    root = Path(releaseguard_root).resolve()
    ai_settings = LocalAiSettingsService(root / ".runtime")
    return LocalWebDependencies(
        releaseguard_root=root,
        ai_settings=ai_settings,
        runs=LocalReviewRunService(
            releaseguard_root=root,
            ai_settings=ai_settings,
            output_root=root / "outputs" / "runs",
        ),
        folder_picker=WindowsFolderPicker(),
    )


def build_ui_router(
    dependencies: LocalWebDependencies,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "ai_status": dependencies.ai_settings.public_status(),
                "latest_run_id": dependencies.runs.store.latest_run_id(),
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

    @router.get(
        "/runs/{run_id}", response_class=HTMLResponse, include_in_schema=False
    )
    def run_page(request: Request, run_id: str) -> HTMLResponse:
        record = dependencies.runs.get_record(run_id)
        result = dependencies.runs.result(run_id)
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
        }
        filename = names.get(artifact)
        if filename is None:
            raise HTTPException(status_code=404, detail="下载类型不存在。")
        path = dependencies.runs.store.run_directory(run_id) / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="结果文件不存在。")
        return FileResponse(path, filename=filename)

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
