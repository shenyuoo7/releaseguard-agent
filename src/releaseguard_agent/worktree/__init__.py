"""Git Worktree physical isolation for parallel and safe agent execution."""

from releaseguard_agent.worktree.cleaner import (
    cleanup_stale_worktrees,
    has_worktree_changes,
)
from releaseguard_agent.worktree.manager import WorktreeManager, try_fast_restore_head
from releaseguard_agent.worktree.models import Worktree, WorktreeSession
from releaseguard_agent.worktree.setup import setup_worktree_environment
from releaseguard_agent.worktree.slug import (
    slug_to_branch_name,
    slug_to_dir_name,
    validate_slug,
)

__all__ = [
    "Worktree",
    "WorktreeManager",
    "WorktreeSession",
    "cleanup_stale_worktrees",
    "has_worktree_changes",
    "setup_worktree_environment",
    "slug_to_branch_name",
    "slug_to_dir_name",
    "try_fast_restore_head",
    "validate_slug",
]
