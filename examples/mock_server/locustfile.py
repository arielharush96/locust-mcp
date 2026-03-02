"""
Mock server example: 10 zero-latency tools for gateway overhead measurement.

This is the exact workload used to characterize MCP Gateway overhead
with a perf-mock-server (Go, static responses, 0ms processing).

Usage:
    # direct to mock server
    locust -f locustfile.py --host http://mock-server:8080 --headless -u 64 -r 8 -t 300s

    # via gateway
    TOOL_PREFIX="mock_" HOST_HEADER="mock.mcp.local" \
        locust -f locustfile.py --host http://gateway:8080 --headless -u 64 -r 8 -t 300s
"""

from locust import task
from locust_mcp import MCPUser


class MockServerTest(MCPUser):
    """Zero-latency mock: isolates pure gateway overhead."""

    tools = [
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

    @task
    def call_tools(self):
        self.call_next_tool()
