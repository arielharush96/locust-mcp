"""
Base MCPUser class for Locust load testing of MCP servers.

Handles the full MCP session lifecycle automatically:
    initialize → initialized notification → tools/list → tool calls

Subclass MCPUser and define your tool sequence to create a load test.

Example:
    from locust import task
    from locust_mcp import MCPUser

    class MyTest(MCPUser):
        tools = [
            {"name": "my_tool", "args": {"key": "value"}},
        ]

        @task
        def call_tools(self):
            self.call_next_tool()
"""

import os
import time
import logging
from typing import Optional

from locust import User, task, between, events

from locust_mcp.client import MCPClient, MCPResponse

logger = logging.getLogger(__name__)

# ── configuration via environment variables ──────────────────────────────────

TOOL_PREFIX = os.environ.get("TOOL_PREFIX", "")
HOST_HEADER = os.environ.get("HOST_HEADER", "")
CALLS_PER_SESSION = int(os.environ.get("CALLS_PER_SESSION", "0"))


def _report(name: str, resp: MCPResponse):
    """Fire a Locust request event for statistics tracking."""
    if resp.success:
        events.request.fire(
            request_type="MCP",
            name=name,
            response_time=resp.response_time_ms,
            response_length=len(str(resp.data)) if resp.data else 0,
            exception=None,
            context={},
        )
    else:
        events.request.fire(
            request_type="MCP",
            name=f"FAIL:{name}",
            response_time=resp.response_time_ms,
            response_length=0,
            exception=Exception(resp.error),
            context={},
        )


class MCPUser(User):
    """
    Base Locust user for MCP load testing.

    Manages the full MCP session lifecycle: initialize, tools/list, and
    tool calls with automatic session restart based on CALLS_PER_SESSION.

    Subclasses should define:
        tools: list of dicts with "name" and "args" keys.

    Environment variables:
        TOOL_PREFIX:        prefix added to tool names (e.g. "kube_" for gateway)
        HOST_HEADER:        Host header override for gateway routing
        CALLS_PER_SESSION:  tool calls before session restart (0 = persistent)

    Example:
        class MyTest(MCPUser):
            tools = [
                {"name": "echo", "args": {"msg": "hello"}},
                {"name": "time", "args": {}},
            ]

            @task
            def run(self):
                self.call_next_tool()
    """

    abstract = True
    wait_time = between(0.1, 0.5)

    # override in subclass: list of {"name": str, "args": dict}
    tools: list = []

    # optional: override to customize the display name in Locust stats
    tool_display_name = None  # callable(entry) -> str, or None for default

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mcp: Optional[MCPClient] = None
        self.calls_done = 0
        self.seq_index = 0

    def open_session(self) -> bool:
        """Start a new MCP session. Returns True on success."""
        self.mcp = MCPClient(
            base_url=self.host,
            host_header=HOST_HEADER or None,
        )

        r = self.mcp.initialize()
        _report("initialize", r)
        if not r.success:
            self.mcp = None
            return False

        self.mcp.initialized_notification()

        r = self.mcp.list_tools()
        _report("tools/list", r)
        if not r.success:
            self.mcp = None
            return False

        self.calls_done = 0
        return True

    def close_session(self):
        """Close current session."""
        self.mcp = None
        self.calls_done = 0

    def ensure_session(self) -> bool:
        """Ensure an active session exists, opening one if needed."""
        if self.mcp is None:
            if not self.open_session():
                time.sleep(1)
                return False

        if CALLS_PER_SESSION > 0 and self.calls_done >= CALLS_PER_SESSION:
            self.close_session()
            if not self.open_session():
                time.sleep(1)
                return False

        return True

    def call_next_tool(self) -> Optional[MCPResponse]:
        """
        Call the next tool in the round-robin sequence.

        Handles session management automatically. Returns the MCPResponse
        or None if the session could not be established.
        """
        if not self.ensure_session():
            return None

        if not self.tools:
            logger.error("no tools defined — set the 'tools' class attribute")
            time.sleep(1)
            return None

        entry = self.tools[self.seq_index % len(self.tools)]
        self.seq_index += 1

        tool_name = f"{TOOL_PREFIX}{entry['name']}"
        args = dict(entry.get("args", {}))

        r = self.mcp.call_tool(tool_name, args)

        # display name
        if self.tool_display_name:
            display = self.tool_display_name(entry)
        else:
            display = entry.get("short", entry["name"])
        _report(f"call:{display}", r)

        self.calls_done += 1
        return r

    def call_tool(self, name: str, args: Optional[dict] = None,
                  display_name: Optional[str] = None) -> Optional[MCPResponse]:
        """
        Call a specific tool by name.

        Handles session management automatically.

        Args:
            name: Tool name (TOOL_PREFIX is added automatically).
            args: Tool arguments.
            display_name: Name shown in Locust stats (default: tool name).
        """
        if not self.ensure_session():
            return None

        tool_name = f"{TOOL_PREFIX}{name}"
        r = self.mcp.call_tool(tool_name, args or {})
        _report(f"call:{display_name or name}", r)

        self.calls_done += 1
        return r
