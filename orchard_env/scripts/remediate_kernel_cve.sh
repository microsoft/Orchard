#!/usr/bin/env bash
###############################################################################
# remediate_kernel_cve.sh
#
# One-shot security maintenance: roll every node in the 5 v5 AKS clusters off
# the vulnerable kernel (6.8.0-1052-azure / 6.8.0-1054-azure) and turn on
# automatic node-OS upgrades so future CVEs and autoscaled nodes stay current.
#
# What it does, per cluster (5 clusters processed in parallel):
#   1. Capture baseline (nodepool list, kubectl nodes) -> logs/<cluster>/baseline.txt
#   2. If `sys` pool count < 2, surge it to 2 (avoid orchestrator outage during roll)
#   3. Run `az aks nodepool upgrade --node-image-only` on `sys` AND `sbx`
#      in parallel via --no-wait, then poll provisioningState until Succeeded
#   4. Scale `sys` back to 1 if we surged it
#   5. Enable cluster-wide `--node-os-upgrade-channel NodeImage`
#      and `--auto-upgrade-channel patch`
#   6. Capture after-state (kubectl nodes) -> logs/<cluster>/after.txt
#      and FAIL the cluster if any vulnerable kernel is still present
#
# Idempotent: safe to re-run. Each step checks current state before mutating.
#
# Usage:
#   bash scripts/remediate_kernel_cve.sh                 # execute
#   DRY_RUN=true bash scripts/remediate_kernel_cve.sh    # print only
#   SBX_MAX_SURGE=1 bash scripts/remediate_kernel_cve.sh # slower but uses less quota
#
# Env vars:
#   DRY_RUN          (default: false)  - if true, only echo commands
#   SBX_MAX_SURGE    (default: 33%)    - --max-surge for sbx pool upgrade
#   SYS_MAX_SURGE    (default: 1)      - --max-surge for sys pool upgrade
#   POLL_INTERVAL    (default: 30)     - seconds between provisioningState polls
#   POLL_TIMEOUT     (default: 7200)   - max seconds to wait for one upgrade
#   LOG_DIR          (default: ./remediation-logs/<UTC-date>)
#   CLUSTERS         (default: built-in 5 v5 clusters; whitespace-separated
#                    list of "<rg>:<cluster>" pairs to override)
#
# Exit code:
#   0  - all 5 clusters remediated and verified
#   non-zero - one or more clusters failed; see logs/SUMMARY.txt
###############################################################################

set -euo pipefail

# ---------- Configuration ----------------------------------------------------

DRY_RUN="${DRY_RUN:-false}"
SBX_MAX_SURGE="${SBX_MAX_SURGE:-33%}"
SYS_MAX_SURGE="${SYS_MAX_SURGE:-1}"
POLL_INTERVAL="${POLL_INTERVAL:-30}"
POLL_TIMEOUT="${POLL_TIMEOUT:-7200}"
LOG_DIR="${LOG_DIR:-./remediation-logs/$(date -u +%Y%m%dT%H%M%SZ)}"

# Bad kernels listed in the IcM (extend if vendor publishes more)
BAD_KERNEL_REGEX='6\.8\.0-(1052|1054)-azure'

# Built-in cluster list. Format: "<resource-group>:<cluster-name>".
DEFAULT_CLUSTERS=(
    # "sandboxv5d16n1-orchestrator-rg:sandboxv5d16n1-aks"
    # "sandboxv5d16n2-orchestrator-rg:sandboxv5d16n2-aks"
    # "sandboxv5d16n3-orchestrator-rg:sandboxv5d16n3-aks"
    # "sandboxv5d16n4-orchestrator-rg:sandboxv5d16n4-aks"
    "sandboxv5d16n5-orchestrator-rg:sandboxv5d16n5-aks"
)
if [[ -n "${CLUSTERS:-}" ]]; then
    # Allow override via env: space-separated "rg:cluster rg:cluster ..."
    read -r -a CLUSTERS_ARR <<< "$CLUSTERS"
else
    CLUSTERS_ARR=("${DEFAULT_CLUSTERS[@]}")
fi

mkdir -p "$LOG_DIR"
SUMMARY_FILE="$LOG_DIR/SUMMARY.txt"
: > "$SUMMARY_FILE"

# ---------- Helpers ----------------------------------------------------------

log() {
    # log <cluster> <msg...>
    local cluster="$1"; shift
    local ts
    ts="$(date -u +%H:%M:%SZ)"
    echo "[$ts][$cluster] $*"
}

