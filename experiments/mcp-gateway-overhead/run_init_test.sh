#!/usr/bin/env bash
#
# Quick 20s test to capture initialize + tools/list latency.
# No warmup — these one-time-per-session events fire in the first few seconds.
#
# Usage:
#   ./run_init_test.sh [-v]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/configs"
LOAD_TESTER_DIR="${SCRIPT_DIR}"
MOCK_SERVER_DIR="${SCRIPT_DIR}/../../mock-servers/perf-mock-server"
RESULTS_BASE="${SCRIPT_DIR}/results"
NAMESPACE="performance-test"

TIMESTAMP=$(date +"%d_%m_%H_%M")
RESULTS_DIR="${RESULTS_BASE}/${TIMESTAMP}_init"

VERBOSE=false
for arg in "$@"; do
    case "$arg" in
        -v|--verbose) VERBOSE=true ;;
    esac
done

# ── experiment parameters ─────────────────────────────────────────────────────

CONCURRENCY_LEVELS=(2 4 8 16 32 64 128 256 512)
USERS_PER_WORKER=32
TEST_DURATION=20
WARMUP=0
CPS=0
TARGETS=("server" "gateway")

TOTAL_JOBS=$(( ${#CONCURRENCY_LEVELS[@]} * ${#TARGETS[@]} ))
CURRENT_JOB=0
START_TIME=$(date +%s)

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
    if [[ $CURRENT_JOB -eq 0 ]]; then echo "calculating..."; return; fi
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
    [[ $workers -lt 1 ]] && workers=1
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

    sed \
        -e "s|__JOB_NAME__|${job_name}|g" \
        -e "s|__TARGET__|${target}|g" \
        -e "s|__TOOL_PREFIX__|${tool_prefix}|g" \
        -e "s|__HOST_HEADER__|${host_header}|g" \
        -e "s|__HOST_URL__|${host_url}|g" \
        -e "s|__CPS__|${CPS}|g" \
        -e "s|__USERS__|${users}|g" \
        -e "s|__DURATION__|${TEST_DURATION}|g" \
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
    log "    waiting for master job/${job_name}-master (${TEST_DURATION}s test, no warmup)..."

    kubectl wait --for=condition=complete "job/${job_name}-master" -n "${NAMESPACE}" --timeout=$(( TEST_DURATION + 120 ))s &>/dev/null &
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
            printf "\r    [%s] %ds/%ds | %s | elapsed: %s | ETA: %s   " \
                "$(date +%H:%M:%S)" "$waited" "$TEST_DURATION" \
                "$(progress_bar)" "$(elapsed_str)" "$(eta_str)"
        fi
        sleep 5
        waited=$(( waited + 5 ))
    done

    [[ -n "$logs_pid" ]] && { kill "$logs_pid" 2>/dev/null || true; wait "$logs_pid" 2>/dev/null || true; }

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
}

# ── main ──────────────────────────────────────────────────────────────────────

log "=== Init/ToolsList Quick Test (20s, no warmup) ==="
log "concurrency:   ${CONCURRENCY_LEVELS[*]}"
log "users/worker:  ${USERS_PER_WORKER}"
log "targets:       ${TARGETS[*]}"
log "total jobs:    ${TOTAL_JOBS}"
log "warmup:        ${WARMUP}s (none)"
log "measurement:   ${TEST_DURATION}s per job"
log "est. total:    ~$(( TOTAL_JOBS * (TEST_DURATION + 60) / 60 ))min"
log "results:       ${RESULTS_DIR}"
log ""

# show worker plan
log "worker plan:"
for users in "${CONCURRENCY_LEVELS[@]}"; do
    workers=$(calc_workers "$users")
    log "    u${users}: ${workers} worker(s)"
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

# save metadata
mkdir -p "${RESULTS_DIR}"
cat > "${RESULTS_DIR}/metadata.txt" <<EOF
experiment:      mcp_gateway_mock_perf — init/tools_list quick test
timestamp:       ${TIMESTAMP}
server:          perf-mock-server (10 zero-latency tools)
concurrency:     ${CONCURRENCY_LEVELS[*]}
users_per_worker: ${USERS_PER_WORKER}
warmup:          ${WARMUP}s
measurement:     ${TEST_DURATION}s
cps:             ${CPS}
purpose:         capture initialize + tools/list latency (lost in main experiment due to warmup)
EOF

# archive configs
mkdir -p "${RESULTS_DIR}/configs"
cp "${CONFIG_DIR}/infrastructure.yaml" "${RESULTS_DIR}/configs/"
cp "${CONFIG_DIR}/template_distributed.yaml" "${RESULTS_DIR}/configs/"
cp "${SCRIPT_DIR}/locustfile.py" "${RESULTS_DIR}/configs/"

for users in "${CONCURRENCY_LEVELS[@]}"; do
    num_workers=$(calc_workers "$users")
    users_dir="${RESULTS_DIR}/cps0/u${users}"
    mkdir -p "${users_dir}"

    log "━━━ u${users} (${num_workers} workers) ━━━"

    for target in "${TARGETS[@]}"; do
        job_name="init-u${users}-${target}"

        CURRENT_JOB=$(( CURRENT_JOB + 1 ))
        log ""
        log "  [${CURRENT_JOB}/${TOTAL_JOBS}] u${users}/${target}"
        log "    elapsed: $(elapsed_str) | ETA: $(eta_str)"

        cleanup_distributed "${job_name}"

        gen_yaml="${users_dir}/${target}_job.yaml"
        generate_distributed_yaml "${job_name}" "${target}" "${users}" "${num_workers}" > "${gen_yaml}"
        kubectl apply -f "${gen_yaml}"

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

if [[ -f "${SCRIPT_DIR}/generate_plots.py" ]]; then
    log "generating plots..."
    python3 "${SCRIPT_DIR}/generate_plots.py" "${RESULTS_DIR}" || log "WARNING: plot generation failed"
fi

log ""
log "=== Done ==="
log "elapsed: $(elapsed_str)"
log "results: ${RESULTS_DIR}"

# quick summary of initialize + tools/list
log ""
log "=== Initialize & Tools/List Summary ==="
for users in "${CONCURRENCY_LEVELS[@]}"; do
    for target in server gateway; do
        csv="${RESULTS_DIR}/cps0/u${users}/${target}_stats.csv"
        if [[ -f "$csv" ]]; then
            init_line=$(grep ',initialize,' "$csv" 2>/dev/null || true)
            tl_line=$(grep ',tools/list,' "$csv" 2>/dev/null || true)
            if [[ -n "$init_line" || -n "$tl_line" ]]; then
                log "  u${users}/${target}:"
                [[ -n "$init_line" ]] && log "    initialize: $(echo "$init_line" | awk -F',' '{printf "count=%s avg=%sms p95=%sms p99=%sms", $3, $6, $9, $10}')"
                [[ -n "$tl_line" ]] && log "    tools/list: $(echo "$tl_line" | awk -F',' '{printf "count=%s avg=%sms p95=%sms p99=%sms", $3, $6, $9, $10}')"
            fi
        fi
    done
done
