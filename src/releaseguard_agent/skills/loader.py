"""Three-tier skill package loader with YAML frontmatter parsing and hot-reloading."""

import json
from pathlib import Path
from typing import Any
import yaml

from releaseguard_agent.skills.models import SkillContextMode, SkillDef, SkillMode


def parse_skill_file(file_path: Path) -> SkillDef | None:
    """Parse a SKILL.md file or standalone .md file into a SkillDef."""
    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except Exception:
        return None

    if not raw_text.startswith("---"):
        return None

    parts = raw_text.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter_raw = parts[1].strip()
    body = parts[2].strip()

    try:
        meta = yaml.safe_load(frontmatter_raw)
        if not isinstance(meta, dict):
            return None
    except Exception:
        return None

    name = str(meta.get("name") or file_path.parent.name or file_path.stem).lower()
    desc = str(meta.get("description") or "").strip()

    # Parse allowed tools
    raw_tools = meta.get("allowedTools") or meta.get("allowed_tools") or []
    if isinstance(raw_tools, list):
        allowed_tools = tuple(str(t) for t in raw_tools)
    else:
        allowed_tools = ()

    # Mode
    mode_str = str(meta.get("mode", "inline")).lower()
    mode = SkillMode.FORK if mode_str == "fork" else SkillMode.INLINE

    # Context mode
    ctx_str = str(meta.get("context", "full")).lower()
    if ctx_str == "none":
        context_mode = SkillContextMode.NONE
    elif ctx_str == "recent":
        context_mode = SkillContextMode.RECENT
    else:
        context_mode = SkillContextMode.FULL

    model = meta.get("model")

    # Check for exclusive tools in tool.json or tools.json
    exclusive_tools: list[dict[str, Any]] = []
    parent_dir = file_path.parent
    for tool_file_name in ("tool.json", "tools.json"):
        t_path = parent_dir / tool_file_name
        if t_path.is_file():
            try:
                data = json.loads(t_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    exclusive_tools.extend(data)
                elif isinstance(data, dict):
                    exclusive_tools.append(data)
            except Exception:
                pass

    return SkillDef(
        name=name,
        description=desc,
        prompt_body=body,
        allowed_tools=allowed_tools,
        model=model,
        mode=mode,
        context=context_mode,
        exclusive_tools=tuple(exclusive_tools),
        file_path=file_path,
    )


class SkillLoader:
    """Discovers and hot-reloads skills across Project, User, and Builtin directories."""

    def __init__(
        self,
        workspace_root: Path,
        user_home: Path | None = None,
        builtin_dir: Path | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.user_home = (user_home or Path.home()).resolve()
        self.builtin_dir = (
            builtin_dir.resolve() if builtin_dir else Path(__file__).parent / "builtin"
        )

    def _get_search_dirs(self) -> list[tuple[str, Path]]:
        """Return search paths in descending priority: Project > User > Built-in."""
        return [
            ("project", self.workspace_root / ".releaseguard" / "skills"),
            ("user", self.user_home / ".releaseguard" / "skills"),
            ("builtin", self.builtin_dir),
        ]

    def load_all(self) -> dict[str, SkillDef]:
        """Load all skills across all tiers. Higher priority directories override lower tiers."""
        skills: dict[str, SkillDef] = {}

        # Scan in ascending priority so that higher priorities overwrite lower priorities
        search_dirs = list(reversed(self._get_search_dirs()))

        for tier, base_dir in search_dirs:
            if not base_dir.is_dir():
                continue

            # 1. Directory-based skills: subdirs containing SKILL.md
            for sub in base_dir.iterdir():
                if sub.is_dir():
                    for target_file in ("SKILL.md", "skill.md"):
                        p = sub / target_file
                        if p.is_file():
                            s = parse_skill_file(p)
                            if s:
                                skills[s.name] = s
                            break

                # 2. Standalone file-based skills: *.md directly under base_dir
                elif sub.is_file() and sub.suffix.lower() == ".md":
                    s = parse_skill_file(sub)
                    if s:
                        skills[s.name] = s

        return skills

    def load_skill(self, name: str) -> SkillDef | None:
        """Find and hot-reload a single skill by name checking Project -> User -> Built-in."""
        target_name = name.lower().strip()
        search_dirs = self._get_search_dirs()

        for tier, base_dir in search_dirs:
            if not base_dir.is_dir():
                continue

            # Check directory-based skill
            dir_candidate = base_dir / target_name
            if dir_candidate.is_dir():
                for target_file in ("SKILL.md", "skill.md"):
                    p = dir_candidate / target_file
                    if p.is_file():
                        s = parse_skill_file(p)
                        if s and s.name == target_name:
                            return s

            # Check file-based skill
            file_candidate = base_dir / f"{target_name}.md"
            if file_candidate.is_file():
                s = parse_skill_file(file_candidate)
                if s and s.name == target_name:
                    return s

        return None
