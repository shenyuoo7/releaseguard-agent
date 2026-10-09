"""Active skill manager pinning SOPs to environment context and system tool LoadSkill."""

from typing import Any

from releaseguard_agent.skills.loader import SkillLoader
from releaseguard_agent.skills.models import SkillDef
from releaseguard_agent.tools.base import BaseTool, ToolContext, ToolResult


class ActiveSkillManager:
    """Tracks active skills in current session and generates pinned SOP context."""

    def __init__(self, loader: SkillLoader) -> None:
        self.loader = loader
        self._active_skills: dict[str, SkillDef] = {}

    def activate(self, skill_name: str, arguments: str = "") -> tuple[bool, str]:
        """Activate a skill by name using hot-reloading from loader."""
        name = skill_name.strip().lower()
        skill = self.loader.load_skill(name)
        if not skill:
            return (
                False,
                f"未找到名为 '{name}' 的技能包。请确认技能是否存在于项目、用户或内置目录中。",
            )

        self._active_skills[skill.name] = skill
        return (
            True,
            f"✅ 技能 '{skill.name}' 已成功激活！已将其标准操作流程 (SOP) 钉入当前环境上下文。",
        )

    def deactivate(self, skill_name: str) -> bool:
        """Deactivate a skill."""
        name = skill_name.strip().lower()
        if name in self._active_skills:
            del self._active_skills[name]
            return True
        return False

    def clear_active_skills(self) -> None:
        """Reset all active skills (e.g. upon /clear)."""
        self._active_skills.clear()

    def get_active_skills(self) -> list[SkillDef]:
        """Return list of active SkillDefs."""
        return list(self._active_skills.values())

    def pin_to_env_context(self, arguments_map: dict[str, str] | None = None) -> str:
        """Render active skill SOPs to be pinned at the top of the environment context."""
        if not self._active_skills:
            return ""

        arg_map = arguments_map or {}
        sections: list[str] = ["# 当前激活的专业技能包 (Active Skills)", ""]
        for skill in self._active_skills.values():
            user_arg = arg_map.get(skill.name, "")
            rendered = skill.render_prompt(user_arg)
            sections.append(f"## 技能: {skill.name}")
            sections.append(f"> {skill.description}\n")
            sections.append(rendered)
            sections.append("\n---\n")

        return "\n".join(sections).strip()


class LoadSkillTool(BaseTool):
    """System tool allowing the agent or user to activate skill SOPs on-demand."""

    def __init__(self, skill_manager: ActiveSkillManager) -> None:
        self.mgr = skill_manager

    @property
    def name(self) -> str:
        return "LoadSkill"

    @property
    def description(self) -> str:
        return "按需激活指定的 Skill 技能包，将其详细标准操作流程(SOP)加载到环境中。"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要激活的技能名称 (例如: review, test, fix-deps)",
                }
            },
            "required": ["name"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        skill_name = str(arguments.get("name", "")).strip()
        if not skill_name:
            return ToolResult(
                content="缺少必须的参数 'name'。请提供要激活的技能名称。",
                is_error=True,
            )

        success, msg = self.mgr.activate(skill_name)
        return ToolResult(content=msg, is_error=not success)
