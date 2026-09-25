"""SUBSTRATE broker: task authority, one-use approvals, and durable admission.

No model, caller-supplied envelope, skill, or approval flag is authoritative.
This module has no host-mutating code; all execution uses the narrow adapter.
"""
from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import threading
import time
from typing import Any, Callable

SCHEMA = 1
ID = re.compile(r"[0-9a-f]{32}\Z")
UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.service\Z")
READ_IMPLEMENTED = frozenset({"system.identify", "memory.snapshot", "service.status"})
RESERVED_PREFIXES = ("substrate", "omarchy", "systemd", "dbus", "polkit", "ssh", "nix-daemon")
MAX_RECORDS = 10000
MAX_AUDIT = 50000


class Denied(Exception):
    """Fixed, non-sensitive error code safe to return to an untrusted caller."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_json(raw: str | bytes) -> Any:
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise Denied("duplicate_json_key")
            obj[key] = value
        return obj

    def constant(_):
        raise Denied("nonfinite_json_number")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Denied("invalid_json") from exc


def fields(obj: Any, required: set[str]) -> None:
    if type(obj) is not dict or set(obj) != required:
        raise Denied("invalid_fields")


def identifier(value: Any) -> str:
    if type(value) is not str or not ID.fullmatch(value):
        raise Denied("invalid_identifier")
    return value


def safe_unit(value: Any) -> str:
    if type(value) is not str or not UNIT.fullmatch(value):
        raise Denied("invalid_service_unit")
    if value.lower().startswith(RESERVED_PREFIXES):
        raise Denied("protected_service_unit")
    return value


@dataclasses.dataclass(frozen=True)
class Peer:
    pid: int
    uid: int
    gid: int


@dataclasses.dataclass(frozen=True)
class Policy:
    allowed_uids: frozenset[int]
    read_operations: frozenset[str]
    service_units: frozenset[str]
    restart_units: frozenset[str]
    enable_service_restart: bool
    task_ttl_seconds: int
    approval_ttl_seconds: int
    max_calls: int
    fingerprint: str

    @classmethod
    def load(cls, obj: dict, catalog: dict) -> Policy:
        fields(obj, {"schema", "allowed_uids", "read_operations", "service_units",
                     "restart_units", "enable_service_restart", "task_ttl_seconds",
                     "approval_ttl_seconds", "max_calls"})
        if type(obj["schema"]) is not int or obj["schema"] != SCHEMA:
            raise Denied("unsupported_policy_schema")
        for key in ("allowed_uids", "read_operations", "service_units", "restart_units"):
            if type(obj[key]) is not list or len(obj[key]) > 128:
                raise Denied("invalid_policy_list")
        uids = obj["allowed_uids"]
        if any(type(uid) is not int or not 0 < uid < 2**32 - 1 for uid in uids):
            raise Denied("workers_must_have_nonroot_uid")
        if any(type(op) is not str or op not in READ_IMPLEMENTED for op in obj["read_operations"]):
            raise Denied("unsupported_read_operation")
        units = frozenset(safe_unit(unit) for unit in obj["service_units"])
        restarts = frozenset(safe_unit(unit) for unit in obj["restart_units"])
        if not restarts <= units or type(obj["enable_service_restart"]) is not bool:
            raise Denied("invalid_restart_policy")
        for key, maximum in (("task_ttl_seconds", 3600), ("approval_ttl_seconds", 120), ("max_calls", 128)):
            if type(obj[key]) is not int or not 1 <= obj[key] <= maximum:
                raise Denied("invalid_policy_bound")
        # A malformed or downgraded catalog must not weaken the executable boundary.
        for op in READ_IMPLEMENTED:
            if catalog.get(op) != {"permission_class": "READ", "requires_privilege": False}:
                raise Denied("invalid_read_catalog")
        if catalog.get("service.restart") != {"permission_class": "CHANGE", "requires_privilege": True}:
            raise Denied("invalid_restart_catalog")
        for entry in catalog.values():
            fields(entry, {"permission_class", "requires_privilege"})
            if entry["permission_class"] not in {"READ", "CHANGE", "DANGEROUS"} or type(entry["requires_privilege"]) is not bool:
                raise Denied("invalid_catalog")
        return cls(frozenset(uids), frozenset(obj["read_operations"]), units, restarts,
                   obj["enable_service_restart"], obj["task_ttl_seconds"],
                   obj["approval_ttl_seconds"], obj["max_calls"], digest([obj, catalog]))


def host_epoch() -> str:
    """Monotonic deadlines survive daemon restarts, but never a host reboot."""
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    generation = os.path.realpath("/run/current-system") if os.path.exists("/run/current-system") else "non-nixos"
    return digest([boot, generation])


def private_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Denied("unsafe_state_directory")


class Store:
    """One local broker instance; FULL-synchronous SQLite transactions.

    The state directory must be provisioned privately by the administrator.
    Never put it inside a data snapshot that an agent is authorized to restore.
    """
    def __init__(self, directory: Path):
        private_directory(directory)
        self.lock_fd = os.open(directory / "broker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            self._check_fd(self.lock_fd)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            db_path = directory / "broker.sqlite3"
            for suffix in ("", "-wal", "-shm"):
                path = Path(str(db_path) + suffix)
                if os.path.lexists(path):
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        self._check_fd(fd)
                    finally:
                        os.close(fd)
            marker = directory / "initialized"
            if marker.exists() and (not db_path.exists() or db_path.stat().st_size == 0):
                raise Denied("state_missing_after_initialization")
            fd = os.open(db_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                self._check_fd(fd)
            finally:
                os.close(fd)
            self.db = sqlite3.connect(db_path, timeout=5, isolation_level=None, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise Denied("state_integrity_failure")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA) or (marker.exists() and version != SCHEMA):
                raise Denied("unsupported_state_schema")
            self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, envelope TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
                    revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    binding TEXT NOT NULL, deadline INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS requests (
                    key TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL,
                    response TEXT, task_id TEXT, operation TEXT);
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, record TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
                    BEGIN SELECT RAISE(ABORT, 'audit is append only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
                    BEGIN SELECT RAISE(ABORT, 'audit is append only'); END;
                PRAGMA user_version=1;
                COMMIT;
            """)
            with self.transaction():
                pending = self.db.execute("SELECT key,task_id,operation FROM requests WHERE state='started'").fetchall()
                for row in pending:
                    response = {"ok": False, "code": "indeterminate", "may_have_executed": True}
                    self.db.execute("UPDATE requests SET state='indeterminate',response=? WHERE key=?", (canonical(response), row["key"]))
                    self.audit({"stage": "recovery", "request_key": row["key"], "task_id": row["task_id"],
                                "operation": row["operation"], "decision": "indeterminate"})
            if not marker.exists():
                fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.write(fd, b"1\n")
                os.fsync(fd)
                os.close(fd)
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            os.fsync(fd)
            os.close(fd)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            os.close(self.lock_fd)
            raise

    @staticmethod
    def _check_fd(fd):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise Denied("unsafe_state_file")

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def audit(self, record: dict) -> int:
        if self.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] >= MAX_AUDIT:
            raise sqlite3.OperationalError("audit capacity reached")
        record = {"schema": SCHEMA, "time_ns": time.time_ns(), **record}
        cursor = self.db.execute("INSERT INTO audit(record) VALUES (?)", (canonical(record),))
        return cursor.lastrowid

    def close(self):
        self.db.close()
        os.close(self.lock_fd)


