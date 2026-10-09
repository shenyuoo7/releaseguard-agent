"""Change detection and stale orphan worktree cleanup."""

from pathlib import Path
import re
import shutil
import subprocess
import time


def has_worktree_changes(wt_path: Path, head_commit: str = "") -> bool:
    """Check if worktree contains uncommitted working tree changes or new unmerged commits."""
    if not wt_path.is_dir():
        return False

    # 1. Check uncommitted changes (unstaged + staged + untracked)
    try:
        proc_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=wt_path,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if proc_status.returncode == 0 and proc_status.stdout.strip():
            return True
    except Exception:
        pass

    # 2. Check for new commits made on top of head_commit
    if head_commit:
        try:
            proc_rev = subprocess.run(
                ["git", "rev-list", f"{head_commit}..HEAD"],
                cwd=wt_path,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            if proc_rev.returncode == 0 and proc_rev.stdout.strip():
                return True
        except Exception:
            pass

    return False


def cleanup_stale_worktrees(
    repo_root: Path,
    max_age_seconds: float = 3600.0,
) -> list[str]:
    """Scan and prune stale temporary worktrees following the Fail-Closed preservation rule."""
    wt_dir = repo_root / ".releaseguard" / "worktrees"
    if not wt_dir.is_dir():
        return []

    cleaned: list[str] = []
    temp_pattern = re.compile(
        r"^(agent-a[0-9a-f]{7}|agent-[0-9a-f]{7,8}|wf_.*)$", re.IGNORECASE
    )

    for item in wt_dir.iterdir():
        if not item.is_dir():
            continue

        if not temp_pattern.match(item.name):
            continue

        # Age check
        if max_age_seconds > 0:
            age = time.time() - item.stat().st_mtime
            if age < max_age_seconds:
                continue

        # Fail-closed check: if changes exist, NEVER delete
        if has_worktree_changes(item):
            continue

        # Safe to remove
        branch_name = f"worktree-{item.name}"
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(item)],
                cwd=repo_root,
                capture_output=True,
                timeout=10.0,
            )
        except Exception:
            pass

        if item.exists():
            shutil.rmtree(item, ignore_errors=True)

        try:
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=repo_root,
                capture_output=True,
                timeout=5.0,
            )
        except Exception:
            pass

        cleaned.append(str(item))

    return cleaned
