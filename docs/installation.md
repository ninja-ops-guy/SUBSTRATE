# SUBSTRATE / OMARCHY-SRV Installation Guide

## Status

This guide is for development and hardware qualification. Do not treat the current v0.1 branch as production-qualified until CI, R720 hardware validation, interlock/failure-injection, and security gates have passed.

## Prerequisites

- Linux host with cgroup v2
- btrfs tools for rollback experiments
- Rust stable toolchain
- systemd for service deployment
- NUMA-capable hardware for NUMA qualification
- zram only when exercising reclaim/demotion tests

The Dell R720/R720xd is the primary target, but discovered topology—not a hard-coded example—is authoritative.

## Build

```bash
git clone https://github.com/ninja-ops-guy/SUBSTRATE.git
cd SUBSTRATE

cargo build --release --manifest-path src/Cargo.toml
cargo test --manifest-path src/Cargo.toml
```

Binaries are emitted under `src/target/release/`.

## Install Binaries

```bash
sudo install -Dm755 src/target/release/omarchy-configd /usr/bin/omarchy-configd
sudo install -Dm755 src/target/release/omarchy-cgroupd /usr/bin/omarchy-cgroupd
```

## Install Units and Configuration

```bash
sudo install -Dm644 systemd/omarchy-configd.service /etc/systemd/system/omarchy-configd.service
sudo install -Dm644 systemd/omarchy-cgroupd.service /etc/systemd/system/omarchy-cgroupd.service
sudo install -d /etc/omarchy-srv/live /etc/omarchy-srv/ring-buffer /run/omarchy-srv/staging
sudo install -Dm644 config/*.toml /etc/omarchy-srv/
sudo systemctl daemon-reload
```

## Qualification-Mode Start

Review the unit capability/write-path policy on the target first, then:

```bash
sudo systemctl enable --now omarchy-configd
sudo systemctl enable --now omarchy-cgroupd
systemctl --no-pager --full status omarchy-configd omarchy-cgroupd
```

## Test a Configuration Update

```bash
cat <<'EOF' | nc -N -U /run/omarchy-srv/omarchy-configd.sock
[agent]
name = "test"
runtime = "ollama"
model = "llama3.3:70b-q4_k_m"

[resources]
memory_floor_gb = 16
memory_high_gb = 32
numa_policy = "strict_fail"
numa_nodes = [0]
EOF
```

Then inspect the live config and resulting cgroup state:

```bash
sudo cat /etc/omarchy-srv/live/agent.toml
sudo cat /sys/fs/cgroup/test/memory.min
sudo cat /sys/fs/cgroup/test/memory.high
sudo cat /sys/fs/cgroup/test/memory.max
```

Use the hardware qualification harness for authoritative R720 acceptance evidence.
