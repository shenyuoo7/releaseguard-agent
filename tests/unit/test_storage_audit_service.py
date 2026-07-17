from pathlib import Path

from releaseguard_agent.services.storage_audit_service import (
    StorageAuditService,
    StorageClassification,
)


def test_storage_audit_is_metadata_only_and_never_deletes(tmp_path: Path) -> None:
    cache = tmp_path / ".runtime" / "dependency_cache"
    cache.mkdir(parents=True)
    cached_file = cache / "wheel.bin"
    cached_file.write_bytes(b"cache")
    secret = tmp_path / ".runtime" / "secrets"
    secret.mkdir()
    secret_file = secret / "credential.bin"
    secret_file.write_bytes(b"opaque")
    history = tmp_path / "outputs" / "runs" / "rg-20260717-100000-abcdef12"
    history.mkdir(parents=True)
    (history / "result.json").write_text("{}", encoding="utf-8")

    entries = {item.relative_path: item for item in StorageAuditService(tmp_path).scan()}

    assert entries[".runtime/dependency_cache"].classification is StorageClassification.REBUILDABLE_CACHE
    assert entries[".runtime/dependency_cache"].size_bytes == 5
    assert entries[".runtime/secrets"].classification is StorageClassification.SECRET
    assert entries["outputs/runs"].classification is StorageClassification.USER_DATA
    assert entries[".runtime/legacy_runtime"].classification is StorageClassification.HISTORICAL_TEST_TEMP
    assert cached_file.is_file()
    assert secret_file.is_file()
    assert history.is_dir()


def test_active_cache_is_reported_as_active_runtime(tmp_path: Path) -> None:
    target = tmp_path / ".runtime" / "pytest"
    target.mkdir(parents=True)

    entries = StorageAuditService(
        tmp_path,
        active_path_check=lambda path: path == target,
    ).scan()

    pytest_entry = next(item for item in entries if item.relative_path == ".runtime/pytest")
    assert pytest_entry.classification is StorageClassification.ACTIVE_RUNTIME
    assert pytest_entry.may_be_active is True
    assert pytest_entry.rebuildable is False
