from pathlib import Path
import pytest

from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import StreamEnd, StreamEvent, TextDelta
from releaseguard_agent.llm.fake_stream_client import FakeStreamClient
from releaseguard_agent.memory.auto_memory import (
    MemoryManager,
    extract_memory_from_dialogue,
    format_memory_file,
    parse_extraction_output,
    parse_memory_file,
    trigger_async_memory_extraction,
)


def test_format_and_parse_memory_file() -> None:
    """T4: YAML frontmatter serialization and deserialization."""
    raw = format_memory_file(
        name="test-rule",
        description="A test description",
        category="user",
        content="This is the markdown body of the memory.",
    )

    assert "name: test-rule" in raw
    assert "type: user" in raw
    assert "This is the markdown body of the memory." in raw

    meta, body = parse_memory_file(raw)
    assert meta["name"] == "test-rule"
    assert meta["description"] == "A test description"
    assert meta["type"] == "user"
    assert body == "This is the markdown body of the memory."


def test_four_category_directory_routing(tmp_path: Path) -> None:
    """T4 & F5: user/feedback stored in user home; project/reference stored in workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    home = tmp_path / "user_home"
    home.mkdir()

    mgr = MemoryManager(workspace_root=ws, user_home=home)

    # 1. user category -> user home
    p_user = mgr.save_memory(
        "user", "code-indent", "Prefer 4 spaces", "Always 4 spaces"
    )
    assert p_user.is_relative_to(home / ".releaseguard" / "memory")
    assert p_user.is_file()

    # 2. feedback category -> user home
    p_feed = mgr.save_memory(
        "feedback", "no-any-type", "Avoid typing.Any", "Do not use Any"
    )
    assert p_feed.is_relative_to(home / ".releaseguard" / "memory")

    # 3. project category -> workspace
    p_proj = mgr.save_memory(
        "project", "deploy-port", "App listens on 8080", "Port is 8080"
    )
    assert p_proj.is_relative_to(ws / ".releaseguard" / "memory")

    # 4. reference category -> workspace
    p_ref = mgr.save_memory(
        "reference", "api-docs", "API documentation link", "https://api.internal/v1"
    )
    assert p_ref.is_relative_to(ws / ".releaseguard" / "memory")

    # Verify MEMORY.md was generated in both locations
    assert (home / ".releaseguard" / "memory" / "MEMORY.md").is_file()
    assert (ws / ".releaseguard" / "memory" / "MEMORY.md").is_file()


def test_memory_deletion(tmp_path: Path) -> None:
    """Check memory file removal and index refresh."""
    ws = tmp_path / "workspace"
    home = tmp_path / "user_home"
    mgr = MemoryManager(workspace_root=ws, user_home=home)

    mgr.save_memory("project", "temp-note", "Temporary note", "To be deleted")
    mem_file = ws / ".releaseguard" / "memory" / "temp-note.md"
    assert mem_file.is_file()

    deleted = mgr.delete_memory("project", "temp-note")
    assert deleted is True
    assert not mem_file.is_file()

    index_text = (ws / ".releaseguard" / "memory" / "MEMORY.md").read_text(
        encoding="utf-8"
    )
    assert "temp-note" not in index_text


def test_memory_index_budget_and_truncation(tmp_path: Path) -> None:
    """F6: load_memory_index enforces line and byte limits."""
    ws = tmp_path / "workspace"
    home = tmp_path / "user_home"
    mgr = MemoryManager(workspace_root=ws, user_home=home)

    # Populate 10 memories
    for i in range(10):
        mgr.save_memory(
            "project", f"rule-{i:02d}", f"Rule description {i}", f"Rule body {i}"
        )

    # Limit to max 5 items
    idx = mgr.load_memory_index(max_lines=6)
    assert "rule-09" in idx
    assert "已自动省略早期" in idx


def test_parse_extraction_output() -> None:
    """Test parsing LLM extraction output blocks."""
    llm_output = """
ACTION: CREATE
CATEGORY: user
NAME: prefer-pytest
DESCRIPTION: 用户严格要求使用 pytest 而不是 unittest
CONTENT:
对话中用户明确要求所有测试必须编写为 pytest 函数。

ACTION: CREATE
CATEGORY: project
NAME: target-python-version
DESCRIPTION: 项目目标版本为 Python 3.11
CONTENT:
代码库升级到了 Python 3.11。
"""
    actions = parse_extraction_output(llm_output)
    assert len(actions) == 2
    assert actions[0]["category"] == "user"
    assert actions[0]["name"] == "prefer-pytest"
    assert actions[1]["category"] == "project"
    assert actions[1]["name"] == "target-python-version"


@pytest.mark.anyio
async def test_extract_memory_from_dialogue_integration(tmp_path: Path) -> None:
    """T5 & AC3: Dialogue analysis extracts and persists memory asynchronously."""
    ws = tmp_path / "workspace"
    home = tmp_path / "user_home"
    mgr = MemoryManager(workspace_root=ws, user_home=home)

    stream_response: list[StreamEvent] = [
        TextDelta(
            text="""ACTION: CREATE
CATEGORY: user
NAME: user-prefers-typing
DESCRIPTION: 用户要求所有函数包含类型注解
CONTENT:
用户在对话中指出必须始终为函数提供 Python 类型注解。
"""
        ),
        StreamEnd(),
    ]
    client = FakeStreamClient(events=stream_response)

    conv = ConversationManager()
    conv.add_user_message("以后写代码一定要记得加 type hints，必须严格遵守。")
    conv.add_assistant_message("明白了，以后的代码我都会加上完整的类型注解。")

    applied = await extract_memory_from_dialogue(conv, client, mgr)
    assert len(applied) == 1
    assert applied[0]["name"] == "user-prefers-typing"

    # Verify file saved in user home
    saved_file = home / ".releaseguard" / "memory" / "user-prefers-typing.md"
    assert saved_file.is_file()
    content = saved_file.read_text(encoding="utf-8")
    assert "user-prefers-typing" in content
    assert "type: user" in content

    # Test non-blocking background task wrapper
    task = trigger_async_memory_extraction(conv, client, mgr)
    result = await task
    assert isinstance(result, list)
