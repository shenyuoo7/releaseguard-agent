from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class LocalProjectError(ValueError):
    """Safe validation error for a locally selected project directory."""


@dataclass(frozen=True)
class LocalProjectInfo:
    name: str
    path: str
    file_count: int

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "path": self.path, "file_count": self.file_count}


class WindowsFolderPicker:
    """Open the native Windows FolderBrowserDialog in the interactive session."""

    def choose(self) -> str | None:
        if os.name != "nt":
            raise LocalProjectError("当前系统不支持 Windows 文件夹选择窗口，请使用路径输入。")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$dialog.Description='选择需要审查的项目文件夹';"
            "$dialog.ShowNewFolderButton=$false;"
            "if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)"
            "{[Console]::Out.Write($dialog.SelectedPath)}"
        )
        completed = subprocess.run(
            [
                "powershell.exe",
                "-STA",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0:
            raise LocalProjectError("无法打开文件夹选择窗口，请使用路径输入。")
        selected = completed.stdout.strip()
        return selected or None


def inspect_local_project(
    raw_path: str,
    *,
    releaseguard_root: Path,
) -> LocalProjectInfo:
    normalized = raw_path.strip().strip('"').strip("'")
    if not normalized:
        raise LocalProjectError("请选择项目文件夹。")
    path = Path(normalized).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise LocalProjectError(f"项目文件夹不存在：{path}")
    if os.path.normcase(str(path)) == os.path.normcase(
        str(Path(releaseguard_root).resolve())
    ):
        raise LocalProjectError("不能选择 ReleaseGuard 自己的根目录。")
    return LocalProjectInfo(
        name=path.name or str(path),
        path=str(path),
        file_count=_count_files(path),
    )


def _count_files(path: Path, limit: int = 200_000) -> int:
    ignored = {".git", ".venv", "node_modules", "__pycache__", ".runtime"}
    count = 0
    try:
        for root, directories, files in os.walk(path):
            directories[:] = [name for name in directories if name not in ignored]
            count += len(files)
            if count >= limit:
                return limit
    except OSError as exc:
        raise LocalProjectError("无法读取该项目文件夹。") from exc
    return count
