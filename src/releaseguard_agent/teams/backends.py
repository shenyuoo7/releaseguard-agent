"""Execution backend detection and teammate launch command generation."""

import os
import shutil
from typing import Literal

BackendType = Literal["tmux", "iterm2", "in-process"]


def detect_backend() -> BackendType:
    """Detect available execution backend with priority: Tmux session > iTerm2 > installed tmux > in-process."""
    # 1. Already running inside a tmux session
    if os.environ.get("TMUX"):
        return "tmux"

    # 2. Running inside iTerm2 with it2 CLI available
    if os.environ.get("ITERM_SESSION_ID") and shutil.which("it2"):
        return "iterm2"

    # 3. System has tmux installed
    if shutil.which("tmux"):
        return "tmux"

    # 4. Fallback to in-process coroutine backend
    return "in-process"


def build_teammate_command(
    team_name: str,
    agent_id: str,
    backend_type: BackendType,
) -> list[str]:
    """Generate process invocation arguments based on backend type."""
    entrypoint = [
        "python",
        "-m",
        "releaseguard_agent.teams.worker",
        "--team",
        team_name,
        "--agent",
        agent_id,
    ]

    if backend_type == "tmux":
        return ["tmux", "new-window", "-n", agent_id] + entrypoint
    elif backend_type == "iterm2":
        return ["it2", "split-pane"] + entrypoint
    else:
        return entrypoint
