"""Condition expression parser and evaluator for lifecycle hooks."""

import fnmatch
import re
from typing import Any

from releaseguard_agent.hooks.models import HookContext


def _extract_field(field_name: str, ctx: HookContext) -> str:
    """Extract string value from HookContext by field identifier."""
    name = field_name.strip()
    if name == "tool":
        return ctx.tool_name
    if name == "event":
        return ctx.event_name
    if name in ("path", "file_path"):
        return ctx.file_path
    if name == "message":
        return ctx.message
    if name == "error":
        return ctx.error

    if name.startswith("args."):
        key = name[5:]
        val: Any = ctx.tool_args.get(key, "")
        return str(val) if val is not None else ""
    if name.startswith("tool_args."):
        key = name[10:]
        val = ctx.tool_args.get(key, "")
        return str(val) if val is not None else ""

    # Check directly in tool_args as fallback
    if name in ctx.tool_args:
        val = ctx.tool_args[name]
        return str(val) if val is not None else ""

    return ""


def _eval_clause(clause: str, ctx: HookContext) -> bool:
    """Evaluate a single condition clause: field operator target."""
    raw = clause.strip()
    if not raw:
        return True

    # Regex matching: field (==|!=|=~|~=) target
    pattern = r"^([\w\.]+)\s*(==|!=|=~|~=)\s*(.*)$"
    m = re.match(pattern, raw)
    if not m:
        return False

    field_name, op, target_raw = m.groups()
    target = target_raw.strip()
    if (target.startswith('"') and target.endswith('"')) or (
        target.startswith("'") and target.endswith("'")
    ):
        target = target[1:-1]

    val = _extract_field(field_name, ctx)

    # Normalize path separators for path comparisons
    val_norm = val.replace("\\", "/")
    target_norm = target.replace("\\", "/")

    if op == "==":
        return val == target or val_norm == target_norm
    if op == "!=":
        return val != target and val_norm != target_norm
    if op == "=~":
        try:
            return bool(re.search(target, val) or re.search(target_norm, val_norm))
        except re.error:
            return False
    if op == "~=":
        return fnmatch.fnmatch(val_norm, target_norm) or fnmatch.fnmatch(val, target)

    return False


def evaluate_condition(condition_str: str, ctx: HookContext) -> bool:
    """Evaluate condition expression string against HookContext."""
    cond = condition_str.strip()
    if not cond:
        return True

    if "&&" in cond:
        clauses = [c.strip() for c in cond.split("&&")]
        return all(_eval_clause(c, ctx) for c in clauses if c)
    elif "||" in cond:
        clauses = [c.strip() for c in cond.split("||")]
        return any(_eval_clause(c, ctx) for c in clauses if c)
    else:
        return _eval_clause(cond, ctx)
