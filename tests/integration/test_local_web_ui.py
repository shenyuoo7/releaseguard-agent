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
        checks = context["check_results"]
        decision = context["advice_result"]["workflow_result"]["decision"]
        blockers = decision.get("blocking_rule_ids", [])
        actionable = [
            item for item in checks if item["status"] in {"warning", "failed"}
        ]
        passed = [item for item in checks if item["status"] == "passed"]
        evidence_by_rule = {item["rule_id"]: item["evidence_id"] for item in evidence}
        return LLMResponse(
            content=json.dumps(
                {
                    "risk_level": "high" if blockers else "low",
                    "executive_summary": (
                        "确定性检查发现了影响发布的关键问题，当前不宜直接发布。"
                        "这些结论来自实际扫描结果，模型没有改变确定性裁决。\n\n"
                        "建议先按优先级完成修复并执行验证命令，再重新运行 ReleaseGuard。"
                        if actionable
                        else "确定性检查未发现阻断或警告，项目具备当前规则范围内的发布基础。\n\n"
                        "发布前仍应运行完整测试并核对外部服务配置，避免把未扫描内容误认为已经验证。"
                    ),
                    "release_recommendation": (
                        "先完成下列修复并复检，通过后再安排发布。"
                        if blockers
                        else "可以进入发布前最终验证，但仍需确认扫描范围之外的运行环境。"
                    ),
                    "positive_findings": [
                        f"已通过一项确定性发布检查：{item['rule_id']}。"
                        for item in passed[:3]
                    ],
                    "release_status": decision["status"],
                    "release_allowed": decision["release_allowed"],
                    "risk_analysis": [
                        {
                            "title": f"处理 {item['rule_id']} 检查问题",
                            "severity": item["risk_level"],
                            "what_was_found": (
                                f"确定性检查 {item['rule_id']} 报告了需要处理的问题。"
                            ),
                            "why_it_matters": "该问题会降低发布过程的稳定性或可复现性。",
                            "possible_impact": "不同环境可能产生不一致结果，增加发布后回滚风险。",
                            "related_check_ids": [item["check_result_id"]],
                            "evidence_ids": (
                                [evidence_by_rule[item["rule_id"]]]
                                if item["rule_id"] in evidence_by_rule else []
                            ),
                        }
                        for item in actionable
                    ],
                    "prioritized_actions": [
                        {
                            "priority": index,
                            "title": f"修复 {item['rule_id']} 对应问题",
                            "objective": "按确定性检查建议完成对应修复。",
                            "why": "修复后可提升发布流程的一致性和可维护性。",
                            "steps": [
                                "确认当前文件结构和受影响范围。",
                                "按检查建议手动修改具体文件。",
                                "运行验证命令并重新执行 ReleaseGuard。",
                            ],
                            "suggested_files": [],
                            "example": "请结合项目现有结构应用配置。",
                            "verification_command": "python -m pytest -q",
                            "success_criteria": "相关检查变为已通过，且没有新增失败。",
                            "related_check_ids": [item["check_result_id"]],
                            "rule_ids": [item["rule_id"]],
                            "evidence_ids": (
                                [evidence_by_rule[item["rule_id"]]]
                                if item["rule_id"] in evidence_by_rule else []
                            ),
                        }
                        for index, item in enumerate(actionable, start=1)
                    ],
                    "final_verification_steps": [
                        "运行 python -m pytest -q。",
                        "重新执行 ReleaseGuard AI 智能审查并确认问题已解决。",
                    ],
                    "limitations": ["未实际调用目标项目依赖的外部服务。"],
                    "evidence_rule_ids": [item["rule_id"] for item in evidence],
                    "evidence_ids": [item["evidence_id"] for item in evidence],
                    "unsupported_claims": [],
                    "missing_evidence_notes": [],
                },
                ensure_ascii=False,
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
    assert '<html lang="zh-CN">' in home.text


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
        assert "AI 综合分析" in page.text
        assert "优先修复计划" in page.text
        assert "已通过检查（" in page.text
        assert "不适用或已跳过（" in page.text

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


def test_warning_report_filters_evidence_and_builds_specific_file_actions(
    tmp_path: Path,
) -> None:
    project = tmp_path / "中文 项目"
    project.mkdir()
    (project / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (project / ".env.example").write_text("APP_ENV=test\n", encoding="utf-8")
    (project / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n", encoding="utf-8"
    )
    fake = SmartFakeClient()
    client, dependencies = _client(tmp_path, fake)
    _connect_ai(client)

    response = client.post(
        "/api/runs", json={"project_path": str(project), "mode": "ai"}
    )
    run_id = response.json()["run_id"]
    record = dependencies.runs.wait(run_id)

    assert record.result is not None
    result = record.result
    assert result["decision"]["label"] == "可以发布，但建议先修复 2 项问题"
    assert {item["rule_id"] for item in result["evidence"]} == {
        "RG-TEST-001",
        "RG-TEST-006",
    }
    assert all(item["related_findings"] for item in result["evidence"])
    assert all(
        all(path != str(project.resolve()) for path in step["suggested_files"])
        for step in result["fix_plan"]
    )
    assert any(
        str(project / "tests") in step["suggested_files"]
        for step in result["fix_plan"]
    )
    assert any(
        str(project / "pytest.ini") in step["suggested_files"]
        for step in result["fix_plan"]
    )

    page = client.get(f"/runs/{run_id}")
    assert "AI 综合分析" in page.text
    assert "可以发布，但建议先修复 2 项问题" in page.text
    assert '<details class="panel content-section result-details" id="passed-checks">' in page.text
    assert '<details class="panel content-section result-details" id="skipped-checks">' in page.text
    evidence_section = page.text.split('id="evidence"', 1)[1].split(
        "高级执行详情", 1
    )[0]
    assert "RG-FLASK" not in evidence_section
    assert "RG-FASTAPI" not in evidence_section
