"""Core WorktreeManager implementing physical isolation, fast-restore, and lifecycle operations."""

import os
from pathlib import Path
import shutil
import subprocess
import threading

from releaseguard_agent.worktree.cleaner import has_worktree_changes
from releaseguard_agent.worktree.models import Worktree, WorktreeSession
from releaseguard_agent.worktree.setup import setup_worktree_environment
from releaseguard_agent.worktree.slug import (
    slug_to_branch_name,
    slug_to_dir_name,
    validate_slug,
)


def try_fast_restore_head(wt_path: Path) -> str | None:
    """Read HEAD commit SHA directly via filesystem in < 5ms without launching git."""
    git_file = wt_path / ".git"
    if not git_file.is_file():
        return None

    try:
        content = git_file.read_text(encoding="utf-8").strip()
        if not content.startswith("gitdir:"):
            return None
        gitdir_raw = content[len("gitdir:") :].strip()
        gitdir = Path(gitdir_raw)
        if not gitdir.is_absolute():
            gitdir = (wt_path / gitdir).resolve()

        head_file = gitdir / "HEAD"
        if not head_file.is_file():
            return None
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref:"):
            ref_rel = head_content[len("ref:") :].strip()
            # Try directly under gitdir
            ref_file = gitdir / ref_rel
            if ref_file.is_file():
                return ref_file.read_text(encoding="utf-8").strip()

            # Try under main repo commondir
            commondir_file = gitdir / "commondir"
            if commondir_file.is_file():
                common = commondir_file.read_text(encoding="utf-8").strip()
                common_path = (gitdir / common).resolve()
                ref_file2 = common_path / ref_rel
                if ref_file2.is_file():
                    return ref_file2.read_text(encoding="utf-8").strip()

        return head_content
    except Exception:
        return None


class WorktreeManager:
    """Manages creation, fast-restore, explicit-cwd execution, and teardown of Git worktrees."""

    def __init__(self, repo_root: Path | str | None = None) -> None:
        self.repo_root = (
            Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
        )
        self.worktrees_dir = self.repo_root / ".releaseguard" / "worktrees"
        self._lock = threading.Lock()

    def create_worktree(
        self,
        slug: str,
        base_branch: str = "HEAD",
    ) -> Worktree:
        """Create or fast-restore an isolated worktree for the given slug."""
        err = validate_slug(slug)
        if err:
            raise ValueError(f"Invalid worktree slug '{slug}': {err}")

        branch_name = slug_to_branch_name(slug)
        dir_name = slug_to_dir_name(slug)
        wt_path = self.worktrees_dir / dir_name

        with self._lock:
            # 1. Fast restore check
            if wt_path.is_dir():
                head = try_fast_restore_head(wt_path)
                if head:
                    return Worktree(
                        name=slug,
                        path=wt_path,
                        branch=branch_name,
                        base_branch=base_branch,
                        head_commit=head,
                    )

            # 2. Physical creation
            self.worktrees_dir.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ)
            env["GIT_TERMINAL_PROMPT"] = "0"

            proc = subprocess.run(
                [
                    "git",
                    "worktree",
                    "add",
                    "-B",
                    branch_name,
                    str(wt_path),
                    base_branch,
                ],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                env=env,
                stdin=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Failed to create git worktree '{slug}': {proc.stderr.strip()}"
                )

            # 3. Post-creation environment setup
            setup_worktree_environment(self.repo_root, wt_path)

            head_sha = try_fast_restore_head(wt_path) or ""
            return Worktree(
                name=slug,
                path=wt_path,
                branch=branch_name,
                base_branch=base_branch,
                head_commit=head_sha,
            )

    def remove_worktree(
        self,
        slug: str,
        discard_changes: bool = False,
    ) -> bool:
        """Remove a worktree with change protection safeguard."""
        err = validate_slug(slug)
        if err:
            raise ValueError(f"Invalid worktree slug '{slug}': {err}")

        branch_name = slug_to_branch_name(slug)
        dir_name = slug_to_dir_name(slug)
        wt_path = self.worktrees_dir / dir_name

        with self._lock:
            if not wt_path.exists():
                return False

            if not discard_changes and has_worktree_changes(wt_path):
                raise RuntimeError(
                    f"Worktree '{slug}' has uncommitted changes or new commits. "
                    "Use discard_changes=True to discard."
                )

            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=self.repo_root,
                capture_output=True,
                stdin=subprocess.DEVNULL,
            )

            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)

            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=self.repo_root,
                capture_output=True,
                stdin=subprocess.DEVNULL,
            )

            return True

    def enter_session(
        self,
        slug: str,
        base_branch: str = "HEAD",
    ) -> WorktreeSession:
        """Enter a worktree session without altering global os.getcwd()."""
        wt = self.create_worktree(slug, base_branch=base_branch)
        return WorktreeSession(
            original_cwd=self.repo_root,
            worktree_path=wt.path,
            worktree_name=slug,
            worktree_branch=wt.branch,
            original_head_commit=wt.head_commit,
        )

    def exit_session(
        self,
        session: WorktreeSession,
        discard_changes: bool = False,
    ) -> bool:
        """Exit session and cleanup worktree if clean or discarded."""
        if not discard_changes and has_worktree_changes(
            session.worktree_path, session.original_head_commit
        ):
            # Preserve worktree and report changes exist
            return False

        return self.remove_worktree(session.worktree_name, discard_changes=True)
