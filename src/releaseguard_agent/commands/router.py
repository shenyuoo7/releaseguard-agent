"""Command router intercepting slash commands before agent loop dispatch."""

import asyncio
import inspect

from releaseguard_agent.commands.models import CommandContext
from releaseguard_agent.commands.registry import CommandRegistry


class CommandRouter:
    """Interception router routing slash commands to local handlers without LLM calls."""

    def __init__(self, registry: CommandRegistry) -> None:
        self.registry = registry

    @staticmethod
    def is_command(text: str) -> bool:
        """Check whether user input text represents a slash command."""
        return text.strip().startswith("/")

    @staticmethod
    def parse(text: str) -> tuple[str, str]:
        """Split a command string into (command_name, args_string)."""
        stripped = text.strip().lstrip("/")
        parts = stripped.split(maxsplit=1)
        name = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""
        return name, args

    async def handle_input_async(
        self,
        input_text: str,
        context: CommandContext,
    ) -> bool:
        """Asynchronously dispatch slash command; returns True if intercepted, False if normal chat."""
        if not self.is_command(input_text):
            return False

        name, args = self.parse(input_text)
        if not name:
            if context.ui:
                context.ui.add_system_message(
                    "请输入有效的命令名称。输入 /help 查看帮助。"
                )
            return True

        cmd = self.registry.find(name)
        if not cmd:
            if context.ui:
                context.ui.add_system_message(
                    f"未知命令 '/{name}'。输入 /help 查看所有可用命令。"
                )
            return True

        # Check required arguments
        if cmd.arg_prompt and not args.strip():
            if context.ui:
                usage_line = cmd.usage or f"/{cmd.name}"
                context.ui.add_system_message(
                    f"命令 '/{cmd.name}' 需要参数：{cmd.arg_prompt}\n用法: {usage_line}"
                )
            return True

        # Execute command handler safely
        context.args = args.strip()
        # Ensure registry is attached to context
        setattr(context, "registry", self.registry)

        try:
            if inspect.iscoroutinefunction(cmd.handler):
                await cmd.handler(context)
            else:
                res = cmd.handler(context)
                if inspect.iscoroutine(res):
                    await res
        except Exception as e:
            if context.ui:
                context.ui.add_system_message(f"执行命令 '/{cmd.name}' 时发生错误: {e}")

        return True

    def handle_input(
        self,
        input_text: str,
        context: CommandContext,
    ) -> bool:
        """Synchronous convenience wrapper for handle_input_async."""
        if not self.is_command(input_text):
            return False

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # If already running inside an event loop, create task or run
            asyncio.create_task(self.handle_input_async(input_text, context))
            return True
        else:
            return asyncio.run(self.handle_input_async(input_text, context))
