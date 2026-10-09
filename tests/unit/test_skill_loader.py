from pathlib import Path

from releaseguard_agent.skills.loader import SkillLoader, parse_skill_file
from releaseguard_agent.skills.models import SkillContextMode, SkillMode


def test_parse_skill_file_frontmatter(tmp_path: Path) -> None:
    """T1 & T2: Parse YAML frontmatter metadata and SOP markdown body."""
    skill_file = tmp_path / "custom.md"
    skill_file.write_text(
        """---
name: custom-audit
description: Custom code auditing SOP
allowedTools:
  - read_file
  - grep
mode: fork
context: none
---
# Audit Instructions
Please inspect code carefully.
$ARGUMENTS
""",
        encoding="utf-8",
    )

    skill = parse_skill_file(skill_file)
    assert skill is not None
    assert skill.name == "custom-audit"
    assert skill.description == "Custom code auditing SOP"
    assert skill.allowed_tools == ("read_file", "grep")
    assert skill.mode == SkillMode.FORK
    assert skill.context == SkillContextMode.NONE
    assert "Please inspect code carefully." in skill.prompt_body

    rendered = skill.render_prompt("Focus on auth module")
    assert "Focus on auth module" in rendered


def test_parse_directory_based_skill_with_tool_json(tmp_path: Path) -> None:
    """F1 & F2: Parse directory skill with SKILL.md and exclusive tool.json."""
    skill_dir = tmp_path / "custom-deploy"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: custom-deploy
description: Deployment skill
mode: inline
---
Deploy the project to staging.
""",
        encoding="utf-8",
    )
    (skill_dir / "tool.json").write_text(
        """[
  {"name": "deploy_staging", "description": "Deploy to staging cluster"}
]""",
        encoding="utf-8",
    )

    skill = parse_skill_file(skill_dir / "SKILL.md")
    assert skill is not None
    assert skill.name == "custom-deploy"
    assert len(skill.exclusive_tools) == 1
    assert skill.exclusive_tools[0]["name"] == "deploy_staging"


def test_three_tier_priority_override_and_hot_reload(tmp_path: Path) -> None:
    """T2, F3 & AC1: Project overrides User, User overrides Builtin; hot reload without restart."""
    ws = tmp_path / "project"
    user_home = tmp_path / "user_home"
    builtin_dir = tmp_path / "builtin"

    for d in (
        ws / ".releaseguard" / "skills",
        user_home / ".releaseguard" / "skills",
        builtin_dir,
    ):
        d.mkdir(parents=True)

    # 1. Builtin level
    (builtin_dir / "review.md").write_text(
        "---\nname: review\ndescription: Builtin review\n---\nBuiltin SOP",
        encoding="utf-8",
    )
    # 2. User level
    (user_home / ".releaseguard" / "skills" / "review.md").write_text(
        "---\nname: review\ndescription: User review\n---\nUser SOP",
        encoding="utf-8",
    )
    # 3. Project level
    proj_review = ws / ".releaseguard" / "skills" / "review.md"
    proj_review.write_text(
        "---\nname: review\ndescription: Project review\n---\nProject SOP v1",
        encoding="utf-8",
    )

    loader = SkillLoader(
        workspace_root=ws, user_home=user_home, builtin_dir=builtin_dir
    )

    # Check project-level won
    skill = loader.load_skill("review")
    assert skill is not None
    assert skill.description == "Project review"
    assert "Project SOP v1" in skill.prompt_body

    # Hot reload: modify file on disk, verify immediate update without restarting
    proj_review.write_text(
        "---\nname: review\ndescription: Project review updated\n---\nProject SOP v2 (hot reloaded)",
        encoding="utf-8",
    )
    skill_reloaded = loader.load_skill("review")
    assert skill_reloaded is not None
    assert skill_reloaded.description == "Project review updated"
    assert "Project SOP v2 (hot reloaded)" in skill_reloaded.prompt_body


def test_builtin_skills_discoverable(tmp_path: Path) -> None:
    """F7: Builtin review, test, fix-deps skills are loaded properly."""
    loader = SkillLoader(workspace_root=tmp_path)
    skills = loader.load_all()

    assert "review" in skills
    assert "test" in skills
    assert "fix-deps" in skills

    review_skill = skills["review"]
    assert review_skill.mode == SkillMode.FORK
    assert "read_file" in review_skill.allowed_tools
