"""Declarative lifecycle hook engine for loading, condition evaluation, and execution."""

import asyncio
from pathlib import Path
import sys
from typing import Any

import yaml

from releaseguard_agent.hooks.condition import evaluate_condition
from releaseguard_agent.hooks.executors import execute_hook_action
from releaseguard_agent.hooks.models import (
    HookAction,
    HookDef,
    HookContext,
    ToolRejectedError,
)


def _parse_timeout(val: Any) -> float:
    """Parse timeout value which might be int, float, or string like '10s'."""
    if val is None:
        return 10.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().lower()
    if s.endswith("s"):
        s = s[:-1]
    try:
        return float(s)
    except ValueError:
        return 10.0


def _parse_hook_def(data: dict[str, Any]) -> HookDef | None:
    """Parse a dictionary from YAML into a HookDef."""
    hook_id = str(data.get("id", "")).strip()
    event = str(data.get("event", "")).strip()
    if not hook_id or not event:
        return None

    condition_str = str(data.get("condition", data.get("condition_str", ""))).strip()
    reject = bool(data.get("reject", False))
    reason = str(data.get("reason", ""))
    once = bool(data.get("once", False))
    async_exec = bool(data.get("async", data.get("async_exec", False)))

    action: HookAction | None = None
    action_data = data.get("action")
    if isinstance(action_data, dict):
        action_type = str(
            action_data.get("type", action_data.get("action_type", ""))
        ).strip()
        if action_type in ("command", "prompt", "http", "agent"):
            action = HookAction(
                action_type=action_type,  # type: ignore[arg-type]
                command=str(action_data.get("command", "")),
                message=str(action_data.get("message", "")),
                url=str(action_data.get("url", "")),
                method=str(action_data.get("method", "POST")),
                headers=dict(action_data.get("headers", {})),
                body=str(action_data.get("body", "")),
                timeout=_parse_timeout(action_data.get("timeout")),
            )

    return HookDef(
        id=hook_id,
        event=event,
        condition_str=condition_str,
        action=action,
        reject=reject,
        reason=reason,
        once=once,
        async_exec=async_exec,
    )


class HookEngine:
    """Central engine managing declarative lifecycle hooks across events."""

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        hooks: list[HookDef] | None = None,
    ) -> None:
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        )
        self.hooks: list[HookDef] = list(hooks) if hooks is not None else []
        if hooks is None:
            self.load_hooks()

    def load_hooks(self) -> None:
        """Load and merge user-level and project-level hook configurations."""
        self.hooks.clear()

        # 1. User level: ~/.releaseguard/hooks.yaml
        user_file = Path.home() / ".releaseguard" / "hooks.yaml"
        if user_file.is_file():
            self._load_file(user_file)

        # 2. Project level: <workspace_root>/.releaseguard/hooks.yaml
        proj_file = self.workspace_root / ".releaseguard" / "hooks.yaml"
        if proj_file.is_file():
            self._load_file(proj_file)

    def _load_file(self, path: Path) -> None:
        """Parse hooks from a YAML file and append to active hooks."""
        try:
            content = path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                return
            hook_items = data.get("hooks", [])
            if isinstance(hook_items, list):
                for item in hook_items:
                    if isinstance(item, dict):
                        parsed = _parse_hook_def(item)
                        if parsed:
                            self.hooks.append(parsed)
        except Exception as e:
            sys.stderr.write(f"[Hook Warning] Failed to parse hooks from {path}: {e}\n")

    def add_hook(self, hook: HookDef) -> None:
        """Register a hook programmatically."""
        self.hooks.append(hook)

    def reset_session(self) -> None:
        """Reset execution status of hooks for a new session."""
        for h in self.hooks:
            h.executed = False

    async def run_pre_tool_hooks(self, ctx: HookContext) -> None:
        """Evaluate pre_tool_use hooks synchronously before tool execution."""
        ctx.event_name = "pre_tool_use"
        if not ctx.workspace_root:
            ctx.workspace_root = str(self.workspace_root)

        for hook in self.hooks:
            if hook.event != "pre_tool_use":
                continue
            if hook.once and hook.executed:
                continue

            if not evaluate_condition(hook.condition_str, ctx):
                continue

            hook.executed = True

            if hook.reject:
                msg = (
                    hook.reason
                    or f"Tool execution '{ctx.tool_name}' rejected by hook '{hook.id}'."
                )
                raise ToolRejectedError(ctx.expand(msg))

            if hook.action:
                await execute_hook_action(hook.action, ctx)

    async def emit(self, event_name: str, ctx: HookContext) -> list[str]:
        """Dispatch lifecycle event to matching hooks."""
        ctx.event_name = event_name
        if not ctx.workspace_root:
            ctx.workspace_root = str(self.workspace_root)

        notifications: list[str] = []

        for hook in self.hooks:
            if hook.event != event_name:
                continue
            if hook.once and hook.executed:
                continue

            if not evaluate_condition(hook.condition_str, ctx):
                continue

            hook.executed = True

            if not hook.action:
                continue

            if hook.async_exec:
                # Fire and forget background task with error isolation
                asyncio.create_task(execute_hook_action(hook.action, ctx))
            else:
                out = await execute_hook_action(hook.action, ctx)
                if hook.action.action_type == "prompt" and out:
                    notifications.append(out)

        return notifications
