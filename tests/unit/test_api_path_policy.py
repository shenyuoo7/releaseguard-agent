from pathlib import Path

import pytest

from releaseguard_agent.api import (
    ProjectPathNotAllowedError,
    ProjectPathPolicy,
)


def test_path_policy_accepts_root_and_descendant(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    project = root / "project"
    project.mkdir(parents=True)
    policy = ProjectPathPolicy([root])

    assert policy.resolve_allowed(str(root)) == root.resolve()
    assert policy.resolve_allowed(str(project)) == project.resolve()


def test_path_policy_rejects_sibling_with_similar_prefix(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "project"
    sibling = tmp_path / "project-secret"
    allowed.mkdir()
    sibling.mkdir()
    policy = ProjectPathPolicy([allowed])

    with pytest.raises(ProjectPathNotAllowedError):
        policy.resolve_allowed(str(sibling))


def test_path_policy_requires_at_least_one_root() -> None:
    with pytest.raises(ValueError, match="At least one"):
        ProjectPathPolicy([])
