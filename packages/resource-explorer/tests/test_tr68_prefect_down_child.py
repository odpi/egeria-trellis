"""TR-68: `make prefect-down` stops the whole worker (and server) process tree and says "Stopped" only when it did.

`prefect_up.sh` starts `nohup env ... uv run --package resource-explorer resource-explorer prefect worker ...` and records
that wrapper's PID. `prefect_down.sh` killed just the wrapper: it reported "Stopped worker" while the `prefect worker`
child it had spawned kept running. Here the "worker" is a small bash tree (a parent with a child), the same shape.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prefect_down.sh"


@pytest.fixture
def home(tmp_path):
    """A copy of the script where prefect_down.sh expects it (scripts/ beside .prefect-run/)."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "prefect_down.sh")
    (tmp_path / ".prefect-run").mkdir()
    return tmp_path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _tree(home: Path, name: str, child_ignores_term: bool = False):
    """Start parent -> child like `uv run` -> `prefect worker`; the pidfile holds the PARENT's pid. Returns (parent, child).
    The parent's command line carries `prefect <name>` the way prefect_up.sh's `uv run ... prefect worker` wrapper does
    (W-68-1: prefect_down only touches a pid it can see is a Prefect process)."""
    child_pidfile = home / f"{name}.child"
    trap = "trap '' TERM; " if child_ignores_term else ""
    parent = subprocess.Popen(
        ["bash", "-c", f"# uv run --package resource-explorer resource-explorer prefect {name} --pool p\n"
                       f"({trap}exec sleep 300) & echo $! > {child_pidfile}; wait"],
        start_new_session=True)
    for _ in range(50):
        if child_pidfile.exists() and child_pidfile.read_text().strip():
            break
        time.sleep(0.05)
    child = int(child_pidfile.read_text())
    (home / ".prefect-run" / f"{name}.pid").write_text(str(parent.pid))
    return parent, child


def _down(home: Path, **env):
    return subprocess.run(["bash", str(home / "scripts" / "prefect_down.sh")], capture_output=True, text=True,
                          env={**os.environ, **env}, timeout=60)


def _reap(*pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_the_worker_child_is_stopped_with_its_parent(home):
    parent, child = _tree(home, "worker")
    try:
        out = _down(home)
        parent.wait(timeout=10)
        time.sleep(0.2)
        assert not _alive(parent.pid) and not _alive(child), out.stdout
        assert "Stopped worker" in out.stdout
        assert not (home / ".prefect-run" / "worker.pid").exists()
        assert out.returncode == 0
    finally:
        _reap(parent.pid, child)


def test_the_server_tree_is_stopped_the_same_way(home):
    parent, child = _tree(home, "server")
    try:
        out = _down(home)
        parent.wait(timeout=10)
        time.sleep(0.2)
        assert not _alive(child) and "Stopped server" in out.stdout
    finally:
        _reap(parent.pid, child)


def test_a_child_that_will_not_stop_is_not_reported_as_stopped(home):
    parent, child = _tree(home, "worker", child_ignores_term=True)
    try:
        out = _down(home, PREFECT_DOWN_WAIT="1")
        assert _alive(child)
        assert "Stopped worker" not in out.stdout
        assert "did not stop" in out.stdout + out.stderr and str(child) in out.stdout + out.stderr
        assert out.returncode != 0
        assert (home / ".prefect-run" / "worker.pid").exists()      # kept, so a second run tries again
    finally:
        _reap(parent.pid, child)


def test_nothing_running_says_so_and_succeeds(home):
    out = _down(home)
    assert out.returncode == 0
    assert "worker not running" in out.stdout and "server not running" in out.stdout
    assert "Stopped" not in out.stdout


def test_a_stale_pidfile_is_not_reported_as_stopped(home):
    (home / ".prefect-run" / "worker.pid").write_text("999999")
    out = _down(home)
    assert "Stopped worker" not in out.stdout and "worker not running" in out.stdout
    assert not (home / ".prefect-run" / "worker.pid").exists()


def _unrelated(home: Path, name: str):
    """A live process that is not Prefect, with a pid file pointing at it: what a pid file left over from before a reboot
    or crash looks like once the pid has been reused."""
    proc = subprocess.Popen(["sleep", "300"], start_new_session=True)
    (home / ".prefect-run" / f"{name}.pid").write_text(str(proc.pid))
    return proc


def test_a_pid_file_that_points_at_an_unrelated_process_leaves_it_alone(home):
    proc = _unrelated(home, "worker")
    try:
        out = _down(home)
        time.sleep(0.2)
        assert _alive(proc.pid), "an unrelated process was killed"
        assert f"pid {proc.pid} is not a Prefect process (stale pid file); not touching it" in out.stdout
        assert "Stopped worker" not in out.stdout
        assert not (home / ".prefect-run" / "worker.pid").exists()           # the stale file is removed
        assert out.returncode == 0
    finally:
        _reap(proc.pid)


def test_an_unrelated_process_with_children_is_not_killed_with_its_tree(home):
    proc = subprocess.Popen(["bash", "-c", "sleep 300 & wait"], start_new_session=True)
    time.sleep(0.3)
    (home / ".prefect-run" / "server.pid").write_text(str(proc.pid))
    kids = subprocess.run(["pgrep", "-P", str(proc.pid)], capture_output=True, text=True).stdout.split()
    try:
        out = _down(home)
        time.sleep(0.2)
        assert _alive(proc.pid) and all(_alive(int(k)) for k in kids)
        assert "not a Prefect process" in out.stdout
    finally:
        _reap(proc.pid, *[int(k) for k in kids])


def test_a_worker_pid_file_pointing_at_a_prefect_server_is_not_stopped_as_a_worker(home):
    parent, child = _tree(home, "server")
    (home / ".prefect-run" / "server.pid").unlink()                              # only the mislabelled file remains
    (home / ".prefect-run" / "worker.pid").write_text(str(parent.pid))          # mislabelled: a server under worker.pid
    try:
        out = _down(home)
        assert _alive(parent.pid) and _alive(child)
        assert f"pid {parent.pid} is not a Prefect process" in out.stdout
    finally:
        _reap(parent.pid, child)
