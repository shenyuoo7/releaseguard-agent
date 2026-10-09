"""Action executors for lifecycle hooks (command, prompt, http, agent)."""

import asyncio
import subprocess
import sys

import httpx

from releaseguard_agent.hooks.models import HookAction, HookContext


async def execute_command_action(action: HookAction, ctx: HookContext) -> str:
    """Execute a shell command with working directory and timeout safeguards."""
    expanded_cmd = ctx.expand(action.command)
    cwd = ctx.workspace_root if ctx.workspace_root else None
    timeout = action.timeout if action.timeout > 0 else 10.0

    def _run() -> tuple[int, str, str]:
        proc = subprocess.run(
            expanded_cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr

    try:
        code, out, err = await asyncio.to_thread(_run)
        if code != 0:
            sys.stderr.write(
                f"[Hook Warning] Command '{expanded_cmd}' exited with code {code}: {err}\n"
            )
        return out if out else err
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            f"[Hook Warning] Command '{expanded_cmd}' timed out after {timeout}s\n"
        )
        return f"Command timed out after {timeout}s"
    except Exception as e:
        sys.stderr.write(
            f"[Hook Error] Command '{expanded_cmd}' execution failed: {e}\n"
        )
        return str(e)


def execute_prompt_action(action: HookAction, ctx: HookContext) -> str:
    """Render a prompt notification with expanded context variables."""
    rendered = ctx.expand(action.message)
    return f"<hook-notification>\n{rendered}\n</hook-notification>"


async def execute_http_action(action: HookAction, ctx: HookContext) -> str:
    """Send an HTTP webhook request with timeout safeguard."""
    expanded_url = ctx.expand(action.url)
    expanded_body = ctx.expand(action.body) if action.body else ""
    method = (action.method or "POST").upper()
    timeout = action.timeout if action.timeout > 0 else 10.0

    headers = dict(action.headers)
    if "Content-Type" not in headers and expanded_body:
        headers["Content-Type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            content = expanded_body.encode("utf-8") if expanded_body else None
            resp = await client.request(
                method=method,
                url=expanded_url,
                headers=headers,
                content=content,
            )
            return f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        sys.stderr.write(f"[Hook Error] HTTP request to '{expanded_url}' failed: {e}\n")
        return f"HTTP error: {e}"


async def execute_agent_action(action: HookAction, ctx: HookContext) -> str:
    """Delegate to subagent (reserved interface for Ch13)."""
    rendered = ctx.expand(action.message or action.command)
    return f"Subagent delegated: {rendered}"


async def execute_hook_action(action: HookAction, ctx: HookContext) -> str | None:
    """Execute hook action with comprehensive error isolation."""
    try:
        if action.action_type == "command":
            return await execute_command_action(action, ctx)
        elif action.action_type == "prompt":
            return execute_prompt_action(action, ctx)
        elif action.action_type == "http":
            return await execute_http_action(action, ctx)
        elif action.action_type == "agent":
            return await execute_agent_action(action, ctx)
        else:
            sys.stderr.write(
                f"[Hook Warning] Unknown action type: {action.action_type}\n"
            )
            return None
    except Exception as e:
        sys.stderr.write(f"[Hook Error] Action execution error: {e}\n")
        return None
