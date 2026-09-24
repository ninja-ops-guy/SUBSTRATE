# SUBSTRATE / OMARCHY-SRV Development Roadmap

**Target:** qualified RAM-first inference and control substrate for Dell R720-class hardware  
**Last updated:** 2026-09-24

## Current State

The Rust core is now on `main` with clean-runner CI green. The Phase 2 qualification harness is implemented on a dedicated branch with local regression evidence, but physical R720 qualification and destructive recovery evidence are still open.

The correct release posture is:

**v0.1 core implementation candidate + Phase 2 qualification harness candidate; production qualification pending.**

A checkmark in this roadmap means implementation or specification evidence exists. It does not substitute for R720 qualification, failure injection, security review, or release acceptance.

## Workstreams

### A. Core repository + CI — COMPLETE

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
- [x] All bootstrap CI jobs green on `main` head `b2710220…`
- [x] Bootstrap baseline merged to `main`

### B. R720 hardware qualification — HARNESS READY / EXECUTION OPEN

- [x] Add automated NUMA, memory, storage, cgroup-v2, and PSI qualification harness
- [x] Preserve PASS / FAIL / SKIP / ERROR distinctly; SKIP cannot qualify a run
- [x] Add machine-readable JSON and human-readable Markdown receipts
- [ ] Capture immutable host/firmware/CPU/RAM/storage baseline on the R720
- [ ] Discover and verify NUMA topology/locality on the target host
- [ ] Measure local/remote bandwidth and latency with qualification tooling installed
- [ ] Validate cgroup v2 controller availability/delegation on the target host
- [ ] Exercise zram reclaim/demotion on the target host
- [ ] Produce final machine-readable qualification receipt with no required FAIL/ERROR/SKIP

### C. Interlock + failure injection — IMPLEMENTED CANDIDATE / HARDWARE QUALIFICATION OPEN

- [x] Implement pre-freeze/cooperative quiescence interlock
- [x] Add file-backed cross-process lock declarations
- [x] Correct `/proc/locks` PID/device/inode parsing and FD-to-path resolution
- [x] Require callbacks to release and unregister exclusive resources before yield acknowledgment
- [x] Resume surviving SIGSTOP workers on thaw/failure paths
- [x] Fail closed when lingering declared exclusive locks remain
- [x] Add bounded OOM, campaign-owned stall, cgroup-write rejection, and daemon-death injection lanes
- [x] Keep checkpoint-corruption lane blocked until disposable-clone + verifier semantics exist
- [x] Add regression suite: 16 pass locally; root-only R720 integration intentionally skipped outside target host
- [ ] Root-only SIGUSR2 → SIGSTOP → cgroup freeze → thaw integration PASS on R720
- [ ] Repeated lock-contention and timeout stress evidence
- [ ] OOM/governor recovery evidence from a finite, isolated cgroup
- [ ] daemon crash/restart reconciliation evidence
- [ ] rollback partial-failure injection
- [ ] snapshot exhaustion / missing snapshot injection
- [ ] verify durable evidence retention for every live failure lane

### D. Core safety and recovery — PARTIAL

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

### E. Inference integration — OPEN / GATED

- [ ] Query installed Ollama/runtime capabilities rather than assuming them
- [ ] model metadata ingestion for planner calibration
- [ ] measured weight/residency calibration
- [ ] topology-aware placement policy
- [ ] preload/unload lifecycle qualification
- [ ] TTFT and throughput baseline/after-pressure measurements

Ollama integration remains gated on target-host interlock and failure-recovery evidence. No tensor-split or session-persistence feature is considered available until verified against the selected runtime/version.

### F. RESIDUAL MCP integration — OPEN / GATED

- [x] Interface specification
- [ ] MCP transport implementation
- [ ] caller identity/authentication
- [ ] per-tool authorization policy
- [ ] read-only observation tools
- [ ] mutating tools behind safety gates
- [ ] correlation IDs and durable audit receipts
- [ ] restart/replay/idempotency tests
- [ ] RESIDUAL adapter/conformance tests

Mutating MCP integration remains downstream of the same hardware, interlock, rollback, and authorization evidence gates.

### G. Security hardening — OPEN

- [ ] Landlock implementation and negative-path tests
- [ ] seccomp implementation and syscall compatibility tests
- [ ] Unix-socket peer identity/auth review
- [ ] systemd capabilities and writable-path minimization
- [ ] dependency vulnerability audit
- [ ] hostile config/path tests
- [ ] privilege-escalation tests
- [ ] network boundary review
- [ ] audit-log tamper-resistance design/qualification

### H. Release qualification — OPEN

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

1. Get the Phase 2 harness PR green in GitHub Actions and merge it.
2. Execute the R720 hardware qualification and bind its receipts to an exact commit/version.
3. Run the root-only interlock lifecycle and lock-contention stress campaign.
4. Execute bounded failure injection and reconcile every recovery result.
5. Complete rollback verifier/disposable-clone semantics, then enable the blocked checkpoint lane.
6. Only after those gates pass, begin Ollama integration and the narrowest useful read-only MCP surface.
7. Run security hardening and dependency audit.
8. Cut `v0.1.0-rc1` only from an exact qualified SHA.
