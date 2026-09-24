#!/usr/bin/env bash
set -euo pipefail

TARGET_CGROUP="${1:-/sys/fs/cgroup/residual-executor}"
RECLAIM_BYTES="${2:-$((2 * 1024 * 1024 * 1024))}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [kv-demote] $*"; }
error() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [kv-demote] ERROR: $*" >&2; }

[[ "$RECLAIM_BYTES" =~ ^[0-9]+$ ]] || { error "reclaim bytes must be an integer"; exit 2; }
[[ "$TARGET_CGROUP" == /sys/fs/cgroup/* ]] || { error "target must be under /sys/fs/cgroup"; exit 2; }
[[ -d "$TARGET_CGROUP" ]] || { error "cgroup not found: $TARGET_CGROUP"; exit 1; }
[[ -w "${TARGET_CGROUP}/memory.reclaim" ]] || { error "memory.reclaim is not writable"; exit 1; }

ZRAM_DEV="$(swapon --show=NAME --noheadings 2>/dev/null | grep zram | head -1 || true)"
[[ -n "$ZRAM_DEV" ]] || { error "no zram device found"; exit 1; }

MEM_BEFORE="$(cat "${TARGET_CGROUP}/memory.current")"
ZRAM_BEFORE="$(cat "/sys/block/${ZRAM_DEV##*/}/compr_data_size" 2>/dev/null || echo 0)"
log "baseline: cgroup=${MEM_BEFORE} bytes, zram=${ZRAM_BEFORE} bytes"

printf '%s\n' "$RECLAIM_BYTES" > "${TARGET_CGROUP}/memory.reclaim"
sleep 0.5

MEM_AFTER="$(cat "${TARGET_CGROUP}/memory.current")"
ZRAM_AFTER="$(cat "/sys/block/${ZRAM_DEV##*/}/compr_data_size" 2>/dev/null || echo 0)"
MEM_REDUCTION=$((MEM_BEFORE - MEM_AFTER))
ZRAM_INCREASE=$((ZRAM_AFTER - ZRAM_BEFORE))

log "result: cgroup=${MEM_AFTER} (reduced ${MEM_REDUCTION}), zram=${ZRAM_AFTER} (increased ${ZRAM_INCREASE})"
(( MEM_REDUCTION > 0 )) || { error "reclaim had no measurable effect"; exit 1; }
(( ZRAM_INCREASE > 0 )) || log "warning: reclaimed pages were not observed in zram"
