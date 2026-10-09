from pathlib import Path


RELEASEGUARD_BANNER = r"""
  ____      _                         ____                     _ 
 |  _ \ ___| | ___  __ _ ___  ___    / ___|_   _  __ _ _ __ __| |
 | |_) / _ \ |/ _ \/ _` / __|/ _ \  | |  _| | | |/ _` | '__/ _` |
 |  _ <  __/ |  __/ (_| \__ \  __/  | |_| | |_| | (_| | | | (_| |
 |_| \_\___|_|\___|\__,_|___/\___|   \____|\__,_|\__,_|_|  \__,_|
"""


def render_banner(version: str = "0.1.0", cwd: Path | str | None = None) -> str:
    """Render startup banner with version and working directory."""
    effective_cwd = str(cwd or Path.cwd())
    lines = [
        RELEASEGUARD_BANNER.strip("\n"),
        f" ReleaseGuard Agent v{version} | Code Quality & Release Review",
        f" Workspace: {effective_cwd}",
        " " + ("=" * 64),
    ]
    return "\n".join(lines)
