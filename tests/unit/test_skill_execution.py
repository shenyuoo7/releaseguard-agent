from pathlib import Path
import pytest

from releaseguard_agent.commands.registry import CommandRegistry
from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import StreamEnd, TextDelta
from releaseguard_agent.llm.fake_stream_client import FakeStreamClient
from releaseguard_agent.skills.activator import ActiveSkillManager, LoadSkillTool
from releaseguard_agent.skills.executor import SkillExecutor, filter_tools_for_skill
from releaseguard_agent.skills.loader import SkillLoader
from releaseguard_agent.skills.models import SkillContextMode, SkillDef, SkillMode
from releaseguard_agent.tools import ToolContext, build_default_tool_registry


def test_tool_whitelist_filtering() -> None:
    """T4, AC3 & N1: Restrict available tools strictly to allowed_tools, exempting LoadSkill."""
    reg = build_default_tool_registry()
    skill_mgr = ActiveSkillManager(SkillLoader(Path.cwd()))
    reg.register(LoadSkillTool(skill_mgr))

    skill = SkillDef(
        name="readonly-audit",
        description="Audit only",
        prompt_body="Audit",
        allowed_tools=("read_file", "grep"),
    )

    filtered = filter_tools_for_skill(reg, skill)
    tool_names = {t.name for t in filtered.list_tools()}

    # Whitelisted tools
    assert "read_file" in tool_names
    assert "grep" in tool_names
    # LoadSkill system tool exemption
    assert "LoadSkill" in tool_names

    # Mutating / shell tools MUST be removed
    assert "bash" not in tool_names
    assert "write_file" not in tool_names
    assert "edit_file" not in tool_names


@pytest.mark.anyio
async def test_active_skill_manager_and_load_skill_tool(tmp_path: Path) -> None:
    """T3 & AC2: LoadSkill tool activates skill and pins SOP to environment context."""
    skill_dir = tmp_path / ".releaseguard" / "skills" / "sec-audit"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: sec-audit
description: Security auditing SOP
---
# Security SOP
Check for hardcoded secrets.
""",
        encoding="utf-8",
    )

    loader = SkillLoader(workspace_root=tmp_path)
    mgr = ActiveSkillManager(loader)
    tool = LoadSkillTool(mgr)

    ctx = ToolContext(cwd=tmp_path)
    res = await tool.execute({"name": "sec-audit"}, ctx)
    assert not res.is_error
    assert "已成功激活" in res.content

    # Check pinned context
    pinned = mgr.pin_to_env_context()
    assert "当前激活的专业技能包" in pinned
    assert "Check for hardcoded secrets" in pinned

    # Clear active skills
    mgr.clear_active_skills()
    assert mgr.pin_to_env_context() == ""


@pytest.mark.anyio
async def test_fork_mode_execution_isolation(tmp_path: Path) -> None:
    """AC4 & F6: Fork mode executes in isolated conversation and writes summary report to parent."""
    client = FakeStreamClient(
        events=[
            TextDelta(text="审查完成：未发现高危代码注入漏洞，代码质量良好。"),
            StreamEnd(),
        ]
    )
    reg = build_default_tool_registry()

    skill = SkillDef(
        name="review",
        description="Objective review",
        prompt_body="Conduct objective review.",
        allowed_tools=("read_file",),
        mode=SkillMode.FORK,
        context=SkillContextMode.NONE,
    )

    parent_conv = ConversationManager()
    parent_conv.add_user_message("这是主会话中已有的消息，包含旧的上下文。")

    executor = SkillExecutor(client=client, registry=reg)
    report, success = await executor.execute(
        skill, parent_conv, arguments="Focus on security"
    )

    assert success is True
    assert "审查完成" in report
    assert "【review 独立任务审查报告】" in report

    # Parent conversation should now have the single assistant report appended
    messages = parent_conv.get_messages()
    assert len(messages) == 2
    assert messages[0].content == "这是主会话中已有的消息，包含旧的上下文。"
    assert "【review 独立任务审查报告】" in messages[1].content


def test_command_registry_skill_integration() -> None:
    """F1 & AC1: Skills are registered in CommandRegistry as /[skill] with autocomplete."""
    reg = CommandRegistry()
    skill = SkillDef(
        name="commit-msg",
        description="Format git commit message",
        prompt_body="Format commit.",
    )

    reg.register_skills([skill])

    cmd = reg.find("commit-msg")
    assert cmd is not None
    assert "[skill]" in cmd.description

    completions = reg.complete("/com")
    assert "/commit-msg" in completions
