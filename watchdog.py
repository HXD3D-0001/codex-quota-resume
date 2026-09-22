"""Watchdog for the lifecycle listener.

Task Scheduler's restart-on-failure only reacts when the task action itself
reports a failure: a process killed from outside leaves the task "Ready" and
nothing restarts it, which is how startup sync silently died until the next
logon. This supervisor holds the scheduled task's action and keeps the listener
alive within seconds, so an external kill is no longer fatal.

The listener's own loop still covers in-process crashes; this covers the
process dying outright.
"""
import os
from pathlib import Path
import subprocess
import sys
import time

from lifecycle import append_log
from monitor import SingleInstance, state_directory

POLL_SECONDS = 2
HEARTBEAT_STALE_SECONDS = 20
CRASH_WINDOW_SECONDS = 60
FIRST_RESTART_DELAY_SECONDS = 1
RESTART_DELAY_SECONDS = 5
MAX_RESTART_DELAY_SECONDS = 60
MAX_FAST_CRASHES = 10
MAX_ITERATIONS = 200_000


def listener_command(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    return [sys.executable, str(root / 'lifecycle.py')]


def read_pid(path):
    try:
        text = Path(path).read_text(encoding='ascii').strip()
        return int(text) if text.isdigit() else None
    except (OSError, ValueError):
        return None


def process_alive(pid):
    """Delegates to doctor's single implementation of the liveness check."""
    from doctor import process_alive as check
    return check(pid)


def heartbeat_age(directory):
    import json
    try:
        status = json.loads((Path(directory) / 'lifecycle-status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    stamp = status.get('last_heartbeat') or status.get('checked_at')
    try:
        # A timestamp in the future means clock skew, not a healthy heartbeat.
        return max(0.0, time.time() - float(stamp))
    except (TypeError, ValueError):
        return None


def listener_state(directory):
    """Return (pid, healthy) for the listener, using the heartbeat it publishes."""
    pid = read_pid(Path(directory) / 'lifecycle.pid')
    if pid is None or not process_alive(pid):
        return pid, False
    age = heartbeat_age(directory)
    # A live process that stopped writing heartbeats is hung, not healthy.
    return pid, age is not None and age <= HEARTBEAT_STALE_SECONDS


class Watchdog:
    def __init__(self, directory, root=None, spawn=None, alive=None, state=None, clock=time.time):
        self.directory = Path(directory)
        self.root = Path(root) if root is not None else Path(__file__).resolve().parent
        self.spawn = spawn or self.default_spawn
        self.alive = alive or process_alive
        self.state = state or (lambda: listener_state(self.directory))
        self.clock = clock
        self.child = None
        self.started_at = None

    def default_spawn(self):
        return subprocess.Popen(listener_command(self.root), cwd=str(self.root),
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def stop_requested(self):
        return (self.directory / 'lifecycle-stop.request').exists()

    def needed(self):
        """True when the listener must be (re)started."""
        pid, healthy = self.state()
        if pid is None or not self.alive(pid):
            return True
        return not healthy

    def wait(self, seconds):
        # Timing uses the real monotonic clock, never the injectable one: a
        # stubbed clock that does not advance would make this loop spin forever.
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if self.stop_requested():
                return
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

    def terminate(self, pid):
        try:
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass

    def loop(self, once=False):
        """Return the number of restarts performed."""
        (self.directory / 'watchdog.pid').write_text(str(os.getpid()), encoding='ascii')
        append_log(self.directory, 'watchdog started')
        delay = RESTART_DELAY_SECONDS
        restarts = 0
        fast = 0
        established = False
        rounds = 0
        while not self.stop_requested():
            rounds += 1
            if rounds > MAX_ITERATIONS:
                # A hard stop for any logic error: an unbounded supervisor loop
                # must never be possible.
                append_log(self.directory, f'watchdog stopping after {rounds} rounds; restart it manually')
                return restarts
            if self.needed():
                pid, _ = self.state()
                if pid is not None and self.alive(pid):
                    # Alive but not reporting: replace it instead of stacking.
                    append_log(self.directory, f'listener {pid} is not reporting; restarting it')
                    self.terminate(pid)
                    self.wait(1)
                # Recovery after a healthy period is not a crash loop, so it
                # must not wait out the crash backoff before restarting.
                if not established:
                    self.wait(FIRST_RESTART_DELAY_SECONDS)
                self.child = self.spawn()
                self.started_at = self.clock()
                established = False
                append_log(self.directory, f'watchdog started listener pid {self.child.pid}')
            elif not established:
                established = True
                fast = 0
                delay = RESTART_DELAY_SECONDS
            if once:
                return restarts
            self.wait(POLL_SECONDS)
            if self.child is not None and self.started_at is not None:
                lived = self.clock() - self.started_at
                if not self.alive(self.child.pid):
                    fast = fast + 1 if lived < CRASH_WINDOW_SECONDS else 0
                    if fast >= MAX_FAST_CRASHES:
                        append_log(self.directory,
                                   f'giving up after {fast} fast listener crashes; run scripts/start.ps1')
                        return restarts
                    if lived < CRASH_WINDOW_SECONDS:
                        # First fast crash waits RESTART_DELAY_SECONDS, then doubles.
                        delay = RESTART_DELAY_SECONDS if fast <= 1 \
                            else min(delay * 2, MAX_RESTART_DELAY_SECONDS)
                    else:
                        # A crash after real work is not a crash loop.
                        delay = RESTART_DELAY_SECONDS
                    append_log(self.directory,
                               f'listener pid {self.child.pid} exited after {lived:.1f}s; '
                               f'next start in {delay}s')
                    self.wait(delay)
                    restarts += 1
        append_log(self.directory, 'watchdog exit requested')
        (self.directory / 'watchdog.pid').unlink(missing_ok=True)
        return restarts

def run(directory=None, root=None):
    directory = Path(directory) if directory is not None else state_directory()
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    watchdog = Watchdog(directory, root)
    # Single instance plus the scheduled task: two watchdogs would fight over
    # the listener, and the lock also makes a manual start harmless.
    with SingleInstance(directory, name='watchdog'):
        return watchdog.loop()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        raise SystemExit('usage: watchdog.py')
    return run()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        if 'already running' not in str(error):
            raise
        append_log(state_directory(), 'another watchdog holds the lock; exiting')
