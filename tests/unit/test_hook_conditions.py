"""Unit tests for lifecycle hook condition parsing and evaluation."""

from releaseguard_agent.hooks.condition import evaluate_condition
from releaseguard_agent.hooks.models import HookContext


def test_empty_condition_evaluates_true() -> None:
    ctx = HookContext(event_name="turn_start")
    assert evaluate_condition("", ctx) is True
    assert evaluate_condition("   ", ctx) is True


def test_exact_equality_and_inequality() -> None:
    ctx = HookContext(
        event_name="pre_tool_use",
        tool_name="WriteFile",
        tool_args={"path": "src/main.py"},
    )
    assert evaluate_condition('tool == "WriteFile"', ctx) is True
    assert evaluate_condition("tool == WriteFile", ctx) is True
    assert evaluate_condition("tool != ReadFile", ctx) is True
    assert evaluate_condition("tool == ReadFile", ctx) is False
    assert evaluate_condition('tool != "WriteFile"', ctx) is False


def test_regex_matching() -> None:
    ctx = HookContext(
        event_name="post_tool_use",
        tool_name="bash",
        tool_args={"command": "rm -rf /tmp/test"},
    )
    assert evaluate_condition('args.command =~ "rm\\s+-rf"', ctx) is True
    assert evaluate_condition('args.command =~ "^git"', ctx) is False


def test_glob_matching() -> None:
    ctx = HookContext(
        event_name="pre_tool_use",
        tool_name="WriteFile",
        file_path="src/releaseguard_agent/foo.py",
        tool_args={"path": "src/releaseguard_agent/foo.py"},
    )
    assert evaluate_condition('args.path ~= "*.py"', ctx) is True
    assert evaluate_condition('path ~= "src/*"', ctx) is True
    assert evaluate_condition('path ~= "*.js"', ctx) is False

    # Vendor path blocking condition check
    vendor_ctx = HookContext(
        event_name="pre_tool_use",
        tool_name="WriteFile",
        file_path="vendor/secrets.json",
        tool_args={"path": "vendor/secrets.json"},
    )
    assert evaluate_condition('args.path ~= "vendor/*"', vendor_ctx) is True


def test_and_condition_combination() -> None:
    ctx = HookContext(
        event_name="pre_tool_use",
        tool_name="WriteFile",
        file_path="src/index.py",
        tool_args={"path": "src/index.py"},
    )
    # Both true
    cond1 = 'tool == "WriteFile" && args.path ~= "*.py"'
    assert evaluate_condition(cond1, ctx) is True

    # One false
    cond2 = 'tool == "WriteFile" && args.path ~= "*.go"'
    assert evaluate_condition(cond2, ctx) is False


def test_or_condition_combination() -> None:
    ctx = HookContext(
        event_name="pre_tool_use",
        tool_name="ReadFile",
        file_path="config.json",
        tool_args={"path": "config.json"},
    )
    cond1 = 'tool == "WriteFile" || tool == "ReadFile"'
    assert evaluate_condition(cond1, ctx) is True

    cond2 = 'tool == "Bash" || args.path ~= "*.py"'
    assert evaluate_condition(cond2, ctx) is False


def test_invalid_syntax_returns_false() -> None:
    ctx = HookContext(event_name="test")
    assert evaluate_condition("invalid condition without operator", ctx) is False
