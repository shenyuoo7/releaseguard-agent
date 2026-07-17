import json
import os
from pathlib import Path

import pytest

import releaseguard_agent.services.run_history_service as history_module
from releaseguard_agent.services.run_history_service import (
    HistoryOperationError,
    HistoryRemovalScope,
    RunHistoryService,
)


class SimpleRunStore:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def run_directory(self, run_id: str) -> Path:
        if not run_id or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
            for character in run_id
        ):
            raise ValueError("无效 run_id")
        return self.output_root / run_id


def _write_run(
    root: Path,
    run_id: str,
    *,
    project_name: str = "中文 项目",
    reviewed_at: str = "2026-07-17T10:00:00+00:00",
    mode: str = "basic",
) -> Path:
    directory = root / run_id
    directory.mkdir(parents=True)
    payload = {
        "run_id": run_id,
        "project": {"name": project_name, "path": f"E:/项目/{project_name}"},
        "reviewed_at": reviewed_at,
        "mode": mode,
        "mode_label": "基础扫描" if mode == "basic" else "AI 智能审查",
        "decision": {"state": "warning", "label": "可以发布，但建议先修复"},
        "summary": {"blocking": 0, "warning": 2, "passed": 5},
        "ai": {"ai_invoked": mode == "ai", "provider": "DeepSeek", "model": "chat"},
    }
    (directory / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "release_report.md").write_text("report", encoding="utf-8")
    (directory / "fix_plan.md").write_text("fix", encoding="utf-8")
    (directory / "trace.json").write_text("do-not-load", encoding="utf-8")
    return directory


def _service(tmp_path: Path, *, active=None) -> RunHistoryService:  # type: ignore[no-untyped-def]
    root = tmp_path / "outputs" / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return RunHistoryService(
        SimpleRunStore(root),
        hidden_state_path=tmp_path / ".runtime" / "history_hidden.json",
        is_run_active=active,
    )


def test_history_is_reverse_chronological_paginated_and_searchable(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for index in range(25):
        _write_run(
            service.store.output_root,
            f"rg-20260717-1000{index:02d}-abcdef{index:02d}",
            project_name=f"项目 {index}",
            reviewed_at=f"2026-07-17T10:{index:02d}:00+00:00",
            mode="ai" if index % 2 else "basic",
        )

    first = service.list_runs()
    second = service.list_runs(page=2)
    searched = service.list_runs(search="项目 7", visibility="all")
    ai_only = service.list_runs(mode="ai")

    assert first.total == 25
    assert len(first.items) == 20
    assert first.items[0].project_name == "项目 24"
    assert len(second.items) == 5
    assert [item.project_name for item in searched.items] == ["项目 7"]
    assert all(item.mode == "ai" for item in ai_only.items)


def test_summary_does_not_read_report_or_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    _write_run(service.store.output_root, "rg-20260717-100000-abcdef12")
    original = Path.read_text

    def guarded_read(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        assert path.name not in {"release_report.md", "fix_plan.md", "trace.json"}
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    assert service.list_runs().items[0].project_name == "中文 项目"


def test_damaged_result_is_listed_without_breaking_page(tmp_path: Path) -> None:
    service = _service(tmp_path)
    valid = _write_run(service.store.output_root, "rg-20260717-100000-abcdef12")
    damaged = service.store.output_root / "rg-20260717-100001-abcdef13"
    damaged.mkdir()
    (damaged / "result.json").write_text("{broken", encoding="utf-8")

    page = service.list_runs(visibility="all")

    assert len(page.items) == 2
    assert any(item.damaged for item in page.items)
    assert valid.is_dir()


def test_hide_restore_are_persistent_and_keep_files(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "rg-20260717-100000-abcdef12"
    directory = _write_run(service.store.output_root, run_id)

    service.remove(run_id, HistoryRemovalScope.LIST_ONLY)

    assert directory.is_dir()
    assert service.list_runs().total == 0
    assert service.list_runs(visibility="hidden").items[0].hidden is True
    restarted = _service(tmp_path)
    assert restarted.list_runs(visibility="hidden").total == 1

    restarted.restore(run_id)
    assert restarted.list_runs().total == 1
    state = json.loads((tmp_path / ".runtime" / "history_hidden.json").read_text(encoding="utf-8"))
    assert state == {"hidden_run_ids": []}


def test_complete_removal_deletes_only_requested_run(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _write_run(service.store.output_root, "rg-20260717-100000-abcdef12")
    second = _write_run(service.store.output_root, "rg-20260717-100001-abcdef13")

    service.remove(first.name, HistoryRemovalScope.LIST_AND_FILES)

    assert not first.exists()
    assert second.is_dir()
    assert service.store.output_root.is_dir()


@pytest.mark.parametrize("run_id", ["..", "../runs", "rg-../../root", ""])
def test_removal_rejects_traversal_and_root(run_id: str, tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(HistoryOperationError):
        service.remove(run_id, HistoryRemovalScope.LIST_AND_FILES)
    assert service.store.output_root.is_dir()


def test_removal_rejects_active_run(tmp_path: Path) -> None:
    run_id = "rg-20260717-100000-abcdef12"
    service = _service(tmp_path, active=lambda value: value == run_id)
    directory = _write_run(service.store.output_root, run_id)

    with pytest.raises(HistoryOperationError, match="仍在执行"):
        service.remove(run_id, HistoryRemovalScope.LIST_AND_FILES)
    assert directory.is_dir()


def test_removal_rejects_reparse_guard_without_touching_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    directory = _write_run(service.store.output_root, "rg-20260717-100000-abcdef12")
    monkeypatch.setattr(history_module, "_contains_link_or_reparse_point", lambda _path: True)

    with pytest.raises(HistoryOperationError, match="符号链接|重解析点"):
        service.remove(directory.name, HistoryRemovalScope.LIST_AND_FILES)
    assert directory.is_dir()


def test_removal_failure_is_reported_and_not_disguised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    directory = _write_run(service.store.output_root, "rg-20260717-100000-abcdef12")

    def fail_remove(_path: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(history_module.shutil, "rmtree", fail_remove)
    with pytest.raises(HistoryOperationError, match="未能完整删除"):
        service.remove(directory.name, HistoryRemovalScope.LIST_AND_FILES)
    assert directory.is_dir()


def test_hidden_state_atomic_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "rg-20260717-100000-abcdef12"
    _write_run(service.store.output_root, run_id)
    service.hide(run_id)
    runtime = tmp_path / ".runtime"
    assert [item.name for item in runtime.iterdir()] == ["history_hidden.json"]
    assert os.path.getsize(runtime / "history_hidden.json") > 0
