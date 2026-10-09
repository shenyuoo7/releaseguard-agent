"""Layer 5: Human-In-The-Loop (HITL) handshake and dynamic permission learning."""

import asyncio
from pathlib import Path
from typing import Any, Literal
import uuid
import yaml

HITLChoice = Literal["y", "n", "a"]


def append_local_allow_rule(
    workspace_root: Path,
    tool_name: str,
    pattern: str = "*",
) -> None:
    """Persist an allow rule to <workspace_root>/.releaseguard/permissions.local.yaml.

    Enables dynamic permission learning so subsequent operations matching the pattern
    pass without interactive prompting.
    """
    config_dir = workspace_root / ".releaseguard"
    config_dir.mkdir(parents=True, exist_ok=True)
    local_file = config_dir / "permissions.local.yaml"

    rule_str = f"{tool_name}({pattern})" if pattern and pattern != "*" else tool_name

    existing_data: dict[str, Any] = {"rules": []}
    if local_file.is_file():
        try:
            content = local_file.read_text(encoding="utf-8")
            loaded = yaml.safe_load(content)
            if isinstance(loaded, dict) and isinstance(loaded.get("rules"), list):
                existing_data = loaded
        except Exception:
            existing_data = {"rules": []}

    # Avoid duplicate rules
    rules = existing_data.setdefault("rules", [])
    for r in rules:
        if (
            isinstance(r, dict)
            and r.get("rule") == rule_str
            and r.get("effect") == "allow"
        ):
            return

    rules.append({"rule": rule_str, "effect": "allow"})

    with local_file.open("w", encoding="utf-8") as f:
        yaml.safe_dump(existing_data, f, default_flow_style=False, sort_keys=False)


class FutureHITLBridge:
    """Manages asynchronous futures for interactive HITL approvals between Agent and UI."""

    def __init__(self) -> None:
        self._pending_requests: dict[str, asyncio.Future[HITLChoice]] = {}

    def create_request(self) -> tuple[str, asyncio.Future[HITLChoice]]:
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[HITLChoice] = loop.create_future()
        self._pending_requests[request_id] = future
        return request_id, future

    def resolve_request(self, request_id: str, choice: HITLChoice) -> bool:
        future = self._pending_requests.pop(request_id, None)
        if future and not future.done():
            future.set_result(choice)
            return True
        return False

    def cancel_all(self) -> None:
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()
