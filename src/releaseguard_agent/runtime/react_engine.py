import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import Enum
from typing import Any

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
from releaseguard_agent.prompts import assemble_api_payload, format_system_reminder
from releaseguard_agent.runtime.batcher import ToolCallItem, partition_tool_calls
from releaseguard_agent.runtime.context import (
    ContentReplacementState,
    apply_tool_result_budget,
    compute_compact_threshold,
    estimate_context_tokens,
    perform_auto_compact,
)
from releaseguard_agent.security import (
    Decision,
    PermissionEngine,
    append_local_allow_rule,
)
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
        permission_engine: PermissionEngine | None = None,
        window_tokens: int = 200_000,
    ) -> None:
        self.client = client
        self.registry = registry
        self.max_turns = max_turns
        self.permission_engine = permission_engine
        self.window_tokens = window_tokens
        self.replacement_state = ContentReplacementState()
        self.compact_failures = 0
        self.state = LoopState.INITIAL

    async def run(
        self,
        conversation: ConversationManager,
        system_prompt: str = "",
        cancel_token: asyncio.Event | None = None,
        plan_mode: bool = False,
        context: ToolContext | None = None,
        system_reminders: list[str] | None = None,
        permission_engine: PermissionEngine | None = None,
        hitl_handler: Callable[[str, dict[str, Any]], Awaitable[str]] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        turn = 0
        consecutive_unknown_tools = 0
        effective_context = context or ToolContext()
        active_perm_engine = permission_engine or self.permission_engine

        available_tools = (
            [t for t in self.registry.list_tools() if t.is_read_only]
            if plan_mode
            else self.registry.list_tools()
        )
        tool_lookup = {t.name: t for t in available_tools}

        payload = assemble_api_payload(
            conversation_history=conversation,
            enabled_tools=available_tools,
            effective_cwd=effective_context.cwd,
            system_reminders=system_reminders,
            extra_system_prompt=system_prompt,
        )

        effective_system = payload["system"]
        if plan_mode:
            effective_system = f"{effective_system}\n\n{PLAN_MODE_PROMPT}"

        tool_definitions = payload["tools"]

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

            # Context management: Check token limit for Layer 2 Auto-Compact
            compact_threshold = compute_compact_threshold(self.window_tokens)
            current_tokens = estimate_context_tokens(
                conversation.get_messages(), effective_system
            )
            if current_tokens >= compact_threshold:
                compact_ok = await perform_auto_compact(
                    conversation=conversation,
                    client=self.client,
                    window_tokens=self.window_tokens,
                )
                if compact_ok:
                    self.compact_failures = 0
                else:
                    self.compact_failures += 1
                    if self.compact_failures >= 3:
                        self.state = LoopState.ABORTED
                        yield AgentErrorEvent(
                            error="Circuit breaker triggered: 3 consecutive Auto-Compact failures. Aborting loop."
                        )
                        return

            accumulated_text = ""
            accumulated_thinking = ""
            tool_calls: list[ToolCallItem] = []

            # Prepare conversation with dynamic reminder if needed
            stream_conv = conversation
            if system_reminders:
                stream_conv = conversation.clone()
                clean_reminders = [r.strip() for r in system_reminders if r.strip()]
                if clean_reminders:
                    stream_conv.add_user_message(
                        format_system_reminder("\n".join(clean_reminders))
                    )

            # 3. Stream model generation
            try:
                async for event in self.client.stream(
                    conversation=stream_conv,
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
            except Exception as stream_err:
                err_text = str(stream_err).lower()
                if (
                    "prompt_too_long" in err_text
                    or "context_length_exceeded" in err_text
                    or "too long" in err_text
                ):
                    recovered = await perform_auto_compact(
                        conversation=conversation,
                        client=self.client,
                        window_tokens=self.window_tokens,
                        keep_recent_messages=2,
                    )
                    if recovered:
                        continue
                self.state = LoopState.ABORTED
                yield AgentErrorEvent(error=f"LLM streaming error: {stream_err}")
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

            async def _execute_with_permission(
                item: ToolCallItem,
            ) -> ToolResult:
                tool_inst = tool_lookup.get(item.name)
                is_ro = tool_inst.is_read_only if tool_inst else False

                if active_perm_engine:
                    decision = active_perm_engine.check(
                        tool_name=item.name,
                        arguments=item.arguments,
                        is_read_only=is_ro,
                    )
                    if decision == Decision.DENY:
                        return ToolResult(
                            content=f"Permission denied: execution of '{item.name}' was rejected by security policy.",
                            is_error=True,
                        )
                    if decision == Decision.ASK:
                        if hitl_handler:
                            choice = await hitl_handler(item.name, item.arguments)
                            if choice == "a":
                                pattern = str(
                                    item.arguments.get("command")
                                    or item.arguments.get("path")
                                    or "*"
                                )
                                append_local_allow_rule(
                                    effective_context.cwd, item.name, pattern
                                )
                            elif choice != "y":
                                return ToolResult(
                                    content=f"Permission denied: user rejected execution of '{item.name}'.",
                                    is_error=True,
                                )
                        else:
                            return ToolResult(
                                content=f"Permission denied: execution of '{item.name}' requires confirmation, but no interactive HITL handler was configured.",
                                is_error=True,
                            )

                return await self.registry.execute(
                    item.name, item.arguments, effective_context
                )

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
                        res = await _execute_with_permission(item)
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
                        res = await _execute_with_permission(item)
                        dur_ms = (time.perf_counter() - t0) * 1000
                        yield AgentToolResultEvent(
                            tool_id=item.id,
                            tool_name=item.name,
                            result=res,
                            duration_ms=dur_ms,
                        )
                        tool_results_list.append(res.to_tool_result_block(item.id))

            # 8. Apply tool result budget and freeze decisions
            storage_dir = effective_context.cwd / ".runtime" / "session" / "tool-results"
            apply_tool_result_budget(
                tool_results=tool_results_list,
                state=self.replacement_state,
                storage_dir=storage_dir,
            )

            # Append tool results to conversation as user observation message
            conversation.append(
                Message(
                    role="user",
                    content="",
                    tool_results=tool_results_list,
                )
            )

            yield AgentTurnCompleteEvent(turn=turn)
