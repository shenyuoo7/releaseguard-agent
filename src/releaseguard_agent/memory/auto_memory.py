"""Layer 3: 4-category automatic memory extraction, markdown persistence, and MEMORY.md index management."""

import asyncio
from pathlib import Path
import re
from typing import Any
import yaml

from releaseguard_agent.llm.client import StreamLLMClient
from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import TextDelta

VALID_CATEGORIES = ("user", "feedback", "project", "reference")

MEMORY_EXTRACTION_SYSTEM_PROMPT = """你是一个专业的代码与项目经验记忆分析引擎。
请分析最近的对话内容，并对比已有的记忆索引，判断是否产生了值得沉淀的长期经验。

记忆分类：
- user: 用户的个人编码习惯或偏好
- feedback: 用户给出的纠偏反馈、踩坑教训或负向约束
- project: 本项目的架构设计、部署规范或技术栈约定
- reference: 关键文档、外部协议或重要参考资料链接

原则：
- 含义相同或已有类似记录，绝不重复创建；
- 无明显新价值或普通问答，输出 ACTION: NONE；
- 严禁提取会话内的临时变量、报错栈明细或一次性任务。

输出格式（若有多条可重复此段，若无经验输出单个 ACTION: NONE）：
ACTION: CREATE
CATEGORY: user
NAME: user-prefers-typing
DESCRIPTION: 用户偏好严格的 Python 类型注解
CONTENT:
用户在对话中明确指出所有公开函数必须添加类型注解，且不允许使用 Any。
"""


def slugify_name(name: str) -> str:
    """Normalize a memory name into a clean hyphenated slug."""
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-").lower()
    return slug if slug else "unnamed-memory"


