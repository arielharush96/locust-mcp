#!/usr/bin/env bash
#
# Ramp-Up Under Load test for perf-mock-server.
#
# Ramp from 0 to MAX_USERS at SPAWN_RATE user/sec, then hold at peak for
# HOLD_DURATION seconds. Each user maintains a persistent MCP session (CPS=0).
#
# Two jobs total: one targeting the server directly, one through the gateway.
#
# Usage:
#   ./run_rampup.sh              # run both targets
#   ./run_rampup.sh -v           # verbose (stream master logs)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/configs"
LOAD_TESTER_DIR="${SCRIPT_DIR}/../mcp-load-tester"
MOCK_SERVER_DIR="${SCRIPT_DIR}/../phase2/perf-mock-server"
RESULTS_BASE="${SCRIPT_DIR}/results"
NAMESPACE="performance-test"

TIMESTAMP=$(date +"%d_%m_%H_%M")
RESULTS_DIR="${RESULTS_BASE}/${TIMESTAMP}_rampup"

VERBOSE=false
for arg in "$@"; do
    case "$arg" in
        -v|--verbose) VERBOSE=true ;;
    esac
done

# ── experiment parameters ─────────────────────────────────────────────────────

MAX_USERS=8192
SPAWN_RATE=8                       # users per second (slow ramp for clean graph)
HOLD_DURATION=60                   # seconds at peak load
RAMP_DURATION=$(( MAX_USERS / SPAWN_RATE ))  # 1024s to reach peak
TOTAL_DURATION=$(( RAMP_DURATION + HOLD_DURATION ))  # 1084s total
CPS=0

NUM_WORKERS=8                      # 8 pods × 1024 users each
USERS_PER_WORKER=$(( MAX_USERS / NUM_WORKERS ))

TARGETS=("gateway")
TOTAL_JOBS=${#TARGETS[@]}
CURRENT_JOB=0
START_TIME=$(date +%s)

# ── prometheus config ─────────────────────────────────────────────────────────

PROM_URL="https://prometheus-k8s-openshift-monitoring.apps.ariel-train-sno.ibm.rhperfscale.org"
PROM_TOKEN=""
PROM_NAMESPACES="performance-test|mcp-system|gateway-system|istio-system"

prom_refresh_token() {
    PROM_TOKEN=$(kubectl create token prometheus-k8s -n openshift-monitoring 2>/dev/null || true)
}

prom_query() {
    local query="$1"
    curl -sk --max-time 5 \
        -H "Authorization: Bearer ${PROM_TOKEN}" \
        "${PROM_URL}/api/v1/query" \
        --data-urlencode "query=${query}" 2>/dev/null
}

# ── cpu monitor ───────────────────────────────────────────────────────────────

CPU_MONITOR_PID=""

start_cpu_monitor() {
    local out_file="$1"
    echo "timestamp,namespace,pod,cpu_millicores,memory_MiB" > "${out_file}"

    local throttle_file="${out_file%.csv}_throttle.csv"
    echo "timestamp,namespace,pod,throttled_pct" > "${throttle_file}"

    prom_refresh_token

    (
        set +e
        local token_refresh_counter=0
        while true; do
            token_refresh_counter=$(( token_refresh_counter + 1 ))
            if [[ $(( token_refresh_counter % 150 )) -eq 0 ]]; then
                PROM_TOKEN=$(kubectl create token prometheus-k8s -n openshift-monitoring 2>/dev/null || true)
            fi

            ts=$(date +%Y-%m-%dT%H:%M:%S)

            for ns in performance-test mcp-system gateway-system istio-system; do
                kubectl top pods -n "$ns" --no-headers 2>/dev/null | \
                    awk -v ts="$ts" -v ns="$ns" '{
                        cpu=$2; mem=$3;
                        sub(/m$/, "", cpu);
                        sub(/Mi$/, "", mem);
                        print ts","ns","$1","cpu","mem
                    }' || true
            done >> "${out_file}" 2>/dev/null

            throttle_json=$(prom_query "sum(rate(container_cpu_cfs_throttled_periods_total{namespace=~\"${PROM_NAMESPACES}\",container!=\"POD\",container!=\"\"}[5m])) by (namespace,pod) / sum(rate(container_cpu_cfs_periods_total{namespace=~\"${PROM_NAMESPACES}\",container!=\"POD\",container!=\"\"}[5m])) by (namespace,pod) * 100")

            if [[ -n "$throttle_json" ]]; then
                python3 -c "
import json, sys
try:
    data = json.loads(sys.argv[1])
    ts = sys.argv[2]
    for r in data.get('data',{}).get('result',[]):
        ns = r['metric'].get('namespace','')
        pod = r['metric'].get('pod','')
        val = float(r['value'][1])
        if val > 0:
            print(f'{ts},{ns},{pod},{val:.1f}')
except Exception:
    pass
" "$throttle_json" "$ts" >> "${throttle_file}" 2>/dev/null
            fi

            sleep 2
        done
    ) &
    CPU_MONITOR_PID=$!
    log "cpu monitor started"
}

