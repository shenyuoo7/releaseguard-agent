import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from releaseguard_agent.api.app import PROJECT_ROOT, create_app
from releaseguard_agent.api.ui_routes import LocalWebDependencies
from releaseguard_agent.llm import LLMResponse
from releaseguard_agent.services.local_ai_settings import LocalAiSettingsService
from releaseguard_agent.services.local_run_service import LocalReviewRunService


SAMPLES = PROJECT_ROOT / "sample_projects"


class MemorySecretStore:
    def __init__(self) -> None:
        self.value: str | None = None

    def load(self) -> str | None:
        return self.value

    def save(self, secret: str) -> None:
        self.value = secret

    def delete(self) -> None:
        self.value = None


class SmartFakeClient:
    def __init__(self, *, invalid_agent_response: bool = False) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.invalid_agent_response = invalid_agent_response

    def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(tuple(messages))
        if messages[-1].content.startswith("Reply with:"):
            return LLMResponse(
                content="RELEASEGUARD_CONNECTION_OK",
                provider="fake-provider",
                model="fake-model",
            )
        if self.invalid_agent_response:
            return LLMResponse(content="not-json")
        prompt = json.loads(messages[-1].content)
        context = prompt["deterministic_context"]
        evidence = context["retrieval_evidence"]
        decision = context["advice_result"]["workflow_result"]["decision"]
        blockers = decision.get("blocking_rule_ids", [])
        return LLMResponse(
            content=json.dumps(
                {
                    "risk_level": "high" if blockers else "low",
                    "summary": (
                        "真实模型识别到阻断问题，应优先修复。"
                        if blockers
                        else "真实模型确认当前通过基础检查，发布前仍应复核配置。"
                    ),
                    "release_status": decision["status"],
                    "release_allowed": decision["release_allowed"],
                    "prioritized_risks": [],
                    "fix_plan": [
                        {
                            "priority": 1,
                            "title": "修复阻断项",
                            "action": "按确定性检查建议修改。",
                            "rule_ids": blockers[:1],
                            "validation": "重新运行 ReleaseGuard。",
                        }
                    ] if blockers else [],
                    "evidence_rule_ids": blockers,
                    "evidence_ids": [item["evidence_id"] for item in evidence],
                    "unsupported_claims": [],
                    "missing_evidence_notes": [],
                }
            ),
            provider="fake-provider",
            model="fake-model",
        )


class FakeFolderPicker:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def choose(self) -> str | None:
        return self.value


def _dependencies(tmp_path: Path, fake: SmartFakeClient) -> LocalWebDependencies:
    settings = LocalAiSettingsService(
        tmp_path / ".runtime",
        secret_store=MemorySecretStore(),
        client_builder=lambda **kwargs: fake,
    )
    return LocalWebDependencies(
        releaseguard_root=PROJECT_ROOT,
        ai_settings=settings,
        runs=LocalReviewRunService(
            releaseguard_root=PROJECT_ROOT,
            ai_settings=settings,
            output_root=tmp_path / "outputs" / "runs",
        ),
        folder_picker=FakeFolderPicker(str(SAMPLES / "clean_python_project")),  # type: ignore[arg-type]
    )


def _client(tmp_path: Path, fake: SmartFakeClient) -> tuple[TestClient, LocalWebDependencies]:
    dependencies = _dependencies(tmp_path, fake)
    app = create_app(
        allowed_project_roots=[tmp_path, SAMPLES],
        local_web_dependencies=dependencies,
    )
    return TestClient(app), dependencies


