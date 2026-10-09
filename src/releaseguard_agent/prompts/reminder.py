"""System reminder formatting and prompt injection defense."""

import re


def format_system_reminder(text: str) -> str:
    """Wrap dynamic notifications or instructions in <system-reminder> XML tags.

    Escapes any rogue closing tags inside the text to prevent prompt injection / boundary escape.
    """
    clean_text = text.strip()
    safe_text = re.sub(
        r"<\s*/\s*system-reminder\s*>",
        "&lt;/system-reminder&gt;",
        clean_text,
        flags=re.IGNORECASE,
    )
    return f"<system-reminder>\n{safe_text}\n</system-reminder>"
