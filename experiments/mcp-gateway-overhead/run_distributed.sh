#!/usr/bin/env bash
#
# Distributed Locust Performance Test for perf-mock-server.
#
# Uses Locust master + N workers to avoid Python GIL saturation.
# Workers scale with concurrency: max 32 users per worker, minimum 1 worker.
#
# Usage:
#   ./run_distributed.sh              # all concurrency levels
#   ./run_distributed.sh -v           # verbose (stream master logs)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/configs"
LOAD_TESTER_DIR="${SCRIPT_DIR}/../mcp-load-tester"
MOCK_SERVER_DIR="${SCRIPT_DIR}/../phase2/perf-mock-server"
RESULTS_BASE="${SCRIPT_DIR}/results"
NAMESPACE="performance-test"

TIMESTAMP=$(date +"%d_%m_%H_%M")
RESULTS_DIR="${RESULTS_BASE}/${TIMESTAMP}"

VERBOSE=false
for arg in "$@"; do
    case "$arg" in
        -v|--verbose) VERBOSE=true ;;
        *)
            echo "Usage: $0 [-v|--verbose]"
            exit 1
            ;;
    esac
done

# ── experiment parameters ─────────────────────────────────────────────────────

CONCURRENCY_LEVELS=(2 4 8 16 32 64 128 256 512)
USERS_PER_WORKER=32
TEST_DURATION=300
WARMUP=60
CPS=0
TARGETS=("server" "gateway")

