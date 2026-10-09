"""Thread-safe command registry with alias resolution and tab-completion support."""

import threading
from typing import Any

from releaseguard_agent.commands.models import Command, CommandType


class CommandRegistry:
    """Thread-safe registration and resolution center for slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._aliases: dict[str, str] = {}  # alias -> primary_name
        self._lock = threading.RLock()

    def register(self, command: Command) -> None:
        """Register a command ensuring no name or alias collisions."""
        with self._lock:
            name = command.name.lower()
            if name in self._commands or name in self._aliases:
                raise ValueError(f"Command or alias '{name}' is already registered.")

            # Check aliases
            normalized_aliases: list[str] = []
            for alias in command.aliases:
                a = alias.lower()
                if a in normalized_aliases:
                    raise ValueError(f"Duplicate alias '{a}' within command '{name}'.")
                if a in self._commands or a in self._aliases:
                    raise ValueError(
                        f"Alias collision: '{a}' is already registered by another command."
                    )
                normalized_aliases.append(a)

            self._commands[name] = command
            for a in normalized_aliases:
                self._aliases[a] = name

    def unregister(self, name: str) -> bool:
        """Remove a command and its associated aliases."""
        with self._lock:
            key = name.lower().lstrip("/")
            primary_name = self._aliases.get(key, key)
            if primary_name in self._commands:
                cmd = self._commands.pop(primary_name)
                for a in cmd.aliases:
                    self._aliases.pop(a.lower(), None)
                return True
            return False

    def find(self, name: str) -> Command | None:
        """Find command by primary name or alias (case-insensitive)."""
        with self._lock:
            key = name.lower().lstrip("/")
            if key in self._commands:
                return self._commands[key]
            if key in self._aliases:
                primary = self._aliases[key]
                return self._commands.get(primary)
            return None

    def list_commands(self, include_hidden: bool = False) -> list[Command]:
        """List all registered commands sorted by primary name."""
        with self._lock:
            cmds = [
                c for c in self._commands.values() if include_hidden or not c.hidden
            ]
            return sorted(cmds, key=lambda c: c.name)

    def complete(self, prefix: str) -> list[str]:
        """Return sorted candidate completions starting with '/' for a given prefix."""
        with self._lock:
            p = prefix.lstrip("/").lower()
            candidates: set[str] = set()

            for cmd in self._commands.values():
                if cmd.hidden:
                    continue
                if cmd.name.lower().startswith(p):
                    candidates.add(f"/{cmd.name}")
                for alias in cmd.aliases:
                    if alias.lower().startswith(p):
                        candidates.add(f"/{alias}")

            return sorted(candidates)

    def register_skills(self, skills: Any) -> None:
        """Register loaded skills as slash commands, annotated with [skill]."""
        with self._lock:
            items = skills.values() if isinstance(skills, dict) else skills
            for skill in items:
                cmd_name = skill.name.lower()
                if cmd_name in self._commands or cmd_name in self._aliases:
                    continue

                def _bind_handler(target_skill: Any) -> Any:
                    async def _handler(ctx: Any) -> None:
                        if ctx.ui:
                            rendered = target_skill.render_prompt(ctx.args)
                            ctx.ui.send_user_message(
                                f"【执行技能: {target_skill.name}】\n{rendered}"
                            )

                    return _handler

                cmd = Command(
                    name=cmd_name,
                    description=f"{skill.description} [skill]",
                    usage=f"/{cmd_name} [arguments]",
                    command_type=CommandType.PROMPT,
                    handler=_bind_handler(skill),
                )
                self.register(cmd)
