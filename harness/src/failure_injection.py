#!/usr/bin/env python3
"""SUBSTRATE failure-injection qualification campaign.

Live injections require ``--allow-destructive`` and are intended only for an
isolated qualification host.  The module deliberately fails closed when a
requested lane cannot be executed or verified.
"""

from __future__ import annotations

import argparse
import json
import mmap
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional


class FailureType(Enum):
    OOM_KILL = "oom_kill"
    PROCESS_STALL = "process_stall"
    CGROUP_WRITE_FAILURE = "cgroup_write_failure"
    DAEMON_DEATH = "daemon_death"
    CORRUPTED_CHECKPOINT = "corrupted_checkpoint"


class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class InjectionResult:
    failure_type: FailureType
    severity: Severity
    target_component: str
    injection_time: str
    detection_time_ms: Optional[float]
    recovery_time_ms: Optional[float]
    recovery_successful: bool
    data_loss: bool
    system_stable: bool
    details: Dict = field(default_factory=dict)


class FailureInjector:
    def __init__(self, results_dir: str = "./results/failure-injection", *, allow_destructive: bool = False):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.allow_destructive = allow_destructive
        self.results: List[InjectionResult] = []
        self.requested_tests: List[str] = []
        self.execution_errors: List[Dict[str, str]] = []

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _require_destructive(self, action: str) -> None:
        if not self.allow_destructive:
            raise PermissionError(
                f"{action} is destructive; rerun with --allow-destructive on an isolated qualification host"
            )

    @staticmethod
    def _require_cgroupfs_path(path: str) -> Path:
        target = Path(path).resolve()
        root = Path("/sys/fs/cgroup").resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"refusing non-cgroupfs target: {target}")
        return target

    @staticmethod
    def _read_counter(path: Path, key: str) -> int:
        for line in path.read_text().splitlines():
            name, value = line.split(maxsplit=1)
            if name == key:
                return int(value)
        return 0

    @staticmethod
    def _service_unit(name: str) -> str:
        return name if name.endswith(".service") else f"{name}.service"

    @classmethod
    def _service_pid(cls, name: str) -> int:
        cp = subprocess.run(
            ["systemctl", "show", cls._service_unit(name), "--property=MainPID", "--value"],
            capture_output=True, text=True, timeout=5,
        )
        if cp.returncode != 0:
            return 0
        try:
            return int(cp.stdout.strip() or "0")
        except ValueError:
            return 0

    @classmethod
    def _service_active(cls, name: str) -> bool:
        cp = subprocess.run(
            ["systemctl", "is-active", cls._service_unit(name)],
            capture_output=True, text=True, timeout=5,
        )
        return cp.returncode == 0 and cp.stdout.strip() == "active"

    def inject_oom(self, target_cgroup: str = "/sys/fs/cgroup/residual-executor", memory_gb: float = 1.0) -> InjectionResult:
        self._require_destructive("OOM injection")
        cgroup = self._require_cgroupfs_path(target_cgroup)
        procs, max_file, events = (cgroup / "cgroup.procs", cgroup / "memory.max", cgroup / "memory.events")
        if not all(p.exists() for p in (procs, max_file, events)):
            return InjectionResult(FailureType.OOM_KILL, Severity.HIGH, str(cgroup), self._now(), None, None,
                                   False, False, True, {"error": "required cgroup v2 files are missing"})

        limit_text = max_file.read_text().strip()
        if limit_text == "max":
            return InjectionResult(FailureType.OOM_KILL, Severity.HIGH, str(cgroup), self._now(), None, None,
                                   False, False, True, {"error": "memory.max is unlimited; refusing injection"})
        limit = int(limit_text)
        allocation = int(memory_gb * 1024**3)
        if allocation <= limit:
            return InjectionResult(FailureType.OOM_KILL, Severity.HIGH, str(cgroup), self._now(), None, None,
                                   False, False, True,
                                   {"error": "requested allocation does not exceed memory.max",
                                    "allocation_bytes": allocation, "memory_max_bytes": limit})

        before = self._read_counter(events, "oom_kill")
        script = (
            "import mmap,sys,time\n"
            "sys.stdin.buffer.read(1)\n"
            f"n={allocation}\n"
            "m=mmap.mmap(-1,n,access=mmap.ACCESS_WRITE)\n"
            "for i in range(0,n,4096): m[i:i+1]=b'x'\n"
            "time.sleep(30)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE)
        started = time.monotonic()
        detected: Optional[float] = None
        try:
            procs.write_text(str(proc.pid))
            assert proc.stdin is not None
            proc.stdin.write(b"1")
            proc.stdin.flush()
            proc.stdin.close()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self._read_counter(events, "oom_kill") > before:
                    detected = (time.monotonic() - started) * 1000
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        after = self._read_counter(events, "oom_kill")
        ok = after > before
        return InjectionResult(FailureType.OOM_KILL, Severity.HIGH, str(cgroup), self._now(), detected,
                               0.0 if ok else None, ok, False, True,
                               {"oom_kill_before": before, "oom_kill_after": after})

    def inject_process_stall(self, pid: Optional[int] = None, duration_sec: float = 2.0) -> InjectionResult:
        self._require_destructive("process-stall injection")
        owned: Optional[subprocess.Popen] = None
        if pid is None:
            owned = subprocess.Popen(["sleep", str(max(duration_sec + 10, 15))])
            pid = owned.pid
        started = time.monotonic()
        try:
            os.kill(pid, signal.SIGSTOP)
            time.sleep(max(0.05, duration_sec))
            os.kill(pid, signal.SIGCONT)
            running = Path(f"/proc/{pid}").exists()
            return InjectionResult(FailureType.PROCESS_STALL, Severity.MEDIUM, f"pid:{pid}", self._now(),
                                   0.0, (time.monotonic() - started) * 1000, running, False, running,
                                   {"campaign_owned": owned is not None})
        finally:
            if owned is not None and owned.poll() is None:
                owned.terminate()
                try:
                    owned.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    owned.kill()

    def inject_cgroup_write_failure(self, target_file: str) -> InjectionResult:
        self._require_destructive("cgroup-write injection")
        path = self._require_cgroupfs_path(target_file)
        if not path.is_file():
            return InjectionResult(FailureType.CGROUP_WRITE_FAILURE, Severity.MEDIUM, str(path), self._now(),
                                   None, None, False, False, True, {"error": "target file missing"})
        before = path.read_text()
        rejected = False
        try:
            path.write_text("substrate-invalid-value")
        except OSError:
            rejected = True
        unchanged = path.read_text() == before
        ok = rejected and unchanged
        return InjectionResult(FailureType.CGROUP_WRITE_FAILURE, Severity.MEDIUM, str(path), self._now(),
                               0.0, 0.0, ok, False, True,
                               {"kernel_rejected": rejected, "value_unchanged": unchanged})

    def inject_daemon_death(self, daemon_name: str = "omarchy-cgroupd") -> InjectionResult:
        self._require_destructive("daemon-death injection")
        old_pid = self._service_pid(daemon_name)
        if old_pid <= 0:
            return InjectionResult(FailureType.DAEMON_DEATH, Severity.CRITICAL, daemon_name, self._now(),
                                   None, None, False, False, self._service_active(daemon_name),
                                   {"error": "service has no MainPID"})
        started = time.monotonic()
        os.kill(old_pid, signal.SIGKILL)
        new_pid = 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            new_pid = self._service_pid(daemon_name)
            if new_pid > 0 and new_pid != old_pid and self._service_active(daemon_name):
                break
            time.sleep(0.2)
        ok = new_pid > 0 and new_pid != old_pid and self._service_active(daemon_name)
        return InjectionResult(FailureType.DAEMON_DEATH, Severity.CRITICAL, daemon_name, self._now(),
                               0.0, (time.monotonic() - started) * 1000, ok, False, ok,
                               {"old_pid": old_pid, "new_pid": new_pid})

    def inject_corrupted_checkpoint(self, checkpoint_path: str) -> InjectionResult:
        self._require_destructive("checkpoint-corruption injection")
        return InjectionResult(FailureType.CORRUPTED_CHECKPOINT, Severity.HIGH, checkpoint_path, self._now(),
                               None, None, False, False, True,
                               {"status": "BLOCKED_SAFE",
                                "error": "checkpoint corruption lane requires disposable clone + verifier integration",
                                "source_modified": False})

    def run_campaign(self, tests: List[str], *, checkpoint_path: Optional[str] = None,
                     target_cgroup: str = "/sys/fs/cgroup/residual-executor",
                     oom_memory_gb: float = 1.0, stall_duration_sec: float = 2.0,
                     daemon_name: str = "omarchy-cgroupd") -> Dict:
        self.results = []
        self.requested_tests = list(tests)
        self.execution_errors = []
        actions: Dict[str, Optional[Callable[[], InjectionResult]]] = {
            "oom": lambda: self.inject_oom(target_cgroup, oom_memory_gb),
            "stall": lambda: self.inject_process_stall(duration_sec=stall_duration_sec),
            "cgroup_write": lambda: self.inject_cgroup_write_failure(str(Path(target_cgroup) / "memory.max")),
            "daemon_death": lambda: self.inject_daemon_death(daemon_name),
            "corrupted_checkpoint": (lambda: self.inject_corrupted_checkpoint(checkpoint_path)) if checkpoint_path else None,
        }
        for name in tests:
            action = actions.get(name)
            if name not in actions:
                self.execution_errors.append({"test": name, "error": "unknown test"})
                continue
            if action is None:
                self.execution_errors.append({"test": name, "error": "checkpoint path is required for corrupted_checkpoint"})
                continue
            try:
                self.results.append(action())
            except Exception as exc:
                self.execution_errors.append({"test": name, "error": f"{type(exc).__name__}: {exc}"})
        return self.generate_report()

    def generate_report(self) -> Dict:
        report = {
            "timestamp": self._now(),
            "requested_tests": list(self.requested_tests),
            "executed_tests": len(self.results),
            "total_tests": len(self.results),
            "passed": sum(r.recovery_successful for r in self.results),
            "failed": sum(not r.recovery_successful for r in self.results),
            "critical_failures": sum((not r.recovery_successful) and r.severity is Severity.CRITICAL for r in self.results),
            "execution_errors": list(self.execution_errors),
            "complete": len(self.results) == len(self.requested_tests) and not self.execution_errors,
            "results": [asdict(r) for r in self.results],
        }
        out = self.results_dir / f"campaign-{int(time.time())}.json"
        out.write_text(json.dumps(report, indent=2, default=str))
        return report


def main() -> int:
    p = argparse.ArgumentParser(description="SUBSTRATE Failure Injection Campaign")
    p.add_argument("--tests", nargs="+", default=["oom", "stall", "cgroup_write", "daemon_death"])
    p.add_argument("--output", "-o", default="./results/failure-injection")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-destructive", action="store_true")
    p.add_argument("--checkpoint")
    p.add_argument("--target-cgroup", default="/sys/fs/cgroup/residual-executor")
    p.add_argument("--oom-memory-gb", type=float, default=1.0)
    p.add_argument("--stall-duration", type=float, default=2.0)
    p.add_argument("--daemon", default="omarchy-cgroupd")
    args = p.parse_args()
    if args.dry_run:
        print("Dry run mode — would execute tests:")
        for name in args.tests:
            print(f"  - {name}")
        return 0
    injector = FailureInjector(args.output, allow_destructive=args.allow_destructive)
    report = injector.run_campaign(args.tests, checkpoint_path=args.checkpoint,
                                   target_cgroup=args.target_cgroup, oom_memory_gb=args.oom_memory_gb,
                                   stall_duration_sec=args.stall_duration, daemon_name=args.daemon)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["complete"] and report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