class Broker:
    def __init__(self, store: Store, policy: Policy, catalog: dict, executor: Callable,
                 clock: Callable = time.monotonic_ns, epoch: Callable = host_epoch):
        self.store, self.policy, self.catalog, self.executor = store, policy, catalog, executor
        self.clock, self.epoch = clock, epoch
        self.lock = threading.RLock()
        self.poisoned = False

    def protocol_denial(self, peer: Peer, channel: str, code: str) -> None:
        with self.lock:
            try:
                with self.store.transaction():
                    self.store.audit({"stage": "transport", "uid": peer.uid, "pid": peer.pid,
                                      "channel": channel, "decision": "denied", "code": code})
            except (sqlite3.Error, OSError):
                self.poisoned = True

    def handle(self, peer: Peer, channel: str, message: Any) -> dict:
        with self.lock:
            if self.poisoned:
                return {"ok": False, "code": "broker_unavailable", "may_have_executed": True}
            try:
                if type(message) is not dict:
                    raise Denied("invalid_message")
                if type(message.get("schema")) is not int or message["schema"] != SCHEMA:
                    raise Denied("unsupported_schema")
                rid = identifier(message.get("request_id"))
                if channel not in {"agent", "admin"}:
                    raise Denied("invalid_channel")
                if channel == "admin" and peer.uid != 0:
                    raise Denied("administrator_required")
                if channel == "agent" and peer.uid not in self.policy.allowed_uids:
                    raise Denied("worker_not_enrolled")
            except Denied as exc:
                self.protocol_denial(peer, channel, str(exc))
                return {"ok": False, "code": str(exc)}
            key = f"{channel}:{peer.uid}:{rid}"
            hashed = digest(message)
            db = self.store.db
            effect = None
            try:
                with self.store.transaction():
                    old = db.execute("SELECT * FROM requests WHERE key=?", (key,)).fetchone()
                    if old:
                        if old["digest"] != hashed:
                            seq = self._audit(peer, channel, rid, "denied", "request_id_conflict")
                            return {"ok": False, "code": "request_id_conflict", "audit_seq": seq}
                        self._audit(peer, channel, rid, "replayed", "cached_receipt")
                        return strict_json(old["response"]) if old["response"] else {"ok": False, "code": "indeterminate", "may_have_executed": True}
                    if db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] >= MAX_RECORDS:
                        raise sqlite3.OperationalError("request capacity reached")
                    db.execute("INSERT INTO requests(key,digest,state) VALUES (?,?,'received')", (key, hashed))
                    try:
                        response, effect = self._dispatch(peer, channel, message)
                    except Denied as exc:
                        response = {"ok": False, "code": str(exc)}
                    task_id = response.get("task_id")
                    supplied_op = message.get("operation")
                    operation = supplied_op if type(supplied_op) is str and supplied_op in self.catalog else None
                    safe_task = message.get("task_id")
                    if task_id is None and type(safe_task) is str and ID.fullmatch(safe_task):
                        task_id = safe_task
                    seq = self._audit(peer, channel, rid, "admitted" if effect else ("allowed" if response["ok"] else "denied"),
                                      response.get("code", "ok"), task_id, operation,
                                      digest(message.get("arguments")) if operation else None)
                    response["audit_seq"] = seq
                    db.execute("UPDATE requests SET state=?,response=?,task_id=?,operation=? WHERE key=?",
                               ("started" if effect else "done", None if effect else canonical(response), task_id, operation, key))
                if effect is None:
                    return response
                # Admission, audit, budget decrement and approval consumption have all
                # durably committed. Never retry this effect after a process crash.
                try:
                    result = self.executor(*effect)
                    if type(result) is not dict or result.get("status") not in {"succeeded", "failed", "indeterminate"}:
                        raise ValueError("bad adapter result")
                    if len(canonical(result).encode()) > 32768:
                        raise ValueError("oversized adapter result")
                except Exception:
                    result = {"status": "indeterminate"}
                response = {"ok": result["status"] == "succeeded", "code": result["status"],
                            "task_id": task_id, "request_id": rid, "result": result,
                            "may_have_executed": True}
                with self.store.transaction():
                    seq = self._audit(peer, channel, rid, "completed", result["status"], task_id, operation)
                    response["audit_seq"] = seq
                    db.execute("UPDATE requests SET state=?,response=? WHERE key=?",
                               (result["status"], canonical(response), key))
                return response
            except (sqlite3.Error, OSError):
                self.poisoned = True
                return {"ok": False, "code": "broker_unavailable", "may_have_executed": effect is not None}

    def _audit(self, peer, channel, rid, decision, code, task_id=None, operation=None, arguments_hash=None):
        return self.store.audit({"stage": "request", "uid": peer.uid, "pid": peer.pid, "channel": channel,
                                 "request_id": rid, "task_id": task_id, "operation": operation,
                                 "permission_class": self.catalog[operation]["permission_class"] if operation else None,
                                 "arguments_hash": arguments_hash, "decision": decision, "code": code})

    def _task(self, task_id: Any, owner: int | None = None, active=True) -> dict:
        row = self.store.db.execute("SELECT * FROM tasks WHERE id=?", (identifier(task_id),)).fetchone()
        if not row:
            raise Denied("task_unavailable")
        task = strict_json(row["envelope"])
        if owner is not None and task["owner_uid"] != owner:
            raise Denied("task_unavailable")
        if active and (row["revoked"] or self.clock() >= task["deadline_ns"] or task["host_epoch"] != self.epoch()
                       or task["policy_digest"] != self.policy.fingerprint or row["calls"] >= task["max_calls"]):
            raise Denied("task_inactive")
        return {**task, "calls": row["calls"], "revoked": bool(row["revoked"])}

    def _create(self, owner, mode, operations, parent=None):
        db = self.store.db
        if db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] >= 256:
            raise Denied("task_capacity_reached")
        now = self.clock()
        deadline = now + self.policy.task_ttl_seconds * 10**9
        if parent:
            deadline = min(deadline, parent["deadline_ns"])
        task = {"schema": SCHEMA, "task_id": secrets.token_hex(16), "owner_uid": owner, "mode": mode,
                "operations": sorted(operations), "parent_task_id": parent["task_id"] if parent else None,
                "issued_ns": now, "deadline_ns": deadline, "host_epoch": self.epoch(),
                "policy_digest": self.policy.fingerprint, "max_calls": self.policy.max_calls}
        db.execute("INSERT INTO tasks(id,envelope) VALUES (?,?)", (task["task_id"], canonical(task)))
        return {"ok": True, "task_id": task["task_id"], "task": task}

    def _operation(self, operation, arguments):
        if type(operation) is not str or operation not in self.catalog:
            raise Denied("unknown_operation")
        permission = self.catalog[operation]["permission_class"]
        if permission == "DANGEROUS":
            raise Denied("dangerous_operation_disabled")
        if operation in {"system.identify", "memory.snapshot"}:
            fields(arguments, set())
        elif operation in {"service.status", "service.restart"}:
            fields(arguments, {"unit"})
            unit = safe_unit(arguments["unit"])
            if unit not in self.policy.service_units:
                raise Denied("service_not_allowlisted")
            if operation == "service.restart" and (not self.policy.enable_service_restart or unit not in self.policy.restart_units):
                raise Denied("service_restart_disabled")
        else:
            raise Denied("operation_not_implemented")
        return permission

    def _binding(self, task, invocation_id, operation, arguments):
        return digest({"task_id": task["task_id"], "owner_uid": task["owner_uid"],
                       "request_id": invocation_id, "operation": operation, "arguments": arguments,
                       "host_epoch": task["host_epoch"], "policy_digest": task["policy_digest"]})

    def _dispatch(self, peer, channel, msg):
        common = {"schema", "action", "request_id"}
        action = msg.get("action")
        db = self.store.db
        if action == "task.create" and channel == "agent":
            fields(msg, common)
            return self._create(peer.uid, "diagnosis", self.policy.read_operations), None
        if action == "task.get":
            fields(msg, common | {"task_id"})
            task = self._task(msg["task_id"], peer.uid if channel == "agent" else None, active=False)
            return {"ok": True, "task_id": task["task_id"], "task": task}, None
        if action == "task.remediate" and channel == "admin":
            fields(msg, common | {"parent_task_id"})
            parent = self._task(msg["parent_task_id"])
            if parent["mode"] != "diagnosis" or not self.policy.enable_service_restart or not self.policy.restart_units:
                raise Denied("remediation_not_available")
            return self._create(parent["owner_uid"], "remediation", {"service.restart"}, parent), None
        if action == "task.revoke" and channel == "admin":
            fields(msg, common | {"task_id"})
            task = self._task(msg["task_id"], active=False)
            # Children cannot outlive revocation of the diagnosis they derive from.
            ids = {task["task_id"]}
            rows = db.execute("SELECT id,envelope FROM tasks").fetchall()
            while True:
                children = {row["id"] for row in rows if strict_json(row["envelope"])["parent_task_id"] in ids}
                if children <= ids:
                    break
                ids |= children
            for tid in ids:
                db.execute("UPDATE tasks SET revoked=1 WHERE id=?", (tid,))
            return {"ok": True, "task_id": task["task_id"]}, None
        if action == "approval.issue" and channel == "admin":
            fields(msg, common | {"task_id", "invocation_id", "operation", "arguments"})
            task = self._task(msg["task_id"])
            invocation_id = identifier(msg["invocation_id"])
            permission = self._operation(msg["operation"], msg["arguments"])
            if task["mode"] != "remediation" or msg["operation"] not in task["operations"] or permission != "CHANGE":
                raise Denied("approval_outside_task_scope")
            aid = secrets.token_hex(16)
            deadline = min(task["deadline_ns"], self.clock() + self.policy.approval_ttl_seconds * 10**9)
            db.execute("INSERT INTO approvals(id,task_id,binding,deadline) VALUES (?,?,?,?)",
                       (aid, task["task_id"], self._binding(task, invocation_id, msg["operation"], msg["arguments"]), deadline))
            self.store.audit({"stage": "approval", "decision": "issued", "approved_by_uid": peer.uid,
                              "pid": peer.pid, "owner_uid": task["owner_uid"], "task_id": task["task_id"], "approval_id": aid,
                              "invocation_id": invocation_id, "operation": msg["operation"],
                              "permission_class": permission, "arguments_hash": digest(msg["arguments"]),
                              "binding": self._binding(task, invocation_id, msg["operation"], msg["arguments"]),
                              "deadline_ns": deadline})
            return {"ok": True, "task_id": task["task_id"], "approval_id": aid, "approved_by_uid": peer.uid,
                    "invocation_id": invocation_id, "deadline_ns": deadline}, None
        if action == "audit.list" and channel == "admin":
            fields(msg, common | {"after_seq"})
            after = msg["after_seq"]
            if type(after) is not int or not 0 <= after <= 2**63 - 1:
                raise Denied("invalid_audit_cursor")
            rows = db.execute("SELECT * FROM audit WHERE seq>? ORDER BY seq LIMIT 100", (after,)).fetchall()
            return {"ok": True, "records": [{"seq": row["seq"], **strict_json(row["record"])} for row in rows]}, None
        if action == "invoke" and channel == "agent":
            fields(msg, common | {"task_id", "operation", "arguments", "approval_id"})
            task = self._task(msg["task_id"], peer.uid)
            permission = self._operation(msg["operation"], msg["arguments"])
            if msg["operation"] not in task["operations"] or (task["mode"] == "diagnosis" and permission != "READ"):
                raise Denied("outside_task_scope")
            if permission == "READ":
                if msg["operation"] not in self.policy.read_operations or msg["approval_id"] is not None:
                    raise Denied("invalid_read_authority")
            else:
                aid = identifier(msg["approval_id"])
                approval = db.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
                bound = self._binding(task, msg["request_id"], msg["operation"], msg["arguments"])
                if not approval or approval["used"] or approval["deadline"] <= self.clock() or approval["binding"] != bound:
                    raise Denied("invalid_or_consumed_approval")
                db.execute("UPDATE approvals SET used=1 WHERE id=?", (aid,))
            db.execute("UPDATE tasks SET calls=calls+1 WHERE id=?", (task["task_id"],))
            return {"ok": True, "task_id": task["task_id"]}, (msg["operation"], msg["arguments"])
        raise Denied("action_not_permitted")
