# Broker development and operator quickstart

The broker is an opt-in candidate. No existing SUBSTRATE host or live installer is enabled by this addition. Read `specs/08-broker-task-envelope.md` before enabling a mutation adapter.

## Run tests or build without changing the host

```bash
PYTHONPATH=broker python3 -m unittest discover -s broker/tests -v
nix build .#broker --no-update-lock-file
nix build .#checks.x86_64-linux.broker-vm --no-update-lock-file -L
```

The first command needs Python's standard library only. The VM uses a disposable service, not a real inference or production workload.

## NixOS module (read-only deployment example)

Import the separately exported `nixosModules.broker` alongside your host configuration:

```nix
{
  imports = [ inputs.substrate.nixosModules.broker ];
  services.substrate.broker = {
    enable = true;
    allowedUIDs = [ 1000 ]; # Explicitly choose your dedicated worker's actual UID.
    # Only system.identify and memory.snapshot are exposed by default.
    # enableServiceRestart remains false; no units are allowlisted.
  };
  users.users.worker.extraGroups = [ "substrate-broker" ];
}
```

The UID must match the declared worker; adding group membership is not enrollment by itself. Do not give the harness root, passwordless sudo, the admin socket, or another privileged control socket.

## Worker protocol

`substrate-osctl` reads one JSON object from stdin and sends it to the worker socket. A stable random request ID is mandatory. Generate it once for a request and keep the entire payload for retries.

```json
{"schema":1,"request_id":"11111111111111111111111111111111","action":"task.create"}
```

The returned `task_id` identifies a server-owned diagnosis envelope. Inspect it with `task.get`, or submit a read invocation:

```json
{"schema":1,"request_id":"22222222222222222222222222222222","action":"invoke","task_id":"REPLACE_WITH_RETURNED_TASK_ID","operation":"memory.snapshot","arguments":{},"approval_id":null}
```

The repeated digits above are illustrative IDs, not IDs to reuse for unrelated work. IDs are exactly 32 lowercase hexadecimal characters. An edited envelope, `owner_uid`, `mode`, or `permission_class` in these requests is rejected.

## Administrator workflow for an explicitly enabled restart adapter

This workflow is unavailable until machine policy explicitly enables the adapter and allowlists the exact target in both `serviceUnits` and `restartUnits`. No command below grants a general shell or enables DANGEROUS operations.

Use a separately authenticated administrative terminal, not the agent's process. Send administrator requests through `sudo substrate-osctl --admin`.

1. Create a new child with `task.remediate` and `parent_task_id` from the worker's still-active diagnosis task. The parent remains read-only.
2. Preselect a new worker invocation request ID. Send `approval.issue` with `task_id`, `invocation_id`, `operation: "service.restart"`, and `arguments: {"unit": "your-allowlisted.service"}`.
3. Return the resulting `approval_id` to that worker. Its invocation must use the exact task, invocation ID, operation, and arguments approved in step 2.

Each control request also has its own `schema`, `action`, and distinct `request_id`. Approval expiry defaults to 30 seconds; task expiry defaults to 300 seconds. Approval cannot extend task lifetime.

For revocation, send `task.revoke` with `task_id` on the administrator socket. For audit inspection, send `audit.list` with `after_seq: 0`; responses contain at most 100 records and can be paginated by the last sequence number.

## When the result is uncertain

No response, `indeterminate`, or `broker_unavailable` may mean an admitted operation ran. Retry only the identical payload with the SAME request ID to retrieve its receipt. Never generate a new ID merely to repeat an uncertain mutation. Systemctl timing out does not prove the service manager cancelled the job.

Keep `/var/lib/substrate-broker` out of ordinary workspace/data restores. Do not delete the database, initialization marker, or approval journal to “fix” a task. Stop and inspect audit/storage health using an administrative recovery path. Ledger retention and recovery automation are still qualification work.
