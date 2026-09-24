# OMARCHY-SRV Core Architecture

## 1. Concept & Positioning

**Name:** omarchy-srv  
**Target hardware:** Dell PowerEdge R720/R720xd (E5-2600 v1/v2, up to 768GB DDR3)  
**Philosophy:** Omakase for headless inference boxes. One opinionated NixOS base, zero GUI, file-everything, RAM-first I/O, generation-guarded host changes, and btrfs-snapshot-guarded mutable data.

The distro is a **single-purpose inference + control substrate**: the OS exists to give an on-box LLM agent fast, reversible, text-file control over a large RAM pool.

> Qualification note: performance, firmware assumptions, memory ceilings, and NUMA behavior in this document are design targets until validated by the R720 hardware qualification harness.

---

## 2. Hardware Baseline & R720-Specific Tuning

### 2.1 Supported Configurations

| Profile | RAM | Intended Workload |
|---------|-----|-------------------|
| r720.base | 128GB | 13B multi-role agents, light inference |
| r720.std | 256GB | 70B Q4 inference + tmpfs workspace + in-memory services |
| r720.max | 512-768GB | Multi-model serving, large KV caches, RAM-resident datasets |

### 2.2 R720 Firmware/BIOS Assumptions

- **NUMA:** Dual-socket E5-2600 v1/v2 → normally 2 nodes. Default and pinning policy must be validated per host.
- **PERC:** Hardware RAID presented as block devices; supported controller/profile must be recorded by qualification.
- **iDRAC7:** Out-of-band only; agent never touches it.
- **Memory mode:** Optimizer/performance-oriented mode — BIOS pre-flight check.

### 2.3 Excluded Components

- No kernel modesetting/GPU stack by default
- No Wi-Fi, Bluetooth, audio, power management, laptop hotkeys
- No display manager, Wayland/X11, minimal fonts

---

## 3. Filesystem & Storage Layout

### 3.1 Layout (btrfs, RAID1 mirror)

```
/               btrfs subvol (root, snapshotted)
/boot           vfat or ext4
/ram            tmpfs (PRIMARY WORKSPACE)
/var            btrfs subvol (logs, spool)
/srv            btrfs subvol (model weights, datasets)
/.snapshots     btrfs subvol (auto-snapshot store)
```

**Key decision:** Model weights in `/srv` (disk, btrfs-compressed zstd:3). Everything hot in `/ram` (tmpfs).

### 3.2 Recovery Policy

Host software/configuration rollback and mutable-data rollback are separate mechanisms.

| Trigger | Host desired state | Mutable data |
|---------|--------------------|--------------|
| Candidate host change | Build a new Nix generation | Snapshot only if the change can affect mutable state |
| Failed activation/health gate | Revert to the previous qualified generation | Restore the bound snapshot when required |
| Nightly | No generation mutation | Retained btrfs snapshot according to policy |
| RAM-only runtime change | No Nix rebuild | No snapshot unless durable state is touched |

A Nix generation rollback does not roll back databases, datasets, receipts, or other mutable state. Release and rollback receipts must bind the generation and any required data snapshot together.

---

## 4. RAM-First Resource Model

### 4.1 Single Config File

`/etc/omarchy-srv/ram.toml`:

```toml
[ram]
total        = "auto"
workspace    = "96G"
cache        = "auto"
hugepages_2m = "auto"
hugepages_1g = 0

[ram.zram]
size   = "64G"
algo   = "zstd"

[ram.cgroups]
agent.reserved = "80G"
agent.cpu      = "0-15"
system.max     = "32G"
```

**Hard rule:** agent cgroup excluded from what agent optimizes.

### 4.2 tmpfs Hierarchy

```
/ram
├── workspace     # agent scratch
├── kv            # inference KV cache
├── models        # symlink farm → /srv/models
└── run           # runtime sockets, pidfiles
```

### 4.3 Zram Policy

- 25% of RAM, capped at 64GB on 256GB profile
- zstd algorithm, vm.swappiness=10
- Agent may resize upward only with snapshot+reboot

### 4.4 Huge Pages

- 2MB: auto-computed at boot
- 1GB: opt-in, requires reboot
- Exposed as plain integer in ram.toml

---

## 5. Pre-Baked In-Memory Service Stack

| Service | Role | Default Config |
|---------|------|----------------|
| redis | Agent scratch KV | maxmemory 16gb, allkeys-lru |
| postgresql | Structured state | shared_buffers 32GB |
| memcached | Optional L2 cache | 4GB default |
| ollama | Inference runtime | Bound to agent cgroup, NUMA-pinned |

---

## 6. Agent Loop (Feedback & Control)

| Intent | Mechanism |
|--------|-----------|
| Read memory pressure | PSI /proc/pressure/memory |
| Read cgroup usage | /sys/fs/cgroup/agent/memory.current |
| Adjust workspace | Edit ram.toml → restart omarchy-ramd |
| Verify effect | Re-read PSI / cgroup after update |

Target loop latency: <500ms

### Autonomy Tiers

```toml
[agent.autonomy]
tier = 2  # 0=propose, 1=auto+snapshot, 2=auto+tune, 3=unattended
```

---

## 7. NixOS Desired-State & Update Model

### Base

The host base is expressed as NixOS modules and a pinned flake input. The initial profile includes the tools required for storage, NUMA, hardware qualification, and SUBSTRATE operation. Workload services remain explicit rather than being assumed present.

### Explicitly Absent

Desktop/display stacks, Wi-Fi/Bluetooth/audio defaults, and other workstation-oriented services remain outside the server profile unless a qualified use case adds them.

### Updates

- host changes are source changes that produce a candidate Nix system generation
- candidate generations are built and evaluated before activation
- reversible test activation precedes promotion to the boot default
- failed health/qualification gates revert to the previous qualified generation
- mutable state recovery remains a separate btrfs/data-snapshot concern
- exact nixpkgs and dependency inputs must be locked before a release candidate is qualified
- the sub-second PSI/cgroup/NUMA control loop remains in the Rust runtime and does not invoke Nix rebuilds

---

## 8. Security Model

| Layer | Policy |
|-------|--------|
| Disk | LUKS2 on btrfs mirror |
| Agent | Unprivileged user, group-based service restart via polkit |
| Network | nftables default-deny inbound; ssh rate-limited |
| Audit | Append-only agent-changes.jsonl |

---

## 9. R720 Hardware Quirks

| Quirk | Handling |
|-------|----------|
| DDR3 bandwidth constraint | Quantization and measured model/context admission |
| PERC cache state | Warn on degraded/write-cache conditions; validate controller policy |
| iDRAC7 shared NIC | Document dedicated port assumption |
| NUMA imbalance | Static pinning via cgroups + hwloc topology |

---

## 10. Success Criteria

1. Agent config change with snapshot in <5 seconds
2. Bad agent edit rolled back in <60 seconds
3. 256GB R720: target workload admitted only when measured capacity permits it
4. Entire system state readable in plain text files
