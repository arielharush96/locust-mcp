"""
Kubernetes MCP server example: real k8s API-backed tools.

9 tools across heavy/medium/light categories, deterministic round-robin.
Includes cluster discovery to resolve dynamic arguments (namespace, pod name).

Usage:
    # direct to kubernetes-mcp-server
    locust -f locustfile.py --host http://k8s-mcp:8080 --headless -u 16 -r 4 -t 300s

    # via gateway
    TOOL_PREFIX="kube_" HOST_HEADER="k8s.mcp.local" \
        locust -f locustfile.py --host http://gateway:8080 --headless -u 16 -r 4 -t 300s
"""

import os
import time
import logging
import threading
from typing import Dict, Optional

from locust import task, between
from locust_mcp import MCPUser, MCPClient

logger = logging.getLogger(__name__)

TOOL_PREFIX = os.environ.get("TOOL_PREFIX", "kube_")
HOST_HEADER = os.environ.get("HOST_HEADER", "")

# ── cluster discovery ────────────────────────────────────────────────────────

_cluster: Dict = {"namespace": "default", "pod": "", "ready": False}
_discover_lock = threading.Lock()


def _discover(host: str):
    """One-time cluster resource discovery for deterministic arguments."""
    if _cluster["ready"]:
        return

    with _discover_lock:
        if _cluster["ready"]:
            return

        try:
            c = MCPClient(base_url=host, host_header=HOST_HEADER or None, timeout=30.0)
            r = c.initialize()
            if not r.success:
                _cluster["ready"] = True
                return
            c.initialized_notification()

            # discover namespace
            r = c.call_tool(f"{TOOL_PREFIX}namespaces_list", {})
            if r.success and r.data:
                for item in r.data.get("content", []):
                    if item.get("type") != "text":
                        continue
                    for line in item["text"].split("\n"):
                        tok = line.strip().split()
                        if tok and tok[0] not in ("", "NAME", "NAMESPACE", "-"):
                            _cluster["namespace"] = tok[0]
                            break
                    if _cluster["namespace"] != "default":
                        break

            # discover pod
            r = c.call_tool(f"{TOOL_PREFIX}pods_list", {"namespace": _cluster["namespace"]})
            if r.success and r.data:
                for item in r.data.get("content", []):
                    if item.get("type") != "text":
                        continue
                    for line in item["text"].split("\n"):
                        tok = line.strip().split()
                        if tok and tok[0] not in ("", "NAME", "No"):
                            _cluster["pod"] = tok[0]
                            break
                    if _cluster["pod"]:
                        break

            _cluster["ready"] = True
            logger.info("discovered namespace=%s pod=%s", _cluster["namespace"], _cluster["pod"])
        except Exception as e:
            logger.error("discovery failed: %s", e)
            _cluster["ready"] = True


def _resolve_args(template: dict) -> dict:
    """Resolve placeholder arguments."""
    args = dict(template)
    for k, v in list(args.items()):
        if v == "_NS_":
            args[k] = _cluster["namespace"]
        elif v == "_POD_":
            args[k] = _cluster["pod"] if _cluster["pod"] else "unknown"
            if "namespace" not in args:
                args["namespace"] = _cluster["namespace"]
    return args


# ── user class ───────────────────────────────────────────────────────────────


class KubernetesMCPTest(MCPUser):
    """
    Kubernetes MCP server load test.

    9 tools in round-robin: 3 heavy, 3 medium, 3 light.
    """

    wait_time = between(0.1, 0.5)

    tools = [
        {"name": "namespaces_list",        "short": "namespaces",  "args": {}},
        {"name": "events_list",            "short": "events",      "args": {}},
        {"name": "helm_list",              "short": "helm",        "args": {}},
        {"name": "pods_list_in_namespace", "short": "pods",        "args": {"namespace": "_NS_"}},
        {"name": "nodes_top",              "short": "nodes_top",   "args": {}},
        {"name": "pods_top",               "short": "pods_top",    "args": {}},
        {"name": "resources_list",         "short": "resources",   "args": {"apiVersion": "v1", "kind": "Service"}},
        {"name": "pods_log",               "short": "pods_log",    "args": {"name": "_POD_", "tail": 10}},
        {"name": "configuration_view",     "short": "config",      "args": {}},
    ]

    def on_start(self):
        _discover(self.host)

    @task
    def call_tools(self):
        if not self.ensure_session():
            return

        entry = self.tools[self.seq_index % len(self.tools)]
        self.seq_index += 1

        args = _resolve_args(entry.get("args", {}))
        r = self.call_tool(entry["name"], args, display_name=entry.get("short", entry["name"]))
