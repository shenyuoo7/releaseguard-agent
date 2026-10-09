"""Unit tests for WorktreeManager, fast restore, and change protection safeguards."""

from pathlib import Path
import subprocess
import time
import pytest

from releaseguard_agent.worktree.cleaner import (
    cleanup_stale_worktrees,
    has_worktree_changes,
)
from releaseguard_agent.worktree.manager import (
    WorktreeManager,
    try_fast_restore_head,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialize a clean Git repository in temporary directory."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "TestUser"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("# Test Repo", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


def test_create_worktree_and_fast_restore(git_repo: Path) -> None:
    mgr = WorktreeManager(repo_root=git_repo)
    slug = "agent-a123456"

    wt = mgr.create_worktree(slug)
    assert wt.path.is_dir()
    assert wt.branch == "worktree-agent-a123456"
    assert (wt.path / ".git").is_file()
    assert (wt.path / "README.md").is_file()
    assert wt.head_commit != ""

    # Test fast restore: must read HEAD commit in < 5ms without launching git
    t0 = time.perf_counter()
    restored_head = try_fast_restore_head(wt.path)
    dur_ms = (time.perf_counter() - t0) * 1000
    assert restored_head == wt.head_commit
    assert dur_ms < 100.0  # Safe threshold for file I/O on Windows

    # Subsequent create_worktree returns restored worktree
    wt2 = mgr.create_worktree(slug)
    assert wt2.path == wt.path
    assert wt2.head_commit == wt.head_commit


def test_has_worktree_changes_and_remove_protection(git_repo: Path) -> None:
    mgr = WorktreeManager(repo_root=git_repo)
    slug = "agent-b654321"

    wt = mgr.create_worktree(slug)
    assert has_worktree_changes(wt.path, wt.head_commit) is False

    # Modify a file inside worktree
    (wt.path / "new_feature.py").write_text("print('feature')", encoding="utf-8")
    assert has_worktree_changes(wt.path, wt.head_commit) is True

    # Removal without discard_changes MUST raise RuntimeError
    with pytest.raises(RuntimeError) as exc_info:
        mgr.remove_worktree(slug, discard_changes=False)
    assert "has uncommitted changes" in str(exc_info.value)

    # Removal with discard_changes=True must succeed
    assert mgr.remove_worktree(slug, discard_changes=True) is True
    assert not wt.path.exists()


def test_cleanup_stale_worktrees_fail_closed(git_repo: Path) -> None:
    mgr = WorktreeManager(repo_root=git_repo)

    # 1. Clean temporary worktree
    wt_clean = mgr.create_worktree("agent-a111111")
    # 2. Dirty temporary worktree with changes
    wt_dirty = mgr.create_worktree("agent-a222222")
    (wt_dirty.path / "uncommitted.txt").write_text("dirty content", encoding="utf-8")

    # Run cleanup with max_age_seconds=0
    cleaned = cleanup_stale_worktrees(git_repo, max_age_seconds=0.0)

    # The clean worktree was deleted
    assert str(wt_clean.path) in cleaned
    assert not wt_clean.path.exists()

    # The dirty worktree is preserved (Fail-Closed principle)
    assert str(wt_dirty.path) not in cleaned
    assert wt_dirty.path.exists()


def test_enter_and_exit_session(git_repo: Path) -> None:
    mgr = WorktreeManager(repo_root=git_repo)
    slug = "agent-session-test"

    session = mgr.enter_session(slug)
    assert session.worktree_path.is_dir()
    assert session.original_cwd == git_repo

    # Clean exit automatically cleans up worktree
    cleaned = mgr.exit_session(session)
    assert cleaned is True
    assert not session.worktree_path.exists()
