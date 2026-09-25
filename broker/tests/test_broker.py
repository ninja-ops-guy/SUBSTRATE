"""Adversarial tests use no real host mutations. Socket tests use real SO_PEERCRED."""
import concurrent.futures
import dataclasses
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from adapters import LocalAdapter, run_bounded
from core import Broker, Denied, Peer, Policy, Store, canonical, strict_json
from transport import Server, peer_credentials, read_frame, request

ROOT = Path(__file__).resolve().parents[2]
CATALOG = strict_json((ROOT / "src/os_operations.json").read_text())


def rid():
    return secrets.token_hex(16)


def message(action, **kwargs):
    return {"schema": 1, "request_id": rid(), "action": action, **kwargs}


def policy_data(**kwargs):
    return {"schema": 1, "allowed_uids": [1000, 1001],
            "read_operations": ["system.identify", "memory.snapshot", "service.status"],
            "service_units": ["demo.service", "other.service"], "restart_units": ["demo.service", "other.service"],
            "enable_service_restart": True, "task_ttl_seconds": 300, "approval_ttl_seconds": 30,
            "max_calls": 32, **kwargs}


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.store = Store(self.state)
        self.data = policy_data()
        self.policy = Policy.load(self.data, CATALOG)
        self.now = 1_000_000_000
        self.epoch = "boot-a-generation-a"
        self.effects = []
        self.broker = Broker(self.store, self.policy, CATALOG, self.execute,
                             lambda: self.now, lambda: self.epoch)
        self.agent = Peer(101, 1000, 1000)
        self.admin = Peer(102, 0, 0)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def execute(self, operation, arguments):
        self.effects.append((operation, arguments))
        return {"status": "succeeded", "data": {"fixture": True}}

    def call(self, action, admin=False, **kwargs):
        return self.broker.handle(self.admin if admin else self.agent,
                                  "admin" if admin else "agent", message(action, **kwargs))

    def diagnosis(self):
        response = self.call("task.create")
        self.assertTrue(response["ok"], response)
        return response["task_id"]

    def remediation(self):
        parent = self.diagnosis()
        response = self.call("task.remediate", admin=True, parent_task_id=parent)
        self.assertTrue(response["ok"], response)
        return response["task_id"]

    def approved(self):
        task = self.remediation()
        req = message("invoke", task_id=task, operation="service.restart",
                      arguments={"unit": "demo.service"}, approval_id=None)
        grant = self.call("approval.issue", admin=True, task_id=task, invocation_id=req["request_id"],
                          operation=req["operation"], arguments=req["arguments"])
        self.assertTrue(grant["ok"], grant)
        req["approval_id"] = grant["approval_id"]
        return req

    def invoke(self, task, operation="memory.snapshot", arguments=None, approval_id=None):
        return self.call("invoke", task_id=task, operation=operation,
                         arguments={} if arguments is None else arguments, approval_id=approval_id)

    def test_diagnosis_is_server_issued_and_read_only(self):
        task = self.diagnosis()
        result = self.invoke(task)
        self.assertTrue(result["ok"])
        result = self.invoke(task, "service.restart", {"unit": "demo.service"})
        self.assertEqual(result["code"], "outside_task_scope")
        self.assertEqual(len(self.effects), 1)

    def test_forged_owner_mode_and_approval_fields_are_denied(self):
        for extra in ({"owner_uid": 0}, {"mode": "remediation"}, {"approved_by": "root"}, {"policy": "allow-all"}):
            with self.subTest(extra=extra):
                self.assertEqual(self.call("task.create", **extra)["code"], "invalid_fields")
        req = message("invoke", task_id=self.diagnosis(), operation="memory.snapshot", arguments={}, approval_id=None, permission_class="READ")
        self.assertEqual(self.broker.handle(self.agent, "agent", req)["code"], "invalid_fields")
        self.assertFalse(self.effects)

    def test_admin_socket_requires_kernel_root_uid(self):
        result = self.broker.handle(self.agent, "admin", message("task.remediate", parent_task_id=self.diagnosis()))
        self.assertEqual(result["code"], "administrator_required")

    def test_no_admin_action_on_worker_socket(self):
        for action in ("task.remediate", "approval.issue", "task.revoke", "audit.list"):
            with self.subTest(action=action):
                self.assertEqual(self.call(action)["code"], "action_not_permitted")

    def test_admin_cannot_use_worker_execution_interface(self):
        self.assertEqual(self.call("invoke", admin=True)["code"], "action_not_permitted")
        self.assertEqual(self.broker.handle(self.admin, "agent", message("task.create"))["code"], "worker_not_enrolled")

    def test_other_uid_cannot_read_or_execute_task(self):
        task = self.diagnosis()
        for action in ("task.get", "invoke"):
            msg = message(action, task_id=task)
            if action == "invoke":
                msg.update(operation="memory.snapshot", arguments={}, approval_id=None)
            result = self.broker.handle(Peer(999, 1001, 1001), "agent", msg)
            self.assertEqual(result["code"], "task_unavailable")
        self.assertFalse(self.effects)

    def test_unenrolled_uid_denied(self):
        result = self.broker.handle(Peer(100, 1234, 1234), "agent", message("task.create"))
        self.assertEqual(result["code"], "worker_not_enrolled")

    def test_root_cannot_be_enrolled_as_worker(self):
        with self.assertRaises(Denied):
            Policy.load(policy_data(allowed_uids=[0]), CATALOG)

    def test_separate_remediation_task_does_not_upgrade_parent(self):
        parent = self.diagnosis()
        child = self.call("task.remediate", admin=True, parent_task_id=parent)["task_id"]
        self.assertNotEqual(parent, child)
        self.assertEqual(self.call("task.get", task_id=parent)["task"]["mode"], "diagnosis")
        self.assertEqual(self.call("task.get", task_id=child)["task"]["mode"], "remediation")
        self.assertFalse(self.invoke(child)["ok"])

    def test_approved_restart_executes_once(self):
        req = self.approved()
        first = self.broker.handle(self.agent, "agent", req)
        second = self.broker.handle(self.agent, "agent", req)
        self.assertTrue(first["ok"])
        self.assertEqual(first, second)
        self.assertEqual(len(self.effects), 1)

    def test_remediation_without_approval_is_denied(self):
        self.assertFalse(self.invoke(self.remediation(), "service.restart", {"unit": "demo.service"})["ok"])
        self.assertFalse(self.effects)

    def test_approval_cannot_change_target_task_or_request(self):
        req = self.approved()
        mutations = [{"arguments": {"unit": "other.service"}}, {"request_id": rid()}, {"task_id": self.remediation()}]
        for change in mutations:
            with self.subTest(change=change):
                altered = {**req, **change}
                result = self.broker.handle(self.agent, "agent", altered)
                self.assertFalse(result["ok"])
        self.assertFalse(self.effects)

    def test_approval_must_not_authorize_diagnosis_task(self):
        result = self.call("approval.issue", admin=True, task_id=self.diagnosis(), invocation_id=rid(),
                           operation="service.restart", arguments={"unit": "demo.service"})
        self.assertEqual(result["code"], "approval_outside_task_scope")

    def test_approval_expiry_and_task_expiry(self):
        req = self.approved()
        self.now += 31 * 10**9
        self.assertEqual(self.broker.handle(self.agent, "agent", req)["code"], "invalid_or_consumed_approval")
        task = self.diagnosis()
        self.now += 301 * 10**9
        self.assertEqual(self.invoke(task)["code"], "task_inactive")
        self.assertFalse(self.effects)

    def test_revocation_cascades_to_remediation(self):
        req = self.approved()
        child = self.call("task.get", task_id=req["task_id"])["task"]
        self.call("task.revoke", admin=True, task_id=child["parent_task_id"])
        self.assertEqual(self.broker.handle(self.agent, "agent", req)["code"], "task_inactive")
        self.assertFalse(self.effects)

    def test_policy_or_host_generation_change_invalidates_task(self):
        task = self.diagnosis()
        self.epoch = "boot-a-generation-b"
        self.assertEqual(self.invoke(task)["code"], "task_inactive")
        self.epoch = "boot-a-generation-a"
        self.broker.policy = Policy.load(policy_data(max_calls=31), CATALOG)
        self.assertEqual(self.invoke(task)["code"], "task_inactive")

    def test_monotonic_expiry_does_not_use_wall_clock(self):
        task = self.diagnosis()
        with patch("time.time_ns", return_value=1):
            self.now += 301 * 10**9
            self.assertEqual(self.invoke(task)["code"], "task_inactive")

    def test_budget_consumed_before_executor(self):
        self.broker.policy = Policy.load(policy_data(max_calls=1), CATALOG)
        task = self.diagnosis()
        def effect(op, args):
            self.assertEqual(self.store.db.execute("SELECT calls FROM tasks WHERE id=?", (task,)).fetchone()[0], 1)
            self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM requests WHERE state='started'").fetchone()[0], 1)
            self.assertFalse(self.store.db.in_transaction)
            return {"status": "succeeded"}
        self.broker.executor = effect
        self.assertTrue(self.invoke(task)["ok"])
        self.assertEqual(self.invoke(task)["code"], "task_inactive")

    def test_concurrent_duplicate_executes_only_once(self):
        req = self.approved()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.broker.handle(self.agent, "agent", req), range(16)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.effects), 1)

    def test_request_id_conflict_is_not_new_authority(self):
        task = self.diagnosis()
        req = message("invoke", task_id=task, operation="memory.snapshot", arguments={}, approval_id=None)
        self.broker.handle(self.agent, "agent", req)
        result = self.broker.handle(self.agent, "agent", {**req, "operation": "system.identify"})
        self.assertEqual(result["code"], "request_id_conflict")
        self.assertEqual(len(self.effects), 1)

    def test_durable_replay_after_restart(self):
        req = self.approved()
        result = self.broker.handle(self.agent, "agent", req)
        self.store.close()
        self.store = Store(self.state)
        self.broker = Broker(self.store, self.policy, CATALOG, self.execute, lambda: self.now, lambda: self.epoch)
        self.assertEqual(self.broker.handle(self.agent, "agent", req), result)
        self.assertEqual(len(self.effects), 1)

    def test_crash_after_reservation_never_reexecutes(self):
        req = self.approved()
        def crash(*args):
            raise SystemExit("simulated process death")
        self.broker.executor = crash
        with self.assertRaises(SystemExit):
            self.broker.handle(self.agent, "agent", req)
        self.store.close()
        self.store = Store(self.state)
        self.broker = Broker(self.store, self.policy, CATALOG, self.execute, lambda: self.now, lambda: self.epoch)
        result = self.broker.handle(self.agent, "agent", req)
        self.assertEqual(result["code"], "indeterminate")
        self.assertTrue(result["may_have_executed"])
        self.assertEqual(self.store.db.execute("SELECT used FROM approvals WHERE id=?", (req["approval_id"],)).fetchone()[0], 1)
        self.assertFalse(self.effects)

    def test_real_process_death_after_effect_keeps_consumed_receipt(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            state.mkdir(mode=0o700)
            script = Path(tmp) / "crash.py"
            script.write_text("""
import json, os, sys
from pathlib import Path
from core import Broker, Store, Policy, Peer
state = Path(sys.argv[1])
catalog = json.loads(sys.argv[2])
policy = Policy.load(json.loads(sys.argv[3]), catalog)
store = Store(state)
def execute(*args):
    fd = os.open(state.parent / 'effect', os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(fd, b'executed once')
    os.fsync(fd)
    os._exit(77)
broker = Broker(store, policy, catalog, execute, lambda: 1, lambda: 'epoch')
peer = Peer(os.getpid(), 1000, 1000)
task = broker.handle(peer, 'agent', {'schema':1,'action':'task.create','request_id':'1'*32})['task_id']
req = {'schema':1,'action':'invoke','request_id':'2'*32,'task_id':task,'operation':'memory.snapshot','arguments':{},'approval_id':None}
(state.parent / 'request.json').write_text(json.dumps(req))
broker.handle(peer, 'agent', req)
""")
            child = subprocess.run([sys.executable, str(script), str(state), canonical(CATALOG), canonical(policy_data())],
                                   env={**os.environ, "PYTHONPATH": str(ROOT / "broker")}, timeout=10)
            self.assertEqual(child.returncode, 77)
            recovered = Store(state)
            try:
                broker = Broker(recovered, self.policy, CATALOG, self.execute, lambda: 1, lambda: "epoch")
                req = strict_json((Path(tmp) / "request.json").read_text())
                result = broker.handle(self.agent, "agent", req)
                self.assertEqual(result["code"], "indeterminate")
                self.assertFalse(self.effects)
                self.assertEqual((Path(tmp)/"effect").read_text(), "executed once")
            finally:
                recovered.close()

    def test_denied_operations_never_reach_executor(self):
        task = self.diagnosis()
        for op in [*CATALOG, "shell.exec"]:
            if op not in {"memory.snapshot", "system.identify", "service.status"}:
                with self.subTest(operation=op):
                    self.assertFalse(self.invoke(task, op)["ok"])
        self.assertFalse(self.effects)

    def test_dangerous_grants_are_disabled_even_for_admin(self):
        task = self.remediation()
        for op, definition in CATALOG.items():
            if definition["permission_class"] == "DANGEROUS":
                with self.subTest(operation=op):
                    result = self.call("approval.issue", admin=True, task_id=task, invocation_id=rid(), operation=op, arguments={})
                    self.assertEqual(result["code"], "dangerous_operation_disabled")

    def test_service_injection_extra_arguments_and_unlisted_targets(self):
        task = self.diagnosis()
        for unit in ["-a.service", "demo;touch /tmp/PWN.service", "../demo.service", "a@b.service", "*.service", "demo.service\n", "sshd.service", "unlisted.service"]:
            with self.subTest(unit=unit):
                self.assertFalse(self.invoke(task, "service.status", {"unit": unit})["ok"])
        self.assertFalse(self.invoke(task, "service.status", {"unit": "demo.service", "command": "id"})["ok"])
        self.assertFalse(self.effects)

    def test_default_policy_enrolls_nobody_and_disables_mutation(self):
        default = strict_json((ROOT / "config/broker-policy.json").read_text())
        configured = Policy.load(default, CATALOG)
        self.assertFalse(configured.allowed_uids)
        self.assertFalse(configured.enable_service_restart)
        self.assertFalse(configured.restart_units)

    def test_catalog_downgrade_fails_startup(self):
        changed = {**CATALOG, "service.restart": {"permission_class": "READ", "requires_privilege": False}}
        with self.assertRaises(Denied):
            Policy.load(self.data, changed)

    def test_audit_failure_before_admission_prevents_effect(self):
        req = self.approved()
        with patch.object(self.store, "audit", side_effect=sqlite3.OperationalError("full")):
            response = self.broker.handle(self.agent, "agent", req)
        self.assertEqual(response["code"], "broker_unavailable")
        self.assertFalse(self.effects)
        self.assertTrue(self.broker.poisoned)
        self.assertEqual(self.store.db.execute("SELECT used FROM approvals WHERE id=?", (req["approval_id"],)).fetchone()[0], 0)

    def test_completion_failure_is_indeterminate_not_reexecuted(self):
        req = self.approved()
        original = self.store.audit
        def audit(record):
            if record.get("decision") == "completed":
                raise sqlite3.OperationalError("disk failure")
            return original(record)
        with patch.object(self.store, "audit", side_effect=audit):
            response = self.broker.handle(self.agent, "agent", req)
        self.assertEqual(response["code"], "broker_unavailable")
        self.assertTrue(response["may_have_executed"])
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM requests WHERE state='started'").fetchone()[0], 1)

    def test_audit_is_append_only_and_no_denied_secret_arguments(self):
        task = self.diagnosis()
        self.invoke(task, "shell.exec", {"secret": "do-not-store-me"})
        rows = self.store.db.execute("SELECT record FROM audit").fetchall()
        self.assertNotIn("do-not-store-me", str([row[0] for row in rows]))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("DELETE FROM audit")
        self.assertFalse(self.call("audit.list", after_seq=0)["ok"])
        self.assertTrue(self.call("audit.list", admin=True, after_seq=0)["ok"])

    def test_second_broker_cannot_open_same_state(self):
        with self.assertRaises(BlockingIOError):
            Store(self.state)

    def test_state_directory_permissions_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o755)
            with self.assertRaises(Denied):
                Store(Path(tmp))

    def test_state_symlink_hardlink_and_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            path = Path(tmp)
            target = Path(other) / "target"
            target.write_text("not a database")
            target.chmod(0o600)
            db = path / "broker.sqlite3"
            db.symlink_to(target)
            with self.assertRaises(OSError):
                Store(path)
            db.unlink()
            os.link(target, db)
            with self.assertRaises(Denied):
                Store(path)
            db.unlink()
            db.write_text("corrupt bytes")
            db.chmod(0o600)
            with self.assertRaises(sqlite3.DatabaseError):
                Store(path)

    def test_missing_or_truncated_initialized_state_is_not_recreated(self):
        for truncate in (False, True):
            with self.subTest(truncate=truncate), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)
                store = Store(path)
                store.close()
                db = path / "broker.sqlite3"
                if truncate:
                    db.write_bytes(b"")
                else:
                    db.unlink()
                with self.assertRaises(Denied):
                    Store(path)