stop_cpu_monitor() {
    if [[ -n "${CPU_MONITOR_PID}" ]]; then
        kill "${CPU_MONITOR_PID}" 2>/dev/null || true
        wait "${CPU_MONITOR_PID}" 2>/dev/null || true
        CPU_MONITOR_PID=""
        log "cpu monitor stopped"
    fi
}

# ── helpers ───────────────────────────────────────────────────────────────────

log() { echo "[$(date +%H:%M:%S)] $*"; }

elapsed_str() {
    local now=$(date +%s)
    local elapsed=$(( now - START_TIME ))
    printf "%dm%02ds" $(( elapsed / 60 )) $(( elapsed % 60 ))
}

generate_rampup_yaml() {
    local job_name="$1"
    local target="$2"

    local host_url tool_prefix host_header
    if [[ "$target" == "server" ]]; then
        host_url="http://perf-mock-server.performance-test.svc.cluster.local:8080"
        tool_prefix=""
        host_header=""
    else
        host_url="http://mcp-gateway-istio.gateway-system.svc.cluster.local:8080"
        tool_prefix="mock_"
        host_header="perf-mock.mcp.local"
    fi

    sed \
        -e "s|__JOB_NAME__|${job_name}|g" \
        -e "s|__TARGET__|${target}|g" \
        -e "s|__TOOL_PREFIX__|${tool_prefix}|g" \
        -e "s|__HOST_HEADER__|${host_header}|g" \
        -e "s|__HOST_URL__|${host_url}|g" \
        -e "s|__CPS__|${CPS}|g" \
        -e "s|__USERS__|${MAX_USERS}|g" \
        -e "s|__SPAWN_RATE__|${SPAWN_RATE}|g" \
        -e "s|__DURATION__|${TOTAL_DURATION}|g" \
        -e "s|__NUM_WORKERS__|${NUM_WORKERS}|g" \
        "${CONFIG_DIR}/template_rampup.yaml"
}

cleanup_distributed() {
    local job_name="$1"
    kubectl delete job "${job_name}-master" -n "${NAMESPACE}" 2>/dev/null || true
    kubectl delete job "${job_name}-workers" -n "${NAMESPACE}" 2>/dev/null || true
    kubectl delete svc "${job_name}-master" -n "${NAMESPACE}" 2>/dev/null || true
}

wait_for_master() {
    local job_name="$1"
    log "    waiting for ${NUM_WORKERS} worker(s) to connect..."
    sleep 20

    log "    waiting for job/${job_name}-master (${RAMP_DURATION}s ramp + ${HOLD_DURATION}s hold = ${TOTAL_DURATION}s total)..."

    kubectl wait --for=condition=complete "job/${job_name}-master" -n "${NAMESPACE}" --timeout=$(( TOTAL_DURATION + 120 ))s &>/dev/null &
    local wait_pid=$!

    local logs_pid=""
    if [[ "$VERBOSE" == "true" ]]; then
        echo ""
        log "    ── master logs ──"
        sleep 5
        kubectl logs -f "job/${job_name}-master" -n "${NAMESPACE}" 2>/dev/null &
        logs_pid=$!
    fi

    local waited=0
    while kill -0 "$wait_pid" 2>/dev/null; do
        if [[ "$VERBOSE" != "true" ]]; then
            local phase="ramp"
            [[ $waited -ge $RAMP_DURATION ]] && phase="hold"
            printf "\r    [%s] %ds/%ds (%s) | elapsed: %s   " \
                "$(date +%H:%M:%S)" "$waited" "$TOTAL_DURATION" "$phase" "$(elapsed_str)"
        fi
        sleep 5
        waited=$(( waited + 5 ))
    done

    [[ -n "$logs_pid" ]] && { kill "$logs_pid" 2>/dev/null || true; wait "$logs_pid" 2>/dev/null || true; log "    ── end logs ──"; }

    if wait "$wait_pid"; then return 0; else log "    ERROR: master job failed"; return 1; fi
}

