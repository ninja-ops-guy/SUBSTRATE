# Typed AI-to-OS Boundary

## 1. Purpose

This specification reconciles SUBSTRATE with the Omarchy reference-architecture study without turning SUBSTRATE into an Omarchy clone.

The study's central lesson is that the reusable component is the boundary between Linux events, OS knowledge/actions, and replaceable agent harnesses. SUBSTRATE already has a typed MCP direction, an explicit RESIDUAL/OS division of labor, and a kernel/runtime enforcement model. The missing piece is to make the semantic OS boundary explicit and shared.

## 2. Authority Model

Models, harnesses, prompts, and skills are **requesters**, not authorization authorities.

The trusted path is:

```
model / harness / skill
        |
        | typed request
        v
semantic OS boundary
        |
        +-- classify
        +-- validate arguments
        +-- bind task/correlation identity
        +-- policy decision / approval
        +-- audit
        |
        v
qualified SUBSTRATE mechanism
```

MCP may transport these requests, but MCP is not itself the security boundary.

## 3. Permission Classes

Every semantic operation has one static minimum class:

- **READ** — observation only; may be automatically permitted by policy.
- **CHANGE** — bounded mutation; requires explicit pre-authorization or an approval path.
- **DANGEROUS** — high-blast-radius mutation; requires explicit per-operation authorization and stronger interlocks.

Unknown operations fail closed.

Initial vocabulary:

### READ

`system.identify`, `memory.snapshot`, `crash.list`, `crash.inspect`,
`journal.query`, `service.status`, `package.query`, `network.snapshot`,
`cgroup.usage`, `governor.metrics`, `snapshot.list`.

### CHANGE

`service.restart`, `package.install`, `config.patch`, `snapshot.create`.

### DANGEROUS

`snapshot.rollback`, `cgroup.set_limit`, `governor.set_policy`,
`system.boot_config`, `storage.partition`, `security.policy_change`.

This vocabulary is intentionally narrower than a root shell.

## 4. Diagnosis Is Not Remediation

A diagnosis task exposes READ operations only. A subsequent remediation request is a new task with a different authority envelope.

A skill may recommend remediation but cannot authorize it.

## 5. Event Contract

Event providers normalize implementation-specific events before they reach an agent task.

Initial crash schema:

```json
{
  "type": "process.crash",
  "pid": 4312,
  "uid": 1000,
  "comm": "example",
  "exe": "/usr/bin/example",
  "signal": "SIGSEGV",
  "timestamp": "2026-09-24T14:32:10-04:00",
  "source": "systemd-coredump"
}
```

The rest of the control plane must not depend on systemd-coredump specifically. Future providers may emit the same semantic event.

## 6. Harness Independence

SUBSTRATE must not encode Claude/Codex/Ori-specific authorization semantics into the OS layer.

A future harness registry may describe launch mechanics and capabilities. It must not grant OS authority. Replacing a harness must not require changing event providers, policy classification, or privileged OS mechanisms.

## 7. Task Identity and Notifications

Event producers create typed tasks. They do not emit executable shell strings.

Where notifications or UIs are later added, they should carry opaque task identifiers and resolve trusted task metadata from a local task store. Untrusted event text remains data.

## 8. Sensitive Evidence

Raw coredumps are sensitive process memory and must never be automatically exported to a remote model.

Default remote evidence should be locally reduced to metadata, sanitized journal excerpts, package/module versions, symbolic backtraces, and resource state. Export of environment variables, memory strings, credential-bearing command lines, or user-document fragments requires explicit policy.

## 9. Audit Contract

Every semantic OS invocation must eventually produce a structured record binding:

- task ID
- correlation ID
- requesting actor/harness identity
- operation + normalized arguments
- permission class
- policy decision
- approving identity when applicable
- result/exit status
- before/after hashes or diffs for file mutation where applicable

The audit record must avoid becoming a second secret store.

## 10. Relationship to Existing SUBSTRATE Components

- `omarchy-configd`: remains atomic configuration mechanism; it does not become the authorization authority.
- `omarchy-cgroupd`: remains runtime resource enforcement.
- NixOS: remains host desired-state/generation mechanism.
- Btrfs: remains mutable-data recovery mechanism.
- RESIDUAL: remains mission/governance/verification layer.
- MCP: becomes one transport for the semantic operation vocabulary.
- Landlock/seccomp/cgroups: remain independent enforcement boundaries after qualification.

## 11. Explicit Non-Goals for This Increment

This increment does **not** claim to implement:

- a privileged broker daemon;
- local approval UI;
- harness registry/installer;
- systemd-coredump watcher;
- task store;
- remote model/provider routing;
- Landlock or seccomp enforcement.

Those remain separate qualification lanes. This increment establishes the shared typed contracts first.
