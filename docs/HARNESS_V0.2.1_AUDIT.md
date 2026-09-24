# SUBSTRATE Harness v0.2.1 — Safety and CI Audit

## Disposition

**Ready for CI and R720 qualification; not yet hardware-qualified for RC.**

The v0.2.0 bundle compiled, but several defects could have produced false-positive safety evidence. v0.2.1 fixes the CI-detectable defects, adds regression coverage, and makes destructive testing explicit. Physical R720 evidence is still required before the Phase 2 gate can close.

## Release-blocking defects corrected

1. **`/proc/locks` parsing was invalid.** Linux exposes the PID in field 5 and a device/inode identity rather than a path. v0.2.0 attempted to parse the lock mode as the PID and expected a pathname, which could make prohibited-lock inspection return empty. v0.2.1 parses PID + device/inode and resolves matching open FDs back to paths.
2. **Stopped-state acknowledgment was not recognized correctly.** Linux reports `State: T (stopped)`; the old check searched for `(T)`. v0.2.1 parses the state code.
3. **Thaw did not resume cooperative agents.** Agents that acknowledged by `SIGSTOP` were in `yielded_pids`, but thaw only resumed agents outside that set. v0.2.1 sends `SIGCONT` to every surviving agent after cgroup thaw.
4. **Lock declarations could be hidden without releasing the resource.** The agent handler removed declarations automatically after callbacks. v0.2.1 requires callbacks to release the resource and unregister it explicitly; lingering exclusive declarations block quiescence.
5. **Per-process lock declarations were not visible to the orchestrator.** Separate `LockRegistry` instances had separate in-memory maps. v0.2.1 reads registry files and resolves declarations by PID.
6. **`SKIP` could be counted as `PASS`.** The qualification base runner only recognized explicit `FAIL`. v0.2.1 preserves PASS/FAIL/SKIP/ERROR, and a full qualification is not complete while tests are skipped.
7. **The 1 GiB hugepage probe checked the 2 MiB path.** v0.2.1 checks the kernel's size-specific hugepage sysfs entries.
8. **RAID discovery used an invalid shell invocation.** v0.2.1 runs `lspci` directly and filters output in Python.
9. **Failure campaigns could exit green with missing tests.** Early-return failures and exceptions were not always recorded. v0.2.1 tracks requested vs executed tests and marks incomplete campaigns failed.
10. **Process-stall injection could target an arbitrary host `sleep`.** v0.2.1 creates and owns its own target when no PID is supplied.
11. **Cgroup-write injection could mutate controller state.** v0.2.1 uses an invalid kernel value and requires that the controller reject it without changing the prior value. Targets are restricted to cgroupfs.
12. **Checkpoint-corruption injection mutated the source checkpoint and did not prove rollback integrity.** v0.2.1 disables that destructive implementation and leaves the lane blocked until a disposable clone + verifier contract is available.
13. **OOM detection could confuse normal process exit or historical OOMs with a new OOM.** v0.2.1 gates the allocator inside the target cgroup before allocation and requires a delta in `memory.events:oom_kill`.

## CI evidence

Local verification on the review environment:

- `python3 -m py_compile src/*.py` — PASS
- `pytest -q` — **16 passed, 1 skipped**
- Skipped test: root-only R720 cgroup lifecycle integration, intentionally disabled unless `SUBSTRATE_R720_INTEGRATION=1`.
- Failure-injection CLI dry run — PASS

The GitHub Actions workflow in `.github/workflows/harness-ci.yml` runs compile, unit tests, and a non-destructive failure-injection dry run.

## R720 qualification sequence

```bash
# 1. Static + unit qualification
cd <repo>/harness
python3 -m py_compile src/*.py
pytest -q

# 2. Hardware qualification
cd <repo>
sudo python3 harness/src/qualification_harness.py --output ./harness/results/r720

# 3. Real cgroup interlock lifecycle
sudo SUBSTRATE_R720_INTEGRATION=1 \
  pytest -q harness/tests/integration/test_interlock_r720.py

# 4. Inspect target memory.max before OOM injection
cat /sys/fs/cgroup/residual-executor/memory.max

# 5. Live core failure campaign on the isolated qualification host
sudo python3 harness/src/failure_injection.py \
  --allow-destructive \
  --target-cgroup /sys/fs/cgroup/residual-executor \
  --oom-memory-gb 1.0 \
  --tests oom stall cgroup_write daemon_death
```

Choose `--oom-memory-gb` so the requested allocation is greater than the target cgroup's finite `memory.max`; the injector refuses an unlimited cgroup or an allocation that cannot trigger the intended boundary.

## Remaining Phase 2 gates

- R720 full qualification report with no FAIL/ERROR/SKIP on required RC checks.
- Root-only interlock integration PASS on the actual cgroup v2 host.
- Stress repetitions and lock-contention negative-path evidence.
- OOM/governor recovery evidence from a bounded target cgroup.
- Daemon restart/reconciliation evidence.
- Rollback coordinator disposable-clone + verifier interface for the checkpoint-corruption lane.
- Independent governor safety evidence before Ollama integration is enabled.
