"""Post-creation environment initialization for isolated git worktrees."""

from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


def setup_worktree_environment(
    repo_root: Path,
    wt_path: Path,
    config: dict[str, Any] | None = None,
) -> None:
    """Initialize newly created worktree with configs, hooks, and dependency symlinks."""
    cfg = config or {}

    # 1. Copy local configuration files and .worktreeinclude entries
    include_file = repo_root / ".worktreeinclude"
    if include_file.is_file():
        try:
            for line in include_file.read_text(encoding="utf-8").splitlines():
                item = line.strip()
                if not item or item.startswith("#"):
                    continue
                src = repo_root / item
                dst = wt_path / item
                if src.is_file() and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                elif src.is_dir() and not dst.exists():
                    shutil.copytree(src, dst)
        except Exception as e:
            sys.stderr.write(
                f"[Worktree Setup Warning] Failed processing .worktreeinclude: {e}\n"
            )

    # Local settings
    local_settings = repo_root / ".releaseguard" / "settings.local.json"
    if local_settings.is_file():
        dst_settings = wt_path / ".releaseguard" / "settings.local.json"
        if not dst_settings.exists():
            dst_settings.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(local_settings, dst_settings)
            except Exception:
                pass

    # 2. Inherit Git Hooks if core.hooksPath is configured
    try:
        hooks_proc = subprocess.run(
            ["git", "config", "core.hooksPath"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if hooks_proc.returncode == 0 and hooks_proc.stdout.strip():
            hooks_path = hooks_proc.stdout.strip()
            subprocess.run(
                ["git", "config", "core.hooksPath", hooks_path],
                cwd=wt_path,
                capture_output=True,
            )
    except Exception:
        pass

    # 3. Create symlinks for heavy dependency directories (.venv, node_modules)
    dep_dirs = cfg.get("symlink_dirs", [".venv", "node_modules"])
    for d in dep_dirs:
        src_dep = repo_root / d
        dst_dep = wt_path / d
        if src_dep.is_dir() and not dst_dep.exists():
            try:
                dst_dep.symlink_to(src_dep, target_is_directory=True)
            except OSError as sym_err:
                # Catch Windows permission errors when developer mode is off
                sys.stderr.write(
                    f"[Worktree Setup Notice] Could not symlink {d} (expected on non-admin Windows): {sym_err}\n"
                )
