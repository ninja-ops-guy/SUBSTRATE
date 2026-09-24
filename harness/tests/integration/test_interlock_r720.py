"""Root-only interlock integration test for the R720 qualification host.

Run explicitly on the isolated qualification server:
  sudo SUBSTRATE_R720_INTEGRATION=1 pytest -q tests/integration/test_interlock_r720.py
"""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / 'src'
sys.path.insert(0, str(SRC))

from interlock_protocol import InterlockProtocol, InterlockState  # noqa: E402


pytestmark = pytest.mark.skipif(
    os.environ.get('SUBSTRATE_R720_INTEGRATION') != '1' or os.geteuid() != 0,
    reason='requires root and SUBSTRATE_R720_INTEGRATION=1 on isolated qualification host',
)


def _wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise TimeoutError(f'timed out waiting for {path}')


def test_real_cgroup_cooperative_quiescence_and_thaw(tmp_path: Path):
    cgroup_root = Path('/sys/fs/cgroup')
    cgroup = cgroup_root / f'substrate-interlock-test-{uuid.uuid4().hex[:8]}'
    registry = Path('/run/substrate') / f'interlock-test-{uuid.uuid4().hex[:8]}'
    lock_path = tmp_path / 'worker.lock'
    ready_path = tmp_path / 'ready'
    child = None

    cgroup.mkdir()
    registry.mkdir(parents=True)
    script = f'''
import fcntl, os, signal, sys, time
sys.path.insert(0, {str(SRC)!r})
from interlock_protocol import AgentInterlockHandler, LockType
lock_path = {str(lock_path)!r}
ready_path = {str(ready_path)!r}
registry_path = {str(registry)!r}
h = AgentInterlockHandler('stress-agent', registry_path=registry_path)
f = open(lock_path, 'w')
fcntl.flock(f.fileno(), fcntl.LOCK_EX)
h.declare_lock(lock_path, LockType.FLOCK)
def release():
    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    h.release_lock(lock_path)
h.register_yield_callback(release)
open(ready_path, 'w').write(str(os.getpid()))
while True:
    time.sleep(0.1)
'''

    try:
        child = subprocess.Popen([sys.executable, '-c', script])
        (cgroup / 'cgroup.procs').write_text(str(child.pid))
        _wait_for(ready_path)

        protocol = InterlockProtocol(str(cgroup), registry_path=str(registry))
        assert protocol.request_quiescence(timeout_ms=3000) is True
        assert protocol.state is InterlockState.AGENT_YIELDED
        assert protocol.freeze_cgroup() is True
        assert protocol.state is InterlockState.FROZEN
        assert protocol.thaw_cgroup() is True
        assert protocol.state is InterlockState.IDLE

        # The child should be running again after SIGCONT.
        assert child.poll() is None
    finally:
        if child is not None and child.poll() is None:
            try:
                os.kill(child.pid, 18)  # SIGCONT in case an assertion failed while stopped
            except ProcessLookupError:
                pass
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        try:
            (cgroup / 'cgroup.freeze').write_text('0')
        except OSError:
            pass
        try:
            cgroup.rmdir()
        except OSError:
            pass
        for item in registry.glob('*'):
            try:
                item.unlink()
            except OSError:
                pass
        try:
            registry.rmdir()
        except OSError:
            pass
