# SPEC-SUBSTRATE-BROKER-001 — Task envelope and local OS broker

Status: executable implementation candidate; disabled by default; independent security review and physical-host qualification are release gates.

Extends `07-ai-os-boundary.md`. This is not an MCP server, new agent harness, or replacement for RESIDUAL's mission store. It is the local authority and execution boundary for a deliberately narrow subset of the semantic OS API.

## 1. Implementation and ownership

`broker/` is an independent Python 3.11+ standard-library-only service, packaged with a pinned Nix Python closure. SQLite supplies durable transactions without adding a Cargo dependency or rewriting `omarchy-configd`/`omarchy-cgroupd`.

Rust `classify()` and the executable broker use the same `src/os_operations.json` catalog. Its permission class is a minimum classification, never an authorization decision. Executable adapters perform additional argument and target checks. Reserved catalog operations do not acquire an implementation merely by appearing in the catalog.

NixOS supplies `nixosModules.broker` and `packages.x86_64-linux.broker`. The module is opt-in. It does not enable broker authority on the existing R720 profile or installer, grant workers sudo, expose legacy daemon sockets, or contact iDRAC.

## 2. Trusted and untrusted inputs

Trusted: kernel peer credentials; administrator-controlled machine policy and installed code/catalog; broker-owned task/approval state; the kernel, systemd, and local SQLite storage.

Untrusted: every request byte, copied task envelope, task/approval identifier, model response, skill, harness, and user/project configuration.

Host root remains trusted. An authenticated root socket call proves administrator identity, not that a human was physically present. Agents must use separate non-root worker UIDs and must not inherit administrator sockets, sudo access, container-manager sockets, or other privileged paths. This broker does not itself implement the planned Landlock/seccomp harness sandbox.

## 3. Transport and identity

Two AF_UNIX stream sockets live below `/run/substrate-broker`, owned by root with group `substrate-broker`:

| Socket | Mode | Authority |
| --- | --- | --- |
| `agent.sock` | 0660 | Explicitly enrolled non-root UID; group membership alone is insufficient |
| `admin.sock` | 0600 | Kernel-authenticated UID 0; no worker execution API |

Identity is read with Linux SO_PEERCRED, never from JSON. The protocol is one 4-byte network-order length followed by one UTF-8 JSON object per connection. Schema version is 1. Duplicate keys, non-finite numbers, unknown fields, oversized frames, invalid identifiers, and unknown actions fail closed.

Requests are capped at 16 KiB and replies at 64 KiB. Frame assembly has an absolute two-second deadline, including slow-drip input. There are at most eight worker connections and two separate administrator slots. Saturated or malformed transport connections may be closed without a response; an absent response never proves that a previously admitted operation did not execute.

## 4. Server-issued task envelope

A worker can create only a diagnosis task. It cannot choose a UID, mode, operation grant, expiry, approval identity, or policy version. The returned envelope contains:

- schema, opaque random 128-bit task ID, kernel-derived owner UID;
- mode (`diagnosis` or `remediation`) and exact operation names;
- parent diagnosis ID for a remediation task;
- monotonic issue/deadline timestamps and maximum invocation count;
- host epoch (boot ID plus current NixOS generation) and machine-policy/catalog digest.

The stored envelope is authoritative. Sending an edited copy does nothing: the invocation API accepts only its identifier. Task ownership, expiry, revocation, host epoch, policy digest, and remaining budget are checked before new execution. Broker restarts preserve leases on the same boot; reboot, generation change, or policy/catalog change invalidates old leases.

`task.remediate` is an administrator-only creation of a new child task, not an upgrade to diagnosis. Its deadline cannot exceed its parent's. Revocation cascades to children. Existing receipts remain queryable through identical request replay; revocation denies new execution, not historical evidence.

## 5. Executable operation matrix

| Operation | Minimum class | Implemented behavior |
| --- | --- | --- |
| `system.identify` | READ | ID, VERSION_ID, PRETTY_NAME only; no hostname/machine-ID |
| `memory.snapshot` | READ | MemTotal, MemAvailable, SwapTotal, SwapFree in bytes |
| `service.status` | READ | Explicit unit allowlist; selected systemd state properties only |
| `service.restart` | CHANGE | Disabled by default; exact target allowlist AND remediation task AND one-use administrator approval |
| Other READ/CHANGE vocabulary | Reserved | `operation_not_implemented`; no executor dispatch |
| All DANGEROUS vocabulary | DANGEROUS | Disabled, including for administrator approval requests |
| Unknown operations / `shell.exec` | Unknown | Denied |

No adapter accepts a shell command, arbitrary filesystem path, environment override, provider key, raw journal text, coredump, or arbitrary executable. Service units must be exact non-template names: no wildcards, option-like names, slashes, escape syntax, or command separators. Core SUBSTRATE, SSH, systemd, D-Bus, polkit, and Nix-daemon prefixes are protected. Canonical systemd unit identity is checked before a restart; aliases cannot silently authorize another unit.