class WireTests(unittest.TestCase):
    def test_peer_credentials_are_kernel_values(self):
        left, right = socket.socketpair()
        with left, right:
            peer = peer_credentials(left)
            self.assertEqual((peer.pid, peer.uid, peer.gid), (os.getpid(), os.getuid(), os.getgid()))

    def test_duplicate_keys_nonfinite_and_malformed_json(self):
        for payload in [b'{"uid":0,"uid":1000}', b'{"number":NaN}', b'\xff', b'[[[']:
            with self.subTest(payload=payload), self.assertRaises(Denied):
                strict_json(payload)

    def test_oversized_truncated_and_timed_out_frames(self):
        for data in [struct.pack("!I", 16385), struct.pack("!I", 4)+b"a"]:
            with self.subTest(data=data):
                left, right = socket.socketpair()
                with left, right:
                    right.sendall(data)
                    right.shutdown(socket.SHUT_WR)
                    with self.assertRaises(Denied):
                        read_frame(left)
        left, right = socket.socketpair()
        with left, right:
            with self.assertRaises(Denied):
                read_frame(left, timeout=0.02)

    def test_socket_server_uses_peer_not_payload_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            run = root / "run"
            state.mkdir(mode=0o700)
            run.mkdir(mode=0o750)
            store = Store(state)
            policy = Policy.load(policy_data(allowed_uids=[os.getuid()] if os.getuid() else [1000]), CATALOG)
            # Do not manufacture root enrollment: a root client must be rejected.
            broker = Broker(store, policy, CATALOG, lambda *_: {"status": "succeeded"})
            server = Server(broker, run)
            stop = threading.Event()
            thread = threading.Thread(target=server.serve, args=(stop,))
            thread.start()
            try:
                if os.getuid() == 0:
                    with self.assertRaises((Denied, OSError)):
                        request(str(run/"agent.sock"), message("task.create", owner_uid=1000))
                else:
                    result = request(str(run/"agent.sock"), message("task.create"))
                    self.assertEqual(result["task"]["owner_uid"], os.getuid())
                    result = request(str(run/"agent.sock"), message("task.create", owner_uid=0))
                    self.assertEqual(result["code"], "invalid_fields")
                self.assertEqual((run/"agent.sock").stat().st_mode & 0o777, 0o660)
                self.assertEqual((run/"admin.sock").stat().st_mode & 0o777, 0o600)
            finally:
                stop.set()
                thread.join()
                time.sleep(0.05)
                server.close()
                store.close()

    def test_socket_path_refuses_non_socket_or_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root)
            broker = Broker(store, Policy.load(policy_data(), CATALOG), CATALOG, lambda *_: None)
            path = root/"agent.sock"
            path.write_text("do not delete")
            with self.assertRaises(Denied):
                Server(broker, root)
            self.assertEqual(path.read_text(), "do not delete")
            path.unlink()
            path.symlink_to("target")
            with self.assertRaises(Denied):
                Server(broker, root)
            store.close()


