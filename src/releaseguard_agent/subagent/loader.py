"""Loader and parser for specialized SubAgent markdown definitions."""

from pathlib import Path
import sys
from typing import Any

import yaml

from releaseguard_agent.subagent.models import AgentDefinition


def parse_agent_file(path: Path) -> AgentDefinition | None:
    """Parse an agent definition markdown file with YAML frontmatter."""
    if not path.is_file():
        return None

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        sys.stderr.write(f"[SubAgent Loader Warning] Failed to read {path}: {e}\n")
        return None

    frontmatter_dict: dict[str, Any] = {}
    system_prompt = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_fm = parts[1]
            system_prompt = parts[2].strip()
            try:
                parsed = yaml.safe_load(raw_fm)
                if isinstance(parsed, dict):
                    frontmatter_dict = parsed
            except Exception as e:
                sys.stderr.write(
                    f"[SubAgent Loader Warning] Invalid YAML frontmatter in {path}: {e}\n"
                )

    name = str(frontmatter_dict.get("name") or path.stem).strip()
    description = str(frontmatter_dict.get("description", "")).strip()

    raw_tools = frontmatter_dict.get("tools", ())
    tools = (
        tuple(str(t) for t in raw_tools) if isinstance(raw_tools, (list, tuple)) else ()
    )

    raw_disallowed = frontmatter_dict.get(
        "disallowedTools", frontmatter_dict.get("disallowed_tools", ())
    )
    disallowed_tools = (
        tuple(str(t) for t in raw_disallowed)
        if isinstance(raw_disallowed, (list, tuple))
        else ()
    )

    model = frontmatter_dict.get("model")
    model_str = str(model) if model else None

    max_turns = int(
        frontmatter_dict.get("maxTurns", frontmatter_dict.get("max_turns", 30))
    )
    permission_mode = str(
        frontmatter_dict.get(
            "permissionMode", frontmatter_dict.get("permission_mode", "default")
        )
    )

    return AgentDefinition(
        name=name,
        description=description,
        system_prompt=system_prompt,
        tools=tools,
        disallowed_tools=disallowed_tools,
        model=model_str,
        max_turns=max_turns,
        permission_mode=permission_mode,
    )


class AgentLoader:
    """Discovers and loads agent definitions following project > user > builtin priority."""

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        )
        self.builtin_dir = Path(__file__).parent / "builtin"

    def load_agent(self, name: str) -> AgentDefinition | None:
        """Load an agent by name with hot-reloading and 3-tier precedence."""
        name_clean = name.strip().lower()
        candidates = [
            # 1. Project level: <workspace_root>/.releaseguard/agents/{name}.md
            self.workspace_root / ".releaseguard" / "agents" / f"{name_clean}.md",
            # 2. User level: ~/.releaseguard/agents/{name}.md
            Path.home() / ".releaseguard" / "agents" / f"{name_clean}.md",
            # 3. Built-in level: builtin/{name}.md
            self.builtin_dir / f"{name_clean}.md",
            # Also check matching by replacing '-' with '_'
            self.builtin_dir / f"{name_clean.replace('-', '_')}.md",
        ]

        for p in candidates:
            if p.is_file():
                agent_def = parse_agent_file(p)
                if agent_def:
                    return agent_def

        # If not found directly, scan all to match case-insensitively on name
        all_agents = self.load_all()
        return all_agents.get(name_clean)

    def load_all(self) -> dict[str, AgentDefinition]:
        """Load all discoverable agents merged across the 3 tiers."""
        agents: dict[str, AgentDefinition] = {}

        # 1. Built-in tier
        if self.builtin_dir.is_dir():
            for p in self.builtin_dir.glob("*.md"):
                item = parse_agent_file(p)
                if item:
                    agents[item.name.lower()] = item

        # 2. User tier
        user_dir = Path.home() / ".releaseguard" / "agents"
        if user_dir.is_dir():
            for p in user_dir.glob("*.md"):
                item = parse_agent_file(p)
                if item:
                    agents[item.name.lower()] = item

        # 3. Project tier
        proj_dir = self.workspace_root / ".releaseguard" / "agents"
        if proj_dir.is_dir():
            for p in proj_dir.glob("*.md"):
                item = parse_agent_file(p)
                if item:
                    agents[item.name.lower()] = item

        return agents