collect_results() {
    local job_name="$1"
    local out_dir="$2"
    local label="$3"
    mkdir -p "${out_dir}"

    local full_log="${out_dir}/${label}.log"
    kubectl logs -n "${NAMESPACE}" "job/${job_name}-master" > "${full_log}" 2>&1

    awk '/^===CSV_STATS===$/{f=1; next} /^===CSV_STATS_HISTORY===$/{f=0} f' "${full_log}" > "${out_dir}/${label}_stats.csv" 2>/dev/null || true
    awk '/^===CSV_STATS_HISTORY===$/{f=1; next} /^===CSV_FAILURES===$/{f=0} f' "${full_log}" > "${out_dir}/${label}_stats_history.csv" 2>/dev/null || true
    awk '/^===CSV_FAILURES===$/{f=1; next} /^===CSV_END===$/{f=0} f' "${full_log}" > "${out_dir}/${label}_failures.csv" 2>/dev/null || true

    if [[ -s "${out_dir}/${label}_stats.csv" ]]; then
        log "    collected: ${label}_stats.csv ($(wc -l < "${out_dir}/${label}_stats.csv") lines)"
    else
        log "    WARNING: no stats CSV for ${label}"
    fi
    if [[ -s "${out_dir}/${label}_stats_history.csv" ]]; then
        log "    collected: ${label}_stats_history.csv ($(wc -l < "${out_dir}/${label}_stats_history.csv") lines)"
    else
        log "    WARNING: no history CSV for ${label}"
    fi
}

# ── main ──────────────────────────────────────────────────────────────────────

log "=== Ramp-Up Under Load Test ==="
log "max users:     ${MAX_USERS}"
log "spawn rate:    ${SPAWN_RATE} user/sec"
log "ramp duration: ${RAMP_DURATION}s (0 → ${MAX_USERS})"
log "hold duration: ${HOLD_DURATION}s (at ${MAX_USERS} users)"
log "total duration:${TOTAL_DURATION}s per target"
log "mode:          distributed (${NUM_WORKERS} workers × ${USERS_PER_WORKER} users)"
log "targets:       ${TARGETS[*]}"
log "cps:           ${CPS} (persistent session)"
log "est. total:    ~$(( TOTAL_JOBS * (TOTAL_DURATION + 60) / 60 ))min"
log "results:       ${RESULTS_DIR}"
log ""

# apply mock server deployment
log "applying perf-mock-server deployment..."
kubectl apply -f "${MOCK_SERVER_DIR}/deployment.yaml"
log ""