class AdapterTests(unittest.TestCase):
    def test_real_read_adapters_have_minimal_output(self):
        adapter = LocalAdapter("/not-executed/systemctl")
        self.assertEqual(adapter("memory.snapshot", {})["status"], "succeeded")
        import io
        with patch("builtins.open", return_value=io.StringIO('ID=nixos\nVERSION_ID="26.05"\nPRETTY_NAME="NixOS"\nSECRET=redact-me\n')):
            identity = adapter("system.identify", {})
        self.assertEqual(identity["status"], "succeeded")
        self.assertTrue(set(identity["data"]) <= {"ID", "VERSION_ID", "PRETTY_NAME"})

    def test_restart_argv_is_not_shell(self):
        adapter = LocalAdapter("/nix/store/fake-systemd/bin/systemctl")
        with patch("adapters.run_bounded", side_effect=[(0, b"Id=demo.service\nLoadState=loaded\n"), (0, b"")]) as run:
            result = adapter("service.restart", {"unit": "demo.service"})
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(run.call_args.args[0], ["/nix/store/fake-systemd/bin/systemctl", "--system", "--no-pager", "--no-ask-password", "restart", "--", "demo.service"])

    def test_timeout_is_indeterminate(self):
        with patch("adapters.run_bounded", return_value=(None, b"")):
            self.assertEqual(LocalAdapter("/systemctl")("service.restart", {"unit": "demo.service"})["status"], "indeterminate")

    def test_bounded_runner_kills_timeout_and_caps_output(self):
        import sys
        status, data = run_bounded([sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.05)
        self.assertIsNone(status)
        status, data = run_bounded([sys.executable, "-c", "print('x'*1000000)"], timeout=2)
        self.assertIsNone(status)
        self.assertEqual(data, b"")

    def test_restart_rejects_alias_before_mutation(self):
        with patch("adapters.run_bounded", return_value=(0, b"Id=sshd.service\nLoadState=loaded\n")) as run:
            result = LocalAdapter("/systemctl")("service.restart", {"unit": "demo.service"})
            self.assertEqual(result["code"], "unit_not_loaded_or_alias")
            self.assertEqual(run.call_count, 1)
