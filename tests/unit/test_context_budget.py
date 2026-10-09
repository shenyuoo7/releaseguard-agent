from pathlib import Path

from releaseguard_agent.llm.messages import ToolResultBlock
from releaseguard_agent.runtime.context.result_budget import (
    ContentReplacementState,
    apply_tool_result_budget,
)


def test_single_result_exceeds_threshold_persisted(tmp_path: Path) -> None:
    """T2 & AC1: Tool result > 50K chars is persisted to disk with 2KB preview in context."""
    storage_dir = tmp_path / "tool-results"
    state = ContentReplacementState()

    large_content = "X" * 60_000
    res = ToolResultBlock(tool_use_id="call_big_1", content=large_content)

    apply_tool_result_budget([res], state, storage_dir, single_threshold=50_000)

    # Context replaced with preview block
    assert "<persisted-output" in res.content
    assert "call_big_1.txt" in res.content
    assert len(res.content) < 3_000

    # Disk file created with full 60,000 characters
    saved_file = storage_dir / "call_big_1.txt"
    assert saved_file.is_file()
    assert saved_file.read_text(encoding="utf-8") == large_content


def test_aggregate_results_exceed_threshold_persisted(tmp_path: Path) -> None:
    """T2 & F2: Aggregate output > threshold persists largest items first."""
    storage_dir = tmp_path / "tool-results"
    state = ContentReplacementState()

    r1 = ToolResultBlock(tool_use_id="call_1", content="A" * 30_000)
    r2 = ToolResultBlock(tool_use_id="call_2", content="B" * 40_000)
    r3 = ToolResultBlock(tool_use_id="call_3", content="C" * 20_000)

    # Total = 90,000 chars. Aggregate limit = 60,000 chars. Single limit = 50,000 chars.
    apply_tool_result_budget(
        [r1, r2, r3],
        state,
        storage_dir,
        single_threshold=50_000,
        aggregate_threshold=60_000,
    )

    # Largest item (r2 with 40,000 chars) should be persisted first
    assert "<persisted-output" in r2.content
    assert (storage_dir / "call_2.txt").is_file()

    # Once r2 is replaced by ~2KB preview, total is now 30K + 2K + 20K = 52K <= 60K limit!
    # So r1 and r3 should remain intact without persistence
    assert r1.content == "A" * 30_000
    assert r3.content == "C" * 20_000


def test_decision_freezing_across_turns(tmp_path: Path) -> None:
    """T2 & AC2: Decisions made in earlier turns are frozen and never flipped later."""
    storage_dir = tmp_path / "tool-results"
    state = ContentReplacementState()

    # Turn 1: r1 has 25K chars (below single 50K and aggregate 40K limit)
    r1 = ToolResultBlock(tool_use_id="turn1_res", content="1" * 25_000)
    apply_tool_result_budget(
        [r1],
        state,
        storage_dir,
        single_threshold=50_000,
        aggregate_threshold=40_000,
    )
    assert r1.content == "1" * 25_000
    assert "turn1_res" in state.seen_ids
    assert "turn1_res" not in state.replacements

    # Turn 2: r2 has 30K chars. Total of [r1, r2] = 55K > 40K aggregate limit.
    # Because r1 was evaluated in Turn 1 and frozen as 'not persisted',
    # only r2 is considered for aggregate persistence!
    r2 = ToolResultBlock(tool_use_id="turn2_res", content="2" * 30_000)
    apply_tool_result_budget(
        [r1, r2],
        state,
        storage_dir,
        single_threshold=50_000,
        aggregate_threshold=40_000,
    )

    # r1 must remain exactly unpersisted
    assert r1.content == "1" * 25_000

    # r2 was persisted
    assert "<persisted-output" in r2.content
    assert (storage_dir / "turn2_res.txt").is_file()
