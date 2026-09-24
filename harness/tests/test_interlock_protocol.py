import os
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from interlock_protocol import (  # noqa: E402
    InterlockProtocol,
    InterlockState,
    LockRecord,
    LockRegistry,
    LockType,
)


def make_protocol(tmp_path: Path) -> InterlockProtocol:
    cgroup = tmp_path / 'cgroup'
    cgroup.mkdir()
    (cgroup / 'cgroup.procs').write_text('')
    proc = tmp_path / 'proc'
    proc.mkdir()
    locks = proc / 'locks'
    locks.write_text('')
    registry = tmp_path / 'registry'
    return InterlockProtocol(
        str(cgroup), proc_root=str(proc), proc_locks_path=str(locks),
        registry_path=str(registry)
    )


def test_proc_locks_parser_uses_linux_field_layout():
    parsed = InterlockProtocol._parse_lock_identity(
        '1: POSIX  ADVISORY  WRITE 123 08:01:456 0 EOF'
    )
    assert parsed == (123, 0x08, 0x01, 456)


def test_pid_lock_resolution_maps_inode_to_fd_path(tmp_path: Path):
    protocol = make_protocol(tmp_path)
    pid = 123
    target = tmp_path / 'protected.lock'
    target.write_text('x')
    st = target.stat()

    fd_dir = tmp_path / 'proc' / str(pid) / 'fd'
    fd_dir.mkdir(parents=True)
    (fd_dir / '3').symlink_to(target)

    major = os.major(st.st_dev)
    minor = os.minor(st.st_dev)
    (tmp_path / 'proc' / 'locks').write_text(
        f'1: POSIX ADVISORY WRITE {pid} {major:02x}:{minor:02x}:{st.st_ino} 0 EOF\n'
    )

    assert protocol._get_pid_locks(pid) == [str(target.resolve())]


def test_ack_recognizes_stopped_state(tmp_path: Path):
    protocol = make_protocol(tmp_path)
    pid = 321
    status = tmp_path / 'proc' / str(pid) / 'status'
    status.parent.mkdir(parents=True)
    status.write_text('Name:\ttest\nState:\tT (stopped)\n')
    protocol.agent_pids = [pid]

    protocol._check_acknowledgments()

    assert protocol.yielded_pids == {pid}


def test_missing_cgroup_fails_closed(tmp_path: Path):
    protocol = InterlockProtocol(
        str(tmp_path / 'missing'),
        proc_root=str(tmp_path / 'proc'),
        proc_locks_path=str(tmp_path / 'locks'),
        registry_path=str(tmp_path / 'registry'),
    )
    assert protocol.request_quiescence(timeout_ms=1) is False
    assert protocol.state is InterlockState.TIMEOUT


def test_registry_is_visible_across_instances_by_pid(tmp_path: Path):
    registry_path = tmp_path / 'registry'
    writer = LockRegistry(str(registry_path))
    writer.register_lock('logical-agent', LockRecord(
        lock_type=LockType.FCNTL,
        resource='/tmp/a.lock',
        pid=777,
        held_since=1.0,
        sharable=False,
    ))

    reader = LockRegistry(str(registry_path))
    locks = reader.get_locks_for_pid(777)
    assert len(locks) == 1
    assert locks[0].resource == '/tmp/a.lock'


def test_forced_break_refuses_lingering_declared_exclusive_lock(tmp_path: Path, monkeypatch):
    protocol = make_protocol(tmp_path)
    pid = 900
    protocol.agent_pids = [pid]
    protocol.registry.register_lock('agent-x', LockRecord(
        lock_type=LockType.MUTEX,
        resource='/tmp/work.lock',
        pid=pid,
        held_since=1.0,
        sharable=False,
    ))
    signals = []
    monkeypatch.setattr(os, 'kill', lambda p, sig: signals.append((p, sig)))
    monkeypatch.setattr(protocol, '_get_pid_locks', lambda p: [])

    assert protocol._forced_break() is False
    assert protocol.state is InterlockState.TIMEOUT
    assert (pid, signal.SIGSTOP) in signals
    assert (pid, signal.SIGCONT) in signals


def test_thaw_resumes_cooperatively_stopped_agents(tmp_path: Path, monkeypatch):
    protocol = make_protocol(tmp_path)
    pid = 901
    protocol.agent_pids = [pid]
    protocol.yielded_pids = {pid}
    protocol.state = InterlockState.FROZEN
    (protocol.cgroup_path / 'cgroup.freeze').write_text('1')

    signals = []
    monkeypatch.setattr(os, 'kill', lambda p, sig: signals.append((p, sig)))

    assert protocol.thaw_cgroup() is True
    assert (pid, signal.SIGCONT) in signals
    assert protocol.state is InterlockState.IDLE
    assert protocol.yielded_pids == set()
