import asyncio
import time
from collections.abc import AsyncIterator
from enum import Enum

from releaseguard_agent.llm.client import StreamLLMClient
from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import (
    StreamEnd,
    TextDelta,
    ThinkingDelta,
    ToolCallComplete,
)
from releaseguard_agent.llm.messages import (
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from releaseguard_agent.runtime.batcher import ToolCallItem, partition_tool_calls
from releaseguard_agent.runtime.events import (
    AgentErrorEvent,
    AgentEvent,
    AgentLoopCompleteEvent,
    AgentTextEvent,
    AgentThinkingEvent,
    AgentToolResultEvent,
    AgentToolUseEvent,
    AgentTurnCompleteEvent,
)
from releaseguard_agent.tools.base import ToolContext, ToolResult
from releaseguard_agent.tools.registry import ToolRegistry


class LoopState(Enum):
    INITIAL = "INITIAL"
    STREAMING = "STREAMING"
    EXECUTING_TOOLS = "EXECUTING_TOOLS"
    TERMINAL = "TERMINAL"
    ABORTED = "ABORTED"


PLAN_MODE_PROMPT = """
# Plan Mode Active
You are in read-only planning mode. You may explore the project using read-only tools (read_file, glob, grep) to investigate and design solutions.
You must NOT perform code modifications or run destructive commands. Propose a structured plan in text.
""".strip()


class ReactAgentEngine:
    """Autonomous ReAct loop driving Think -> Act -> Observe iterations until completion."""

    def __init__(
        self,
        client: StreamLLMClient,
        registry: ToolRegistry,
        max_turns: int = 50,
    ) -> None:
        self.client = client
        self.registry = registry
        self.max_turns = max_turns
        self.state = LoopState.INITIAL

    async def run(
        self,
        conversation: ConversationManager,
        system_prompt: str = "",
        cancel_token: asyncio.Event | None = None,
        plan_mode: bool = False,
        context: ToolContext | None = None,
    ) -> AsyncIterator[AgentEvent]:
        turn = 0
        consecutive_unknown_tools = 0
        effective_context = context or ToolContext()

        # Build effective system prompt and exposed tool schemas
        effective_system = system_prompt.strip()
        if plan_mode:
            effective_system = (
                f"{effective_system}\n\n{PLAN_MODE_PROMPT}"
                if effective_system
                else PLAN_MODE_PROMPT
            )
            # Only expose read-only tools in plan mode
            available_tools = [t for t in self.registry.list_tools() if t.is_read_only]
            tool_definitions = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters_schema,
                }
                for t in available_tools
            ]
            tool_lookup = {t.name: t for t in available_tools}
        else:
            tool_definitions = self.registry.definitions()
            tool_lookup = {t.name: t for t in self.registry.list_tools()}

        while True:
            # 1. Turn limit check
            if turn >= self.max_turns:
                self.state = LoopState.ABORTED
                yield AgentErrorEvent(
                    error=f"Maximum iteration limit ({self.max_turns} turns) reached. Aborting loop."
                )
                return

            # 2. Cancellation check
            if cancel_token and cancel_token.is_set():
                self.state = LoopState.ABORTED
                yield AgentErrorEvent(error="Agent loop was cancelled by user.")
                return

            turn += 1
            self.state = LoopState.STREAMING

            accumulated_text = ""
            accumulated_thinking = ""
            tool_calls: list[ToolCallItem] = []

            # 3. Stream model generation
            try:
                async for event in self.client.stream(
                    conversation=conversation,
                    system=effective_system,
                    tools=tool_definitions,
                ):
                    if cancel_token and cancel_token.is_set():
                        self.state = LoopState.ABORTED
                        yield AgentErrorEvent(error="Agent loop was cancelled by user.")
                        return

                    if isinstance(event, ThinkingDelta):
                        accumulated_thinking += event.thinking
                        yield AgentThinkingEvent(thinking=event.thinking)

                    elif isinstance(event, TextDelta):
                        accumulated_text += event.text
                        yield AgentTextEvent(delta=event.text)

                    elif isinstance(event, ToolCallComplete):
                        item = ToolCallItem(
                            id=event.tool_id,
                            name=event.tool_name,
                            arguments=event.arguments,
                        )
                        tool_calls.append(item)
                        yield AgentToolUseEvent(
                            tool_id=item.id,
                            tool_name=item.name,
                            arguments=item.arguments,
                        )

                    elif isinstance(event, StreamEnd):
                        break

            except asyncio.CancelledError:
                self.state = LoopState.ABORTED
                yield AgentErrorEvent(error="Stream was cancelled.")
                return

            # Post-stream cancellation check
            if cancel_token and cancel_token.is_set():
                self.state = LoopState.ABORTED
                yield AgentErrorEvent(error="Agent loop was cancelled by user.")
                return

            # 4. Construct Assistant message and update conversation
            thinking_blocks = (
                [ThinkingBlock(thinking=accumulated_thinking)]
                if accumulated_thinking
                else []
            )
            tool_use_blocks = [
                ToolUseBlock(
                    tool_use_id=tc.id,
                    tool_name=tc.name,
                    arguments=tc.arguments,
                )
                for tc in tool_calls
            ]
            conversation.add_assistant_message(
                text=accumulated_text,
                thinking=thinking_blocks if thinking_blocks else None,
                tool_uses=tool_use_blocks if tool_use_blocks else None,
            )

            # 5. Terminal check: if no tool calls were generated, model has finished its answer
            if not tool_calls:
                self.state = LoopState.TERMINAL
                yield AgentTurnCompleteEvent(turn=turn)
                yield AgentLoopCompleteEvent(
                    total_turns=turn, final_content=accumulated_text
                )
                return

            # 6. Unknown tool circuit breaker check
            all_unknown = all(tc.name not in tool_lookup for tc in tool_calls)
            if all_unknown:
                consecutive_unknown_tools += 1
                if consecutive_unknown_tools >= 3:
                    self.state = LoopState.ABORTED
                    yield AgentErrorEvent(
                        error="Circuit breaker triggered: 3 consecutive unknown tool calls. Aborting loop."
                    )
                    return
            else:
                consecutive_unknown_tools = 0

            # 7. Partition tool calls into concurrent and serial batches
            self.state = LoopState.EXECUTING_TOOLS
            batches = partition_tool_calls(tool_calls, tool_lookup)
            tool_results_list: list[ToolResultBlock] = []

            for batch in batches:
                if cancel_token and cancel_token.is_set():
                    self.state = LoopState.ABORTED
                    yield AgentErrorEvent(error="Agent loop was cancelled by user.")
                    return

                if batch.is_concurrent:
                    # Parallel execution of concurrent read-only tools
                    async def execute_one(
                        item: ToolCallItem,
                    ) -> tuple[ToolCallItem, ToolResult, float]:
                        t0 = time.perf_counter()
                        res = await self.registry.execute(
                            item.name, item.arguments, effective_context
                        )
                        dur_ms = (time.perf_counter() - t0) * 1000
                        return item, res, dur_ms

                    results = await asyncio.gather(
                        *(execute_one(c) for c in batch.calls)
                    )
                    for item, res, dur_ms in results:
                        yield AgentToolResultEvent(
                            tool_id=item.id,
                            tool_name=item.name,
                            result=res,
                            duration_ms=dur_ms,
                        )
                        tool_results_list.append(res.to_tool_result_block(item.id))
                else:
                    # Serial execution of mutating tools
                    for item in batch.calls:
                        t0 = time.perf_counter()
                        res = await self.registry.execute(
                            item.name, item.arguments, effective_context
                        )
                        dur_ms = (time.perf_counter() - t0) * 1000
                        yield AgentToolResultEvent(
                            tool_id=item.id,
                            tool_name=item.name,
                            result=res,
                            duration_ms=dur_ms,
                        )
                        tool_results_list.append(res.to_tool_result_block(item.id))

            # 8. Append tool results to conversation as user observation message
            conversation.append(
                Message(
                    role="user",
                    content="",
                    tool_results=tool_results_list,
                )
            )

            yield AgentTurnCompleteEvent(turn=turn)
