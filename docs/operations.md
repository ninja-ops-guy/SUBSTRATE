# SUBSTRATE / OMARCHY-SRV Operations Guide

## Current Operating Boundary

The v0.1 implementation is a qualification candidate. The operator must not assume that specified-but-unimplemented mechanisms such as the full MCP gateway, Landlock/seccomp policy, governor, or interlock are active.

## Observe Memory Pressure

```bash
cat /proc/pressure/memory
cat /sys/fs/cgroup/residual-executor/memory.current 2>/dev/null || true
cat /sys/fs/cgroup/residual-executor/memory.events 2>/dev/null || true
```

## Inspect Services

```bash
systemctl --no-pager --full status omarchy-configd omarchy-cgroupd
journalctl -u omarchy-configd -u omarchy-cgroupd --since -30min
```

## Configuration Dispatch

During qualification, a trusted operator can submit TOML through the local socket:

```bash
cat agent-config.toml | nc -N -U /run/omarchy-srv/omarchy-configd.sock
```

Production deployment requires caller authorization appropriate to the authority of the requested change.

## Ollama Model Unload

The rollback coordinator uses Ollama's documented model-unload behavior: a generate request with `keep_alive: 0`. SUBSTRATE does **not** assume a per-session delete/rollback endpoint.

Example operator diagnostic:

```bash
curl -sS http://127.0.0.1:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"MODEL_NAME","prompt":"","stream":false,"keep_alive":0}'
```

This unloads the model/runtime state associated with that model; it is not a filesystem rollback.

## Filesystem Rollback Qualification

The implemented coordinator follows the sequence:

1. freeze the configured cgroup
2. request Ollama model unload
3. restore the selected btrfs subvolume snapshot
4. reset Git to the checkpoint SHA
5. unfreeze

If the unload, filesystem restore, or Git reset fails, the operation returns an error. Failure-injection tests must verify the resulting safety behavior before production use.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Config rejected | `journalctl -u omarchy-configd`; TOML; NUMA node existence |
| Cgroup not created | `systemctl status omarchy-cgroupd`; cgroup v2 mount; unit permissions |
| Unexpected memory pressure | PSI + `memory.current` + `memory.events` |
| Rollback failure | btrfs snapshot existence, workspace path, Ollama availability, Git SHA |
| Slow post-rollback inference | model reload/context reconstruction and measured TTFT |

## Break Glass

Do not improvise destructive rollback commands while workers hold unknown locks. Use the qualified interlock/rollback procedure once that lane is complete. Until then, a human operator should establish quiescence and preserve evidence before manual recovery.
