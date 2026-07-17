from pathlib import Path

import pytest

from releaseguard_agent.services.local_project_picker import (
    LocalProjectError,
    inspect_local_project,
)


def test_inspect_project_supports_spaces_and_unicode(tmp_path: Path) -> None:
    root = tmp_path / "ReleaseGuard"
    project = tmp_path / "中文 project"
    root.mkdir()
    project.mkdir()
    (project / "app.py").write_text("print('ok')\n", encoding="utf-8")

    info = inspect_local_project(f'"{project}"', releaseguard_root=root)

    assert info.name == "中文 project"
    assert info.file_count == 1
    assert info.path == str(project.resolve())


def test_inspect_project_rejects_missing_path_and_releaseguard_root(tmp_path: Path) -> None:
    root = tmp_path / "ReleaseGuard"
    root.mkdir()

    with pytest.raises(LocalProjectError, match="不存在"):
        inspect_local_project(str(tmp_path / "missing"), releaseguard_root=root)
    with pytest.raises(LocalProjectError, match="不能选择"):
        inspect_local_project(str(root), releaseguard_root=root)
