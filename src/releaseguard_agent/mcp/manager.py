"""MCPManager lifecycle controller managing connections, handshake, and clean shutdown."""

import asyncio
from contextlib import AsyncExitStack
import os
from pathlib import Path
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)
from releaseguard_agent.mcp.config import MCPConfig, MCPServerConfig
from releaseguard_agent.mcp.tool import MCPToolWrapper


class MCPManager:
    """Manages active MCP server connections, discovery, and process lifecycle."""

    def __init__(self, config: MCPConfig, workspace_root: Path) -> None:
        self.config = config
        self.workspace_root = workspace_root.resolve()
        self._exit_stacks: dict[str, AsyncExitStack] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._tools: list[MCPToolWrapper] = []

    @property
    def tools(self) -> list[MCPToolWrapper]:
        """Return list of active adapted MCP tools."""
        return list(self._tools)

    async def start(self) -> list[MCPToolWrapper]:
        """Start and initialize all configured MCP servers concurrently.

        Enforces a 30s timeout per server. Failures on individual servers
        are isolated without preventing other servers or the main application from starting.
        """
        if not self.config.servers:
            return []

        async def _init_single_server(
            name: str, server_cfg: MCPServerConfig
        ) -> list[MCPToolWrapper]:
            try:
                return await asyncio.wait_for(
                    self._connect_and_discover(name, server_cfg),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                sys.stderr.write(
                    f"[ReleaseGuard MCP] Warning: Server '{name}' timed out after 30 seconds during startup, skipping.\n"
                )
                return []
            except Exception as e:
                sys.stderr.write(
                    f"[ReleaseGuard MCP] Warning: Failed to connect to server '{name}': {e}, skipping.\n"
                )
                return []

        results = await asyncio.gather(
            *(
                _init_single_server(name, cfg)
                for name, cfg in self.config.servers.items()
            )
        )

        all_tools: list[MCPToolWrapper] = []
        for server_tools in results:
            all_tools.extend(server_tools)

        self._tools = all_tools
        return list(self._tools)

    async def _connect_and_discover(
        self, name: str, cfg: MCPServerConfig
    ) -> list[MCPToolWrapper]:
        """Connect to a single MCP server, perform handshake, and list tools."""
        stack = AsyncExitStack()
        try:
            session: ClientSession

            if cfg.type == "stdio":
                merged_env = {**os.environ, **cfg.env}
                params = StdioServerParameters(
                    command=cfg.command,
                    args=list(cfg.args),
                    env=merged_env,
                )
                read_stream, write_stream = await stack.enter_async_context(
                    stdio_client(params)
                )
                session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
            elif cfg.type == "http":
                http_client = (
                    create_mcp_http_client(headers=cfg.headers) if cfg.headers else None
                )
                read_stream, write_stream = await stack.enter_async_context(
                    streamable_http_client(url=cfg.url, http_client=http_client)
                )
                session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
            else:
                raise ValueError(f"Unsupported MCP transport type '{cfg.type}'")

            # Perform handshake
            await session.initialize()

            # Discover tools
            list_res = await session.list_tools()
            discovered: list[MCPToolWrapper] = []

            raw_tools = getattr(list_res, "tools", [])
            for t in raw_tools:
                annotations = getattr(t, "annotations", None)
                is_ro = False
                if annotations:
                    is_ro = bool(getattr(annotations, "readOnlyHint", False))

                wrapper = MCPToolWrapper(
                    server_name=name,
                    remote_name=t.name,
                    description=getattr(t, "description", ""),
                    input_schema=getattr(t, "inputSchema", {}),
                    is_read_only=is_ro,
                    session=session,
                )
                discovered.append(wrapper)

            self._exit_stacks[name] = stack
            self._sessions[name] = session
            return discovered

        except Exception:
            # In case of initialization error, ensure stack is unwound immediately
            await stack.aclose()
            raise

    async def close(self) -> None:
        """Gracefully close all active sessions and terminate child processes within 5s."""
        if not self._exit_stacks:
            return

        stacks_to_close = list(self._exit_stacks.values())
        self._exit_stacks.clear()
        self._sessions.clear()
        self._tools.clear()

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(stack.aclose() for stack in stacks_to_close),
                    return_exceptions=True,
                ),
                timeout=5.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            sys.stderr.write(
                f"[ReleaseGuard MCP] Warning: Error or timeout closing MCP connections: {e}\n"
            )
