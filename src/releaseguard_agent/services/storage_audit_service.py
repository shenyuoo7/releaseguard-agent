from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable


class StorageClassification(str, Enum):
    SOURCE = "SOURCE"
    USER_DATA = "USER_DATA"
    SECRET = "SECRET"
    ACTIVE_RUNTIME = "ACTIVE_RUNTIME"
    REBUILDABLE_CACHE = "REBUILDABLE_CACHE"
    HISTORICAL_TEST_TEMP = "HISTORICAL_TEST_TEMP"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StorageAuditEntry:
    relative_path: str
    classification: StorageClassification
    size_bytes: int
    size_label: str
    file_count: int
    last_modified: str | None
    creator_consumer: str
    may_be_active: bool
    rebuildable: bool
    recommendation: str
    history_impact: str
    performance_impact: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["classification"] = self.classification.value
        return value


TARGETS = (
    (".runtime/dependency_cache", StorageClassification.REBUILDABLE_CACHE, "历史依赖安装缓存；由 pip 或里程碑依赖验证创建。"),
    (".runtime/pip-cache", StorageClassification.REBUILDABLE_CACHE, "当前启动脚本为 pip 指定的仓库内缓存位置。"),
    (".runtime/pytest", StorageClassification.HISTORICAL_TEST_TEMP, "当前规范下 pytest 唯一临时根目录；运行测试时可能在用。"),
    (".runtime/pytest-temp", StorageClassification.HISTORICAL_TEST_TEMP, "历史 Web/报告测试临时目录。"),
    (".runtime/m0_pytest_temp", StorageClassification.HISTORICAL_TEST_TEMP, "M0 阶段历史 pytest 临时目录。"),
    (".runtime/milestone_temp", StorageClassification.HISTORICAL_TEST_TEMP, "M1-M8 阶段历史测试和演示临时目录。"),
    (".runtime/temp", StorageClassification.ACTIVE_RUNTIME, "启动器设置的 TEMP/TMP 位置；服务或子进程运行时可能在用。"),
    (".runtime/tests", StorageClassification.HISTORICAL_TEST_TEMP, "历史测试夹具与运行副本。"),
    (".runtime/validation", StorageClassification.HISTORICAL_TEST_TEMP, "历史本地验收临时数据。"),
    (".runtime/dpapi-smoke", StorageClassification.HISTORICAL_TEST_TEMP, "Windows 凭据存储安全冒烟测试目录，不是有效凭据库。"),
    (".runtime/legacy_runtime", StorageClassification.HISTORICAL_TEST_TEMP, "已核对为旧启动器验收项目、pytest、pip 和临时输出的原样迁入副本。"),
    (".runtime/secrets", StorageClassification.SECRET, "本机安全凭据存储；只统计元数据，绝不读取内容。"),
    (".runtime/provider.json", StorageClassification.ACTIVE_RUNTIME, "当前 Provider 非敏感配置；可能被本地 Web 服务读取，禁止当缓存清理。"),
    ("outputs/runs", StorageClassification.USER_DATA, "Web 审查历史及报告的主要事实来源。"),
    ("outputs/latest_review", StorageClassification.USER_DATA, "CLI/兼容入口最近一次审查产物。"),
    ("outputs/latest_verification", StorageClassification.USER_DATA, "CLI/兼容入口最近一次复检产物。"),
)


