# CLIENT is supplied by the NixOS test fixture, not caller configuration.
import json
from pathlib import Path
import secrets
import subprocess


def msg(action, **fields):
    return {"schema": 1, "request_id": secrets.token_hex(16), "action": action, **fields}


def call(data, admin=False):
    argv = [CLIENT, "--admin"] if admin else ["runuser", "-u", "worker", "--", CLIENT]
    result = subprocess.run(argv, input=json.dumps(data), text=True, capture_output=True, timeout=25)
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


parent = call(msg("task.create"))
assert parent["ok"] and parent["task"]["owner_uid"] == 1000, parent
read = call(msg("invoke", task_id=parent["task_id"], operation="memory.snapshot", arguments={}, approval_id=None))
assert read["ok"], read
blocked = call(msg("invoke", task_id=parent["task_id"], operation="service.restart",
                   arguments={"unit": "demo.service"}, approval_id=None))
assert blocked["code"] == "outside_task_scope", blocked
spoof = call(msg("task.create", owner_uid=0, mode="remediation"))
assert spoof["code"] == "invalid_fields", spoof
child = call(msg("task.remediate", parent_task_id=parent["task_id"]), admin=True)
assert child["ok"] and child["task_id"] != parent["task_id"], child
invocation = msg("invoke", task_id=child["task_id"], operation="service.restart",
                 arguments={"unit": "demo.service"}, approval_id=None)
approval = call(msg("approval.issue", task_id=child["task_id"], invocation_id=invocation["request_id"],
                    operation="service.restart", arguments={"unit": "demo.service"}), admin=True)
assert approval["ok"], approval
invocation["approval_id"] = approval["approval_id"]
count = Path("/var/lib/broker-demo/count")
before = int(count.read_text())
result = call(invocation)
assert result["ok"], result
assert int(count.read_text()) == before + 1
assert call(invocation) == result
assert int(count.read_text()) == before + 1
subprocess.run(["systemctl", "restart", "substrate-broker.service"], check=True)
# Wait for the listener, not just Type=simple's process start.
import time
for attempt in range(100):
    if Path("/run/substrate-broker/agent.sock").exists():
        break
    time.sleep(0.1)
assert call(invocation) == result
assert int(count.read_text()) == before + 1
revoked = call(msg("task.revoke", task_id=parent["task_id"]), admin=True)
assert revoked["ok"], revoked
invocation["request_id"] = secrets.token_hex(16)
assert call(invocation)["code"] == "task_inactive"
print("broker VM: peer identity, diagnosis denial, approval, single dispatch, durable replay, revocation PASS")
