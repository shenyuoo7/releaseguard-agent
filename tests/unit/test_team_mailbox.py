"""Unit tests for atomic file mailbox concurrency, jitter retry, and stale lock recovery."""

import concurrent.futures
from pathlib import Path
import time

from releaseguard_agent.teams.mailbox import read_from_mailbox, write_to_mailbox


def test_basic_mailbox_write_and_read(tmp_path: Path) -> None:
    mb_dir = tmp_path / "mailbox"

    msg1 = {"from": "alice", "summary": "hello", "message": "Hi Bob"}
    assert write_to_mailbox(mb_dir, "bob", msg1) is True

    msg2 = {"from": "charlie", "summary": "update", "message": "Status green"}
    assert write_to_mailbox(mb_dir, "bob", msg2) is True

    # Read messages with clear=True
    received = read_from_mailbox(mb_dir, "bob", clear=True)
    assert len(received) == 2
    assert received[0]["from"] == "alice"
    assert received[1]["from"] == "charlie"

    # Subsequent read is empty
    empty = read_from_mailbox(mb_dir, "bob", clear=True)
    assert len(empty) == 0


def test_concurrent_mailbox_writes_zero_data_loss(tmp_path: Path) -> None:
    mb_dir = tmp_path / "mailbox"
    num_writers = 20

    def _send(idx: int) -> bool:
        return write_to_mailbox(
            mb_dir,
            "coordinator",
            {
                "from": f"worker-{idx}",
                "summary": f"Report {idx}",
                "message": f"Data {idx}",
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(_send, range(num_writers)))

    assert all(results)

    received = read_from_mailbox(mb_dir, "coordinator", clear=True)
    assert len(received) == num_writers
    senders = {m["from"] for m in received}
    assert len(senders) == num_writers


def test_stale_lock_cleanup(tmp_path: Path) -> None:
    mb_dir = tmp_path / "mailbox"
    mb_dir.mkdir(parents=True, exist_ok=True)
    lock_file = mb_dir / "target.lock"

    # Create an artificially stale lock (> 10s old)
    lock_file.write_text("dummy", encoding="utf-8")
    old_time = time.time() - 15.0
    import os

    os.utime(lock_file, (old_time, old_time))

    # Writing should safely detect and clean the stale lock, succeeding
    ok = write_to_mailbox(
        mb_dir, "target", {"from": "sys", "summary": "ping", "message": "pong"}
    )
    assert ok is True

    msgs = read_from_mailbox(mb_dir, "target")
    assert len(msgs) == 1
    assert msgs[0]["summary"] == "ping"
