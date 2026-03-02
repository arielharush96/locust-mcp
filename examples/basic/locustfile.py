"""
Basic example: load test any MCP server with a simple tool list.

Usage:
    pip install locust-mcp

    # direct to server
    locust -f locustfile.py --host http://localhost:8080 --headless -u 10 -r 2 -t 60s

    # via MCP Gateway
    TOOL_PREFIX="myprefix_" HOST_HEADER="myserver.mcp.local" \
        locust -f locustfile.py --host http://gateway:8080 --headless -u 10 -r 2 -t 60s
"""

from locust import task
from locust_mcp import MCPUser


class BasicMCPTest(MCPUser):
    """Load test with round-robin tool calls."""

    # define your tools here
    tools = [
        {"name": "echo", "args": {"message": "hello"}},
        {"name": "time", "args": {}},
    ]

    @task
    def call_tools(self):
        self.call_next_tool()
