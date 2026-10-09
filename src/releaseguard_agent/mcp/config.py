"""Two-tier MCP configuration loading, validation, and variable expansion."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sys
from typing import Any, Literal
import yaml


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for a single MCP server connection."""

    name: str
    type: Literal["stdio", "http"]
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPConfig:
    """Container holding all configured MCP server definitions."""

    servers: dict[str, MCPServerConfig] = field(default_factory=dict)


_ENV_VAR_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def expand_env_vars(value: str, warned_vars: set[str] | None = None) -> str:
    """Expand ${VAR} environment variable templates.

    If an environment variable is undefined, replaces it with an empty string
    and outputs a warning to sys.stderr once per variable name.
    """
    if not value or not isinstance(value, str):
        return value

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name in os.environ:
            return os.environ[var_name]
        if warned_vars is not None and var_name not in warned_vars:
            warned_vars.add(var_name)
            sys.stderr.write(
                f"[ReleaseGuard MCP] Warning: Environment variable '{var_name}' is not set, replacing with empty string.\n"
            )
        return ""

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _expand_dict_values(data: dict[str, Any], warned_vars: set[str]) -> dict[str, str]:
    """Recursively expand environment variables in a dictionary of string values."""
    expanded: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(v, str):
            expanded[k] = expand_env_vars(v, warned_vars)
        else:
            expanded[k] = str(v)
    return expanded


def _parse_server_def(
    name: str, raw: dict[str, Any], warned_vars: set[str]
) -> MCPServerConfig | None:
    """Parse and validate a raw server configuration dictionary."""
    if not isinstance(raw, dict):
        return None

    raw_type = str(raw.get("type", "")).strip().lower()
    if raw_type not in ("stdio", "http"):
        sys.stderr.write(
            f"[ReleaseGuard MCP] Warning: Unknown server type '{raw_type}' for server '{name}'. Must be 'stdio' or 'http'.\n"
        )
        return None

    server_type: Literal["stdio", "http"] = "stdio" if raw_type == "stdio" else "http"

    if server_type == "stdio":
        command = expand_env_vars(str(raw.get("command", "")).strip(), warned_vars)
        if not command:
            sys.stderr.write(
                f"[ReleaseGuard MCP] Warning: Server '{name}' has type 'stdio' but missing 'command'.\n"
            )
            return None
        raw_args = raw.get("args", [])
        args = tuple(
            expand_env_vars(str(a), warned_vars)
            for a in (raw_args if isinstance(raw_args, list) else [])
        )
        raw_env = raw.get("env", {})
        env = _expand_dict_values(
            raw_env if isinstance(raw_env, dict) else {}, warned_vars
        )
        return MCPServerConfig(
            name=name,
            type="stdio",
            command=command,
            args=args,
            env=env,
        )
    else:
        url = expand_env_vars(str(raw.get("url", "")).strip(), warned_vars)
        if not url:
            sys.stderr.write(
                f"[ReleaseGuard MCP] Warning: Server '{name}' has type 'http' but missing 'url'.\n"
            )
            return None
        raw_headers = raw.get("headers", {})
        headers = _expand_dict_values(
            raw_headers if isinstance(raw_headers, dict) else {}, warned_vars
        )
        return MCPServerConfig(
            name=name,
            type="http",
            url=url,
            headers=headers,
        )


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Safely load YAML file, handling missing or corrupted files gracefully."""
    if not path.is_file():
        return {}
    try:
        content = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(content)
        return loaded if isinstance(loaded, dict) else {}
    except Exception as e:
        sys.stderr.write(
            f"[ReleaseGuard MCP] Warning: Failed to read MCP config at {path}: {e}\n"
        )
        return {}


def load_mcp_config(
    project_root: Path,
    user_home: Path | None = None,
) -> MCPConfig:
    """Load and merge user-level and project-level MCP configuration.

    Merges by server key: project-level server definitions completely override
    user-level ones of the same name.
    """
    home = user_home or Path.home()
    user_path = home / ".releaseguard" / "config.yaml"
    project_path = project_root / ".releaseguard" / "config.yaml"

    user_data = _load_yaml_file(user_path)
    project_data = _load_yaml_file(project_path)

    raw_user_servers = user_data.get("mcpServers", {})
    raw_project_servers = project_data.get("mcpServers", {})

    merged_raw: dict[str, dict[str, Any]] = {}
    if isinstance(raw_user_servers, dict):
        for k, v in raw_user_servers.items():
            if isinstance(v, dict):
                merged_raw[k] = v

    # Project-level overrides user-level completely for same server key
    if isinstance(raw_project_servers, dict):
        for k, v in raw_project_servers.items():
            if isinstance(v, dict):
                merged_raw[k] = v

    warned_vars: set[str] = set()
    servers: dict[str, MCPServerConfig] = {}

    for name, s_def in merged_raw.items():
        parsed = _parse_server_def(name, s_def, warned_vars)
        if parsed:
            servers[name] = parsed

    return MCPConfig(servers=servers)