def _connect_ai(client: TestClient, key: str = "private-key") -> None:
    response = client.post(
        "/api/settings/ai/test",
        json={
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": key,
            "remember_device": False,
            "timeout_seconds": 60,
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert key not in response.text


def test_home_and_ai_settings_render_as_user_pages(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, SmartFakeClient())

    home = client.get("/")
    settings = client.get("/settings/ai")

    assert home.status_code == 200
    assert "ReleaseGuard AI 发布审查" in home.text
    assert "开始审查" in home.text
    assert settings.status_code == 200
    assert "测试连接" in settings.text
    assert "private-key" not in settings.text


def test_folder_picker_select_and_cancel_are_safe(tmp_path: Path) -> None:
    client, dependencies = _client(tmp_path, SmartFakeClient())

    selected = client.post("/api/local/select-folder", json={})
    assert selected.json()["project"]["name"] == "clean_python_project"

    dependencies.folder_picker = FakeFolderPicker(None)  # type: ignore[assignment]
    cancelled = client.post("/api/local/select-folder", json={})
    assert cancelled.json() == {"cancelled": True}


def test_basic_scan_never_calls_llm_and_renders_result(tmp_path: Path) -> None:
    fake = SmartFakeClient()
    client, dependencies = _client(tmp_path, fake)
    response = client.post(
        "/api/runs",
        json={"project_path": str(SAMPLES / "fastapi_bad_project"), "mode": "basic"},
    )
    run_id = response.json()["run_id"]
    record = dependencies.runs.wait(run_id)

    assert record.status == "completed"
    assert fake.calls == []
    assert record.result is not None
    assert record.result["ai"]["ai_invoked"] is False
    assert (tmp_path / "outputs" / "runs" / run_id / "result.json").is_file()
    page = client.get(f"/runs/{run_id}")
    assert "本次仅运行确定性基础扫描，未调用大模型" in page.text


def test_ai_blocking_and_clean_runs_call_client_and_render_grounded_results(tmp_path: Path) -> None:
    fake = SmartFakeClient()
    client, dependencies = _client(tmp_path, fake)
    _connect_ai(client)

    for sample in ("fastapi_bad_project", "clean_python_project"):
        response = client.post(
            "/api/runs",
            json={"project_path": str(SAMPLES / sample), "mode": "ai"},
        )
        run_id = response.json()["run_id"]
        record = dependencies.runs.wait(run_id)
        assert record.status == "completed"
        assert record.result is not None
        assert record.result["ai"]["ai_invoked"] is True
        assert record.result["evidence"]
        assert "risk_agent" in record.result["route_history"]
        page = client.get(f"/runs/{run_id}")
        assert "真实 AI 已调用并返回结构化结果" in page.text
        assert "DeepSeek" in page.text
        assert "规则证据" in page.text

    assert len(fake.calls) == 3


def test_ai_failure_is_explicit_and_preserves_deterministic_result(tmp_path: Path) -> None:
    fake = SmartFakeClient(invalid_agent_response=True)
    client, dependencies = _client(tmp_path, fake)
    _connect_ai(client)
    response = client.post(
        "/api/runs",
        json={"project_path": str(SAMPLES / "fastapi_bad_project"), "mode": "ai"},
    )
    run_id = response.json()["run_id"]
    record = dependencies.runs.wait(run_id)

    assert record.result is not None
    assert record.result["ai"]["ai_invoked"] is True
    assert record.result["ai"]["fallback_used"] is True
    assert record.result["decision"]["release_allowed"] is False
    page = client.get(f"/runs/{run_id}")
    assert "AI 调用失败，本次已保留基础扫描结果" in page.text
    assert "重新测试连接" in page.text


def test_unconfigured_ai_run_is_rejected_but_basic_remains_available(tmp_path: Path) -> None:
    client, dependencies = _client(tmp_path, SmartFakeClient())

    ai = client.post(
        "/api/runs",
        json={"project_path": str(SAMPLES / "clean_python_project"), "mode": "ai"},
    )
    basic = client.post(
        "/api/runs",
        json={"project_path": str(SAMPLES / "clean_python_project"), "mode": "basic"},
    )

    assert ai.status_code == 400
    assert "测试连接" in ai.text
    assert basic.status_code == 200
    dependencies.runs.wait(basic.json()["run_id"])


def test_progress_latest_download_and_html_escaping(tmp_path: Path) -> None:
    project = tmp_path / "项目 & review"
    project.mkdir()
    (project / "app.py").write_text("print('safe')\n", encoding="utf-8")
    client, dependencies = _client(tmp_path, SmartFakeClient())

    response = client.post(
        "/api/runs", json={"project_path": str(project), "mode": "basic"}
    )
    run_id = response.json()["run_id"]
    dependencies.runs.wait(run_id)
    status = client.get(f"/api/runs/{run_id}/status").json()

    assert status["status"] == "completed"
    assert {step["key"] for step in status["steps"]} == {
        "read_project",
        "deterministic",
        "evidence",
        "ai_risk",
        "fix_plan",
        "report",
        "complete",
    }
    page = client.get(f"/runs/{run_id}")
    assert "项目 &amp; review" in page.text
    assert "项目 & review" not in page.text
    latest = client.get("/runs/latest", follow_redirects=False)
    assert latest.headers["location"] == f"/runs/{run_id}"
    markdown = client.get(f"/runs/{run_id}/download/markdown")
    result_json = client.get(f"/runs/{run_id}/download/json")
    assert markdown.status_code == 200
    assert "ReleaseGuard 发布审查报告" in markdown.text
    assert result_json.status_code == 200
    assert result_json.json()["run_id"] == run_id
