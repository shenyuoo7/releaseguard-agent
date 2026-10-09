"""Unified AgentTool allowing primary agent to delegate subtasks to specialized or forked subagents."""

import asyncio
from pathlib import Path
from typing import Any
import uuid

from releaseguard_agent.llm.client import StreamLLMClient
from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.runtime.react_engine import ReactAgentEngine
from releaseguard_agent.subagent.filters import resolve_subagent_tools
from releaseguard_agent.subagent.loader import AgentLoader
from releaseguard_agent.subagent.runner import FORK_BOILERPLATE, run_to_completion
from releaseguard_agent.subagent.task_manager import TaskManager
from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult
from releaseguard_agent.tools.registry import ToolRegistry
from releaseguard_agent.worktree.cleaner import has_worktree_changes
from releaseguard_agent.worktree.manager import WorktreeManager
from releaseguard_agent.worktree.models import WorktreeSession


class AgentTool(BaseTool):
    """Tool allowing primary agent to spawn and delegate subtasks to subagents."""

    def __init__(
        self,
        llm_client: StreamLLMClient,
        parent_registry: ToolRegistry,
        task_manager: TaskManager | None = None,
        agent_loader: AgentLoader | None = None,
        workspace_root: Path | str | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.parent_registry = parent_registry
        self.task_manager = task_manager or TaskManager()
        self.agent_loader = agent_loader or AgentLoader(workspace_root=workspace_root)
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        )

    @property
    def name(self) -> str:
        return "Agent"

    @property
    def description(self) -> str:
        return (
            "Delegate a subtask to an isolated specialized expert subagent or forked execution worker. "
            "Returns the conclusion or an asynchronous task confirmation."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Specific task prompt and instructions for the subagent.",
                },
                "description": {
                    "type": "string",
                    "description": "One-line description of the delegation purpose.",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "Optional type of specialized expert (e.g. Explore, Plan, Verification, general-purpose). If omitted, forks the current agent.",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override for the subagent.",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Whether to run the subagent asynchronously in the background.",
                },
                "name": {
                    "type": "string",
                    "description": "Optional custom name for the task.",
                },
                "isolation": {
                    "type": "string",
                    "description": "Optional execution environment isolation mode.",
                },
            },
            "required": ["prompt", "description"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        prompt = str(arguments.get("prompt", "")).strip()
        description = str(arguments.get("description", "")).strip()
        subagent_type = str(arguments.get("subagent_type", "")).strip()
        run_in_background = bool(arguments.get("run_in_background", False))
        custom_name = str(arguments.get("name", "")).strip()

        if not prompt:
            return ToolResult(
                content="缺少必须的参数 'prompt'。请提供具体的子任务要求。",
                is_error=True,
            )

        task_id = f"agent-{uuid.uuid4().hex[:8]}"
        task_name = custom_name or description or subagent_type or "SubAgent Task"
        isolation = str(arguments.get("isolation", "")).strip().lower()

        effective_context = context or ToolContext(cwd=self.workspace_root)

        # 1. Physical isolation via Git Worktree if requested
        wt_mgr: WorktreeManager | None = None
        wt_session: WorktreeSession | None = None
        if isolation == "worktree":
            try:
                wt_mgr = WorktreeManager(self.workspace_root)
                slug = f"agent-a{uuid.uuid4().hex[:7]}"
                wt_session = wt_mgr.enter_session(slug)
                effective_context = ToolContext(
                    cwd=wt_session.worktree_path,
                    extra=dict(effective_context.extra),
                )
            except Exception as wt_err:
                return ToolResult(
                    content=f"Failed to initialize isolated worktree: {wt_err}",
                    is_error=True,
                )

        # 2. Determine subagent mode: Definition-based vs Fork-based
        if subagent_type:
            agent_def = self.agent_loader.load_agent(subagent_type)
            if not agent_def:
                if wt_session and wt_mgr:
                    wt_mgr.exit_session(wt_session, discard_changes=True)
                return ToolResult(
                    content=f"未找到类型为 '{subagent_type}' 的专家定义。可用类型包括: Explore, Plan, Verification, general-purpose。",
                    is_error=True,
                )

            # Blank independent conversation
            sub_conv = ConversationManager()
            system_prompt = agent_def.system_prompt
            max_turns = agent_def.max_turns
            sub_tools = resolve_subagent_tools(
                self.parent_registry,
                definition=agent_def,
                is_async=run_in_background,
            )
        else:
            # Fork-based temporary worker
            parent_conv = effective_context.extra.get("conversation")
            if isinstance(parent_conv, ConversationManager):
                sub_conv = parent_conv.clone()
            else:
                sub_conv = ConversationManager()

            system_prompt = FORK_BOILERPLATE
            max_turns = 30
            run_in_background = True  # Fork-based workers are forced into background mode per specification
            sub_tools = resolve_subagent_tools(
                self.parent_registry,
                definition=None,
                is_async=True,
            )

        if wt_session:
            system_prompt = (
                f"{system_prompt}\n\n"
                f"【Worktree Notice】You are operating in an isolated git worktree at: {wt_session.worktree_path}.\n"
                f"Branch: {wt_session.worktree_branch}. All file operations are isolated from the main branch."
            )

        # Subagent ReAct Engine
        sub_engine = ReactAgentEngine(
            client=self.llm_client,
            registry=sub_tools,
            max_turns=max_turns,
        )

        # Subagent execution coroutine
        async def _run() -> str:
            try:
                out = await run_to_completion(
                    engine=sub_engine,
                    conversation=sub_conv,
                    task_prompt=prompt,
                    system_prompt=system_prompt,
                    context=effective_context,
                )
                if wt_session and wt_mgr:
                    if has_worktree_changes(
                        wt_session.worktree_path, wt_session.original_head_commit
                    ):
                        out = (
                            f"{out}\n\n[Worktree Saved] Changes preserved in branch "
                            f"'{wt_session.worktree_branch}' at {wt_session.worktree_path}."
                        )
                    else:
                        wt_mgr.exit_session(wt_session, discard_changes=True)
                return out
            except Exception as run_err:
                if wt_session and wt_mgr:
                    if not has_worktree_changes(
                        wt_session.worktree_path, wt_session.original_head_commit
                    ):
                        wt_mgr.exit_session(wt_session, discard_changes=True)
                raise run_err

        # 3. Asynchronous background execution
        if run_in_background:
            self.task_manager.launch(
                task_id=task_id,
                name=task_name,
                coro=_run(),
            )
            return ToolResult(
                content=f"SubAgent 后台任务 '{task_name}' (ID: {task_id}) 已成功启动。任务完成后将通过通知回传结果。",
                metadata={"task_id": task_id, "async": True},
            )

        # 4. Synchronous foreground execution with 120s timeout handover
        coro_task = asyncio.create_task(_run())
        try:
            result = await asyncio.wait_for(coro_task, timeout=120.0)
            return ToolResult(content=result, metadata={"task_id": task_id})
        except (TimeoutError, asyncio.TimeoutError):
            self.task_manager.adopt_running(
                task_id=task_id,
                name=task_name,
                running_task=coro_task,
            )
            return ToolResult(
                content=f"SubAgent 任务 '{task_name}' 前台执行超过 120s，已平滑移交后台运行 (ID: {task_id})。执行完毕后将以通知形式回传结果。",
                metadata={"task_id": task_id, "adopted": True},
            )
        except Exception as e:
            return ToolResult(
                content=f"SubAgent 执行异常: {e}",
                is_error=True,
                metadata={"task_id": task_id},
            )
