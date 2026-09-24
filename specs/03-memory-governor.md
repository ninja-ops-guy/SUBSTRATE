# Memory Budget Governor Specification

## 1. Purpose

A deterministic, non-LLM daemon continuously enforces RAM policy set through an authorized control plane. The agent is not the real-time controller; the governor is.

> Status: specification. The governor described here is not considered production-qualified until its implementation, timing behavior, pressure-response logic, and recovery paths pass the relevant qualification suite.

---

## 2. Architecture

```
omarchy-gov: deterministic bounded controller

Loop (target 10 Hz):
    1. Read PSI (memory, cpu, io)
    2. Read cgroup v2 stats
    3. Read governor policy (cached, inotify-reload)
    4. Compute current_state vs target_band
    5. Apply bounded corrections
    6. Log telemetry
    7. Sleep until next tick
```

---

## 3. Policy File

```toml
# /etc/omarchy-srv/governor.toml

[governor]
tick_hz        = 10
target_band    = { psi_some_max = 20, psi_full_max = 5 }
aggressiveness = "moderate"

[governor.bounds]
workspace_min     = "32G"
workspace_max     = "128G"
zram_min          = "16G"
zram_max          = "64G"
memory_high_floor = "16G"

[governor.kv]
demote_to_zram_psi  = 30
demote_to_cxl_psi   = 50
demote_to_nvme_psi  = 70
promote_to_dram_psi = 10
```

---

## 4. Agent ↔ Governor Separation

| Responsibility | Owner |
|----------------|-------|
| Set policy (target band, aggressiveness) | Authorized control plane |
| Enforce policy (continuous adjustment) | Governor |
| Escalate when policy insufficient | Governor → health path → RESIDUAL/human |
| Emergency override | Human break-glass path |

---

## 5. Governor Response Ladder

The exact thresholds are policy inputs and must be qualified against real hardware before production use.

```
1. PSI some above band  → adjust memory.high within configured bounds
2. Sustained pressure   → request/perform qualified cache reclaim or demotion
3. Higher pressure      → reduce admitted workload or context budget
4. Critical pressure    → freeze/rollback/evict only through qualified safety path
```

Each action should produce telemetry sufficient to correlate cause, action, and measured effect.

```json
{"ts":"...","action":"memory_reclaim","cgroup":"executor","requested_bytes":2147483648,"psi_before":42.1,"psi_after":18.3}
```
