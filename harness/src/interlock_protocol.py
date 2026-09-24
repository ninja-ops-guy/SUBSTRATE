#!/usr/bin/env python3
"""SUBSTRATE cooperative quiescence/interlock protocol.

The orchestrator asks workers to yield with SIGUSR2.  Workers release registered
exclusive resources, unregister those declarations, then acknowledge by
SIGSTOP.  The orchestrator verifies both registry declarations and kernel file
locks before allowing cgroup freeze.  Timeout handling is fail-closed.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple


class LockType(Enum):
    FCNTL = "fcntl"
    FLOCK = "flock"
    MUTEX = "mutex"
    FILE = "file"
    SOCKET = "socket"
    OTHER = "other"


@dataclass
class LockRecord:
    lock_type: LockType
    resource: str
    pid: int
    held_since: float
    sharable: bool = False


class LockRegistry:
    """File-backed per-agent declaration registry visible across processes."""

    def __init__(self, registry_path: str = "/run/substrate/interlock-registry"):
        self.path = Path(registry_path)
        self.path.mkdir(parents=True, exist_ok=True)

    def _agent_file(self, agent_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in agent_id)
        return self.path / f"{safe}.json"

    @staticmethod
    def _decode(item: Dict) -> LockRecord:
        return LockRecord(
            lock_type=LockType(item["lock_type"]),
            resource=item["resource"],
            pid=int(item["pid"]),
            held_since=float(item["held_since"]),
            sharable=bool(item.get("sharable", False)),
        )

    def _read(self, agent_id: str) -> List[LockRecord]:
        path = self._agent_file(agent_id)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text())
            return [self._decode(x) for x in raw.get("locks", [])]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def _write(self, agent_id: str, records: Iterable[LockRecord]) -> None:
        path = self._agent_file(agent_id)
        encoded = []
        for record in records:
            item = asdict(record)
            item["lock_type"] = record.lock_type.value
            encoded.append(item)
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps({"agent_id": agent_id, "locks": encoded}, indent=2))
        os.replace(tmp, path)

    def register_lock(self, agent_id: str, record: LockRecord) -> None:
        records = [r for r in self._read(agent_id) if not (r.resource == record.resource and r.pid == record.pid)]
        records.append(record)
        self._write(agent_id, records)

    def release_lock(self, agent_id: str, resource: str, pid: Optional[int] = None) -> None:
        records = [r for r in self._read(agent_id)
                   if not (r.resource == resource and (pid is None or r.pid == pid))]
        self._write(agent_id, records)

    def get_locks(self, agent_id: str) -> List[LockRecord]:
        return self._read(agent_id)

    def get_locks_for_pid(self, pid: int) -> List[LockRecord]:
        result: List[LockRecord] = []
        for path in self.path.glob("*.json"):
            try:
                raw = json.loads(path.read_text())
                result.extend(self._decode(x) for x in raw.get("locks", []) if int(x.get("pid", -1)) == pid)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return result


class InterlockState(Enum):
    IDLE = "idle"
    QUIESCING = "quiescing"
    AGENT_YIELDED = "agent_yielded"
    FORCED_BREAK = "forced_break"
    FROZEN = "frozen"
    TIMEOUT = "timeout"
    ERROR = "error"


class InterlockProtocol:
    DEFAULT_PROHIBITED_PREFIXES = (
        "/var/log/",
        "/etc/omarchy-srv/",
        "/run/omarchy-srv/",
        "/sys/fs/cgroup/",
    )

    def __init__(self, cgroup_path: str, *, timeout_ms: int = 5000,
                 registry_path: str = "/run/substrate/interlock-registry",
                 proc_root: str = "/proc", proc_locks_path: str = "/proc/locks",
                 prohibited_prefixes: Optional[Iterable[str]] = None):
        self.cgroup_path = Path(cgroup_path)
        self.timeout_ms = timeout_ms
        self.registry = LockRegistry(registry_path)
        self.proc_root = Path(proc_root)
        self.proc_locks_path = Path(proc_locks_path)
        self.prohibited_prefixes = tuple(prohibited_prefixes or self.DEFAULT_PROHIBITED_PREFIXES)
        self.state = InterlockState.IDLE
        self.agent_pids: List[int] = []
        self.yielded_pids: Set[int] = set()
        self.stats = {"requests": 0, "yields": 0, "forced_breaks": 0, "timeouts": 0}

    def _load_agent_pids(self) -> bool:
        procs = self.cgroup_path / "cgroup.procs"
        if not procs.exists():
            return False
        pids: List[int] = []
        for line in procs.read_text().splitlines():
            try:
                pid = int(line.strip())
            except ValueError:
                continue
            if pid > 0 and (self.proc_root / str(pid)).exists():
                pids.append(pid)
        self.agent_pids = sorted(set(pids))
        return True

    @staticmethod
    def _parse_lock_identity(line: str) -> Optional[Tuple[int, int, int, int]]:
        """Return (pid, major, minor, inode) from a Linux /proc/locks row."""
        parts = line.split()
        if len(parts) < 6:
            return None
        try:
            pid = int(parts[4])
            dev_inode = parts[5]
            dev, inode_text = dev_inode.rsplit(":", 1)
            major_text, minor_text = dev.split(":", 1)
            return pid, int(major_text, 16), int(minor_text, 16), int(inode_text)
        except (ValueError, IndexError):
            return None

    def _resolve_identity(self, pid: int, major: int, minor: int, inode: int) -> List[str]:
        fd_dir = self.proc_root / str(pid) / "fd"
        paths: List[str] = []
        try:
            entries = list(fd_dir.iterdir())
        except OSError:
            return paths
        for fd in entries:
            try:
                target = fd.resolve(strict=True)
                st = target.stat()
                if os.major(st.st_dev) == major and os.minor(st.st_dev) == minor and st.st_ino == inode:
                    paths.append(str(target))
            except OSError:
                continue
        return paths

    def _get_pid_locks(self, pid: int) -> List[str]:
        try:
            lines = self.proc_locks_path.read_text().splitlines()
        except OSError:
            return []
        paths: List[str] = []
        for line in lines:
            parsed = self._parse_lock_identity(line)
            if not parsed or parsed[0] != pid:
                continue
            paths.extend(self._resolve_identity(*parsed))
        return sorted(set(paths))

    def _status_code(self, pid: int) -> Optional[str]:
        try:
            for line in (self.proc_root / str(pid) / "status").read_text().splitlines():
                if line.startswith("State:"):
                    fields = line.split()
                    return fields[1] if len(fields) > 1 else None
        except OSError:
            return None
        return None

    def _check_acknowledgments(self) -> None:
        for pid in self.agent_pids:
            if self._status_code(pid) in {"T", "t"}:
                self.yielded_pids.add(pid)

    def _declared_exclusive_locks(self, pid: int) -> List[LockRecord]:
        return [r for r in self.registry.get_locks_for_pid(pid) if not r.sharable]

    def _prohibited_kernel_locks(self, pid: int) -> List[str]:
        return [path for path in self._get_pid_locks(pid)
                if any(path.startswith(prefix) for prefix in self.prohibited_prefixes)]

    def _verify_quiescent(self) -> bool:
        for pid in self.agent_pids:
            if self._declared_exclusive_locks(pid):
                return False
            if self._prohibited_kernel_locks(pid):
                return False
        return True

    def _resume_survivors(self) -> None:
        for pid in self.agent_pids:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass

    def _forced_break(self) -> bool:
        """Stop all workers, inspect locks, and fail closed on ambiguous ownership.

        A worker holding a prohibited kernel lock is terminated so the kernel can
        release it.  A lingering *declared* exclusive lock is considered
        ambiguous application state and therefore blocks the freeze instead of
        being hidden by killing the process.
        """
        self.stats["forced_breaks"] += 1
        self.state = InterlockState.FORCED_BREAK
        for pid in self.agent_pids:
            try:
                os.kill(pid, signal.SIGSTOP)
            except ProcessLookupError:
                pass
        time.sleep(0.05)

        if any(self._declared_exclusive_locks(pid) for pid in self.agent_pids):
            self._resume_survivors()
            self.state = InterlockState.TIMEOUT
            self.stats["timeouts"] += 1
            return False

        for pid in list(self.agent_pids):
            if self._prohibited_kernel_locks(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        time.sleep(0.05)
        if not self._verify_quiescent():
            self._resume_survivors()
            self.state = InterlockState.TIMEOUT
            self.stats["timeouts"] += 1
            return False
        self.yielded_pids = set(self.agent_pids)
        self.state = InterlockState.AGENT_YIELDED
        return True

    def request_quiescence(self, timeout_ms: Optional[int] = None) -> bool:
        self.stats["requests"] += 1
        self.state = InterlockState.QUIESCING
        self.yielded_pids.clear()
        if not self._load_agent_pids():
            self.state = InterlockState.TIMEOUT
            self.stats["timeouts"] += 1
            return False
        for pid in self.agent_pids:
            try:
                os.kill(pid, signal.SIGUSR2)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + (timeout_ms if timeout_ms is not None else self.timeout_ms) / 1000.0
        while time.monotonic() < deadline:
            self._check_acknowledgments()
            live = [pid for pid in self.agent_pids if (self.proc_root / str(pid)).exists()]
            if all(pid in self.yielded_pids for pid in live) and self._verify_quiescent():
                self.stats["yields"] += len(self.yielded_pids)
                self.state = InterlockState.AGENT_YIELDED
                return True
            time.sleep(0.02)
        return self._forced_break()

    def freeze_cgroup(self) -> bool:
        if self.state is not InterlockState.AGENT_YIELDED or not self._verify_quiescent():
            return False
        freeze = self.cgroup_path / "cgroup.freeze"
        if not freeze.exists():
            self.state = InterlockState.ERROR
            return False
        try:
            freeze.write_text("1")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if freeze.read_text().strip() == "1":
                    self.state = InterlockState.FROZEN
                    return True
                time.sleep(0.02)
        except OSError:
            pass
        self.state = InterlockState.ERROR
        return False

    def thaw_cgroup(self) -> bool:
        freeze = self.cgroup_path / "cgroup.freeze"
        try:
            if freeze.exists():
                freeze.write_text("0")
            self._resume_survivors()
            self.yielded_pids.clear()
            self.state = InterlockState.IDLE
            return True
        except OSError:
            self.state = InterlockState.ERROR
            return False


class AgentInterlockHandler:
    """Worker-side helper.  Yield callbacks must release *and unregister* locks."""

    def __init__(self, agent_id: str, registry_path: str = "/run/substrate/interlock-registry"):
        self.agent_id = agent_id
        self.registry = LockRegistry(registry_path)
        self.callbacks: List[Callable[[], None]] = []
        signal.signal(signal.SIGUSR2, self._handle_yield)

    def declare_lock(self, resource: str, lock_type: LockType = LockType.OTHER, *, sharable: bool = False) -> None:
        self.registry.register_lock(self.agent_id, LockRecord(
            lock_type=lock_type, resource=str(Path(resource)), pid=os.getpid(), held_since=time.time(), sharable=sharable
        ))

    def release_lock(self, resource: str) -> None:
        self.registry.release_lock(self.agent_id, str(Path(resource)), os.getpid())

    def register_yield_callback(self, callback: Callable[[], None]) -> None:
        self.callbacks.append(callback)

    def _handle_yield(self, _signum, _frame) -> None:
        try:
            for callback in self.callbacks:
                callback()
            if any(not record.sharable for record in self.registry.get_locks_for_pid(os.getpid())):
                return
            os.kill(os.getpid(), signal.SIGSTOP)
        except Exception:
            return


def main() -> int:
    p = argparse.ArgumentParser(description="SUBSTRATE Interlock Protocol")
    p.add_argument("cgroup")
    p.add_argument("action", choices=["quiesce", "freeze", "thaw"])
    p.add_argument("--timeout-ms", type=int, default=5000)
    args = p.parse_args()
    protocol = InterlockProtocol(args.cgroup, timeout_ms=args.timeout_ms)
    if args.action == "quiesce":
        ok = protocol.request_quiescence()
    elif args.action == "freeze":
        ok = protocol.request_quiescence() and protocol.freeze_cgroup()
    else:
        ok = protocol.thaw_cgroup()
    print(json.dumps({"ok": ok, "state": protocol.state.value, "stats": protocol.stats}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
