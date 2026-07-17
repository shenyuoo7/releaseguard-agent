from __future__ import annotations

import json
import os
import re
import shutil
import stat
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Protocol


class RunStore(Protocol):
    output_root: Path

    def run_directory(self, run_id: str) -> Path: ...


RUN_ID_PATTERN = re.compile(r"^rg-[a-z0-9-]{1,120}$")


class HistoryRemovalScope(str, Enum):
    LIST_ONLY = "list_only"
    LIST_AND_FILES = "list_and_files"


class HistoryOperationError(ValueError):
    """A safe, user-facing history operation error."""


@dataclass(frozen=True)
class RunArtifactAvailability:
    markdown: bool
    json: bool
    fix_plan: bool
    trace: bool


@dataclass(frozen=True)
class RunHistorySummary:
    run_id: str
    project_name: str
    project_path: str
    reviewed_at: str | None
    reviewed_at_label: str
    mode: str
    mode_label: str
    ai_invoked: bool
    provider: str | None
    model: str | None
    decision_state: str
    decision_label: str
    blocking: int
    warning: int
    passed: int
    status: str
    size_bytes: int
    size_label: str
    artifacts: RunArtifactAvailability
    hidden: bool
    damaged: bool = False
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["artifacts"] = asdict(self.artifacts)
        return value


@dataclass(frozen=True)
class RunHistoryPage:
    items: tuple[RunHistorySummary, ...]
    page: int
    page_size: int
    total: int
    pages: int


class HiddenHistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> set[str]:
        with self._lock:
            if not self.path.is_file():
                return set()
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return set()
            return {
                item
                for item in value.get("hidden_run_ids", [])
                if isinstance(item, str) and RUN_ID_PATTERN.fullmatch(item)
            } if isinstance(value, dict) else set()

    def set_hidden(self, run_id: str, hidden: bool) -> None:
        with self._lock:
            values = self.load()
            if hidden:
                values.add(run_id)
            else:
                values.discard(run_id)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps(
                    {"hidden_run_ids": sorted(values)},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)