# apply infrastructure
log "applying infrastructure (HTTPRoute + MCPServerRegistration + DestinationRule)..."
kubectl apply -f "${CONFIG_DIR}/infrastructure.yaml"
log "waiting for MCPServerRegistration..."
for i in $(seq 1 30); do
    ready=$(kubectl get mcpserverregistration perf-mock-server -n "${NAMESPACE}" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
    tools=$(kubectl get mcpserverregistration perf-mock-server -n "${NAMESPACE}" -o jsonpath='{.status.discoveredTools}' 2>/dev/null || echo "0")
    if [[ "$ready" == "True" && "$tools" -gt 0 ]]; then
        log "MCPServerRegistration ready (${tools} tools)"
        break
    fi
    [[ $i -eq 30 ]] && log "WARNING: MCPServerRegistration not ready, proceeding"
    sleep 2
done
log ""

# update configmap
log "updating locust-scripts-mock configmap..."
kubectl delete configmap locust-scripts-mock -n "${NAMESPACE}" 2>/dev/null || true
kubectl create configmap locust-scripts-mock -n "${NAMESPACE}" \
    --from-file=mcp_client.py="${LOAD_TESTER_DIR}/mcp_client.py" \
    --from-file=locustfile.py="${SCRIPT_DIR}/locustfile.py"
log "configmap updated"
log ""

# restart mock server for clean state
log "restarting perf-mock-server..."
kubectl rollout restart deployment/perf-mock-server -n "${NAMESPACE}" 2>/dev/null || true
kubectl rollout status deployment/perf-mock-server -n "${NAMESPACE}" --timeout=60s 2>/dev/null || true
log "perf-mock-server ready"
log ""

# restart broker-router so it reconnects to fresh mock server
log "restarting broker-router for clean tool discovery..."
kubectl rollout restart deployment/mcp-gateway-broker-router -n mcp-system 2>/dev/null || true
kubectl rollout status deployment/mcp-gateway-broker-router -n mcp-system --timeout=120s 2>/dev/null || true

# verify broker has registered mock_ tools before starting test
log "verifying broker tool registry..."
for i in $(seq 1 30); do
    mock_tools=$(kubectl exec -n mcp-system deploy/mcp-gateway-broker-router -- wget -q -O- http://localhost:8080/status 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
for s in d.get('servers',[]):
    if 'perf-mock-server' == s.get('name','').split('/')[-1] and s.get('ready'):
        print(s.get('totalTools',0)); break
else:
    print(0)
" 2>/dev/null || echo "0")
    if [[ "$mock_tools" -gt 0 ]]; then
        log "broker ready: perf-mock-server registered with ${mock_tools} tools"
        break
    fi
    [[ $i -eq 30 ]] && { log "ERROR: broker failed to discover mock tools after 60s — aborting"; exit 1; }
    sleep 2
done
log ""

# save metadata
mkdir -p "${RESULTS_DIR}"
cat > "${RESULTS_DIR}/metadata.txt" <<EOF
experiment:      ramp-up under load
timestamp:       ${TIMESTAMP}
server:          perf-mock-server (10 zero-latency tools)
max_users:       ${MAX_USERS}
spawn_rate:      ${SPAWN_RATE} user/sec
ramp_duration:   ${RAMP_DURATION}s
hold_duration:   ${HOLD_DURATION}s
total_duration:  ${TOTAL_DURATION}s
workers:         ${NUM_WORKERS} (${USERS_PER_WORKER} users each)
mode:            distributed
cps:             ${CPS}
gateway_prefix:  mock_
gateway_host:    perf-mock.mcp.local
tools:           alpha bravo charlie delta echo foxtrot golf hotel india juliet
EOF

# archive configs
mkdir -p "${RESULTS_DIR}/configs"
cp "${CONFIG_DIR}/infrastructure.yaml" "${RESULTS_DIR}/configs/"
cp "${CONFIG_DIR}/template_rampup.yaml" "${RESULTS_DIR}/configs/"
cp "${MOCK_SERVER_DIR}/deployment.yaml" "${RESULTS_DIR}/configs/"
cp "${SCRIPT_DIR}/locustfile.py" "${RESULTS_DIR}/configs/"
cp "${LOAD_TESTER_DIR}/mcp_client.py" "${RESULTS_DIR}/configs/"
cp "${SCRIPT_DIR}/run_rampup.sh" "${RESULTS_DIR}/configs/"

# start cpu monitoring
start_cpu_monitor "${RESULTS_DIR}/cpu_usage.csv"
trap 'stop_cpu_monitor' EXIT

for target in "${TARGETS[@]}"; do
    job_name="rampup-${target}"

    CURRENT_JOB=$(( CURRENT_JOB + 1 ))
    log ""
    log "━━━ [${CURRENT_JOB}/${TOTAL_JOBS}] ramp-up → ${target} ━━━"

    cleanup_distributed "${job_name}"

    gen_yaml="${RESULTS_DIR}/${target}_job.yaml"
    generate_rampup_yaml "${job_name}" "${target}" > "${gen_yaml}"
    kubectl apply -f "${gen_yaml}"

    echo ""
    if wait_for_master "${job_name}"; then
        echo ""
        log "    completed, collecting results..."
    fi

    collect_results "${job_name}" "${RESULTS_DIR}" "${target}"
    cleanup_distributed "${job_name}"
done

# ── generate plots ────────────────────────────────────────────────────────────

stop_cpu_monitor

if [[ -f "${SCRIPT_DIR}/generate_rampup_plots.py" ]]; then
    log ""
    log "generating ramp-up plots..."
    python3 "${SCRIPT_DIR}/generate_rampup_plots.py" "${RESULTS_DIR}" || log "WARNING: ramp-up plot generation failed"
fi

if [[ -f "${SCRIPT_DIR}/generate_cpu_plots.py" ]]; then
    log "generating cpu/memory utilization plots..."
    python3 "${SCRIPT_DIR}/generate_cpu_plots.py" "${RESULTS_DIR}" || log "WARNING: cpu plot generation failed"
fi

log ""
log "=== Done ==="
log "elapsed: $(elapsed_str)"
log "results: ${RESULTS_DIR}"
ls -la "${RESULTS_DIR}"
