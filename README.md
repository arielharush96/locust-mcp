# locust-mcp

Load testing for [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) servers using [Locust](https://locust.io/).

`locust-mcp` provides an MCP Streamable HTTP client and a base Locust user class that handles the full MCP session lifecycle: initialize, tool discovery, and tool calls.

## Repository Structure

```
locust-mcp/
├── src/locust_mcp/               # Python package
│   ├── client.py                 # MCP Streamable HTTP client
│   └── user.py                   # Base MCPUser class for Locust
├── examples/                     # Example locustfiles
│   ├── basic/                    # Minimal 2-tool example
│   ├── mock_server/              # 10 zero-latency tools (gateway overhead)
│   └── kubernetes_mcp/           # Real Kubernetes MCP server
├── mock-servers/                 # Mock MCP servers 
│   ├── perf-mock-server/         # 0ms latency - isolates gateway overhead
│   └── perf-mock-server-1s/      # 1s latency - proves constant overhead
├── k8s/                          # Reusable Kubernetes templates (generalized)
│   ├── templates/                # Locust job templates
│   └── infrastructure/           # Mock server gateway integration
├── experiments/                  # Experiment orchestration & analysis
│   └── mcp-gateway-overhead/     # MCP Gateway overhead characterization
│       ├── configs/              # Experiment-specific K8s YAML templates
│       ├── run_distributed.sh    # 0ms overhead sweep (2–512 users)
│       ├── run_distributed_1s.sh # 1s overhead sweep (2–512 users)
│       ├── run_rampup.sh         # Ramp-up saturation test (8192 users)
│       ├── run_init_test.sh      # Initialize/tools_list metrics
│       ├── generate_plots.py     # Overhead comparison bar charts
│       ├── generate_rampup_plots.py  # Ramp-up time-series plots
│       └── generate_cpu_plots.py # CPU/memory utilization plots
├── Dockerfile
├── pyproject.toml
└── LICENSE
```

## Installation

```bash
pip install locust-mcp
```

Or from source:

```bash
git clone https://github.com/Kuadrant/locust-mcp.git
cd locust-mcp
pip install -e .
```

Run it:

```bash
# direct to MCP server
locust -f locustfile.py --host http://localhost:8080 --headless -u 10 -r 2 -t 60s

# via MCP Gateway (with tool prefix and Host header)
TOOL_PREFIX="myprefix_" HOST_HEADER="myserver.mcp.local" \
    locust -f locustfile.py --host http://gateway:8080 --headless -u 10 -r 2 -t 60s
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
| `templates/distributed.yaml` | Master + workers for high-concurrency tests |
| `templates/rampup.yaml` | Distributed ramp-up (gradual user increase) |
| `templates/rampup-single.yaml` | Single-pod ramp-up (simpler, no worker coordination) |
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
| `__CPS__` | Calls per session (0 = persistent) |
| `__WARMUP__` | Warmup seconds before stats reset |
| `__NUM_WORKERS__` | Number of Locust worker pods (distributed only) |
| `__SPAWN_RATE__` | Users spawned per second (ramp-up only) |
| `__TARGET__` | Label value: `server` or `gateway` |

## Experiments

The `experiments/` directory contains complete, reproducible experiment suites with orchestration scripts, analysis tools, and Kubernetes-specific config templates.

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

## Examples

- **[basic](examples/basic/)** — minimal example with two tools
- **[mock_server](examples/mock_server/)** — 10 zero-latency tools for overhead measurement
- **[kubernetes_mcp](examples/kubernetes_mcp/)** — real Kubernetes MCP server with cluster discovery

## MCP Protocol Support

- Transport: [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http)
- JSON-RPC 2.0 over HTTP POST
- SSE (Server-Sent Events) response parsing
- Session management via `Mcp-Session-Id` header
- Protocol version: `2025-03-26`

## Contributing

Contributions welcome. Please open an issue or PR.

```bash
# dev setup
git clone https://github.com/Kuadrant/locust-mcp.git
cd locust-mcp
pip install -e ".[dev]"
```

## License

Apache-2.0
