"""Environment context collector for ReleaseGuard Agent."""

from pathlib import Path
import platform


def get_environment_context(effective_cwd: Path | str) -> str:
    """Collect stable, deterministic environment context without timestamps or random tokens.

    Guarantees byte stability for prompt caching across calls with the same parameters.
    """
    resolved_cwd = Path(effective_cwd).resolve()
    # Normalize to forward slashes for cross-platform stability
    normalized_cwd = resolved_cwd.as_posix()

    os_name = platform.system()
    python_ver = platform.python_version()

    return (
        f"- 操作系统: {os_name}\n"
        f"- Python 版本: {python_ver}\n"
        f"- 工作目录: {normalized_cwd}"
    )
