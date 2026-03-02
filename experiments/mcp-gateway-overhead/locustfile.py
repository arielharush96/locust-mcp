"""
MCP Load Tester for perf-mock-server (zero-latency mock).

10 tools, deterministic round-robin, no cluster discovery needed.
All tools return instantly — this experiment isolates MCP Gateway overhead.

Environment variables:
    TOOL_PREFIX:         prefix for tool names ("mock_" via gateway, "" direct)
    HOST_HEADER:         Host header for gateway routing (empty = none)
    CALLS_PER_SESSION:   tool calls per session (0 = infinite)
"""

import os
import time
import logging
from typing import Optional

import gevent
from locust import User, task, between, events

from mcp_client import MCPClient, MCPResponse

logger = logging.getLogger(__name__)

# ── configuration ──────────────────────────────────────────────────────────────

TOOL_PREFIX = os.environ.get("TOOL_PREFIX", "")
HOST_HEADER = os.environ.get("HOST_HEADER", "")
CALLS_PER_SESSION = int(os.environ.get("CALLS_PER_SESSION", "0"))
WARMUP_SECONDS = int(os.environ.get("WARMUP_SECONDS", "0"))


# ── warmup: reset stats after warmup period ──────────────────────────────────

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    if WARMUP_SECONDS > 0:
        def reset_after_warmup():
            logger.info("warmup: %ds, stats will be reset after", WARMUP_SECONDS)
            gevent.sleep(WARMUP_SECONDS)
            environment.runner.stats.reset_all()
            logger.info("warmup complete: stats reset")
        gevent.spawn(reset_after_warmup)

# ── deterministic tool sequence ───────────────────────────────────────────────
#
# 10 zero-latency tools, equal round-robin.
# every user cycles through the same sequence in the same order.

TOOL_SEQUENCE = [
    {"name": "alpha",   "args": {"input": "test"}},
    {"name": "bravo",   "args": {"input": "test"}},
    {"name": "charlie", "args": {"input": "test"}},
    {"name": "delta",   "args": {"input": "test"}},
    {"name": "echo",    "args": {"input": "test"}},
    {"name": "foxtrot", "args": {"input": "test"}},
    {"name": "golf",    "args": {"input": "test"}},
    {"name": "hotel",   "args": {"input": "test"}},
    {"name": "india",   "args": {"input": "test"}},
    {"name": "juliet",  "args": {"input": "test"}},
]

# ── locust reporting ──────────────────────────────────────────────────────────


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


# ── user class ────────────────────────────────────────────────────────────────


class MCPSessionUser(User):
    """
    Deterministic round-robin MCP session user for mock server.

    Each user cycles through TOOL_SEQUENCE in order:
        tool[0], tool[1], ..., tool[9], tool[0], tool[1], ...
    """

    wait_time = between(0.1, 0.5)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mcp: Optional[MCPClient] = None
        self.calls_done = 0
        self.seq_index = 0

    def _open_session(self) -> bool:
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

    def _close_session(self):
        """Close current session."""
        self.mcp = None
        self.calls_done = 0

    @task
    def do_tool_call(self):
        """Execute next tool in the deterministic sequence."""

        # open session if needed
        if self.mcp is None:
            if not self._open_session():
                time.sleep(1)
                return

        # session restart check
        if CALLS_PER_SESSION > 0 and self.calls_done >= CALLS_PER_SESSION:
            self._close_session()
            if not self._open_session():
                time.sleep(1)
                return

        entry = TOOL_SEQUENCE[self.seq_index % len(TOOL_SEQUENCE)]
        self.seq_index += 1

        tool_name = f"{TOOL_PREFIX}{entry['name']}"
        args = dict(entry["args"])

        r = self.mcp.call_tool(tool_name, args)
        _report(f"call:{entry['name']}", r)

        self.calls_done += 1
