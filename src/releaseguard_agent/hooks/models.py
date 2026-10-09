"""Data models for declarative lifecycle hooks."""

from dataclasses import dataclass, field
import re
from typing import Any, Literal


class ToolRejectedError(Exception):
    """Raised when a pre_tool_use hook explicitly rejects tool execution."""

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class HookAction:
    """Action executed when a lifecycle hook triggers."""

    action_type: Literal["command", "prompt", "http", "agent"]
    command: str = ""
    message: str = ""
    url: str = ""
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    timeout: float = 10.0


@dataclass
class HookDef:
    """Definition of a declarative lifecycle hook."""

    id: str
    event: str
    condition_str: str = ""
    action: HookAction | None = None
    reject: bool = False
    reason: str = ""
    once: bool = False
    async_exec: bool = False
    executed: bool = False


@dataclass
class HookContext:
    """Runtime context passed to hook condition evaluators and action executors."""

    event_name: str
    workspace_root: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    message: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if not self.file_path and self.tool_args:
            path_val = self.tool_args.get("path") or self.tool_args.get("file_path")
            if path_val is not None:
                self.file_path = str(path_val)

    def expand(self, template: str) -> str:
        """Expand template variables with context values."""
        if not template:
            return ""

        res = template
        # Support both $VAR and ${VAR}
        replacements = {
            "EVENT": self.event_name,
            "TOOL_NAME": self.tool_name,
            "FILE_PATH": self.file_path,
            "MESSAGE": self.message,
            "ERROR": self.error,
        }
        for k, v in replacements.items():
            res = res.replace(f"${{{k}}}", str(v))
            res = res.replace(f"${k}", str(v))

        # Dynamic expansion for $TOOL_ARGS.<key> and ${TOOL_ARGS.<key>}
        def _replace_arg(match: re.Match[str]) -> str:
            key = match.group(1)
            val = self.tool_args.get(key, "")
            return str(val) if val is not None else ""

        res = re.sub(r"\$\{TOOL_ARGS\.([a-zA-Z0-9_]+)\}", _replace_arg, res)
        res = re.sub(r"\$TOOL_ARGS\.([a-zA-Z0-9_]+)", _replace_arg, res)

        return res
