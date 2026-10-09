import json
from pathlib import Path
import time

from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.memory.session import (
    TIME_LAPSE_NOTICE,
    SessionManager,
    SessionRecord,
)


def test_session_append_and_persistence(tmp_path: Path) -> None:
    """T2 & F3: Messages are appended as JSONL lines in real time and can be loaded."""
    manager = SessionManager(storage_dir=tmp_path / "sessions")
    session = manager.create_session("sess-001")

    session.append(role="user", content="Hello agent", ts=1000)
    session.append(
        role="assistant",
        content="Hello user, let me check files",
        tool_uses=[
            {
                "tool_use_id": "call_1",
                "tool_name": "read_file",
                "arguments": {"path": "a.txt"},
            }
        ],
        ts=1001,
    )
    session.append(
        role="user",
        content="",
        tool_results=[
            {"tool_use_id": "call_1", "content": "file contents", "is_error": False}
        ],
        ts=1002,
    )
    session.close()

    records, meta = manager.load_session("sess-001", now_ts=1005)
    assert len(records) == 3
    assert meta["corrupted_lines"] == 0
    assert meta["truncated_dangling_calls"] == 0
    assert records[0].role == "user"
    assert records[0].content == "Hello agent"
    assert records[1].role == "assistant"
    assert len(records[1].tool_uses) == 1
    assert records[2].tool_results[0]["content"] == "file contents"


def test_load_session_skips_corrupted_lines(tmp_path: Path) -> None:
    """AC2 & F4: Corrupted or half-written JSON lines caused by crashes are skipped."""
    storage = tmp_path / "sessions"
    storage.mkdir()
    sess_file = storage / "crash-sess.jsonl"

    rec1 = json.dumps({"role": "user", "content": "Valid query 1", "ts": 100})
    corrupt_line = '{"role": "assistant", "content": "Half-written...'  # Truncated json
    empty_junk = ""
    rec2 = json.dumps({"role": "assistant", "content": "Valid answer 1", "ts": 102})

    sess_file.write_text(
        f"{rec1}\n{corrupt_line}\n{empty_junk}\n{rec2}\n", encoding="utf-8"
    )

    manager = SessionManager(storage_dir=storage)
    records, meta = manager.load_session("crash-sess", now_ts=150)

    assert len(records) == 2
    assert meta["corrupted_lines"] == 1
    assert records[0].content == "Valid query 1"
    assert records[1].content == "Valid answer 1"


def test_load_session_heals_dangling_tool_chain(tmp_path: Path) -> None:
    """AC2 & F4: Trailing tool_use calls without tool_results are truncated."""
    storage = tmp_path / "sessions"
    storage.mkdir()
    sess_file = storage / "dangling-sess.jsonl"

    turn1_user = json.dumps(
        {"role": "user", "content": "What is the capital of France?", "ts": 100}
    )
    turn1_asst = json.dumps({"role": "assistant", "content": "Paris", "ts": 101})

    turn2_user = json.dumps(
        {"role": "user", "content": "Now run tests please", "ts": 200}
    )
    # Assistant called tool 'run_pytest' but process was killed before tool result arrived
    turn2_asst_dangling = json.dumps(
        {
            "role": "assistant",
            "content": "Running tests...",
            "tool_uses": [
                {
                    "tool_use_id": "call_pytest_99",
                    "tool_name": "bash",
                    "arguments": {"cmd": "pytest"},
                }
            ],
            "ts": 201,
        }
    )

    sess_file.write_text(
        f"{turn1_user}\n{turn1_asst}\n{turn2_user}\n{turn2_asst_dangling}\n",
        encoding="utf-8",
    )

    manager = SessionManager(storage_dir=storage)
    records, meta = manager.load_session("dangling-sess", now_ts=210)

    # Dangling assistant message should be cut, leaving session ending on turn2_user
    assert len(records) == 3
    assert meta["truncated_dangling_calls"] == 1
    assert records[0].content == "What is the capital of France?"
    assert records[1].content == "Paris"
    assert records[2].content == "Now run tests please"


def test_load_session_inserts_24h_time_lapse_notice(tmp_path: Path) -> None:
    """F4: Inactivity > 24 hours appends time-lapse reminder notice."""
    storage = tmp_path / "sessions"
    storage.mkdir()
    sess_file = storage / "old-sess.jsonl"

    now = 1_000_000
    older_than_24h = now - (86400 + 3600)  # 25 hours ago

    rec1 = json.dumps(
        {"role": "user", "content": "Prior instruction", "ts": older_than_24h}
    )
    rec2 = json.dumps(
        {"role": "assistant", "content": "Done", "ts": older_than_24h + 5}
    )
    sess_file.write_text(f"{rec1}\n{rec2}\n", encoding="utf-8")

    manager = SessionManager(storage_dir=storage)
    records, meta = manager.load_session("old-sess", now_ts=now)

    assert meta["had_time_lapse_notice"] is True
    assert len(records) == 3
    assert TIME_LAPSE_NOTICE in records[-1].content


def test_clean_expired_sessions(tmp_path: Path) -> None:
    """Check that sessions older than 30 days are purged."""
    storage = tmp_path / "sessions"
    storage.mkdir()
    manager = SessionManager(storage_dir=storage)

    fresh = storage / "fresh.jsonl"
    fresh.write_text("{}", encoding="utf-8")

    old = storage / "old.jsonl"
    old.write_text("{}", encoding="utf-8")

    # Set mtime of old to 35 days ago
    old_mtime = time.time() - (35 * 86400)
    import os

    os.utime(old, (old_mtime, old_mtime))

    deleted = manager.clean_expired_sessions(max_age_days=30)
    assert deleted == 1
    assert not old.exists()
    assert fresh.exists()


def test_restore_to_conversation(tmp_path: Path) -> None:
    """Test hydrating ConversationManager from SessionRecords."""
    manager = SessionManager(storage_dir=tmp_path)
    records = [
        SessionRecord(role="user", content="Hello"),
        SessionRecord(role="assistant", content="Hi there"),
    ]
    conv = ConversationManager()
    manager.restore_to_conversation(records, conv)

    messages = conv.get_messages()
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Hello"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Hi there"