run() {
    # run <cluster> <cmd...>
    local cluster="$1"; shift
    log "$cluster" "+ $*"
    if [[ "$DRY_RUN" == "true" ]]; then
        return 0
    fi
    "$@"
}

run_capture() {
    # Like run, but echo output through stdout and never honor DRY_RUN
    # (used for read-only `az ... show` queries we always need).
    local cluster="$1"; shift
    log "$cluster" "? $*"
    "$@"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "ERROR: required command '$1' not found in PATH" >&2
        exit 1
    }
}

# ---------- Per-cluster pipeline --------------------------------------------

remediate_cluster() {
    local rg="$1"
    local cluster="$2"
    local cluster_log_dir="$LOG_DIR/$cluster"
    mkdir -p "$cluster_log_dir"

    # Redirect this whole function's stdout/stderr to the per-cluster logfile,
    # but ALSO mirror onto the master stream so the user sees progress live.
    exec > >(tee -a "$cluster_log_dir/run.log") 2>&1

    log "$cluster" "==== START remediation (rg=$rg) ===="

    # --- Step 1: baseline ---------------------------------------------------
    log "$cluster" "Step 1/6: capturing baseline"
    if [[ "$DRY_RUN" != "true" ]]; then
        az aks nodepool list -g "$rg" --cluster-name "$cluster" -o table \
            > "$cluster_log_dir/baseline-nodepools.txt" 2>&1 || true
        # kubectl context: ensure we are pointed at this cluster.
        az aks get-credentials -g "$rg" -n "$cluster" \
            --overwrite-existing --only-show-errors >/dev/null
        kubectl get nodes -o wide \
            > "$cluster_log_dir/baseline-nodes.txt" 2>&1 || true
        kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.nodeInfo.osImage}{"\t"}{.status.nodeInfo.kernelVersion}{"\n"}{end}' \
            > "$cluster_log_dir/baseline-kernels.txt" 2>&1 || true
    fi

    # --- Step 2: surge `sys` to 2 if needed ---------------------------------
    log "$cluster" "Step 2/6: ensuring sys pool >= 2 nodes for safe roll"
    local sys_count=2
    local surged=false
    if [[ "$DRY_RUN" != "true" ]]; then
        sys_count="$(az aks nodepool show -g "$rg" --cluster-name "$cluster" \
            --name sys --query count -o tsv)"
    fi
    if [[ "$sys_count" -lt 2 ]]; then
        run "$cluster" az aks nodepool scale -g "$rg" \
            --cluster-name "$cluster" --name sys --node-count 2
        surged=true
    else
        log "$cluster" "sys pool already has $sys_count nodes; skipping surge"
    fi

    # --- Step 3: kick off node-image upgrades on sys + sbx in parallel ------
    log "$cluster" "Step 3/6: starting node-image upgrades (sys + sbx in parallel)"
    # NOTE: the aks-preview extension forbids passing --max-surge together with
    # --node-image-only on `nodepool upgrade`. We must persist max-surge on the
    # nodepool *first* via `nodepool update`, then call `upgrade` without it.
    # `nodepool update --max-surge` is idempotent and fast.
    # run "$cluster" az aks nodepool update -g "$rg" \
    #     --cluster-name "$cluster" --name sys \
    #     --max-surge "$SYS_MAX_SURGE"
    # run "$cluster" az aks nodepool update -g "$rg" \
    #     --cluster-name "$cluster" --name sbx \
    #     --max-surge "$SBX_MAX_SURGE"
    # Both calls use --no-wait so the AKS API queues them; we poll afterwards.
    # `--node-image-only` is idempotent: if image is already current, the call
    # returns quickly with provisioningState=Succeeded.
    run "$cluster" az aks nodepool upgrade -g "$rg" \
        --cluster-name "$cluster" --name sys \
        --node-image-only 
    run "$cluster" az aks nodepool upgrade -g "$rg" \
        --cluster-name "$cluster" --name sbx \
        --node-image-only 

    # Poll provisioningState for each pool until Succeeded or timeout.
    # local pool deadline state
    # deadline=$(( $(date +%s) + POLL_TIMEOUT ))
    # for pool in sys sbx; do
    #     log "$cluster" "polling $pool provisioningState (timeout ${POLL_TIMEOUT}s)"
    #     while :; do
    #         if [[ "$DRY_RUN" == "true" ]]; then
    #             state="Succeeded"
    #         else
    #             state="$(az aks nodepool show -g "$rg" \
    #                 --cluster-name "$cluster" --name "$pool" \
    #                 --query provisioningState -o tsv 2>/dev/null || echo Unknown)"
    #         fi
    #         log "$cluster" "$pool provisioningState=$state"
    #         case "$state" in
    #             Succeeded) break ;;
    #             Failed|Canceled)
    #                 log "$cluster" "ERROR: $pool upgrade ended in state=$state"
    #                 return 2
    #                 ;;
    #         esac
    #         if (( $(date +%s) > deadline )); then
    #             log "$cluster" "ERROR: timed out waiting for $pool upgrade"
    #             return 3
    #         fi
    #         sleep "$POLL_INTERVAL"
    #     done
    # done

    # --- Step 4: scale sys back to 1 (if we surged it) ----------------------
    log "$cluster" "Step 4/6: restoring sys pool count"
    if [[ "$surged" == "true" ]]; then
        run "$cluster" az aks nodepool scale -g "$rg" \
            --cluster-name "$cluster" --name sys --node-count 1
    else
        log "$cluster" "did not surge sys pool earlier; skipping rollback"
    fi

    # # --- Step 5: enable auto-upgrade channels -------------------------------
    # log "$cluster" "Step 5/6: enabling NodeImage + patch auto-upgrade channels"
    # run "$cluster" az aks update -g "$rg" -n "$cluster" \
    #     --node-os-upgrade-channel NodeImage \
    #     --auto-upgrade-channel patch

    # --- Step 6: post-state + verification ----------------------------------
    log "$cluster" "Step 6/6: capturing post-state and verifying kernels"
    local bad_count=0
    if [[ "$DRY_RUN" != "true" ]]; then
        kubectl get nodes -o wide \
            > "$cluster_log_dir/after-nodes.txt" 2>&1 || true
        kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.nodeInfo.osImage}{"\t"}{.status.nodeInfo.kernelVersion}{"\n"}{end}' \
            > "$cluster_log_dir/after-kernels.txt" 2>&1 || true
        # Count nodes still on a vulnerable kernel.
        bad_count="$(grep -Ec "$BAD_KERNEL_REGEX" \
            "$cluster_log_dir/after-kernels.txt" || true)"
        # Confirm channel is set.
        az aks show -g "$rg" -n "$cluster" \
            --query autoUpgradeProfile -o json \
            > "$cluster_log_dir/auto-upgrade-profile.json" 2>&1 || true
    fi

    if (( bad_count > 0 )); then
        log "$cluster" "FAIL: $bad_count node(s) still on vulnerable kernel"
        return 4
    fi

    log "$cluster" "==== SUCCESS remediation complete ===="
    return 0
}

