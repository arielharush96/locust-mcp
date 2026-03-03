# locust-mcp

Load testing for [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) servers using [Locust](https://locust.io/).

`locust-mcp` provides an MCP Streamable HTTP client and a base Locust user class that handles the full MCP session lifecycle: initialize, tool discovery, and tool calls.

## Installation
```bash
git clone https://github.com/Kuadrant/locust-mcp.git
cd locust-mcp
pip install -e .
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TOOL_PREFIX` | `""` | Prefix added to tool names (e.g. `mock_` for gateway routing) |
| `HOST_HEADER` | `""` | Host header override for gateway routing |
| `CALLS_PER_SESSION` | `0` | Tool calls before session restart (0 = persistent) |
| `WARMUP_SECONDS` | `0` | Seconds to warm up before resetting stats (0 = disabled) |


**Methods:**
- `call_next_tool()` — call the next tool in round-robin sequence
- `call_tool(name, args, display_name)` — call a specific tool by name
- `open_session()` / `close_session()` — manual session control
- `ensure_session()` — open session if needed, restart if CPS limit reached

## Mock Servers

The `mock-servers/` directory contains Go-based MCP servers for performance testing:

| Server | Latency | Purpose |
|---|---|---|
| `perf-mock-server` | 0ms | Isolates pure gateway overhead (all measured latency = gateway cost) |
| `perf-mock-server-1s` | 1s | Proves gateway overhead is constant and additive, not proportional |

Both servers expose 10 tools (alpha through juliet) using the `modelcontextprotocol/go-sdk` with Streamable HTTP transport.

### Templates

| Template | Description |
|---|---|
| `templates/rampup.yaml` | Distributed ramp-up (gradual user increase) |
| `templates/rampup-single.yaml` | Single-pod ramp-up |
| `templates/server-direct.yaml` | Single pod, direct to MCP server |
| `templates/gateway.yaml` | Single pod, via MCP Gateway |

### Infrastructure

| File | Description |
|---|---|
| `infrastructure/mock-server.yaml` | HTTPRoute + DestinationRule + MCPServerRegistration for 0ms mock |
| `infrastructure/mock-server-1s.yaml` | Same for 1-second delay mock server |

### Placeholder Variables

All templates use placeholder variables that are replaced by the orchestration script at runtime:

| Variable | Description |
|---|---|
| `__JOB_NAME__` | Kubernetes Job name |
| `__NAMESPACE__` | Target namespace |
| `__USERS__` | Number of concurrent users |
| `__DURATION__` | Test duration in seconds |
| `__HOST_URL__` | Target MCP server URL |
| `__TOOL_PREFIX__` | Tool name prefix for gateway routing |
| `__HOST_HEADER__` | Host header for gateway routing |
| `__CPS__` | Calls per session (0 = same session for all rest of tool calling till the end of the experiment) |
| `__WARMUP__` | Warmup seconds before stats reset |
| `__NUM_WORKERS__` | Number of Locust worker pods (multiple workers let us generate enough concurrent load (8192 users) |
| `__SPAWN_RATE__` | Users spawned per second (ramp-up only) |
| `__TARGET__` | Label value: `server` or `gateway` |


### MCP Gateway Overhead (`experiments/mcp-gateway-overhead/`)

Characterizes the latency overhead introduced by the MCP Gateway (Envoy + Broker + Router) compared to direct MCP server access.

| Script | Description |
|---|---|
| `run_distributed.sh` | Distributed sweep: 9 concurrency levels (2–512), 0ms mock, 5min per level |
| `run_distributed_1s.sh` | Same sweep with 1s-delay mock (proves constant overhead) |
| `run_rampup.sh` | Ramp-up: 0→8192 users at 8/sec, 60s steady-state (saturation test) |
| `run_init_test.sh` | Short test capturing `initialize` and `tools/list` latency |
| `generate_plots.py` | Generates latency/throughput bar charts from sweep results |
| `generate_rampup_plots.py` | Generates time-series plots from ramp-up results |
| `generate_cpu_plots.py` | Generates CPU/memory utilization plots |

Each run script:
1. Deploys mock server and gateway infrastructure
2. Runs Locust jobs at each concurrency level (server-direct + gateway)
3. Collects CSV results and pod metrics
4. Generates comparison plots


## Contributing

Contributions welcome. Please open an issue or PR.


## License

Apache-2.0
