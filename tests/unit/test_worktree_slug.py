"""Unit tests for Worktree slug validation and naming sanitation."""

from releaseguard_agent.worktree.slug import (
    slug_to_branch_name,
    slug_to_dir_name,
    validate_slug,
)


def test_validate_slug_rejects_empty_or_whitespace() -> None:
    assert validate_slug("") == "name cannot be empty"
    assert validate_slug("   ") == "name cannot be empty"


def test_validate_slug_rejects_too_long() -> None:
    long_name = "a" * 65
    assert validate_slug(long_name) == "name too long (max 64 chars)"


def test_validate_slug_rejects_directory_traversal() -> None:
    assert validate_slug(".") == "name must not contain '.' or '..'"
    assert validate_slug("..") == "name must not contain '.' or '..'"
    assert validate_slug("../../etc/passwd") == "name must not contain '.' or '..'"
    assert validate_slug("foo/../bar") == "name must not contain '.' or '..'"
    assert validate_slug("foo/./bar") == "name must not contain '.' or '..'"


def test_validate_slug_rejects_invalid_characters() -> None:
    assert validate_slug("foo@bar") is not None
    assert validate_slug("foo*bar") is not None
    assert validate_slug("foo bar") is not None
    assert validate_slug("foo$bar") is not None


def test_validate_slug_accepts_valid_names() -> None:
    assert validate_slug("feature-1") is None
    assert validate_slug("agent-a3f2b1c") is None
    assert validate_slug("team-refactor/alice") is None
    assert validate_slug("fix_deps.v2") is None


def test_slug_to_branch_and_dir_name_conversion() -> None:
    slug = "team-refactor/alice"
    assert slug_to_branch_name(slug) == "worktree-team-refactor+alice"
    assert slug_to_dir_name(slug) == "team-refactor+alice"
