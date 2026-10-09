"""Layer 2: Path sandbox and symlink traversal escape defense."""

import os
from pathlib import Path
from typing import Sequence


def _is_subpath(target: Path, root: Path) -> bool:
    """Check if target path is equal to or located strictly inside root path."""
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def is_path_confined(
    target_path: str | Path,
    allowed_roots: Sequence[str | Path],
) -> bool:
    """Verify that target_path resolves strictly within at least one allowed root directory.

    Guards against directory traversal (../..) and symlink breakouts.
    Follows Fail-Closed: returns False on any ambiguity, empty string, or resolution failure.
    """
    if not target_path or not allowed_roots:
        return False

    try:
        raw_target = Path(target_path)
    except Exception:
        return False

    # Normalize allowed roots to resolved realpaths
    normalized_roots: list[Path] = []
    for r in allowed_roots:
        try:
            resolved_r = Path(os.path.realpath(os.path.abspath(str(r))))
            normalized_roots.append(resolved_r)
        except Exception:
            continue

    if not normalized_roots:
        return False

    primary_root = normalized_roots[0]

    # Resolve target path
    try:
        # If relative, resolve against the primary allowed root
        if not raw_target.is_absolute():
            absolute_target = primary_root / raw_target
        else:
            absolute_target = raw_target

        # Trace existing ancestors for files that do not exist yet (e.g. WriteFile)
        curr = absolute_target
        non_existent_parts: list[str] = []
        while not os.path.lexists(curr) and curr.parent != curr:
            non_existent_parts.append(curr.name)
            curr = curr.parent

        real_ancestor = Path(os.path.realpath(curr))
        # Re-attach parts in forward order
        real_target = real_ancestor
        for part in reversed(non_existent_parts):
            real_target = real_target / part

        # Final check if real_target resolves within any allowed root
        for root in normalized_roots:
            if _is_subpath(real_target, root):
                return True

        return False
    except Exception:
        # Fail-closed
        return False
