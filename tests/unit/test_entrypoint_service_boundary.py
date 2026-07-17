import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINTS = (
    PROJECT_ROOT / "src" / "releaseguard_agent" / "cli" / "main.py",
    PROJECT_ROOT / "src" / "releaseguard_agent" / "api" / "app.py",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_cli_and_api_use_public_service_boundary() -> None:
    for entrypoint in ENTRYPOINTS:
        modules = _imports(entrypoint)
        assert "releaseguard_agent.services" in modules
        assert not any(
            module.startswith(
                (
                    "releaseguard_agent.checkers",
                    "releaseguard_agent.reports",
                    "releaseguard_agent.agents",
                )
            )
            for module in modules
        )