Systemctl is an administrator-selected absolute executable, pinned by Nix in the supported deployment. Execution uses an argument vector, fixed environment, closed stdin, no shell, bounded output, and a timeout. A timed-out systemctl process may already have submitted a job to systemd: timeout is **indeterminate**, never an automatic retry instruction.

## 6. One-use approval binding

`approval.issue` exists only on the administrator socket. It accepts an existing remediation task, a preselected invocation request ID, and the exact implemented operation/arguments. The grant is bound to:

`task ID + owner UID + invocation ID + operation + canonical arguments + host epoch + machine policy/catalog digest`

The grant expires after at most the configured approval TTL and never after its task. It does not authorize another target, task, UID, request ID, policy, or generation. A copied approval ID is not sufficient authority. `approved_by_uid` is derived from the verified administrator, not caller text. There is no blanket pre-approval or DANGEROUS override in this slice.

## 7. Durable admission, replay, and recovery

State is stored in a private `/var/lib/substrate-broker` directory, with one exclusive process lock. Symlinks, unexpected hardlinks, wrong ownership/permissions, corrupt SQLite state, and missing/truncated previously initialized state are rejected. SQLite uses WAL and FULL synchronous commits.

Each `(socket role, UID, request ID)` identifies one canonical request. An identical retry returns its saved response, without execution. Reusing the ID with different content is a conflict.

Before adapter invocation, one transaction commits the request reservation, task-budget decrement, approval consumption (when needed), and admission audit. A second transaction records completion. On startup, leftover `started` reservations become `indeterminate`; they are never automatically dispatched again. A process death after a side effect but before the completion receipt does not re-enable the grant.

This is **at-most-one broker dispatch per request ID**, not an exactly-once guarantee about external side effects. Failed/indeterminate changes require inspection and a new explicit administrative decision, not retry with a freshly generated ID.

Audit/storage failure before admission prevents execution. Failure after admission poisons the broker and preserves uncertainty; it must not report that nothing happened. No action is permitted while audit storage is unavailable.

The ledger is limited to 256 tasks, 10,000 requests, and 50,000 audit records. Exhaustion fails closed; automatic pruning, export/retention automation, and fleet-scale storage are not implemented. Keep this ledger outside ordinary agent-controlled data rollback. Restore it only through an administrative recovery procedure; this implementation does not defeat hostile root or same-boot rollback of its own storage.

## 8. Audit evidence and privacy

Audit records are transactional and append-only through the application/schema, not cryptographically tamper-proof against root. They record task/request correlation, kernel UID/PID, socket role, operation/minimum class, normalized argument hash, policy decision, approval binding/administrator identity, and completion or recovery outcome. Unknown request bodies and denied secret-bearing arguments are not copied into the audit log. No model/provider credential routing or raw-core export exists.

This extends the earlier `audit.rs` contract with explicit admission/completion/recovery states. Request ID is the correlation ID for this local protocol. A future MCP adapter must preserve the same identity, binding, and replay semantics rather than accepting client-supplied classified requests as authority.

## 9. Qualification commands and acceptance

```bash
PYTHONPATH=broker python3 -m unittest discover -s broker/tests -v
nix build .#packages.x86_64-linux.broker --no-update-lock-file
nix build .#checks.x86_64-linux.broker-vm --no-update-lock-file -L
cargo test --manifest-path src/Cargo.toml --locked --all-targets
```

The adversarial suite covers forged envelopes/identity, cross-UID task use, root/worker role confusion, diagnosis-to-mutation attempts, approval scope/expiry/replay, policy/generation invalidation, concurrent duplicates, budget reservation, real process death after a side effect, durable restart recovery, failed audit commits, unsafe state paths, catalog downgrades, protected services, shell/option injection, and bounded transport/execution.

The NixOS VM fixture uses a non-root worker and a disposable `demo.service`. It checks real Unix identity and permissions, read-only diagnosis, explicit approved restart, one side effect across replay and broker restart, revocation, and absence of direct worker systemctl authority. It does not run against the owner's hardware or production services.

## 10. Remaining gates

Independent security review; exact-head VM and build evidence; physical-host service/privilege qualification; broader failure injection; protected ledger retention/recovery procedures; durable external audit anchoring; and a qualified MCP transport remain release work. The crash watcher, harness registry, provider routing, approval UI, DANGEROUS adapters, Nix-generation mutation, Btrfs rollback, and automatic remediation remain out of scope.

## Reference basis

The uploaded *Omarchy as a Reference Architecture for a Distro-Agnostic AI-to-OS Integration Layer* (2026-09-24), especially pages 23–28 and 34–36, supplies the conceptual separation of harness requests, policy broker, diagnosis/remediation, and audit. The concrete lease, credential, transaction, replay, and adapter rules above are SUBSTRATE implementation decisions, not claims that the paper already specifies them.

Primary API references: Linux `unix(7)` SO_PEERCRED documentation; Python `sqlite3` transaction-control documentation; systemd `systemctl` and `systemd.exec` manuals. The implementation does not depend on any particular agent CLI's approval flags.
