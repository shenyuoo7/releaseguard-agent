"""Data models for Git Worktree physical isolation."""

from dataclasses import dataclass, field
from pathlib import Path
import time


@dataclass(frozen=True)
class Worktree:
    """Represents an isolated git worktree working directory."""

    name: str
    path: Path
    branch: str
    base_branch: str
    head_commit: str
    created_at: float = field(default_factory=time.time)


@dataclass
class WorktreeSession:
    """Session state when an agent switches effective execution context to a worktree."""

    original_cwd: Path
    worktree_path: Path
    worktree_name: str
    worktree_branch: str
    original_branch: str = ""
    original_head_commit: str = ""
