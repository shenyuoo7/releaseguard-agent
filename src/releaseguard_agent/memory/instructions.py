"""Layer 1: Project instruction loader with recursive @include expansion and sandbox protection."""

from pathlib import Path


def process_includes(
    content: str,
    base_dir: Path,
    workspace_root: Path,
    depth: int = 0,
    visited: set[Path] | None = None,
) -> str:
    """Expand @include directives recursively with cycle detection and sandbox boundary checks.

    Constraints:
    - Recursion depth limit: <= 5
    - Boundary check: resolved target path must remain inside workspace_root
    - Cycle check: already visited paths are skipped
    """
    if depth >= 5:
        return content

    visited = visited if visited is not None else set()
    result_lines: list[str] = []
    resolved_root = workspace_root.resolve()

    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("@include "):
            rel_path = trimmed[len("@include ") :].strip()
            # Strip quotes if present (e.g. @include "./foo.md")
            if (rel_path.startswith('"') and rel_path.endswith('"')) or (
                rel_path.startswith("'") and rel_path.endswith("'")
            ):
                rel_path = rel_path[1:-1].strip()

            target_path = (base_dir / rel_path).resolve()

            # 1. Sandbox traversal check
            try:
                is_within_root = target_path.is_relative_to(resolved_root)
            except ValueError:
                is_within_root = False

            if not is_within_root:
                result_lines.append("<!-- @include blocked: path outside project -->")
                continue

            # 2. Cycle detection check
            if target_path in visited:
                continue

            # 3. File existence check
            if not target_path.is_file():
                result_lines.append(f"<!-- @include missing: {rel_path} -->")
                continue

            visited.add(target_path)
            try:
                sub_content = target_path.read_text(encoding="utf-8")
                expanded = process_includes(
                    sub_content,
                    base_dir=target_path.parent,
                    workspace_root=workspace_root,
                    depth=depth + 1,
                    visited=visited,
                )
                result_lines.append(expanded)
            except Exception as e:
                result_lines.append(f"<!-- @include error reading {rel_path}: {e} -->")
        else:
            result_lines.append(line)

    return "\n".join(result_lines)


def load_project_instructions(
    workspace_root: Path,
    user_home: Path | None = None,
) -> str:
    """Load and concatenate multi-tier instructions in priority order.

    Hierarchy:
    1. Project root `RELEASEGUARD.md`
    2. Project local `.releaseguard/RELEASEGUARD.md`
    3. User global `~/.releaseguard/RELEASEGUARD.md`

    Higher priority instructions appear first, concatenated with `---`.
    """
    home = (user_home or Path.home()).resolve()
    ws_root = workspace_root.resolve()

    candidates: list[tuple[Path, Path]] = [
        (ws_root / "RELEASEGUARD.md", ws_root),
        (ws_root / ".releaseguard" / "RELEASEGUARD.md", ws_root),
        (home / ".releaseguard" / "RELEASEGUARD.md", home / ".releaseguard"),
    ]

    parts: list[str] = []
    seen_files: set[Path] = set()

    for file_path, allowed_root in candidates:
        resolved_file = file_path.resolve()
        if resolved_file in seen_files or not resolved_file.is_file():
            continue
        seen_files.add(resolved_file)

        try:
            content = resolved_file.read_text(encoding="utf-8")
            visited_in_tree: set[Path] = {resolved_file}
            expanded = process_includes(
                content=content,
                base_dir=resolved_file.parent,
                workspace_root=allowed_root,
                depth=0,
                visited=visited_in_tree,
            )
            if expanded.strip():
                parts.append(expanded.strip())
        except Exception:
            pass

    return "\n\n---\n\n".join(parts)
