import io
import os
from pathlib import Path
import sys

from releaseguard_agent.mcp.config import (
    expand_env_vars,
    load_mcp_config,
)


def test_expand_env_vars() -> None:
    """T2 & AC1: Verify ${VAR} expansion with existing and undefined environment variables."""
    os.environ["RELEASEGUARD_TEST_TOKEN"] = "token_xyz_123"

    # Existing variable
    val = expand_env_vars("Bearer ${RELEASEGUARD_TEST_TOKEN}")
    assert val == "Bearer token_xyz_123"

    # Undefined variable should be replaced with empty string and trigger warning
    warned: set[str] = set()
    old_stderr = sys.stderr
    sys.stderr = capture_stderr = io.StringIO()
    try:
        missing_val = expand_env_vars(
            "Bearer ${RELEASEGUARD_MISSING_VAR}", warned_vars=warned
        )
        assert missing_val == "Bearer "
        assert "RELEASEGUARD_MISSING_VAR" in warned
        assert "Warning" in capture_stderr.getvalue()
    finally:
        sys.stderr = old_stderr
        del os.environ["RELEASEGUARD_TEST_TOKEN"]


def test_load_mcp_config_merge_and_override(tmp_path: Path) -> None:
    """T2 & AC1: Project-level server config must completely override user-level config."""
    user_home = tmp_path / "user_home"
    project_root = tmp_path / "project_root"

    user_cfg_dir = user_home / ".releaseguard"
    project_cfg_dir = project_root / ".releaseguard"
    user_cfg_dir.mkdir(parents=True)
    project_cfg_dir.mkdir(parents=True)

    # User config: defines 'github' and 'sentry'
    user_yaml = """
mcpServers:
  github:
    type: stdio
    command: github-mcp-user
    args: ["--user-flag"]
  sentry:
    type: http
    url: "https://sentry.example.com/mcp"
    headers:
      Authorization: "Bearer user-token"
"""
    (user_cfg_dir / "config.yaml").write_text(user_yaml, encoding="utf-8")

    # Project config: overrides 'github' with project-specific command, defines 'db'
    project_yaml = """
mcpServers:
  github:
    type: stdio
    command: github-mcp-project
    args: ["--repo", "my-org/my-repo"]
  db:
    type: stdio
    command: db-mcp
"""
    (project_cfg_dir / "config.yaml").write_text(project_yaml, encoding="utf-8")

    config = load_mcp_config(project_root=project_root, user_home=user_home)

    assert "github" in config.servers
    assert "sentry" in config.servers
    assert "db" in config.servers

    # Project override confirmed
    assert config.servers["github"].command == "github-mcp-project"
    assert config.servers["github"].args == ("--repo", "my-org/my-repo")

    # User inherited server confirmed
    assert config.servers["sentry"].type == "http"
    assert config.servers["sentry"].url == "https://sentry.example.com/mcp"

    # Project new server confirmed
    assert config.servers["db"].command == "db-mcp"


def test_load_mcp_config_corrupted_yaml_graceful(tmp_path: Path) -> None:
    """T2: Corrupted YAML file produces warning and empty configuration without raising."""
    broken_dir = tmp_path / ".releaseguard"
    broken_dir.mkdir(parents=True)
    (broken_dir / "config.yaml").write_text(
        "mcpServers: [invalid yaml {:::", encoding="utf-8"
    )

    old_stderr = sys.stderr
    sys.stderr = capture_stderr = io.StringIO()
    try:
        config = load_mcp_config(project_root=tmp_path, user_home=tmp_path)
        assert len(config.servers) == 0
        assert "Warning" in capture_stderr.getvalue()
    finally:
        sys.stderr = old_stderr
