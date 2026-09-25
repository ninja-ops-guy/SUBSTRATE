"""Minimal local adapters. No shell, arbitrary paths, logs, coredumps, or providers."""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from core import Denied, safe_unit

MAX_OUTPUT = 16384
STATUS_PROPERTIES = ("Id", "LoadState", "ActiveState", "SubState", "Result")


def run_bounded(argv: list[str], timeout: float = 10) -> tuple[int | None, bytes]:
    """Drain bounded stdout, discard stderr, kill the process group on timeout.

    Killing systemctl does NOT cancel a job already accepted by systemd. A
    timed-out mutation must be reported indeterminate and must not be retried.
    """
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, shell=False, cwd="/",
                            env={"LC_ALL": "C", "SYSTEMD_COLORS": "0", "SYSTEMD_PAGER": ""},
                            start_new_session=True, close_fds=True)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ)
            while sel.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                for key, _ in sel.select(min(remaining, 0.1)):
                    block = os.read(key.fileobj.fileno(), 4096)
                    if not block:
                        sel.unregister(key.fileobj)
                    else:
                        output.extend(block)
                        if len(output) > MAX_OUTPUT:
                            raise TimeoutError
        return proc.wait(timeout=max(0.001, deadline - time.monotonic())), bytes(output)
    except (TimeoutError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        return None, b""
    finally:
        proc.stdout.close()


class LocalAdapter:
    def __init__(self, systemctl: str):
        if not os.path.isabs(systemctl):
            raise Denied("absolute_systemctl_required")
        self.systemctl = systemctl

    def __call__(self, operation: str, arguments: dict) -> dict:
        if operation == "system.identify":
            # Do not expose hostname, machine-id, environment, or arbitrary files.
            info = {}
            with open("/etc/os-release", encoding="utf-8") as stream:
                text = stream.read(MAX_OUTPUT + 1)
            if len(text) > MAX_OUTPUT:
                return {"status": "failed", "code": "os_release_too_large"}
            for line in text.splitlines():
                key, sep, value = line.partition("=")
                if sep and key in {"ID", "VERSION_ID", "PRETTY_NAME"}:
                    info[key] = value.strip('"')[:256]
            return {"status": "succeeded", "data": info}
        if operation == "memory.snapshot":
            wanted = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
            values = {}
            with open("/proc/meminfo", encoding="ascii") as stream:
                for line in stream:
                    key, _, value = line.partition(":")
                    if key in wanted:
                        values[key] = int(value.strip().split()[0]) * 1024
            return {"status": "succeeded", "data": {"bytes": values}}
        if operation not in {"service.status", "service.restart"}:
            raise Denied("adapter_not_implemented")
        unit = safe_unit(arguments["unit"])
        argv = [self.systemctl, "--system", "--no-pager", "--no-ask-password"]
        # Resolve before BOTH observation and mutation. An allowlisted alias may
        # not silently route a restart to a different/protected canonical unit.
        status, output = run_bounded(argv + ["show", "--property=" + ",".join(STATUS_PROPERTIES), "--", unit])
        if status is None:
            return {"status": "indeterminate", "code": "adapter_timeout_or_output_limit"}
        if status != 0:
            return {"status": "failed", "exit_code": status}
        data = {}
        for line in output.decode("utf-8", errors="replace").splitlines():
            key, sep, value = line.partition("=")
            if sep and key in STATUS_PROPERTIES:
                data[key] = value[:256]
        if data.get("LoadState") != "loaded" or data.get("Id") != unit:
            # Aliases are not silently accepted as the authorized target.
            return {"status": "failed", "code": "unit_not_loaded_or_alias"}
        if operation == "service.restart":
            status, _ = run_bounded(argv + ["restart", "--", unit])
            if status is None:
                return {"status": "indeterminate", "code": "adapter_timeout_or_output_limit"}
            # A failed restart may have stopped the unit. Never retry automatically.
            return {"status": "succeeded" if status == 0 else "failed", "exit_code": status}
        return {"status": "succeeded", "data": data}
