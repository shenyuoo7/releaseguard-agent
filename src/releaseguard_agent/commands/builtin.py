"""Built-in Slash Commands providing zero-token local utilities, UI controls, and prompt templates."""

from pathlib import Path

from releaseguard_agent.commands.models import Command, CommandContext, CommandType
from releaseguard_agent.commands.registry import CommandRegistry


async def handle_help(ctx: CommandContext) -> None:
    """Display available commands or detailed help for a specific command."""
    if not ctx.ui:
        return

    arg = ctx.args.strip().lower()
    # Access registry if attached to ctx or config
    registry: CommandRegistry | None = getattr(ctx, "registry", None) or (
        ctx.config.get("registry") if isinstance(ctx.config, dict) else None
    )

    if not registry:
        ctx.ui.add_system_message(
            "可用内置命令：\n"
            "- /help, /h, /?: 查看帮助\n"
            "- /status, /s: 查看当前状态与 Token 统计\n"
            "- /compact, /c: 手动压缩上下文\n"
            "- /clear: 清空屏幕并重置会话\n"
            "- /plan, /p: 切换至只读规划模式\n"
            "- /do: 切换回执行模式\n"
            "- /session: 会话管理 (list / resume / new)\n"
            "- /memory: 记忆管理 (list / add)\n"
            "- /permission: 权限系统状态\n"
            "- /review: 触发多维度代码审查"
        )
        return

    if arg:
        cmd = registry.find(arg)
        if not cmd:
            ctx.ui.add_system_message(
                f"未找到命令 '/{arg}'。输入 /help 查看所有可用命令。"
            )
            return
        aliases_str = ", ".join(f"/{a}" for a in cmd.aliases) if cmd.aliases else "无"
        usage_str = cmd.usage or f"/{cmd.name}"
        ctx.ui.add_system_message(
            f"命令: /{cmd.name}\n"
            f"说明: {cmd.description}\n"
            f"用法: {usage_str}\n"
            f"别名: {aliases_str}\n"
            f"类型: {cmd.command_type.value}"
        )
        return

    # List all commands
    cmds = registry.list_commands()
    lines = ["💡 **ReleaseGuard Slash Commands 快速指南**", ""]
    for c in cmds:
        alias_part = f" ({', '.join('/' + a for a in c.aliases)})" if c.aliases else ""
        lines.append(f"- **/{c.name}**{alias_part}: {c.description}")
    lines.append("\n使用 `/help <cmd>` 可查看特定命令的详细参数。")
    ctx.ui.add_system_message("\n".join(lines))


async def handle_status(ctx: CommandContext) -> None:
    """Display system status, token metrics, and active runtime configuration."""
    if not ctx.ui:
        return

    tokens = ctx.ui.get_token_count()
    ws = ctx.workspace_root or Path.cwd()

    lines = [
        "📊 **ReleaseGuard Agent 状态面板**",
        f"- **工作目录**: `{ws}`",
        f"- **当前 Token 估计**: {tokens:,} tokens",
        "- **系统架构**: ReAct Autonomous Loop with 3-tier Memory",
        "- **版本**: v1.0.0",
    ]
    ctx.ui.add_system_message("\n".join(lines))


async def handle_compact(ctx: CommandContext) -> None:
    """Manually invoke conversation auto-compaction and report delta."""
    if not ctx.ui:
        return

    tokens_before = ctx.ui.get_token_count()
    conv = ctx.conversation

    if not conv or len(getattr(conv, "messages", [])) < 3:
        ctx.ui.add_system_message(
            f"当前上下文仅有 {tokens_before} tokens，消息条数较少，无需执行压缩。"
        )
        return

    try:
        from releaseguard_agent.runtime.context.auto_compact import perform_auto_compact

        client = getattr(ctx, "agent", None) and getattr(ctx.agent, "client", None)
        if client:
            success = await perform_auto_compact(conversation=conv, client=client)
            tokens_after = ctx.ui.get_token_count()
            if success:
                reduction = max(0, tokens_before - tokens_after)
                ctx.ui.add_system_message(
                    f"✅ 上下文压缩完成！Token 从 {tokens_before:,} 降至 {tokens_after:,}（减少 {reduction:,} tokens）。"
                )
                return
    except Exception as e:
        ctx.ui.add_system_message(f"⚠️ 压缩过程中遇到错误: {e}")
        return

    ctx.ui.add_system_message(
        f"上下文压缩完成。当前 Token: {ctx.ui.get_token_count():,}。"
    )


