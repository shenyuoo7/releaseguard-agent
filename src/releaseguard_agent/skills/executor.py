"""Skill execution engine supporting tool whitelist narrowing, inline mode, and fork isolation."""

from releaseguard_agent.llm.client import StreamLLMClient
from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.runtime.events import (
    AgentErrorEvent,
    AgentLoopCompleteEvent,
)
from releaseguard_agent.runtime.react_engine import ReactAgentEngine
from releaseguard_agent.skills.models import SkillContextMode, SkillDef, SkillMode
from releaseguard_agent.tools.base import ToolContext
from releaseguard_agent.tools.registry import ToolRegistry


def filter_tools_for_skill(
    registry: ToolRegistry,
    skill: SkillDef,
) -> ToolRegistry:
    """Filter tools in registry according to skill.allowed_tools, exempting system tools like LoadSkill."""
    if not skill.allowed_tools:
        return registry

    allowed_lower = {t.strip().lower() for t in skill.allowed_tools if t.strip()}
    # LoadSkill is always exempted to support skill nesting
    allowed_lower.add("loadskill")

    filtered = ToolRegistry()
    for tool in registry.list_tools():
        if tool.name.lower() in allowed_lower:
            filtered.register(tool)

    return filtered


class SkillExecutor:
    """Executes a SkillDef either in inline mode or fork isolation mode."""

    def __init__(
        self,
        client: StreamLLMClient,
        registry: ToolRegistry,
    ) -> None:
        self.client = client
        self.registry = registry

    async def execute(
        self,
        skill: SkillDef,
        conversation: ConversationManager,
        arguments: str = "",
        context: ToolContext | None = None,
        max_turns: int = 15,
    ) -> tuple[str, bool]:
        """Dispatch execution based on skill.mode (inline or fork)."""
        if skill.mode == SkillMode.FORK:
            return await self.execute_fork(
                skill=skill,
                parent_conversation=conversation,
                arguments=arguments,
                context=context,
                max_turns=max_turns,
            )
        else:
            return await self.execute_inline(
                skill=skill,
                conversation=conversation,
                arguments=arguments,
                context=context,
                max_turns=max_turns,
            )

    async def execute_inline(
        self,
        skill: SkillDef,
        conversation: ConversationManager,
        arguments: str = "",
        context: ToolContext | None = None,
        max_turns: int = 15,
    ) -> tuple[str, bool]:
        """Execute skill in-place within the current active conversation."""
        prompt = skill.render_prompt(arguments)
        conversation.add_user_message(f"【激活技能: {skill.name}】\n{prompt}")

        filtered_tools = filter_tools_for_skill(self.registry, skill)
        engine = ReactAgentEngine(
            client=self.client,
            registry=filtered_tools,
            max_turns=max_turns,
        )

        final_content = ""
        success = True

        async for event in engine.run(conversation, context=context):
            if isinstance(event, AgentLoopCompleteEvent):
                final_content = event.final_content
            elif isinstance(event, AgentErrorEvent):
                final_content = f"Error: {event.error}"
                success = False

        return final_content, success

    async def execute_fork(
        self,
        skill: SkillDef,
        parent_conversation: ConversationManager,
        arguments: str = "",
        context: ToolContext | None = None,
        max_turns: int = 15,
    ) -> tuple[str, bool]:
        """Execute skill in an isolated sub-agent conversation and summarize back to parent."""
        # 1. Create isolated conversation
        forked_conv = ConversationManager()

        if skill.context == SkillContextMode.RECENT:
            parent_msgs = parent_conversation.get_messages()
            for msg in parent_msgs[-5:]:
                forked_conv.append(msg.clone())
        elif skill.context == SkillContextMode.FULL:
            parent_msgs = parent_conversation.get_messages()
            for msg in parent_msgs:
                forked_conv.append(msg.clone())
        # If NONE, leaves forked_conv empty

        # 2. Inject skill SOP prompt
        prompt = skill.render_prompt(arguments)
        forked_conv.add_user_message(
            f"【独立任务技能: {skill.name}】\n{prompt}\n\n请独立客观执行上述流程并输出最终报告。"
        )

        # 3. Restrict tools
        filtered_tools = filter_tools_for_skill(self.registry, skill)
        engine = ReactAgentEngine(
            client=self.client,
            registry=filtered_tools,
            max_turns=max_turns,
        )

        final_content = ""
        success = True

        async for event in engine.run(forked_conv, context=context):
            if isinstance(event, AgentLoopCompleteEvent):
                final_content = event.final_content
            elif isinstance(event, AgentErrorEvent):
                final_content = f"Error: {event.error}"
                success = False

        # 4. Feed back to parent conversation as single assistant report
        summary_report = f"📋 **【{skill.name} 独立任务审查报告】**\n\n{final_content}"
        parent_conversation.add_assistant_message(summary_report)

        return summary_report, success
