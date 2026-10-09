"""API payload assembly pipeline distributing 7 sources to 3 fields."""

from pathlib import Path
from typing import Any

from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.messages import Message
from releaseguard_agent.prompts.environment import get_environment_context
from releaseguard_agent.prompts.modules import build_static_system_prompt
from releaseguard_agent.prompts.reminder import format_system_reminder
from releaseguard_agent.tools.base import BaseTool
from releaseguard_agent.tools.registry import ToolRegistry


def assemble_api_payload(
    conversation_history: list[Any] | ConversationManager,
    enabled_tools: list[Any] | ToolRegistry,
    effective_cwd: str | Path,
    system_reminders: list[str] | None = None,
    project_instructions: str | None = None,
    memory_context: str | None = None,
    extra_system_prompt: str = "",
) -> dict[str, Any]:
    """Assemble API payload distributing 7 data sources to system, messages, and tools fields.

    Sources:
    1. Static 7-module system prompt -> 'system'
    2. Stable environment context (OS, Python, cwd) -> 'system'
    3. Tool schemas -> 'tools'
    4. Project instructions (RELEASEGUARD.md) -> 'messages' (initial)
    5. Automatic memory (MEMORY.md) -> 'messages' (initial)
    6. Dynamic runtime reminders (<system-reminder>) -> 'messages' (tail)
    7. Conversation history -> 'messages' (middle)
    """
    cwd_path = Path(effective_cwd).resolve()

    # 1 & 2. Byte-stable System Prompt
    env_info = get_environment_context(cwd_path)
    system_content = f"{build_static_system_prompt()}\n\n# 运行环境\n{env_info}"
    if extra_system_prompt.strip():
        system_content = f"{system_content}\n\n{extra_system_prompt.strip()}"

    # 4 & 5. Auto-discover project instructions and memory if not explicitly provided
    if project_instructions is None:
        try:
            from releaseguard_agent.memory.instructions import load_project_instructions

            project_instructions = load_project_instructions(workspace_root=cwd_path)
        except Exception:
            inst_file = cwd_path / "RELEASEGUARD.md"
            if inst_file.is_file():
                try:
                    project_instructions = inst_file.read_text(encoding="utf-8")
                except Exception:
                    project_instructions = None

    if memory_context is None:
        try:
            from releaseguard_agent.memory.auto_memory import MemoryManager

            memory_context = MemoryManager(workspace_root=cwd_path).load_memory_index()
        except Exception:
            mem_file = cwd_path / "MEMORY.md"
            if mem_file.is_file():
                try:
                    memory_context = mem_file.read_text(encoding="utf-8")
                except Exception:
                    memory_context = None

    # Construct messages list
    messages: list[dict[str, Any]] = []

    if project_instructions and project_instructions.strip():
        messages.append(
            {
                "role": "user",
                "content": f"# 项目指令 (RELEASEGUARD.md)\n{project_instructions.strip()}",
            }
        )

    if memory_context and memory_context.strip():
        messages.append(
            {
                "role": "user",
                "content": f"# 记忆上下文 (MEMORY.md)\n{memory_context.strip()}",
            }
        )

    # 7. Conversation history
    history_items: list[Any]
    if isinstance(conversation_history, ConversationManager):
        history_items = conversation_history.get_messages()
    else:
        history_items = list(conversation_history)

    for item in history_items:
        if isinstance(item, Message):
            messages.append({"role": item.role, "content": item.content})
        elif isinstance(item, dict):
            messages.append(dict(item))
        else:
            messages.append(
                {
                    "role": getattr(item, "role", "user"),
                    "content": str(getattr(item, "content", item)),
                }
            )

    # 6. Dynamic runtime reminders appended at the tail for recency effect
    if system_reminders:
        active_reminders = [r.strip() for r in system_reminders if r.strip()]
        if active_reminders:
            joined_text = "\n".join(active_reminders)
            messages.append(
                {
                    "role": "user",
                    "content": format_system_reminder(joined_text),
                }
            )

    # 3. Tool schemas
    tool_list: list[Any]
    if isinstance(enabled_tools, ToolRegistry):
        tool_list = enabled_tools.list_tools()
    else:
        tool_list = list(enabled_tools)

    tool_schemas: list[dict[str, Any]] = []
    for t in tool_list:
        if isinstance(t, BaseTool):
            tool_schemas.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters_schema,
                }
            )
        elif isinstance(t, dict):
            tool_schemas.append(t)
        elif hasattr(t, "parameters_schema"):
            tool_schemas.append(
                {
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", ""),
                    "input_schema": t.parameters_schema,
                }
            )
        else:
            tool_schemas.append(t)

    return {
        "system": system_content,
        "messages": messages,
        "tools": tool_schemas,
    }