async def handle_clear(ctx: CommandContext) -> None:
    """Clear visual chat and reset conversation state."""
    if ctx.ui:
        ctx.ui.clear_chat()
    if ctx.conversation and hasattr(ctx.conversation, "messages"):
        ctx.conversation.messages.clear()
    if ctx.ui:
        ctx.ui.add_system_message("🧹 会话与屏幕已重置，已开启全新的对话上下文。")


async def handle_plan(ctx: CommandContext) -> None:
    """Activate read-only plan mode."""
    if ctx.ui:
        ctx.ui.set_plan_mode(True)
        ctx.ui.add_system_message(
            "🛡️ 已切换至 **只读规划模式 (Plan Mode)**。\n"
            "所有文件修改与系统执行命令将被阻断，Agent 仅能使用只读工具分析并输出解决方案。"
        )


async def handle_do(ctx: CommandContext) -> None:
    """Deactivate plan mode and return to standard execution mode."""
    if ctx.ui:
        ctx.ui.set_plan_mode(False)
        ctx.ui.add_system_message(
            "⚡ 已切换回 **正常执行模式 (Execution Mode)**。\n"
            "所有读写、执行与自动化工具已恢复可用。"
        )


async def handle_session(ctx: CommandContext) -> None:
    """Manage conversation sessions: list, resume, or start new."""
    if not ctx.ui:
        return

    subcmd = ctx.args.strip()
    session_mgr = getattr(ctx, "session", None)
    if not session_mgr:
        from releaseguard_agent.memory.session import SessionManager

        session_mgr = SessionManager.default_for_workspace(ctx.workspace_root)

    if not subcmd or subcmd == "list":
        sessions = session_mgr.list_sessions()
        if not sessions:
            ctx.ui.add_system_message("📂 当前没有任何已保存的历史会话。")
            return
        lines = ["📂 **历史会话清单**："]
        for s in sessions[:10]:
            lines.append(f"- `{s['session_id']}` ({s['size_bytes']:,} bytes)")
        lines.append("\n使用 `/session resume <id>` 可断点恢复会话。")
        ctx.ui.add_system_message("\n".join(lines))
        return

    if subcmd.startswith("resume"):
        parts = subcmd.split(maxsplit=1)
        if len(parts) < 2:
            ctx.ui.add_system_message("用法: `/session resume <session_id>`")
            return
        sess_id = parts[1].strip()
        try:
            records, meta = session_mgr.load_session(sess_id)
            if ctx.conversation:
                session_mgr.restore_to_conversation(records, ctx.conversation)
            ctx.ui.add_system_message(
                f"✅ 会话 `{sess_id}` 恢复成功！载入 {len(records)} 条历史记录。"
            )
        except Exception as e:
            ctx.ui.add_system_message(f"❌ 恢复会话失败: {e}")
        return

    if subcmd == "new":
        new_sess = session_mgr.create_session()
        ctx.ui.add_system_message(f"✨ 已创建新会话: `{new_sess.session_id}`。")
        return

    ctx.ui.add_system_message(
        f"未知子命令 '{subcmd}'。用法: /session [list | resume <id> | new]"
    )


async def handle_memory(ctx: CommandContext) -> None:
    """View or manage long-term memories."""
    if not ctx.ui:
        return

    from releaseguard_agent.memory.auto_memory import MemoryManager

    mem_mgr = MemoryManager(workspace_root=ctx.workspace_root)
    subcmd = ctx.args.strip()

    if not subcmd or subcmd == "list":
        index = mem_mgr.load_memory_index()
        if not index:
            ctx.ui.add_system_message("🧠 当前没有记录任何长期经验记忆。")
            return
        ctx.ui.add_system_message(index)
        return

    if subcmd.startswith("add"):
        parts = subcmd.split(maxsplit=3)
        if len(parts) < 4:
            ctx.ui.add_system_message(
                "用法: `/memory add <user|feedback|project|reference> <name> <content>`"
            )
            return
        cat, name, content = parts[1], parts[2], parts[3]
        try:
            mem_mgr.save_memory(
                category=cat,
                name=name,
                description=content[:50],
                content=content,
            )
            ctx.ui.add_system_message(f"✅ 记忆 `[{cat}:{name}]` 保存成功！")
        except Exception as e:
            ctx.ui.add_system_message(f"❌ 保存记忆失败: {e}")
        return

    ctx.ui.add_system_message("用法: /memory [list | add <cat> <name> <content>]")


