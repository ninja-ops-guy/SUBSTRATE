# RESIDUAL × OMARCHY-SRV Integration Architecture

## 0. Thesis

RESIDUAL and OMARCHY-SRV are complementary halves of a single architecture:

- **RESIDUAL:** Governance and verification layer — decides what should happen, verifies correctness, brakes when wrong
- **OS:** Execution substrate — provides mechanisms (memory, snapshots, kernel enforcement, model serving)

Integration seam: TOML config file, cgroup interface, snapshot/rollback contract.

> Qualification note: this file defines the intended integration contract. A mechanism is not considered implemented or trusted merely because it appears here; implementation and qualification evidence are tracked separately.

---

## 1. Integration Points

### 1.1 Ollama as Local Inference Backbone

**RESIDUAL side:** First-class Ollama route — install, download, start/stop, selection. Provider bridge talks to localhost:11434.

**OS side:** On suitably provisioned servers, Ollama serves quantized models subject to admission and measured hardware limits.

| OS Responsibility | Mechanism |
|-------------------|-----------|
| Pin model weights to NUMA nodes | qualified placement mechanism |
| Prevent unwanted reclamation | memory policy / locking where supported |
| Expose cache pressure as observable | cgroup + runtime telemetry |
| Keep Ollama responsive | Dedicated cgroup, PSI-aware governor |

**Seam:** RESIDUAL hits localhost:11434. It does not own NUMA policy. The OS does not own mission routing.

### 1.2 Git Worktrees + Btrfs Subvolumes = Two-Level Isolation

**RESIDUAL:** Parallel implementation in isolated Git worktrees. Receipts reference Git SHAs.

**OS:** Each qualified worktree layout can be paired with a Btrfs rollback boundary.

**Result: Dual-addressable checkpoints**

```
RESIDUAL checkpoint: Git SHA + receipt + evidence
OS checkpoint: Btrfs subvolume generation + snapshot ID
Together: Roll back code OR system state, independently
```

### 1.3 Agent Privilege Firewall (APF) Enforcement

**RESIDUAL:** Policy — what agent can/can't touch.

**OS:** Mechanism — Landlock LSM, seccomp-BPF, cgroup v2 as implemented and qualified.

**Bridge: TOML config**

```toml
[agent.sandbox]
landlock.read    = ["/srv/residual/worktrees/worker-1/"]
landlock.write   = ["/srv/residual/worktrees/worker-1/", "/ram/workspace/"]
seccomp.profile  = "residual-implementer"

[agent.cgroup]
memory.max  = "48G"
memory.high = "40G"

[agent.mcp]
tools.allow = ["memory.get_state", "snapshot.create"]
tools.deny  = ["snapshot.rollback", "governor.set_policy"]
```

**Enforcement chain:**

```
RESIDUAL APF decides policy
    ↓
APF writes TOML
    ↓
OS validates and applies qualified enforcement mechanisms
    ↓
Agent remains bounded by the resulting kernel/runtime policy
```

### 1.4 Swarm Control Plane → cgroup Arbitration

**RESIDUAL:** orchestrates multiple agents.

**OS:** cgroup layer is physical arbiter.

```
RESIDUAL: requests workers
OS: admits only what measured capacity allows
Worker OOMs: OS records event, RESIDUAL correlates evidence and brakes as policy requires
```

### 1.5 Receipt-Indexed Memory on Fast Path

**Hot path (RAM):**
```
/ram/residual/
├── active-mission/
├── recent-evidence/
├── receipt-index/
└── agent-scratch/
```

**Cold path (disk):**
```
/srv/residual/
├── receipts/
├── evidence/
├── missions/
└── memory/
```

### 1.6 Observation Layer Taps Kernel Signals

| Kernel Signal | Source | RESIDUAL Relevance |
|---------------|--------|-------------------|
| PSI memory pressure | /proc/pressure/memory | Task failure: logic error or memory exhaustion? |
| cgroup OOM events | memory.events | OS kill or self-crash? |
| Btrfs scrub results | btrfs scrub status | Filesystem corruption explaining verification failure? |
| Governor telemetry | /var/log/governor.jsonl | Governor misallocation during task? |

### 1.7 Recursive Loop Safety Net

```
1. RESIDUAL proposes/authorizes self-modification
2. OS establishes the required rollback boundary
3. Change is applied
4. OS-side health checks execute
5. RESIDUAL verification executes
6. Both pass → continue
7. Either fails → rollback/brake path executes according to policy
```

---

## 2. Division of Labor

| Layer | Owns | Does NOT Own |
|-------|------|--------------|
| RESIDUAL | Missions, goal contracts, verification, receipts, quarantine, loop brakes, provider routing | Memory management, cgroups, snapshots, kernel enforcement |
| OS | Memory, cgroups, snapshots, model serving, kernel enforcement | Mission semantics, verification logic, swarm routing |
| TOML config | Contract — RESIDUAL writes policy, OS validates/reads/enforces | — |

---

## 3. Communication Contract

### 3.1 Config File

```toml
# /etc/omarchy-srv/agents/<agent-name>.toml
# Written by: authorized RESIDUAL control-plane path
# Read by: OS enforcement, MCP gateway, governor
```

### 3.2 cgroup Interface

```
/sys/fs/cgroup/residual/<agent-name>/
    memory.current      ← kernel state, RESIDUAL may observe
    memory.high         ← governor policy boundary
    memory.max          ← hard memory ceiling
    cpu.stat            ← kernel state
    memory.events       ← kernel state
```

No non-standard cgroup file is assumed to exist unless SUBSTRATE creates and qualifies an out-of-band metadata mechanism for it.

### 3.3 Snapshot/Rollback Contract

**Target OS guarantees after qualification:**
- Required high-risk dispatch/config/self-modification operations establish the configured pre-change rollback boundary
- Rollback availability is explicitly verified
- Snapshot integrity is checked

**RESIDUAL guarantees:**
- Authorized actions produce receipts carrying the relevant Git SHA + snapshot identifier
- Failures retain evidence with snapshot correlation
- Verification runs before a candidate snapshot/change is declared good
