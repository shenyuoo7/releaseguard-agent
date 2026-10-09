from pathlib import Path
import time
import pytest

from releaseguard_agent.commands.builtin import build_default_command_registry
from releaseguard_agent.commands.models import CommandContext
from releaseguard_agent.commands.router import CommandRouter


class MockUIController:
    def __init__(self) -> None:
        self.system_messages: list[str] = []
        self.user_messages: list[str] = []
        self.plan_mode: bool = False
        self.cleared: bool = False
        self.status_refreshed: bool = False

    def add_system_message(self, text: str) -> None:
        self.system_messages.append(text)

    def send_user_message(self, text: str) -> None:
        self.user_messages.append(text)

    def set_plan_mode(self, enabled: bool) -> None:
        self.plan_mode = enabled

    def get_token_count(self) -> int:
        return 1250

    def refresh_status(self) -> None:
        self.status_refreshed = True

    def clear_chat(self) -> None:
        self.cleared = True


@pytest.mark.anyio
async def test_builtin_command_registry_population() -> None:
    """T3: Default registry contains all 10 core commands."""
    reg = build_default_command_registry()
    commands = {c.name for c in reg.list_commands()}
    expected = {
        "help",
        "status",
        "compact",
        "clear",
        "plan",
        "do",
        "session",
        "memory",
        "permission",
        "review",
    }
    assert expected.issubset(commands)


@pytest.mark.anyio
async def test_command_router_non_command_bypass() -> None:
    """T4: Regular conversation messages are bypassed by router."""
    reg = build_default_command_registry()
    router = CommandRouter(reg)
    ui = MockUIController()
    ctx = CommandContext(ui=ui)

    assert await router.handle_input_async("hello agent", ctx) is False
    assert await router.handle_input_async("how are you?", ctx) is False
    assert len(ui.system_messages) == 0


@pytest.mark.anyio
async def test_status_command_fast_execution(tmp_path: Path) -> None:
    """AC1: /status is intercepted and executed in < 10ms with zero LLM calls."""
    reg = build_default_command_registry()
    router = CommandRouter(reg)
    ui = MockUIController()
    ctx = CommandContext(ui=ui, workspace_root=tmp_path)

    t0 = time.perf_counter()
    handled = await router.handle_input_async("/status", ctx)
    duration_ms = (time.perf_counter() - t0) * 1000

    assert handled is True
    assert duration_ms < 50  # comfortably under 50ms (usually < 5ms)
    assert len(ui.system_messages) == 1
    assert "ReleaseGuard Agent 状态面板" in ui.system_messages[0]
    assert "1,250 tokens" in ui.system_messages[0]


@pytest.mark.anyio
async def test_plan_and_do_mode_toggle() -> None:
    """F5: /plan and /do toggle UI plan mode."""
    reg = build_default_command_registry()
    router = CommandRouter(reg)
    ui = MockUIController()
    ctx = CommandContext(ui=ui)

    # 1. /plan
    await router.handle_input_async("/plan", ctx)
    assert ui.plan_mode is True
    assert "只读规划模式" in ui.system_messages[-1]

    # 2. /do
    await router.handle_input_async("/do", ctx)
    assert ui.plan_mode is False
    assert "正常执行模式" in ui.system_messages[-1]


@pytest.mark.anyio
async def test_clear_command() -> None:
    """F5: /clear resets UI chat."""
    reg = build_default_command_registry()
    router = CommandRouter(reg)
    ui = MockUIController()
    ctx = CommandContext(ui=ui)

    await router.handle_input_async("/clear", ctx)
    assert ui.cleared is True
    assert "会话与屏幕已重置" in ui.system_messages[-1]


@pytest.mark.anyio
async def test_alias_dispatch_compact() -> None:
    """AC2: /c alias routes to /compact."""
    reg = build_default_command_registry()
    router = CommandRouter(reg)
    ui = MockUIController()
    ctx = CommandContext(ui=ui)

    handled = await router.handle_input_async("/c", ctx)
    assert handled is True
    assert len(ui.system_messages) == 1
    assert "上下文" in ui.system_messages[0]


@pytest.mark.anyio
async def test_review_prompt_injection() -> None:
    """AC4: /review injects structured code review prompt with user focus."""
    reg = build_default_command_registry()
    router = CommandRouter(reg)
    ui = MockUIController()
    ctx = CommandContext(ui=ui)

    handled = await router.handle_input_async("/review 重点关注SQL注入风险", ctx)
    assert handled is True
    assert len(ui.user_messages) == 1
    assert "重点关注SQL注入风险" in ui.user_messages[0]
    assert "多维度、深度的生产发布就绪审查" in ui.user_messages[0]


@pytest.mark.anyio
async def test_unknown_command_guidance() -> None:
    """F6: Unknown slash commands output /help guidance without crashing."""
    reg = build_default_command_registry()
    router = CommandRouter(reg)
    ui = MockUIController()
    ctx = CommandContext(ui=ui)

    handled = await router.handle_input_async("/nonexistent_xyz", ctx)
    assert handled is True
    assert len(ui.system_messages) == 1
    assert "未知命令 '/nonexistent_xyz'" in ui.system_messages[0]
    assert "/help" in ui.system_messages[0]