async def handle_permission(ctx: CommandContext) -> None:
    """Inspect and manage tool permission policy."""
    if not ctx.ui:
        return

    lines = [
        "🔒 **工具执行安全策略**",
        "- **只读工具**: `read_file`, `glob`, `grep` -> 自动放行 (ALLOW)",
        "- **修改与命令工具**: `write_file`, `edit_file`, `bash` -> 策略拦截与确认 (ASK/DENY)",
        "- **当前环境**: 本地安全沙箱模式",
    ]
    ctx.ui.add_system_message("\n".join(lines))


async def handle_review(ctx: CommandContext) -> None:
    """Prompt-type command: Inject structured code review instructions."""
    if not ctx.ui:
        return

    focus = ctx.args.strip()
    focus_section = f"\n\n**审查重点关注**：{focus}" if focus else ""

    review_prompt = (
        "请对当前工作空间中的代码进行多维度、深度的生产发布就绪审查：\n"
        "1. **架构与契约**: 接口签名、类型安全性与模块边界\n"
        "2. **关键逻辑**: 边界异常处理、并发竞态与资源泄露风险\n"
        "3. **安全防护**: 避免注入、敏感凭据脱敏与沙箱边界\n"
        "4. **测试与质量**: 单元测试覆盖与断言完整性"
        f"{focus_section}\n\n"
        "请使用只读工具逐步排查代码并输出结构化的评审报告与改进建议。"
    )

    ctx.ui.send_user_message(review_prompt)


def build_default_command_registry() -> CommandRegistry:
    """Construct and populate CommandRegistry with the 10 built-in commands."""
    reg = CommandRegistry()

    reg.register(
        Command(
            name="help",
            description="显示所有可用命令或特定命令的帮助信息",
            aliases=("h", "?"),
            usage="/help [command]",
            command_type=CommandType.LOCAL,
            handler=handle_help,
        )
    )

    reg.register(
        Command(
            name="status",
            description="查看当前运行模式、Token 统计与系统状态",
            aliases=("s",),
            usage="/status",
            command_type=CommandType.LOCAL,
            handler=handle_status,
        )
    )

    reg.register(
        Command(
            name="compact",
            description="手动触发上下文压缩并显示前后 Token 统计对比",
            aliases=("c",),
            usage="/compact",
            command_type=CommandType.LOCAL,
            handler=handle_compact,
        )
    )

    reg.register(
        Command(
            name="clear",
            description="清空当前屏幕并重置对话会话",
            aliases=(),
            usage="/clear",
            command_type=CommandType.LOCAL_UI,
            handler=handle_clear,
        )
    )

    reg.register(
        Command(
            name="plan",
            description="切换至只读规划模式 (只允许读取，禁止修改代码或执行命令)",
            aliases=("p",),
            usage="/plan",
            command_type=CommandType.LOCAL_UI,
            handler=handle_plan,
        )
    )

    reg.register(
        Command(
            name="do",
            description="退出规划模式，恢复全功能正常执行模式",
            aliases=(),
            usage="/do",
            command_type=CommandType.LOCAL_UI,
            handler=handle_do,
        )
    )

    reg.register(
        Command(
            name="session",
            description="会话持久化管理 (list, resume <id>, new)",
            aliases=(),
            usage="/session [list | resume <id> | new]",
            command_type=CommandType.LOCAL,
            handler=handle_session,
        )
    )

    reg.register(
        Command(
            name="memory",
            description="长期经验记忆管理与检索 (list, add)",
            aliases=(),
            usage="/memory [list | add <cat> <name> <content>]",
            command_type=CommandType.LOCAL,
            handler=handle_memory,
        )
    )

    reg.register(
        Command(
            name="permission",
            description="查看当前工具执行权限规则与策略",
            aliases=(),
            usage="/permission",
            command_type=CommandType.LOCAL,
            handler=handle_permission,
        )
    )

    reg.register(
        Command(
            name="review",
            description="参数化注入多维度代码审查指引至 Agent 对话",
            aliases=(),
            usage="/review [focus_area]",
            command_type=CommandType.PROMPT,
            handler=handle_review,
        )
    )

    return reg
