"""Non-interactive closed-loop runner for subagent task completion."""

import asyncio

from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.runtime.events import (
    AgentErrorEvent,
    AgentLoopCompleteEvent,
    AgentTextEvent,
)
from releaseguard_agent.runtime.react_engine import ReactAgentEngine
from releaseguard_agent.tools.base import ToolContext


FORK_BOILERPLATE = """
<fork_boilerplate>
You are an autonomous execution worker delegated with an isolated subtask.
1. You MUST NOT fork or spawn any subagents.
2. You MUST NOT ask questions to the user or request interactive input.
3. Focus strictly on executing the assigned subtask using available tools.
4. Your final response MUST be a concise, structured summary report under 500 words.
</fork_boilerplate>
""".strip()


async def run_to_completion(
    engine: ReactAgentEngine,
    conversation: ConversationManager,
    task_prompt: str,
    system_prompt: str = "",
    context: ToolContext | None = None,
    cancel_token: asyncio.Event | None = None,
) -> str:
    """Execute subagent loop non-interactively until completion or turn limit."""
    conversation.add_user_message(task_prompt)

    final_content = ""
    accumulated_text = ""
    error_msg = ""

    async for event in engine.run(
        conversation=conversation,
        system_prompt=system_prompt,
        cancel_token=cancel_token,
        context=context,
    ):
        if isinstance(event, AgentTextEvent):
            accumulated_text += event.delta
        elif isinstance(event, AgentLoopCompleteEvent):
            final_content = event.final_content
        elif isinstance(event, AgentErrorEvent):
            error_msg = event.error

    if final_content:
        return final_content
    if error_msg:
        return f"SubAgent task error: {error_msg}"
    return accumulated_text.strip()
