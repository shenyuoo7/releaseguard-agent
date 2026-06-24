import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLI_MAIN_PATH = (
    PROJECT_ROOT
    / "src"
    / "releaseguard_agent"
    / "cli"
    / "main.py"
)


def _import_modules() -> set[str]:
    tree = ast.parse(CLI_MAIN_PATH.read_text(encoding="utf-8"))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)

        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)

    return modules


def test_cli_uses_agent_package_public_api_for_agent_imports() -> None:
    modules = _import_modules()

    assert "releaseguard_agent.agents" in modules


def test_cli_does_not_import_internal_agent_decision_modules() -> None:
    modules = _import_modules()
    internal_agent_modules = sorted(
        module
        for module in modules
        if module.startswith(
            "releaseguard_agent.agents.release_decision"
        )
    )

    assert internal_agent_modules == []
