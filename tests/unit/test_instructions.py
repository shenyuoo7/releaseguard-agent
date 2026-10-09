from pathlib import Path

from releaseguard_agent.memory.instructions import (
    load_project_instructions,
    process_includes,
)


def test_process_includes_basic_expansion(tmp_path: Path) -> None:
    """T1 & AC1: Standard @include directives are recursively expanded."""
    sub_dir = tmp_path / "docs"
    sub_dir.mkdir()
    (sub_dir / "rules.md").write_text(
        "- Rule 1: Always use pytest\n- Rule 2: Keep functions small", encoding="utf-8"
    )
    (sub_dir / "style.md").write_text(
        "@include ./rules.md\n- Style: PEP8", encoding="utf-8"
    )

    main_content = "# Guidelines\n@include ./docs/style.md\n# End"
    expanded = process_includes(
        main_content, base_dir=tmp_path, workspace_root=tmp_path
    )

    assert "- Rule 1: Always use pytest" in expanded
    assert "- Style: PEP8" in expanded
    assert "# End" in expanded


def test_process_includes_cycle_prevention(tmp_path: Path) -> None:
    """AC1: Mutual recursive includes do not cause infinite loops."""
    file_a = tmp_path / "a.md"
    file_b = tmp_path / "b.md"

    file_a.write_text("Header A\n@include ./b.md\nFooter A", encoding="utf-8")
    file_b.write_text("Header B\n@include ./a.md\nFooter B", encoding="utf-8")

    expanded = process_includes(
        content=file_a.read_text(encoding="utf-8"),
        base_dir=tmp_path,
        workspace_root=tmp_path,
    )

    assert "Header A" in expanded
    assert "Header B" in expanded
    # Should terminate cleanly without recursion error


def test_process_includes_sandbox_path_traversal_blocked(tmp_path: Path) -> None:
    """AC1: Attempts to escape workspace root via @include are blocked."""
    outside_dir = tmp_path.parent / "secret"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "passwords.txt").write_text("secret_password_123", encoding="utf-8")

    ws_root = tmp_path / "project"
    ws_root.mkdir()
    main_file = ws_root / "RELEASEGUARD.md"
    main_file.write_text(
        "Project rules\n@include ../../secret/passwords.txt\nMore rules",
        encoding="utf-8",
    )

    expanded = process_includes(
        content=main_file.read_text(encoding="utf-8"),
        base_dir=ws_root,
        workspace_root=ws_root,
    )

    assert "secret_password_123" not in expanded
    assert "<!-- @include blocked: path outside project -->" in expanded
    assert "More rules" in expanded


def test_process_includes_depth_limit(tmp_path: Path) -> None:
    """AC1: Recursion depth limit of 5 is strictly enforced."""
    current_dir = tmp_path
    for i in range(7):
        next_file = f"step_{i + 1}.md"
        content = f"Level {i}\n@include ./{next_file}"
        (current_dir / f"step_{i}.md").write_text(content, encoding="utf-8")

    root_content = (tmp_path / "step_0.md").read_text(encoding="utf-8")
    expanded = process_includes(
        root_content, base_dir=tmp_path, workspace_root=tmp_path
    )

    # Should expand up to depth 5 and not crash
    assert "Level 0" in expanded
    assert "Level 4" in expanded


def test_load_project_instructions_multi_tier_ordering(tmp_path: Path) -> None:
    """F1: Project root, project local, and user global are loaded in order separated by ---."""
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    user_home = tmp_path / "user_home"
    user_home.mkdir()

    # 1. Project root
    (ws_root / "RELEASEGUARD.md").write_text(
        "# Project Root Instructions", encoding="utf-8"
    )

    # 2. Project local
    local_dir = ws_root / ".releaseguard"
    local_dir.mkdir()
    (local_dir / "RELEASEGUARD.md").write_text(
        "# Local Project Config", encoding="utf-8"
    )

    # 3. User global
    global_dir = user_home / ".releaseguard"
    global_dir.mkdir()
    (global_dir / "RELEASEGUARD.md").write_text(
        "# Global User Preferences", encoding="utf-8"
    )

    instructions = load_project_instructions(
        workspace_root=ws_root, user_home=user_home
    )

    assert "# Project Root Instructions" in instructions
    assert "# Local Project Config" in instructions
    assert "# Global User Preferences" in instructions

    # Ordering check: Project Root before Local Config before Global Preferences
    idx_root = instructions.index("# Project Root Instructions")
    idx_local = instructions.index("# Local Project Config")
    idx_global = instructions.index("# Global User Preferences")
    assert idx_root < idx_local < idx_global

    assert "---" in instructions