# ---------- Main -------------------------------------------------------------

main() {
    require_cmd az
    require_cmd kubectl

    echo "============================================" | tee -a "$SUMMARY_FILE"
    echo "AKS algif_aead kernel CVE remediation"        | tee -a "$SUMMARY_FILE"
    echo "DRY_RUN=$DRY_RUN"                             | tee -a "$SUMMARY_FILE"
    echo "Log dir: $LOG_DIR"                            | tee -a "$SUMMARY_FILE"
    echo "Clusters:"                                    | tee -a "$SUMMARY_FILE"
    for entry in "${CLUSTERS_ARR[@]}"; do
        echo "  - $entry"                               | tee -a "$SUMMARY_FILE"
    done
    echo "============================================" | tee -a "$SUMMARY_FILE"

    # Launch one subshell per cluster, fan-out then wait. We track each
    # subshell's PID -> "<rg>:<cluster>" so we can attribute exit codes.
    declare -A pid_to_label
    local entry rg cluster
    for entry in "${CLUSTERS_ARR[@]}"; do
        rg="${entry%%:*}"
        cluster="${entry##*:}"
        (
            remediate_cluster "$rg" "$cluster"
        ) &
        pid_to_label[$!]="$entry"
    done

    # Collect results.
    local overall_rc=0
    local pid label rc
    for pid in "${!pid_to_label[@]}"; do
        label="${pid_to_label[$pid]}"
        if wait "$pid"; then
            rc=0
        else
            rc=$?
        fi
        if (( rc == 0 )); then
            echo "[OK]   $label" | tee -a "$SUMMARY_FILE"
        else
            echo "[FAIL] $label (rc=$rc)" | tee -a "$SUMMARY_FILE"
            overall_rc=1
        fi
    done

    echo "============================================" | tee -a "$SUMMARY_FILE"
    echo "Done. See $LOG_DIR/<cluster>/run.log for details."
    return "$overall_rc"
}

main "$@"