def get_memory_directory(
    category: str,
    workspace_root: Path,
    user_home: Path | None = None,
) -> Path:
    """Map memory category to user-level or project-level directory."""
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category '{category}'. Must be one of: {VALID_CATEGORIES}"
        )

    if category in ("user", "feedback"):
        home = (user_home or Path.home()).resolve()
        target_dir = home / ".releaseguard" / "memory"
    else:
        ws = workspace_root.resolve()
        target_dir = ws / ".releaseguard" / "memory"

    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def parse_memory_file(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter and Markdown body from a memory file."""
    if not text.startswith("---"):
        return {}, text.strip()

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()

    frontmatter_raw = parts[1].strip()
    body = parts[2].strip()

    try:
        data = yaml.safe_load(frontmatter_raw)
        return data if isinstance(data, dict) else {}, body
    except Exception:
        return {}, body


def format_memory_file(
    name: str,
    description: str,
    category: str,
    content: str,
) -> str:
    """Format memory metadata and body into YAML frontmatter markdown."""
    frontmatter = {
        "name": name,
        "description": description,
        "type": category,
    }
    yaml_header = yaml.safe_dump(
        frontmatter, allow_unicode=True, sort_keys=False
    ).strip()
    return f"---\n{yaml_header}\n---\n\n{content.strip()}\n"


def rebuild_directory_index(dir_path: Path) -> Path:
    """Rebuild the MEMORY.md index within a single memory directory."""
    index_path = dir_path / "MEMORY.md"
    lines: list[str] = ["# 记忆索引", ""]

    files = sorted(dir_path.glob("*.md"))
    for file in files:
        if file.name == "MEMORY.md":
            continue
        try:
            raw = file.read_text(encoding="utf-8")
            meta, _ = parse_memory_file(raw)
            name = meta.get("name", file.stem)
            desc = meta.get("description", "")
            cat = meta.get("type", "unknown")
            lines.append(f"- [{name}]({file.name}) [{cat}]: {desc}")
        except Exception:
            pass

    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return index_path


class MemoryManager:
    """Manages 4-category memory persistence, index synchronization, and prompt injection."""

    def __init__(
        self,
        workspace_root: Path,
        user_home: Path | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.user_home = (user_home or Path.home()).resolve()

    def save_memory(
        self,
        category: str,
        name: str,
        description: str,
        content: str,
    ) -> Path:
        """Save or update a memory markdown file and refresh its directory index."""
        slug = slugify_name(name)
        target_dir = get_memory_directory(category, self.workspace_root, self.user_home)
        file_path = target_dir / f"{slug}.md"

        formatted = format_memory_file(
            name=slug,
            description=description.strip(),
            category=category,
            content=content,
        )
        file_path.write_text(formatted, encoding="utf-8")
        rebuild_directory_index(target_dir)
        return file_path

    def delete_memory(self, category: str, name: str) -> bool:
        """Remove a memory file and rebuild the corresponding index."""
        slug = slugify_name(name)
        target_dir = get_memory_directory(category, self.workspace_root, self.user_home)
        file_path = target_dir / f"{slug}.md"
        if file_path.is_file():
            file_path.unlink()
            rebuild_directory_index(target_dir)
            return True
        return False

    def get_memory(self, category: str, name: str) -> tuple[dict[str, Any], str] | None:
        """Fetch memory frontmatter and body."""
        slug = slugify_name(name)
        target_dir = get_memory_directory(category, self.workspace_root, self.user_home)
        file_path = target_dir / f"{slug}.md"
        if not file_path.is_file():
            return None
        text = file_path.read_text(encoding="utf-8")
        return parse_memory_file(text)

    def load_memory_index(
        self,
        max_lines: int = 200,
        max_bytes: int = 25_000,
    ) -> str:
        """Aggregate user and project memories into an index bounded by lines and bytes."""
        user_dir = self.user_home / ".releaseguard" / "memory"
        project_dir = self.workspace_root / ".releaseguard" / "memory"

        items: list[str] = []

        # Collect user-level items
        if user_dir.is_dir():
            for f in sorted(user_dir.glob("*.md")):
                if f.name == "MEMORY.md":
                    continue
                try:
                    meta, _ = parse_memory_file(f.read_text(encoding="utf-8"))
                    name = meta.get("name", f.stem)
                    desc = meta.get("description", "")
                    cat = meta.get("type", "user")
                    items.append(f"- [{cat}:{name}] {desc}")
                except Exception:
                    pass

        # Collect project-level items
        if project_dir.is_dir():
            for f in sorted(project_dir.glob("*.md")):
                if f.name == "MEMORY.md":
                    continue
                try:
                    meta, _ = parse_memory_file(f.read_text(encoding="utf-8"))
                    name = meta.get("name", f.stem)
                    desc = meta.get("description", "")
                    cat = meta.get("type", "project")
                    items.append(f"- [{cat}:{name}] {desc}")
                except Exception:
                    pass

        if not items:
            return ""

        header = "# 长期记忆索引 (MEMORY.md)\n"
        # Enforce budget: max_lines and max_bytes
        # Reserve lines for header and truncation banner
        allowed_items_count = max(1, max_lines - 5)
        truncated_count = 0

        if len(items) > allowed_items_count:
            truncated_count = len(items) - allowed_items_count
            items = items[-allowed_items_count:]  # keep recent items

        body = "\n".join(items)
        if truncated_count > 0:
            body = (
                f"<!-- 索引超限：已自动省略早期 {truncated_count} 条记忆 -->\n" + body
            )

        full_index = f"{header}{body}"

        # Byte budget check
        raw_bytes = full_index.encode("utf-8")
        if len(raw_bytes) > max_bytes:
            # Cut characters from the beginning of items
            trimmed_text = raw_bytes[-max_bytes:].decode("utf-8", errors="ignore")
            # Ensure header is preserved
            full_index = f"{header}<!-- 索引字节超限截断 -->\n{trimmed_text}"

        return full_index.strip()


def parse_extraction_output(raw_output: str) -> list[dict[str, Any]]:
    """Parse structured memory actions from LLM extraction response."""
    actions: list[dict[str, Any]] = []
    # Split by ACTION: markers
    chunks = re.split(r"(?=ACTION:\s*)", raw_output)

    for chunk in chunks:
        if not chunk.strip():
            continue
        action_match = re.search(r"ACTION:\s*([A-Z]+)", chunk)
        if not action_match:
            continue
        act = action_match.group(1).upper()
        if act == "NONE":
            continue

        cat_match = re.search(r"CATEGORY:\s*([a-zA-Z_-]+)", chunk)
        name_match = re.search(r"NAME:\s*([a-zA-Z0-9_-]+)", chunk)
        desc_match = re.search(r"DESCRIPTION:\s*(.+)", chunk)
        content_match = re.search(r"CONTENT:\s*\n?(.*)", chunk, re.DOTALL)

        cat = cat_match.group(1).lower() if cat_match else "project"
        if cat not in VALID_CATEGORIES:
            cat = "project"

        name = name_match.group(1) if name_match else "unnamed-memory"
        desc = desc_match.group(1).strip() if desc_match else ""
        content = content_match.group(1).strip() if content_match else ""

        actions.append(
            {
                "action": act,
                "category": cat,
                "name": name,
                "description": desc,
                "content": content,
            }
        )

    return actions


async def extract_memory_from_dialogue(
    conversation: ConversationManager,
    client: StreamLLMClient,
    memory_manager: MemoryManager,
) -> list[dict[str, Any]]:
    """Extract memory from recent conversation and apply updates atomically."""
    # Build a prompt containing existing memory index and conversation
    current_index = memory_manager.load_memory_index()
    extraction_conv = ConversationManager()

    context_prompt = (
        f"当前已有记忆索引：\n{current_index if current_index else '暂无任何记忆'}\n\n"
        "请结合以上已有记录，分析以下对话历史并提取长期经验："
    )
    extraction_conv.add_user_message(context_prompt)

    # Add recent messages (up to 10)
    messages = conversation.get_messages()
    for msg in messages[-10:]:
        extraction_conv.append(msg)

    extraction_conv.add_user_message(
        "请按规范格式输出 ACTION, CATEGORY, NAME, DESCRIPTION, CONTENT 或 ACTION: NONE。"
    )

    accumulated = ""
    try:
        async for event in client.stream(
            conversation=extraction_conv,
            system=MEMORY_EXTRACTION_SYSTEM_PROMPT,
            tools=[],
        ):
            if isinstance(event, TextDelta):
                accumulated += event.text
    except Exception:
        return []

    actions = parse_extraction_output(accumulated)
    applied_actions: list[dict[str, Any]] = []

    for item in actions:
        try:
            act = item["action"]
            cat = item["category"]
            name = item["name"]
            if act in ("CREATE", "UPDATE"):
                memory_manager.save_memory(
                    category=cat,
                    name=name,
                    description=item["description"],
                    content=item["content"],
                )
                applied_actions.append(item)
            elif act == "DELETE":
                memory_manager.delete_memory(category=cat, name=name)
                applied_actions.append(item)
        except Exception:
            pass

    return applied_actions


def trigger_async_memory_extraction(
    conversation: ConversationManager,
    client: StreamLLMClient,
    memory_manager: MemoryManager,
) -> asyncio.Task[Any]:
    """Launch memory extraction as a non-blocking background asyncio task."""
    return asyncio.create_task(
        extract_memory_from_dialogue(conversation, client, memory_manager)
    )
