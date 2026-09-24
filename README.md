# SUBSTRATE

SUBSTRATE is a RAM-first inference and control substrate for NUMA server hardware, initially targeting Dell PowerEdge R720/R720xd systems. The OMARCHY-SRV core combines resource admission, cgroup v2 enforcement, rollback primitives, memory-pressure tooling, and a future typed RESIDUAL control surface.

## Release Posture

**Current status: v0.1 core implementation candidate; production qualification pending.**

The repository intentionally separates:

- **policy/specification** — what the system is intended to guarantee
- **mechanism** — the code that implements a control
- **evidence** — CI, hardware qualification, failure injection, and security results proving the control behaves as claimed
- **orchestration** — RESIDUAL/MCP coordination above those primitives

## Core Components

- `omarchy-configd` — validates and publishes agent/resource configuration
- `omarchy-cgroupd` — applies and verifies cgroup v2 resource limits
- capacity planner — GQA-aware model/KV memory estimation and admission decisions
- cgroup mapper — validated memory/NUMA constraint application
- rollback coordinator — freeze → model unload → btrfs restore → Git reset → unfreeze
- OOM/KV isolation primitives
- zram reclaim stress tooling
- systemd hardening baseline

## Build & Test

```bash
cargo fmt --manifest-path src/Cargo.toml --all -- --check
cargo check --manifest-path src/Cargo.toml --all-targets
cargo test --manifest-path src/Cargo.toml --all-targets
cargo clippy --manifest-path src/Cargo.toml --all-targets -- -D warnings
```

GitHub Actions runs the clean-runner qualification subset on every relevant push and pull request.

## Project Layout

```
.
├── .github/workflows/ci.yml
├── ci/
├── config/
├── docs/
├── scripts/
├── specs/
├── src/
├── systemd/
├── tests/
└── ROADMAP.md
```

## Qualification

R720 hardware behavior, NUMA assumptions, zram behavior, interlock safety, fault recovery, and security boundaries are not inferred from architecture documents. They require explicit qualification evidence. See [ROADMAP.md](ROADMAP.md).

## RESIDUAL Integration

RESIDUAL is intended to sit above SUBSTRATE as a governance/orchestration layer. SUBSTRATE owns resource enforcement and host mechanisms; RESIDUAL owns mission semantics, verification, receipts, and higher-level brakes. The proposed MCP boundary is documented in `specs/06-mcp-interface.md`.
