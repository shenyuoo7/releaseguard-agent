from releaseguard_agent.runtime.batcher import (
    ToolCallItem,
    partition_tool_calls,
)
from releaseguard_agent.tools import (
    BashTool,
    EditFileTool,
    GlobTool,
    ReadFileTool,
    WriteFileTool,
)


def test_partition_tool_calls_empty() -> None:
    assert partition_tool_calls([], {}) == []


def test_partition_tool_calls_all_concurrent() -> None:
    read_tool = ReadFileTool()
    glob_tool = GlobTool()
    lookup = {read_tool.name: read_tool, glob_tool.name: glob_tool}

    calls = [
        ToolCallItem(id="1", name="read_file", arguments={"path": "a.py"}),
        ToolCallItem(id="2", name="read_file", arguments={"path": "b.py"}),
        ToolCallItem(id="3", name="glob", arguments={"pattern": "*.py"}),
    ]

    batches = partition_tool_calls(calls, lookup)
    assert len(batches) == 1
    assert batches[0].is_concurrent is True
    assert len(batches[0].calls) == 3


def test_partition_tool_calls_all_serial() -> None:
    write_tool = WriteFileTool()
    bash_tool = BashTool()
    edit_tool = EditFileTool()
    lookup = {
        write_tool.name: write_tool,
        bash_tool.name: bash_tool,
        edit_tool.name: edit_tool,
    }

    calls = [
        ToolCallItem(id="1", name="write_file", arguments={"path": "a.py"}),
        ToolCallItem(id="2", name="edit_file", arguments={"path": "b.py"}),
        ToolCallItem(id="3", name="bash", arguments={"command": "ls"}),
    ]

    batches = partition_tool_calls(calls, lookup)
    assert len(batches) == 3
    assert all(not b.is_concurrent for b in batches)
    assert all(len(b.calls) == 1 for b in batches)


def test_partition_tool_calls_mixed_sequence() -> None:
    read_tool = ReadFileTool()
    write_tool = WriteFileTool()
    bash_tool = BashTool()
    lookup = {
        read_tool.name: read_tool,
        write_tool.name: write_tool,
        bash_tool.name: bash_tool,
    }

    calls = [
        ToolCallItem(id="1", name="read_file", arguments={"path": "a.py"}),
        ToolCallItem(id="2", name="read_file", arguments={"path": "b.py"}),
        ToolCallItem(id="3", name="write_file", arguments={"path": "c.py"}),
        ToolCallItem(id="4", name="read_file", arguments={"path": "d.py"}),
        ToolCallItem(id="5", name="bash", arguments={"command": "pytest"}),
    ]

    batches = partition_tool_calls(calls, lookup)
    assert len(batches) == 4

    # Batch 1: concurrent [read, read]
    assert batches[0].is_concurrent is True
    assert len(batches[0].calls) == 2

    # Batch 2: serial [write]
    assert batches[1].is_concurrent is False
    assert len(batches[1].calls) == 1
    assert batches[1].calls[0].name == "write_file"

    # Batch 3: concurrent [read]
    assert batches[2].is_concurrent is True
    assert len(batches[2].calls) == 1

    # Batch 4: serial [bash]
    assert batches[3].is_concurrent is False
    assert len(batches[3].calls) == 1


def test_partition_tool_calls_unknown_tool() -> None:
    calls = [
        ToolCallItem(id="1", name="unknown_tool", arguments={}),
    ]
    batches = partition_tool_calls(calls, {})
    assert len(batches) == 1
    assert batches[0].is_concurrent is False
