# Security Model

## 1. Three-Layer Confinement

```
Layer 1: Landlock LSM (filesystem)
    └── Per-agent read/write/execute allowlists

Layer 2: seccomp BPF (syscalls)
    └── Per-agent syscall filter

Layer 3: cgroup v2 (resources)
    └── memory.max, cpu.max, cpuset, io.weight, pids.max
```

> Status: security architecture specification. The current v0.1 core implements part of the cgroup resource boundary. Landlock, seccomp, MCP authorization, privilege-escalation testing, and end-to-end APF enforcement remain qualification work and must not be represented as deployed controls until their gates pass.

---

## 2. Policy as TOML

```toml
# /etc/omarchy-srv/agents/residual-worker-1.toml

[agent.sandbox]
landlock.read    = ["/etc/omarchy-srv/", "/srv/models/"]
landlock.write   = ["/ram/workspace/worker-1/"]
landlock.execute = ["/usr/bin/llama-cli", "/usr/bin/python3"]
seccomp.profile  = "agent-standard"

[agent.cgroup]
memory.max  = "48G"
memory.high = "40G"
cpu.max     = "800000 1000000"
pids.max    = 512

[agent.mcp]
tools.allow = ["memory.get_state", "snapshot.create"]
tools.deny  = ["cgroup.set_limit", "snapshot.rollback"]
```

---

## 3. Trust Boundary

Target agent posture:

- no root
- typed local control-plane access rather than unrestricted shell authority
- filesystem/syscall/resource confinement appropriate to the assigned role
- explicit policy gates for mutating operations
- auditable privileged transitions

Privilege elevation must require an independently authorized administrative path. Exact authentication and break-glass mechanics are implementation/qualification requirements, not assumptions.

---

## 4. Disk & Network

| Layer | Target policy |
|-------|---------------|
| Disk | LUKS2 on qualified btrfs mirror |
| Network | nftables default-deny inbound; SSH explicitly constrained |
| Audit | Tamper-evident append-oriented action log with retention policy |

---

## 5. Qualification Gates

Before a production security claim is made:

1. Landlock rules are exercised with positive and negative path tests.
2. seccomp policy is validated against the required runtime syscall set and escape attempts.
3. cgroup boundaries are tested under OOM, fork pressure, CPU pressure, and attempted path escape.
4. local sockets authenticate callers rather than relying only on filesystem permissions where authority is sensitive.
5. systemd capability sets and write paths are reviewed against least privilege.
6. privileged actions produce durable audit evidence.
7. failure of a security control fails closed for protected operations.
