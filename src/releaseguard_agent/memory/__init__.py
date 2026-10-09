"""Three-tier memory system for ReleaseGuard Agent (ch09).

- Layer 1: Long-term project instruction system (RELEASEGUARD.md, recursive @include)
- Layer 2: Mid-term session memory (JSONL streaming persistence, SessionManager)
- Layer 3: Long-term experiential memory (4-category auto-memory, MEMORY.md index, async extraction)
"""

from releaseguard_agent.memory.auto_memory import (
    MEMORY_EXTRACTION_SYSTEM_PROMPT,
    MemoryManager,
    extract_memory_from_dialogue,
    format_memory_file,
    get_memory_directory,
    parse_extraction_output,
    parse_memory_file,
    trigger_async_memory_extraction,
)
from releaseguard_agent.memory.instructions import (
    load_project_instructions,
    process_includes,
)
from releaseguard_agent.memory.session import (
    TIME_LAPSE_NOTICE,
    Session,
    SessionManager,
    SessionRecord,
)

__all__ = [
    "MEMORY_EXTRACTION_SYSTEM_PROMPT",
    "MemoryManager",
    "Session",
    "SessionManager",
    "SessionRecord",
    "TIME_LAPSE_NOTICE",
    "extract_memory_from_dialogue",
    "format_memory_file",
    "get_memory_directory",
    "load_project_instructions",
    "parse_extraction_output",
    "parse_memory_file",
    "process_includes",
    "trigger_async_memory_extraction",
]
