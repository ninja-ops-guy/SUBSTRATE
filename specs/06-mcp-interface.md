# MCP-Native System Interface

## 1. Design Principle

The preferred agent control surface is typed and policy-checked rather than an unrestricted shell. MCP transports the semantic operation vocabulary defined in `07-ai-os-boundary.md`; harness-specific approval flags never authorize OS operations. Every mutating tool must be:

- schema-validated
- authorization/policy checked
- audit logged
- guarded by the appropriate rollback or interlock policy
- independently bounded by OS enforcement where possible

> Status: interface specification. The MCP gateway is not yet production-implemented.

---

## 2. Proposed Tool Surface

```
omarchy-mcpd — local MCP server

Read/observe:
├── memory.get_state()
├── service.get_status(name)
├── snapshot.list()
├── cgroup.get_usage(name)
├── governor.get_policy()
├── governor.get_metrics()
└── system.health()

Mutating:
├── memory.set_workspace(size)
├── memory.set_hugepages(size, type)
├── memory.set_zram(size, algo)
├── service.restart(name)
├── snapshot.create(label)
├── snapshot.rollback(id)
├── cgroup.set_limit(name, key, value)
└── governor.set_policy(key, value)
```

Tools map to the shared READ / CHANGE / DANGEROUS classes. Service restart is CHANGE; rollback, resource-limit changes, and governor-policy mutation are DANGEROUS. Unknown operations fail closed. Diagnosis-only tasks expose READ tools only.

---

## 3. Transport & Authentication Requirements

The production transport must specify and test:

1. caller identity
2. per-tool authorization
3. replay/idempotency semantics
4. request size/time bounds
5. durable audit correlation ID
6. cancellation behavior
7. crash/restart behavior
8. privilege separation between the gateway and privileged helpers

A Unix-domain socket may be used locally, but filesystem permissions alone are not sufficient evidence of authorization for privileged tool calls.

---

## 4. Why MCP

| Approach | Tradeoff |
|----------|----------|
| Shell/CLI | Broad string-typed authority and injection surface |
| REST API | Viable, but adds a separate HTTP auth/schema surface |
| MCP | Fits typed tool invocation and can carry a narrow, schema-defined capability surface |

MCP is a control-plane interface, not a security boundary by itself. Kernel/service enforcement remains authoritative.
