"""Asynchronous file-based mailbox system with atomic locks and random jitter retry."""

import json
import os
from pathlib import Path
import random
import time
from typing import Any


def _acquire_mailbox_lock(lock_file: Path, max_retries: int = 10) -> bool:
    """Acquire exclusive file lock using O_CREAT | O_EXCL with jitter retry and stale lock cleanup."""
    for _ in range(max_retries):
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            # Check for stale lock (> 10s old)
            try:
                if lock_file.exists() and (
                    time.time() - lock_file.stat().st_mtime > 10.0
                ):
                    lock_file.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(random.uniform(0.005, 0.1))
        except OSError:
            time.sleep(random.uniform(0.005, 0.1))

    return False


def write_to_mailbox(
    mailbox_dir: Path,
    target_id: str,
    message_dict: dict[str, Any],
) -> bool:
    """Write message to recipient mailbox with file locking and atomic replacement."""
    mailbox_dir.mkdir(parents=True, exist_ok=True)
    box_file = mailbox_dir / f"{target_id}.json"
    lock_file = mailbox_dir / f"{target_id}.lock"

    if not _acquire_mailbox_lock(lock_file):
        return False

    try:
        existing: list[dict[str, Any]] = []
        if box_file.is_file():
            try:
                content = box_file.read_text(encoding="utf-8").strip()
                if content:
                    existing = json.loads(content)
            except Exception:
                existing = []

        if "timestamp" not in message_dict:
            message_dict["timestamp"] = time.time()

        existing.append(message_dict)

        tmp_file = mailbox_dir / f"{target_id}.tmp"
        tmp_file.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp_file.replace(box_file)
        return True
    finally:
        try:
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass


def read_from_mailbox(
    mailbox_dir: Path,
    target_id: str,
    clear: bool = True,
) -> list[dict[str, Any]]:
    """Read pending messages from mailbox with atomic lock protection."""
    box_file = mailbox_dir / f"{target_id}.json"
    lock_file = mailbox_dir / f"{target_id}.lock"

    if not box_file.is_file():
        return []

    if not _acquire_mailbox_lock(lock_file):
        return []

    try:
        messages: list[dict[str, Any]] = []
        try:
            content = box_file.read_text(encoding="utf-8").strip()
            if content:
                messages = json.loads(content)
        except Exception:
            messages = []

        if clear:
            tmp_file = mailbox_dir / f"{target_id}.tmp"
            tmp_file.write_text("[]", encoding="utf-8")
            tmp_file.replace(box_file)

        return messages
    finally:
        try:
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass
