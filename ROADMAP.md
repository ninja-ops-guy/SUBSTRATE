# SUBSTRATE / OMARCHY-SRV Development Roadmap

**Target:** qualified RAM-first inference and control substrate for Dell R720-class hardware  
**Last updated:** 2026-09-24

## Current State

Architecture and interface specifications exist. The Rust core is under clean-runner CI, and a NixOS desired-state layer now provides the base host configuration, R720 profile, and installer-image scaffold without replacing the fast runtime control plane.

The correct release posture is:

**v0.1 core implementation candidate; production qualification pending.**

A checkmark in this roadmap means implementation or specification evidence exists. It does not substitute for R720 qualification, failure injection, security review, or release acceptance.

## Workstreams

### A. Core repository + CI — IN PROGRESS

- [x] Establish public SUBSTRATE repository
- [x] Add Rust package, core modules, daemons, systemd units, configs, scripts
- [x] Add clean-runner Rust CI: fmt, check, tests, Clippy
- [x] Add TOML, Bash, systemd, and repository-contract checks
- [x] Correct Cargo target paths from initial bundle
- [x] Declare missing runtime dependency for cgroup daemon
- [x] Remove unsupported inotify timeout API dependency
- [x] Align systemd service type with actual daemon readiness behavior
- [x] Make config publish use same-filesystem atomic rename
- [x] Bound config socket payload size
- [x] Replace assumed Ollama session-delete behavior with documented model unload semantics
- [ ] All CI jobs green on final bootstrap head
- [ ] Merge bootstrap PR to `main`

### B. NixOS base + reproducibility — IN PROGRESS

- [x] Add Nix flake on the NixOS 26.05 release line
- [x] Add SUBSTRATE NixOS module
- [x] Add conservative Dell R720 hardware profile
- [x] Add minimal installer ISO definition
- [x] Add Nix evaluation CI
- [x] Preserve iDRAC as a separate out-of-band trust boundary
- [x] Preserve Btrfs for mutable-data recovery rather than package rollback
- [x] Commit a flake lock bound to the selected nixpkgs revision
- [ ] Commit Cargo dependency lock/vendor closure
- [ ] Build the Rust daemons reproducibly as a Nix package
- [ ] Enable omarchy-configd/omarchy-cgroupd from the qualified Nix package
- [ ] Add NixOS VM tests for service startup, failure, rollback, and path confinement
- [ ] Bind Nix system derivation/generation to release qualification receipts

### C. R720 hardware qualification — OWNER LANE

- [ ] Capture immutable host/firmware/CPU/RAM/storage baseline
- [ ] Discover NUMA topology and locality
- [ ] Measure local/remote bandwidth and latency
- [ ] Validate cgroup v2 controller availability/delegation
- [ ] Exercise zram reclaim/demotion
- [ ] Produce machine-readable qualification receipt

This lane is being developed separately and converges with repository release gates.

### D. Interlock + failure injection — OWNER LANE

- [ ] Implement pre-freeze/quiescence interlock
- [ ] Prove safe behavior with open files/locks and stuck workers
- [ ] OOM recovery injection
- [ ] config corruption/rejection injection
- [ ] daemon crash/restart injection
- [ ] rollback partial-failure injection
- [ ] snapshot exhaustion / missing snapshot injection
- [ ] verify evidence retention for each failure

### E. Core safety and recovery — PARTIAL

Implemented candidates:

- [x] GQA-aware capacity math
- [x] cgroup memory min/low/high/max mapping
- [x] cgroup write verification
- [x] OOM protection tier primitives
- [x] KV isolation contract validation
- [x] rollback orchestration skeleton
- [x] model unload via supported Ollama API behavior

Still required:

- [ ] transactional rollback semantics proven under injected faults
- [ ] btrfs restore behavior qualified on target layout
- [ ] explicit rollback receipts
- [ ] crash consistency tests
- [ ] target-host privilege/capability review

### F. Inference integration — OPEN

- [ ] Query installed Ollama/runtime capabilities rather than assuming them
- [ ] model metadata ingestion for planner calibration
- [ ] measured weight/residency calibration
- [ ] topology-aware placement policy
- [ ] preload/unload lifecycle qualification
- [ ] TTFT and throughput baseline/after-pressure measurements

No tensor-split or session-persistence feature is considered available until verified against the selected runtime/version.

### G. RESIDUAL MCP integration — OPEN

- [x] Interface specification
- [ ] MCP transport implementation
- [ ] caller identity/authentication
- [ ] per-tool authorization policy
- [ ] read-only observation tools
- [ ] mutating tools behind safety gates
- [ ] correlation IDs and durable audit receipts
- [ ] restart/replay/idempotency tests
- [ ] RESIDUAL adapter/conformance tests

### H. Security hardening — OPEN

- [ ] Landlock implementation and negative-path tests
- [ ] seccomp implementation and syscall compatibility tests
- [ ] Unix-socket peer identity/auth review
- [ ] systemd capabilities and writable-path minimization
- [ ] dependency vulnerability audit
- [ ] hostile config/path tests
- [ ] privilege-escalation tests
- [ ] network boundary review
- [ ] audit-log tamper-resistance design/qualification

### I. Release qualification — OPEN

A release candidate requires:

- [ ] clean CI on the exact candidate SHA
- [ ] hardware receipt bound to the candidate/version
- [ ] failure-injection campaign pass
- [ ] interlock pass
- [ ] security gates with no unresolved critical findings
- [ ] installation/runbook replay on a clean target
- [ ] exact dependency lockfile
- [ ] release manifest + checksums
- [ ] known limitations and support matrix
- [ ] rollback drill from installed release

## Production-Readiness Boundary

The project is **not production-ready** until all release-qualification gates above are satisfied. Passing generic CI proves source/build/static-test properties; it does not prove hardware correctness, kernel safety, performance, or adversarial resilience.

## Near-Term Convergence

1. Green the bootstrap CI and merge the complete repository baseline.
2. Accept R720 qualification artifacts from the hardware lane.
4. Merge/qualify the interlock and failure-injection harness.
5. Implement the narrowest useful read-only MCP surface.
6. Add mutating MCP tools only after authorization and rollback semantics are qualified.
7. Run security hardening and dependency audit.
8. Cut `v0.1.0-rc1` only from an exact qualified SHA.
