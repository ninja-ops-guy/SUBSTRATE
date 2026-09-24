# SUBSTRATE Phase 2 Qualification Harness

This harness provides the evidence-generating tests for SUBSTRATE's R720 hardware, interlock, and failure-recovery gates. It is intentionally separate from the Rust mechanism tests: generic CI proves source/build properties; this harness generates host and fault evidence.

## Safe CI lane

```bash
cd harness
python3 -m py_compile src/*.py
python3 -m pip install pytest
pytest -q
python3 src/failure_injection.py --dry-run --tests oom stall cgroup_write daemon_death
```

The root-only R720 integration test is skipped in ordinary CI.

## R720 qualification lane

Run only on the isolated qualification host:

```bash
sudo python3 harness/src/qualification_harness.py --output ./harness/results/r720

sudo SUBSTRATE_R720_INTEGRATION=1 \
  pytest -q harness/tests/integration/test_interlock_r720.py
```

Before destructive failure injection, verify that the target cgroup is disposable/bounded and that the selected allocation exceeds its finite `memory.max`:

```bash
cat /sys/fs/cgroup/residual-executor/memory.max

sudo python3 harness/src/failure_injection.py \
  --allow-destructive \
  --target-cgroup /sys/fs/cgroup/residual-executor \
  --oom-memory-gb 1.0 \
  --tests oom stall cgroup_write daemon_death
```

The checkpoint-corruption lane remains disabled until the rollback coordinator exposes a disposable-clone + verifier contract. Do not treat skipped or disabled lanes as release evidence.
