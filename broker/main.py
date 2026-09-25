"""Entrypoints for substrate-brokerd and substrate-osctl (Python stdlib only)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sqlite3
import stat
import sys
import threading

from adapters import LocalAdapter
from core import Broker, Denied, Policy, Store, canonical, strict_json
from transport import MAX_REQUEST, Server, request


def trusted_path(path: Path, regular=True) -> Path:
    """Allow Nix's root-owned symlinks, but never writable policy/code ancestors."""
    resolved = path.resolve(strict=True)
    for part in [resolved, *resolved.parents]:
        info = part.lstat()
        if info.st_uid != 0:
            raise Denied("untrusted_path_owner")
        writable = info.st_mode & 0o022
        # A sticky root-owned directory (e.g. /nix/store or /tmp) cannot have a
        # root-owned entry replaced by an unprivileged different UID.
        if writable and not (stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX):
            raise Denied("untrusted_writable_path")
    if regular and not stat.S_ISREG(resolved.stat().st_mode):
        raise Denied("regular_file_required")
    return resolved


def load_trusted(path: str) -> dict:
    resolved = trusted_path(Path(path))
    with open(resolved, "rb") as stream:
        data = stream.read(32769)
    if len(data) > 32768:
        raise Denied("configuration_too_large")
    obj = strict_json(data)
    if type(obj) is not dict:
        raise Denied("configuration_object_required")
    return obj


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--policy", required=True)
    serve.add_argument("--catalog", required=True)
    serve.add_argument("--systemctl", required=True)
    serve.add_argument("--state-dir", default="/var/lib/substrate-broker")
    serve.add_argument("--run-dir", default="/run/substrate-broker")
    send = commands.add_parser("request", help="Read one JSON request from stdin; emit one JSON response")
    send.add_argument("--socket", default="/run/substrate-broker/agent.sock")
    send.add_argument("--admin", action="store_true")
    args = parser.parse_args()
    if args.command == "request":
        data = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(data) > MAX_REQUEST:
            raise Denied("frame_size_exceeded")
        path = "/run/substrate-broker/admin.sock" if args.admin else args.socket
        result = request(path, strict_json(data))
        print(canonical(result))
        return 0 if result.get("ok") else 1
    if os.geteuid() != 0:
        raise Denied("broker_requires_administrator")
    os.umask(0o077)
    policy_data = load_trusted(args.policy)
    catalog = load_trusted(args.catalog)
    policy = Policy.load(policy_data, catalog)
    systemctl = trusted_path(Path(args.systemctl))
    if not os.access(systemctl, os.X_OK):
        raise Denied("systemctl_not_executable")
    state_path = trusted_path(Path(args.state_dir), regular=False)
    run_path = trusted_path(Path(args.run_dir), regular=False)
    store = Store(state_path)
    server = None
    try:
        broker = Broker(store, policy, catalog, LocalAdapter(str(systemctl)))
        server = Server(broker, run_path)
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda _sig, _frame: stop.set())
        server.serve(stop)
        # Service shutdown kills the process/cgroup; any admitted unfinished
        # effect remains journaled for indeterminate recovery on restart.
    finally:
        if server:
            server.close()
        # Worker threads may be executing. Do not close their DB underneath them;
        # process exit closes it and next startup reconciles pending admissions.
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Denied, OSError, ValueError, sqlite3.Error) as error:
        code = str(error) if isinstance(error, Denied) else "local_io_or_configuration_error"
        print(canonical({"ok": False, "code": code, "may_have_executed": True}), file=sys.stderr)
        sys.exit(2)
