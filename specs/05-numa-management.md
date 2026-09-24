# NUMA Topology Management

## 1. Strict NUMA Zoning

**Problem:** Remote-memory access and asymmetric pressure can invalidate scheduling assumptions and destabilize pressure-based control.

**Target policy:** qualified zoning with explicit fail/overflow behavior.

```toml
# /etc/omarchy-srv/numa-topology.toml

[numa.zones]
zone_0 = { nodes = [0], total_ram_gb = 128, reserved_for = "weights" }
zone_1 = { nodes = [1], total_ram_gb = 128, reserved_for = "kv_cache, OS" }

[numa.policies.residual-executor]
primary_zone    = "zone_0"
fallback_policy = "strict_fail"
```

> The example capacities are placeholders. The R720 qualification harness is authoritative for discovered nodes, installed capacity, locality, and bandwidth.

---

## 2. Allocation Invariant

When strict binding is requested, admission must reject a workload that cannot satisfy the local-node allocation requirement rather than silently crossing the declared boundary.

---

## 3. Pre-Flight Capacity Planner

Example:

```
1. RESIDUAL requests a model/context combination.
2. Planner calculates:
   - quantized model-weight estimate
   - GQA-aware KV-cache estimate using num_kv_heads
   - scratch/reserve budget
3. Planner compares the result with measured/admitted node capacity.
4. If the request does not fit, it returns a bounded decision:
   a. queue
   b. suggest a qualified smaller quant
   c. request an explicitly supported split strategy
   d. evict lower-priority work through policy
5. RESIDUAL or the authorized scheduler chooses among permitted options.
```

For Llama 3.3 70B with 32K context and FP16 KV state, the current planner computes roughly 10 GiB of KV cache from 8 KV heads, 128 head dimension, and 80 layers. Weight estimates remain model/quant metadata inputs and require calibration against actual artifacts.

---

## 4. Interconnect & Locality Tracking

Future NUMA optimization may track remote-memory effects and scheduler placement, but no production claim is made for interconnect-latency correction until the R720 harness demonstrates a stable measurement method and the correction path is qualified.

---

## 5. KV Cache Overflow Policy

```toml
[numa.policies.residual-executor]
kv_overflow_policy = "demote_to_zram"
kv_soft_limit      = "48G"
kv_hard_limit      = "64G"
```

Values are policy examples; hardware qualification determines safe defaults.