TOTAL_JOBS=$(( ${#CONCURRENCY_LEVELS[@]} * ${#TARGETS[@]} ))
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

    local node_file="${out_file%.csv}_node.txt"
    {
        echo "=== Node Capacity ==="
        kubectl describe node | grep -A 8 "Capacity:"
        echo ""
        echo "=== Node Allocatable ==="
        kubectl describe node | grep -A 8 "Allocatable:"
        echo ""
        echo "=== Node Usage ==="
        kubectl top node 2>/dev/null || echo "(metrics-server not available)"
    } > "${node_file}" 2>/dev/null

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
    log "cpu monitor started → $(basename "${out_file}")"
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

eta_str() {
    local now=$(date +%s)
    local elapsed=$(( now - START_TIME ))
    if [[ $CURRENT_JOB -eq 0 ]]; then
        echo "calculating..."
        return
    fi
    local avg_per_job=$(( elapsed / CURRENT_JOB ))
    local remaining=$(( (TOTAL_JOBS - CURRENT_JOB) * avg_per_job ))
    printf "%dm%02ds" $(( remaining / 60 )) $(( remaining % 60 ))
}

progress_bar() {
    local pct=$(( CURRENT_JOB * 100 / TOTAL_JOBS ))
    local filled=$(( pct / 5 ))
    local empty=$(( 20 - filled ))
    printf "[%-20s] %d%% (%d/%d)" \
        "$(printf '#%.0s' $(seq 1 $filled 2>/dev/null) || true)$(printf '.%.0s' $(seq 1 $empty 2>/dev/null) || true)" \
        "$pct" "$CURRENT_JOB" "$TOTAL_JOBS"
}

calc_workers() {
    local users="$1"
    local workers=$(( (users + USERS_PER_WORKER - 1) / USERS_PER_WORKER ))
    if [[ $workers -lt 1 ]]; then
        workers=1
    fi
    echo "$workers"
}

generate_distributed_yaml() {
    local job_name="$1"
    local target="$2"
    local users="$3"
    local num_workers="$4"

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

    local total_duration=$(( TEST_DURATION + WARMUP ))

    sed \
        -e "s|__JOB_NAME__|${job_name}|g" \
        -e "s|__TARGET__|${target}|g" \
        -e "s|__TOOL_PREFIX__|${tool_prefix}|g" \
        -e "s|__HOST_HEADER__|${host_header}|g" \
        -e "s|__HOST_URL__|${host_url}|g" \
        -e "s|__CPS__|${CPS}|g" \
        -e "s|__USERS__|${users}|g" \
        -e "s|__DURATION__|${total_duration}|g" \
        -e "s|__WARMUP__|${WARMUP}|g" \
        -e "s|__NUM_WORKERS__|${num_workers}|g" \
        "${CONFIG_DIR}/template_distributed.yaml"
}

cleanup_distributed() {
    local job_name="$1"
    kubectl delete job "${job_name}-master" -n "${NAMESPACE}" 2>/dev/null || true
    kubectl delete job "${job_name}-workers" -n "${NAMESPACE}" 2>/dev/null || true
    kubectl delete svc "${job_name}-master" -n "${NAMESPACE}" 2>/dev/null || true
}

wait_for_master() {
    local job_name="$1"

    local total_dur=$(( TEST_DURATION + WARMUP ))
    log "    waiting for master job/${job_name}-master (${WARMUP}s warmup + ${TEST_DURATION}s test)..."

    kubectl wait --for=condition=complete "job/${job_name}-master" -n "${NAMESPACE}" --timeout=$(( total_dur + 120 ))s &>/dev/null &
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
            local pbar
            pbar=$(progress_bar)
            printf "\r    [%s] %ds/%ds | %s | elapsed: %s | ETA: %s   " \
                "$(date +%H:%M:%S)" "$waited" "$TEST_DURATION" \
                "$pbar" "$(elapsed_str)" "$(eta_str)"
        fi
        sleep 5
        waited=$(( waited + 5 ))
    done

    if [[ -n "$logs_pid" ]]; then
        kill "$logs_pid" 2>/dev/null || true
        wait "$logs_pid" 2>/dev/null || true
        log "    ── end logs ──"
    fi

    if wait "$wait_pid"; then
        return 0
    else
        log "    ERROR: master job failed"
        return 1
    fi
}

collect_results() {
    local job_name="$1"
    local out_dir="$2"
    local label="$3"

    mkdir -p "${out_dir}"

    local full_log="${out_dir}/${label}.log"
    kubectl logs -n "${NAMESPACE}" "job/${job_name}-master" > "${full_log}" 2>&1

    local stats_csv="${out_dir}/${label}_stats.csv"
    local history_csv="${out_dir}/${label}_stats_history.csv"
    local failures_csv="${out_dir}/${label}_failures.csv"

    awk '/^===CSV_STATS===$/{found=1; next} /^===CSV_STATS_HISTORY===$/{found=0} found' \
        "${full_log}" > "${stats_csv}" 2>/dev/null || true
    awk '/^===CSV_STATS_HISTORY===$/{found=1; next} /^===CSV_FAILURES===$/{found=0} found' \
        "${full_log}" > "${history_csv}" 2>/dev/null || true
    awk '/^===CSV_FAILURES===$/{found=1; next} /^===CSV_END===$/{found=0} found' \
        "${full_log}" > "${failures_csv}" 2>/dev/null || true

    if [[ -s "${stats_csv}" ]]; then
        log "    collected: ${label}_stats.csv ($(wc -l < "${stats_csv}") lines)"
    else
        log "    WARNING: no stats CSV for ${label}"
    fi
    if [[ -s "${history_csv}" ]]; then
        log "    collected: ${label}_stats_history.csv ($(wc -l < "${history_csv}") lines)"
    else
        log "    WARNING: no history CSV for ${label}"
    fi
}

# ── main ──────────────────────────────────────────────────────────────────────

log "=== Distributed Mock Perf Test ==="
log "concurrency:   ${CONCURRENCY_LEVELS[*]}"
log "users/worker:  ${USERS_PER_WORKER}"
log "targets:       ${TARGETS[*]}"
log "total jobs:    ${TOTAL_JOBS}"
log "warmup:        ${WARMUP}s (stats reset after warmup)"
log "measurement:   ${TEST_DURATION}s per job"
log "total locust:  $(( TEST_DURATION + WARMUP ))s per job (warmup + measurement)"
log "est. total:    ~$(( TOTAL_JOBS * (TEST_DURATION + WARMUP + 60) / 60 ))min"
log "results:       ${RESULTS_DIR}"
log ""

# show worker plan
log "worker plan:"
for users in "${CONCURRENCY_LEVELS[@]}"; do
    workers=$(calc_workers "$users")
    log "    u${users}: ${workers} worker(s) × $(( users / workers )) users/worker"
done
log ""

# apply mock server deployment (ensures latest resource config)
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

# restart mock server
log "restarting perf-mock-server..."
kubectl rollout restart deployment/perf-mock-server -n "${NAMESPACE}" 2>/dev/null || true
kubectl rollout status deployment/perf-mock-server -n "${NAMESPACE}" --timeout=60s 2>/dev/null || true
log "perf-mock-server ready"
log ""

# save metadata
mkdir -p "${RESULTS_DIR}"
cat > "${RESULTS_DIR}/metadata.txt" <<EOF
experiment:      mcp_gateway_mock_perf (distributed locust)
timestamp:       ${TIMESTAMP}
server:          perf-mock-server (10 zero-latency tools)
concurrency:     ${CONCURRENCY_LEVELS[*]}
users_per_worker: ${USERS_PER_WORKER}
warmup:          ${WARMUP}s
measurement:     ${TEST_DURATION}s
cps:             ${CPS}
gateway_prefix:  mock_
gateway_host:    perf-mock.mcp.local
tools:           alpha bravo charlie delta echo foxtrot golf hotel india juliet
EOF

# archive configs
log "archiving configs and scripts..."
mkdir -p "${RESULTS_DIR}/configs"
cp "${CONFIG_DIR}/infrastructure.yaml" "${RESULTS_DIR}/configs/"
cp "${CONFIG_DIR}/template_distributed.yaml" "${RESULTS_DIR}/configs/"
cp "${MOCK_SERVER_DIR}/deployment.yaml" "${RESULTS_DIR}/configs/"
cp "${SCRIPT_DIR}/locustfile.py" "${RESULTS_DIR}/configs/"
cp "${LOAD_TESTER_DIR}/mcp_client.py" "${RESULTS_DIR}/configs/"
cp "${SCRIPT_DIR}/run_distributed.sh" "${RESULTS_DIR}/configs/"
log "configs archived"
log ""

# start cpu monitoring
start_cpu_monitor "${RESULTS_DIR}/cpu_usage.csv"
trap 'stop_cpu_monitor' EXIT

for users in "${CONCURRENCY_LEVELS[@]}"; do
    num_workers=$(calc_workers "$users")
    users_dir="${RESULTS_DIR}/cps0/u${users}"
    mkdir -p "${users_dir}"

    log "━━━ u${users} (${num_workers} workers) ━━━"

    # restart mock server between levels → fresh process, zero accumulated memory, no GC
    log "  restarting perf-mock-server (clean GC state)..."
    kubectl rollout restart deployment/perf-mock-server -n "${NAMESPACE}" 2>/dev/null || true
    kubectl rollout status deployment/perf-mock-server -n "${NAMESPACE}" --timeout=60s 2>/dev/null || true

    for target in "${TARGETS[@]}"; do
        job_name="dist-u${users}-${target}"

        CURRENT_JOB=$(( CURRENT_JOB + 1 ))
        log ""
        log "  [${CURRENT_JOB}/${TOTAL_JOBS}] u${users}/${target}"
        log "    elapsed: $(elapsed_str) | ETA: $(eta_str)"

        cleanup_distributed "${job_name}"

        gen_yaml="${users_dir}/${target}_job.yaml"
        generate_distributed_yaml "${job_name}" "${target}" "${users}" "${num_workers}" > "${gen_yaml}"
        kubectl apply -f "${gen_yaml}"

        # give workers time to register with master
        log "    waiting for ${num_workers} worker(s) to connect..."
        sleep $(( num_workers > 4 ? 10 : 5 ))

        echo ""
        if wait_for_master "${job_name}"; then
            echo ""
            log "    completed, collecting results..."
        fi

        collect_results "${job_name}" "${users_dir}" "${target}"
        cleanup_distributed "${job_name}"
    done

    log ""
done

# ── generate plots ────────────────────────────────────────────────────────────

stop_cpu_monitor

if [[ -f "${SCRIPT_DIR}/generate_plots.py" ]]; then
    log "generating comparison plots..."
    python3 "${SCRIPT_DIR}/generate_plots.py" "${RESULTS_DIR}" || log "WARNING: plot generation failed"
    log ""
fi

if [[ -f "${SCRIPT_DIR}/generate_cpu_plots.py" ]]; then
    log "generating cpu/memory utilization plots..."
    python3 "${SCRIPT_DIR}/generate_cpu_plots.py" "${RESULTS_DIR}" || log "WARNING: cpu plot generation failed"
    log ""
fi

log "=== Done ==="
log "elapsed: $(elapsed_str)"
log "results: ${RESULTS_DIR}"
ls -la "${RESULTS_DIR}"