class RunHistoryService:
    """Read lightweight run summaries and perform constrained single-run actions."""

    def __init__(
        self,
        store: RunStore,
        *,
        hidden_state_path: Path,
        is_run_active: Callable[[str], bool] | None = None,
    ) -> None:
        self.store = store
        self.hidden_store = HiddenHistoryStore(hidden_state_path)
        self.is_run_active = is_run_active or (lambda _run_id: False)
        self._operation_lock = threading.RLock()

    def list_runs(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        mode: str = "all",
        decision: str = "all",
        visibility: Literal["visible", "hidden", "all"] = "visible",
    ) -> RunHistoryPage:
        page = max(1, page)
        page_size = min(100, max(1, page_size))
        hidden_ids = self.hidden_store.load()
        summaries = [
            self._summary(directory, directory.name in hidden_ids)
            for directory in self._run_directories()
        ]
        search_key = search.strip().casefold()
        filtered = [
            item
            for item in summaries
            if (not search_key or search_key in item.project_name.casefold())
            and (mode == "all" or item.mode == mode)
            and (decision == "all" or item.decision_state == decision)
            and (
                visibility == "all"
                or (visibility == "hidden" and item.hidden)
                or (visibility == "visible" and not item.hidden)
            )
        ]
        filtered.sort(key=_summary_sort_key, reverse=True)
        total = len(filtered)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return RunHistoryPage(
            items=tuple(filtered[start : start + page_size]),
            page=page,
            page_size=page_size,
            total=total,
            pages=pages,
        )

    def total_size(self) -> tuple[int, int]:
        directories = self._run_directories()
        return len(directories), sum(_directory_size(item)[0] for item in directories)

    def hide(self, run_id: str) -> None:
        self._require_valid_run(run_id)
        self.hidden_store.set_hidden(run_id, True)

    def restore(self, run_id: str) -> None:
        self._require_valid_run(run_id)
        self.hidden_store.set_hidden(run_id, False)

    def remove(self, run_id: str, scope: HistoryRemovalScope) -> None:
        if scope is HistoryRemovalScope.LIST_ONLY:
            self.hide(run_id)
            return
        with self._operation_lock:
            target = self._validated_deletion_target(run_id)
            try:
                shutil.rmtree(target)
            except OSError as exc:
                raise HistoryOperationError(
                    "记录未能完整删除。请关闭正在查看该目录的程序后重试。"
                ) from exc
            self.hidden_store.set_hidden(run_id, False)

    def summary(self, run_id: str) -> RunHistorySummary:
        directory = self._require_valid_run(run_id)
        return self._summary(directory, run_id in self.hidden_store.load())

    def _run_directories(self) -> list[Path]:
        if not self.store.output_root.is_dir():
            return []
        return [
            item
            for item in self.store.output_root.iterdir()
            if item.is_dir() and RUN_ID_PATTERN.fullmatch(item.name)
        ]

    def _require_valid_run(self, run_id: str) -> Path:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise HistoryOperationError("无效的审查记录编号。")
        try:
            directory = self.store.run_directory(run_id)
        except ValueError as exc:
            raise HistoryOperationError(str(exc)) from exc
        if not directory.is_dir():
            raise HistoryOperationError("审查记录不存在。")
        return directory

    def _validated_deletion_target(self, run_id: str) -> Path:
        if self.is_run_active(run_id):
            raise HistoryOperationError("该审查仍在执行，不能删除。")
        target = self._require_valid_run(run_id)
        root = self.store.output_root.resolve()
        if target == self.store.output_root or target.parent.resolve() != root:
            raise HistoryOperationError("删除目标不在审查历史目录内。")
        if _contains_link_or_reparse_point(target):
            raise HistoryOperationError("记录包含符号链接或重解析点，已拒绝删除。")
        if target.resolve().parent != root or target.resolve() == root:
            raise HistoryOperationError("删除目标解析后越过安全边界。")
        return target

    def _summary(self, directory: Path, hidden: bool) -> RunHistorySummary:
        size_bytes, _ = _directory_size(directory)
        artifacts = RunArtifactAvailability(
            markdown=(directory / "release_report.md").is_file(),
            json=(directory / "result.json").is_file(),
            fix_plan=(directory / "fix_plan.md").is_file(),
            trace=(directory / "trace.json").is_file(),
        )
        path = directory / "result.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            project = payload.get("project", {})
            ai = payload.get("ai", {})
            summary = payload.get("summary", {})
            final_decision = payload.get("decision", {})
            if not all(isinstance(item, dict) for item in (project, ai, summary, final_decision)):
                raise ValueError
            reviewed_at = _optional_text(payload.get("reviewed_at"))
            return RunHistorySummary(
                run_id=directory.name,
                project_name=_text(project.get("name"), "未命名项目"),
                project_path=_text(project.get("path"), ""),
                reviewed_at=reviewed_at,
                reviewed_at_label=_format_datetime(reviewed_at, path.stat().st_mtime),
                mode=_text(payload.get("mode"), "unknown"),
                mode_label=_text(payload.get("mode_label"), "未知模式"),
                ai_invoked=bool(ai.get("ai_invoked", False)),
                provider=_optional_text(ai.get("provider")),
                model=_optional_text(ai.get("model")),
                decision_state=_text(final_decision.get("state"), "unknown"),
                decision_label=_text(final_decision.get("label"), "需要人工复核"),
                blocking=_integer(summary.get("blocking")),
                warning=_integer(summary.get("warning")),
                passed=_integer(summary.get("passed")),
                status="completed",
                size_bytes=size_bytes,
                size_label=_format_size(size_bytes),
                artifacts=artifacts,
                hidden=hidden,
            )
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            modified = path.stat().st_mtime if path.exists() else directory.stat().st_mtime
            return RunHistorySummary(
                run_id=directory.name,
                project_name="不完整的审查记录",
                project_path="",
                reviewed_at=None,
                reviewed_at_label=_format_datetime(None, modified),
                mode="unknown",
                mode_label="未知模式",
                ai_invoked=False,
                provider=None,
                model=None,
                decision_state="damaged",
                decision_label="需要人工复核",
                blocking=0,
                warning=0,
                passed=0,
                status="damaged",
                size_bytes=size_bytes,
                size_label=_format_size(size_bytes),
                artifacts=artifacts,
                hidden=hidden,
                damaged=True,
                error_message="该历史记录不完整或已损坏",
            )


def _directory_size(path: Path) -> tuple[int, int]:
    size = 0
    files = 0
    for root, directories, filenames in os.walk(path, followlinks=False):
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for name in filenames:
            file_path = Path(root) / name
            try:
                size += file_path.stat(follow_symlinks=False).st_size
                files += 1
            except OSError:
                continue
    return size, files


def _contains_link_or_reparse_point(path: Path) -> bool:
    pending = [path]
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    while pending:
        current = pending.pop()
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise HistoryOperationError("无法安全检查删除目标。") from exc
        if current.is_symlink() or getattr(metadata, "st_file_attributes", 0) & reparse_flag:
            return True
        if current.is_dir():
            try:
                pending.extend(current.iterdir())
            except OSError as exc:
                raise HistoryOperationError("无法安全检查记录中的文件。") from exc
    return False


def _summary_sort_key(summary: RunHistorySummary) -> tuple[float, str]:
    try:
        timestamp = (
            datetime.fromisoformat(summary.reviewed_at.replace("Z", "+00:00")).timestamp()
            if summary.reviewed_at
            else 0.0
        )
    except ValueError:
        timestamp = 0.0
    return (timestamp, summary.run_id)


def _format_datetime(value: str | None, fallback_timestamp: float) -> str:
    moment: datetime
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.fromtimestamp(fallback_timestamp, timezone.utc)
    except ValueError:
        moment = datetime.fromtimestamp(fallback_timestamp, timezone.utc)
    return moment.astimezone().strftime("%Y年%-m月%-d日 %H:%M:%S") if os.name != "nt" else moment.astimezone().strftime("%Y年%#m月%#d日 %H:%M:%S")


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _text(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: object) -> int:
    return value if isinstance(value, int) else 0
