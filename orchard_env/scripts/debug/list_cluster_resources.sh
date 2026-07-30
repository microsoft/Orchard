#!/usr/bin/env bash
# List total and per-node requested/allocated resources in the cluster.
# Usage: ./scripts/debug/list_cluster_resources.sh [--per-node]

set -euo pipefail

show_total() {
    echo "=== Total Cluster Resource Requests vs Allocatable ==="
    kubectl get pods --all-namespaces -o json | python3 -c "
import json, sys
data = json.load(sys.stdin)
total_cpu_m = 0
total_mem_mi = 0
for pod in data['items']:
    for c in pod['spec'].get('containers', []) + pod['spec'].get('initContainers', []):
        req = c.get('resources', {}).get('requests', {})
        cpu = req.get('cpu', '0')
        mem = req.get('memory', '0')
        if cpu.endswith('m'):
            total_cpu_m += int(cpu[:-1])
        elif cpu:
            total_cpu_m += int(float(cpu) * 1000)
        if mem.endswith('Gi'):
            total_mem_mi += int(float(mem[:-2]) * 1024)
        elif mem.endswith('Mi'):
            total_mem_mi += int(float(mem[:-2]))
        elif mem.endswith('Ki'):
            total_mem_mi += int(float(mem[:-2]) / 1024)
        elif mem.endswith('G'):
            total_mem_mi += int(float(mem[:-1]) * 1000)
        elif mem.endswith('M'):
            total_mem_mi += int(float(mem[:-1]))
        elif mem and mem != '0':
            total_mem_mi += int(int(mem) / (1024*1024))
print(f'Total Requested CPU:    {total_cpu_m}m ({total_cpu_m/1000:.2f} cores)')
print(f'Total Requested Memory: {total_mem_mi}Mi ({total_mem_mi/1024:.2f} Gi)')
"

    kubectl get nodes -o json | python3 -c "
import json, sys
data = json.load(sys.stdin)
total_cpu_m = 0
total_mem_mi = 0
for node in data['items']:
    cap = node['status']['allocatable']
    cpu = cap.get('cpu', '0')
    mem = cap.get('memory', '0')
    if cpu.endswith('m'):
        total_cpu_m += int(cpu[:-1])
    else:
        total_cpu_m += int(float(cpu) * 1000)
    if mem.endswith('Ki'):
        total_mem_mi += int(float(mem[:-2]) / 1024)
    elif mem.endswith('Mi'):
        total_mem_mi += int(float(mem[:-2]))
    elif mem.endswith('Gi'):
        total_mem_mi += int(float(mem[:-2]) * 1024)
print(f'Total Allocatable CPU:    {total_cpu_m}m ({total_cpu_m/1000:.2f} cores)')
print(f'Total Allocatable Memory: {total_mem_mi}Mi ({total_mem_mi/1024:.2f} Gi)')
"
}

show_per_node() {
    echo ""
    echo "=== Per-Node Resource Breakdown ==="
    kubectl describe nodes | python3 -c "
import sys

current_node = None
in_alloc = False
skip = 0
header = f\"{'Node':<55} {'CPU Req':>10} {'CPU Lim':>10} {'Mem Req':>12} {'Mem Lim':>12}\"
print(header)
print('-' * len(header))

for line in sys.stdin:
    line = line.rstrip()
    if line.startswith('Name:'):
        current_node = line.split()[-1]
    if 'Resource' in line and 'Requests' in line and 'Limits' in line:
        in_alloc = True
        skip = 1
        continue
    if in_alloc and skip > 0:
        skip -= 1
        continue
    if in_alloc and line.strip().startswith('cpu'):
        parts = line.split()
        cpu_req_display = parts[1] + ' ' + parts[2] if len(parts) > 2 else parts[1]
        cpu_lim_display = parts[3] + ' ' + parts[4] if len(parts) > 4 else '-'
    if in_alloc and line.strip().startswith('memory'):
        parts = line.split()
        mem_req_display = parts[1] + ' ' + parts[2] if len(parts) > 2 else parts[1]
        mem_lim_display = parts[3] + ' ' + parts[4] if len(parts) > 4 else '-'
        print(f'{current_node:<55} {cpu_req_display:>10} {cpu_lim_display:>10} {mem_req_display:>12} {mem_lim_display:>12}')
        in_alloc = False
"
}

# Main
show_total

if [[ "${1:-}" == "--per-node" ]]; then
    show_per_node
fi