class StorageAuditService:
    """Metadata-only audit; this service deliberately has no delete operation."""

    def __init__(
        self,
        project_root: Path,
        *,
        active_path_check: Callable[[Path], bool] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.active_path_check = active_path_check or (lambda _path: False)
        self._lock = threading.RLock()
        self._cached_entries: tuple[StorageAuditEntry, ...] | None = None
        self._cached_at = 0.0
        self._scanned_at: datetime | None = None

    def scan(self, *, force: bool = False) -> tuple[StorageAuditEntry, ...]:
        with self._lock:
            if (
                not force
                and self._cached_entries is not None
                and time.monotonic() - self._cached_at < 30.0
            ):
                return self._cached_entries
            self._cached_entries = tuple(self._entry(*target) for target in TARGETS)
            self._cached_at = time.monotonic()
            self._scanned_at = datetime.now().astimezone()
            return self._cached_entries

    def totals(self) -> dict[str, object]:
        entries = self.scan()
        cache_size = sum(
            item.size_bytes
            for item in entries
            if item.classification is StorageClassification.REBUILDABLE_CACHE
        )
        historical_size = sum(
            item.size_bytes
            for item in entries
            if item.classification is StorageClassification.HISTORICAL_TEST_TEMP
        )
        scanned_at = self._scanned_at or datetime.now().astimezone()
        return {
            "rebuildable_cache_size": cache_size,
            "rebuildable_cache_size_label": _format_size(cache_size),
            "historical_test_size": historical_size,
            "historical_test_size_label": _format_size(historical_size),
            "scanned_at": scanned_at.isoformat(timespec="seconds"),
            "scanned_at_label": scanned_at.strftime("%Y年%m月%d日 %H:%M:%S"),
        }

    def _entry(
        self,
        relative_path: str,
        classification: StorageClassification,
        creator_consumer: str,
    ) -> StorageAuditEntry:
        path = self.project_root / Path(relative_path)
        size, files, modified = _metadata(path)
        active = self.active_path_check(path)
        effective_classification = (
            StorageClassification.ACTIVE_RUNTIME
            if active and classification in {
                StorageClassification.REBUILDABLE_CACHE,
                StorageClassification.HISTORICAL_TEST_TEMP,
            }
            else classification
        )
        rebuildable = effective_classification in {
            StorageClassification.REBUILDABLE_CACHE,
            StorageClassification.HISTORICAL_TEST_TEMP,
        }
        recommendation, history_impact, performance_impact = _recommendation(
            effective_classification
        )
        return StorageAuditEntry(
            relative_path=relative_path,
            classification=effective_classification,
            size_bytes=size,
            size_label=_format_size(size),
            file_count=files,
            last_modified=modified,
            creator_consumer=creator_consumer,
            may_be_active=active or effective_classification is StorageClassification.ACTIVE_RUNTIME,
            rebuildable=rebuildable,
            recommendation=recommendation,
            history_impact=history_impact,
            performance_impact=performance_impact,
        )


def _metadata(path: Path) -> tuple[int, int, str | None]:
    if not path.exists():
        return 0, 0, None
    if path.is_file():
        metadata = path.stat()
        file_modified = datetime.fromtimestamp(metadata.st_mtime).astimezone()
        return metadata.st_size, 1, file_modified.isoformat(timespec="seconds")
    size = 0
    files = 0
    latest = path.stat().st_mtime
    for root, directories, filenames in os.walk(path, followlinks=False):
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for name in filenames:
            item = Path(root) / name
            try:
                metadata = item.stat(follow_symlinks=False)
            except OSError:
                continue
            size += metadata.st_size
            files += 1
            latest = max(latest, metadata.st_mtime)
    directory_modified = datetime.fromtimestamp(latest).astimezone().isoformat(
        timespec="seconds"
    )
    return size, files, directory_modified


def _recommendation(classification: StorageClassification) -> tuple[str, str, str]:
    if classification is StorageClassification.REBUILDABLE_CACHE:
        return ("可在确认无进程使用后清理", "不影响历史报告", "下次安装依赖可能更慢")
    if classification is StorageClassification.HISTORICAL_TEST_TEMP:
        return ("可在确认测试已结束后逐目录清理", "不影响历史报告", "后续测试会自动重建")
    if classification is StorageClassification.USER_DATA:
        return ("建议保留；仅通过单条历史记录确认删除", "删除会丢失用户报告", "不影响依赖安装速度")
    if classification is StorageClassification.SECRET:
        return ("必须保留，不作为缓存处理", "不影响历史报告", "删除会使 AI 配置失效")
    if classification is StorageClassification.ACTIVE_RUNTIME:
        return ("当前可能在用，禁止清理", "通常不影响历史报告", "可能中断当前服务或测试")
    if classification is StorageClassification.UNKNOWN:
        return ("用途未完全确认，禁止清理", "影响未知", "影响未知")
    return ("正式项目资料，禁止清理", "可能破坏项目", "可能导致项目无法运行")


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"
