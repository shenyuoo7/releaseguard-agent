import sys
from pathlib import Path
import pytest

from releaseguard_agent.tools import (
    BashTool,
    EditFileTool,
    GlobTool,
    GrepTool,
    ReadFileTool,
    ToolContext,
    WriteFileTool,
    build_default_tool_registry,
)


@pytest.mark.anyio
async def test_read_file_tool(tmp_path: Path) -> None:
    tool = ReadFileTool()
    ctx = ToolContext(cwd=tmp_path)

    # 1. Non-existent file
    res = await tool.execute({"path": "missing.txt"}, ctx)
    assert res.is_error
    assert "not found" in res.content

    # 2. Normal text file
    sample = tmp_path / "sample.py"
    sample.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")

    res = await tool.execute({"path": "sample.py", "offset": 2, "limit": 2}, ctx)
    assert not res.is_error
    assert "2\tline 2\n3\tline 3" == res.content
    assert res.metadata["total_lines"] == 5

    # 3. Directory rejection
    sub_dir = tmp_path / "subdir"
    sub_dir.mkdir()
    res = await tool.execute({"path": "subdir"}, ctx)
    assert res.is_error
    assert "directory" in res.content

    # 4. Binary file rejection
    bin_file = tmp_path / "binary.bin"
    bin_file.write_bytes(b"hello\x00world")
    res = await tool.execute({"path": "binary.bin"}, ctx)
    assert res.is_error
    assert "binary" in res.content


@pytest.mark.anyio
async def test_write_and_edit_file_tools(tmp_path: Path) -> None:
    write_tool = WriteFileTool()
    edit_tool = EditFileTool()
    ctx = ToolContext(cwd=tmp_path)

    # 1. Write file with nested parents
    target = "nested/deep/code.py"
    write_res = await write_tool.execute(
        {"path": target, "content": "def main():\n    return 42\n"},
        ctx,
    )
    assert not write_res.is_error
    assert (tmp_path / target).read_text(
        encoding="utf-8"
    ) == "def main():\n    return 42\n"

    # 2. Edit file - Unique replacement
    edit_res = await edit_tool.execute(
        {"path": target, "old_string": "return 42", "new_string": "return 100"},
        ctx,
    )
    assert not edit_res.is_error
    assert "return 100" in (tmp_path / target).read_text(encoding="utf-8")

    # 3. Edit file - Not found
    edit_not_found = await edit_tool.execute(
        {"path": target, "old_string": "non_existent_code", "new_string": "foo"},
        ctx,
    )
    assert edit_not_found.is_error
    assert "was not found" in edit_not_found.content

    # 4. Edit file - Ambiguous duplicate match
    (tmp_path / "duplicate.txt").write_text("foo\nbar\nfoo\n", encoding="utf-8")
    edit_dup = await edit_tool.execute(
        {"path": "duplicate.txt", "old_string": "foo", "new_string": "baz"},
        ctx,
    )
    assert edit_dup.is_error
    assert "matches 2 times" in edit_dup.content


@pytest.mark.anyio
async def test_bash_tool(tmp_path: Path) -> None:
    tool = BashTool()
    ctx = ToolContext(cwd=tmp_path)

    # 1. Successful execution
    py_cmd = f'"{sys.executable}" -c "print(\'hello_from_bash\')"'
    res = await tool.execute({"command": py_cmd}, ctx)
    assert not res.is_error
    assert "hello_from_bash" in res.content
    assert "<exit_code>0</exit_code>" in res.content

    # 2. Non-zero exit code (is_error is False by spec for agent self-repair)
    fail_cmd = f'"{sys.executable}" -c "import sys; print(\'failure_trace\', file=sys.stderr); sys.exit(7)"'
    res_fail = await tool.execute({"command": fail_cmd}, ctx)
    assert not res_fail.is_error
    assert "failure_trace" in res_fail.content
    assert "<exit_code>7</exit_code>" in res_fail.content

    # 3. Timeout enforcement
    sleep_cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    res_timeout = await tool.execute({"command": sleep_cmd, "timeout": 1}, ctx)
    assert res_timeout.is_error
    assert "timed out after 1 seconds" in res_timeout.content

    # 4. Large output truncation
    big_cmd = f'"{sys.executable}" -c "print(\'X\' * 15000)"'
    res_big = await tool.execute({"command": big_cmd}, ctx)
    assert not res_big.is_error
    assert "truncated" in res_big.content


@pytest.mark.anyio
async def test_glob_and_grep_tools(tmp_path: Path) -> None:
    glob_tool = GlobTool()
    grep_tool = GrepTool()
    ctx = ToolContext(cwd=tmp_path)

    # Setup directories and files
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text(
        "def find_me():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "src" / "beta.txt").write_text("irrelevant notes\n", encoding="utf-8")

    # Excluded directories
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "alpha.py").write_text(
        "def find_me():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text(
        "def find_me():\n    pass\n", encoding="utf-8"
    )

    # 1. Glob tool
    glob_res = await glob_tool.execute({"pattern": "**/*.py"}, ctx)
    assert not glob_res.is_error
    assert "src/alpha.py" in glob_res.content
    assert ".git" not in glob_res.content
    assert "__pycache__" not in glob_res.content

    # 2. Grep tool
    grep_res = await grep_tool.execute(
        {"pattern": "def find_me", "include": "*.py"}, ctx
    )
    assert not grep_res.is_error
    assert "src/alpha.py:1: def find_me():" in grep_res.content
    assert ".git" not in grep_res.content

    # 3. Invalid regex pattern
    val_err = grep_tool.validate_input({"pattern": "[unclosed("})
    assert val_err is not None
    assert "Invalid regular expression" in val_err


def test_build_default_tool_registry() -> None:
    registry = build_default_tool_registry()
    tools = registry.list_tools()
    assert len(tools) == 6
    names = {t.name for t in tools}
    assert names == {"read_file", "write_file", "edit_file", "bash", "glob", "grep"}


@pytest.mark.anyio
async def test_tool_call_complete_dispatch_integration(tmp_path: Path) -> None:
    from releaseguard_agent.llm.events import ToolCallComplete

    registry = build_default_tool_registry()
    ctx = ToolContext(cwd=tmp_path)

    # Simulate ToolCallComplete produced by OpenAI / Anthropic stream
    tool_call = ToolCallComplete(
        tool_id="call_auto_99",
        tool_name="write_file",
        arguments={"path": "hello.txt", "content": "hello world"},
    )

    # Dispatch via registry
    result = await registry.execute(tool_call.tool_name, tool_call.arguments, ctx)
    assert not result.is_error
    assert "Successfully wrote" in result.content

    # Map to ToolResultBlock
    result_block = result.to_tool_result_block(tool_call.tool_id)
    assert result_block.tool_use_id == "call_auto_99"
    assert "Successfully wrote" in result_block.content
    assert not result_block.is_error

    # Verify written file on disk
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hello world"
