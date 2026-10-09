"""Layer 2: JSONL-based streaming session persistence and crash-resilient recovery."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
import uuid

from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.messages import (
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

TIME_LAPSE_NOTICE = (
    "[会话恢复提醒: 距离上次交互已过去超过 24 小时，请重新核对当前系统与代码状态。]"
)


@dataclass
class SessionRecord:
    """A single serializable record inside a .jsonl session log."""

    role: str
    content: Any = ""
    thinking_blocks: list[dict[str, Any]] = field(default_factory=list)
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    tool_use_id: str | None = None
    tool_name: str | None = None
    ts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRecord":
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            thinking_blocks=data.get("thinking_blocks", []),
            tool_uses=data.get("tool_uses", []),
            tool_results=data.get("tool_results", []),
            tool_use_id=data.get("tool_use_id"),
            tool_name=data.get("tool_name"),
            ts=int(data.get("ts", 0)),
        )

    @classmethod
    def from_message(cls, message: Message, ts: int | None = None) -> "SessionRecord":
        return cls(
            role=message.role,
            content=message.content,
            thinking_blocks=[b.to_dict() for b in message.thinking_blocks],
            tool_uses=[b.to_dict() for b in message.tool_uses],
            tool_results=[b.to_dict() for b in message.tool_results],
            ts=ts if ts is not None else int(time.time()),
        )

    def to_message(self) -> Message:
        content_str = (
            self.content if isinstance(self.content, str) else str(self.content)
        )
        return Message(
            role=self.role,  # type: ignore[arg-type]
            content=content_str,
            thinking_blocks=[ThinkingBlock.from_dict(b) for b in self.thinking_blocks],
            tool_uses=[ToolUseBlock.from_dict(b) for b in self.tool_uses],
            tool_results=[ToolResultBlock.from_dict(b) for b in self.tool_results],
        )


class Session:
    """Manages appending messages to an active JSONL session log with immediate flush."""

    def __init__(self, session_id: str, file_path: Path) -> None:
        self.session_id = session_id
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.file_path, "a", encoding="utf-8")

    def append(
        self,
        role: str,
        content: Any = "",
        tool_uses: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        thinking_blocks: list[dict[str, Any]] | None = None,
        tool_use_id: str | None = None,
        tool_name: str | None = None,
        ts: int | None = None,
    ) -> SessionRecord:
        record = SessionRecord(
            role=role,
            content=content,
            thinking_blocks=thinking_blocks or [],
            tool_uses=tool_uses or [],
            tool_results=tool_results or [],
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            ts=ts if ts is not None else int(time.time()),
        )
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        self._file.write(line + "\n")
        self._file.flush()
        return record

    def append_message(self, message: Message, ts: int | None = None) -> SessionRecord:
        record = SessionRecord.from_message(message, ts=ts)
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        self._file.write(line + "\n")
        self._file.flush()
        return record

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


class SessionManager:
    """Manages session creation, listing, cleanup, and crash-resilient restoration."""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def default_for_workspace(cls, workspace_root: Path) -> "SessionManager":
        return cls(storage_dir=workspace_root / ".releaseguard" / "sessions")

    def create_session(self, session_id: str | None = None) -> Session:
        if not session_id:
            now_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            short_id = uuid.uuid4().hex[:6]
            session_id = f"{now_str}-{short_id}"

        file_path = self.storage_dir / f"{session_id}.jsonl"
        return Session(session_id=session_id, file_path=file_path)

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for p in self.storage_dir.glob("*.jsonl"):
            try:
                stat = p.stat()
                sessions.append(
                    {
                        "session_id": p.stem,
                        "file_path": p,
                        "mtime": stat.st_mtime,
                        "size_bytes": stat.st_size,
                    }
                )
            except Exception:
                pass
        sessions.sort(key=lambda s: float(s["mtime"]), reverse=True)
        return sessions

    def clean_expired_sessions(self, max_age_days: int = 30) -> int:
        now = time.time()
        max_age_seconds = max_age_days * 86400
        removed = 0
        for p in self.storage_dir.glob("*.jsonl"):
            try:
                stat = p.stat()
                if now - stat.st_mtime > max_age_seconds:
                    p.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                pass
        return removed

    def load_session(
        self,
        session_id_or_path: str | Path,
        now_ts: int | None = None,
    ) -> tuple[list[SessionRecord], dict[str, Any]]:
        """Load session records with crash fault-tolerance, tool-chain repair, and time-lapse check."""
        if isinstance(session_id_or_path, Path):
            path = session_id_or_path
        elif str(session_id_or_path).endswith(".jsonl"):
            path = Path(session_id_or_path)
        else:
            path = self.storage_dir / f"{session_id_or_path}.jsonl"

        if not path.is_file():
            raise FileNotFoundError(f"Session file not found: {path}")

        records: list[SessionRecord] = []
        corrupted_lines = 0

        # 1. Fault-tolerant line reading
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                    if isinstance(data, dict):
                        records.append(SessionRecord.from_dict(data))
                    else:
                        corrupted_lines += 1
                except Exception:
                    corrupted_lines += 1

        # 2. Message chain validation and dangling tool-use truncation
        valid_records, truncated_calls = self._validate_and_heal_tool_chain(records)

        # 3. Check for 24-hour time lapse
        current_time = now_ts if now_ts is not None else int(time.time())
        had_time_lapse = False
        if valid_records and valid_records[-1].ts > 0:
            last_ts = valid_records[-1].ts
            if current_time - last_ts > 86400:
                had_time_lapse = True
                valid_records.append(
                    SessionRecord(
                        role="user",
                        content=TIME_LAPSE_NOTICE,
                        ts=current_time,
                    )
                )

        metadata = {
            "corrupted_lines": corrupted_lines,
            "truncated_dangling_calls": truncated_calls,
            "had_time_lapse_notice": had_time_lapse,
            "total_records": len(valid_records),
        }
        return valid_records, metadata

    def _validate_and_heal_tool_chain(
        self, records: list[SessionRecord]
    ) -> tuple[list[SessionRecord], int]:
        """Truncate incomplete turns if assistant invoked tools that never received results."""
        if not records:
            return [], 0

        # Find all answered tool_use_ids across the entire session
        answered_tool_ids: set[str] = set()
        for r in records:
            if r.tool_results:
                for tr in r.tool_results:
                    tid = tr.get("tool_use_id")
                    if tid:
                        answered_tool_ids.add(tid)
            if r.role == "tool_result" and r.tool_use_id:
                answered_tool_ids.add(r.tool_use_id)

        # Walk backwards from the end to find if the trailing messages contain dangling tool calls
        cut_index = len(records)
        truncated_count = 0

        for i in range(len(records) - 1, -1, -1):
            rec = records[i]
            if rec.role == "assistant" and rec.tool_uses:
                called_ids = {
                    u.get("tool_use_id") for u in rec.tool_uses if u.get("tool_use_id")
                }
                unanswered = called_ids - answered_tool_ids
                if unanswered:
                    # Trailing dangling call found: cut this message and any subsequent orphaned results
                    cut_index = i
                    truncated_count += len(unanswered)

        return records[:cut_index], truncated_count

    def restore_to_conversation(
        self,
        records: list[SessionRecord],
        conversation: ConversationManager,
    ) -> None:
        """Hydrate ConversationManager in-place with validated session records."""
        for rec in records:
            conversation.append(rec.to_message())
