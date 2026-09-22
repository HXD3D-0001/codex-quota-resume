import json
import tempfile
import time
import unittest
from pathlib import Path

import watchdog


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid


class WatchdogTests(unittest.TestCase):
    """The watchdog exists because a task action killed from outside leaves the
    scheduled task "Ready", so its restart policy never fires."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.spawned = []
        self.terminated = []
        self.now = [1000.0]
        self.live = set()
        self.watchdog = watchdog.Watchdog(
            self.directory,
            root=self.directory,
            spawn=self.spawn,
            alive=lambda pid: pid in self.live,
            state=self.state,
            clock=lambda: self.now[0])

    def tearDown(self):
        self.tmp.cleanup()

    def spawn(self):
        process = FakeProcess(5000 + len(self.spawned))
        self.spawned.append(process)
        self.live.add(process.pid)
        return process

    def state(self):
        return self.state_value

    state_value = (None, False)

    def write_heartbeat(self, age=0.0, clock=None):
        stamp = (time.time() if clock is None else clock) - age
        (self.directory / 'lifecycle-status.json').write_text(
            json.dumps({'last_heartbeat': stamp}), encoding='utf-8')

    def test_real_listener_state_uses_pid_liveness_and_heartbeat_age(self):
        state = watchdog.listener_state
        (self.directory / 'lifecycle.pid').write_text('4242', encoding='ascii')
        self.write_heartbeat(age=1)
        saved = watchdog.process_alive
        try:
            watchdog.process_alive = lambda pid: pid == 4242
            self.assertEqual(state(self.directory), (4242, True))
            self.write_heartbeat(age=watchdog.HEARTBEAT_STALE_SECONDS + 5)
            self.assertEqual(state(self.directory), (4242, False))  # hung, not healthy
            watchdog.process_alive = lambda pid: False
            self.assertEqual(state(self.directory), (4242, False))  # dead
        finally:
            watchdog.process_alive = saved

    def test_missing_listener_is_started(self):
        self.state_value = (None, False)
        self.assertEqual(self.watchdog.loop(once=True), 0)
        self.assertEqual(len(self.spawned), 1)
        self.assertIn('watchdog started listener', (self.directory / 'lifecycle.log').read_text(encoding='utf-8'))

    def test_healthy_listener_is_left_alone(self):
        self.state_value = (4242, True)
        self.live.add(4242)
        self.watchdog.loop(once=True)
        self.assertEqual(self.spawned, [])

    def test_hung_listener_is_replaced_instead_of_stacked(self):
        self.state_value = (4242, False)
        self.live.add(4242)
        self.watchdog.terminate = lambda pid: self.terminated.append(pid)
        self.watchdog.wait = lambda seconds: None
        self.watchdog.loop(once=True)
        self.assertEqual(self.terminated, [4242])
        self.assertEqual(len(self.spawned), 1)
        self.assertIn('is not reporting', (self.directory / 'lifecycle.log').read_text(encoding='utf-8'))

    def test_stop_request_ends_the_loop_without_starting_anything(self):
        (self.directory / 'lifecycle-stop.request').touch()
        self.assertEqual(self.watchdog.loop(once=True), 0)
        self.assertEqual(self.spawned, [])
        self.assertFalse((self.directory / 'watchdog.pid').exists())

    def test_a_killed_listener_is_restarted(self):
        # The listener is simply gone, exactly as an external kill leaves it.
        # Assertions stay on observable behaviour plus the delay requested when
        # the loop returns from its very first pass.
        self.state_value = (None, False)
        waits = []
        original_wait = self.watchdog.wait
        def recording_wait(seconds):
            waits.append(seconds)
            original_wait(0)  # record without sleeping
        self.watchdog.wait = recording_wait
        self.assertEqual(self.watchdog.loop(once=True), 0)
        self.assertEqual(len(self.spawned), 1)
        # A recovery must not wait out the crash backoff first.
        self.assertEqual(waits[0], watchdog.FIRST_RESTART_DELAY_SECONDS)
        self.assertIn('watchdog started listener', (self.directory / 'lifecycle.log').read_text(encoding='utf-8'))

    def test_backoff_is_reserved_for_repeated_failures(self):
        # Repeated fast crashes must back off instead of spinning, and the
        # counter must be able to give up. The requested delays are observed
        # where they are asked for; wait() itself is neutralised.
        cap = watchdog.MAX_FAST_CRASHES
        watchdog.MAX_FAST_CRASHES = 2
        try:
            self.state_value = (None, False)
            self.live.clear()
            self.watchdog.alive = lambda pid: False   # every listener dies at once
            self.watchdog.clock = lambda: 0.0         # every death counts as "fast"
            crashes = {}
            real_spawn = self.spawn
            def dying_spawn():
                process = real_spawn()
                crashes[process.pid] = crashes.get(process.pid, 0) + 1
                if len(crashes) >= watchdog.MAX_FAST_CRASHES:
                    (self.directory / 'lifecycle-stop.request').touch()
                return process
            self.watchdog.spawn = dying_spawn
            waits = []
            self.watchdog.wait = lambda seconds: waits.append(seconds)
            self.watchdog.loop()
            self.assertEqual(len(self.spawned), watchdog.MAX_FAST_CRASHES)
            # Backoff is requested after the crash is noticed, not before.
            self.assertIn(watchdog.RESTART_DELAY_SECONDS, waits)
            self.assertIn('giving up', (self.directory / 'lifecycle.log').read_text(encoding='utf-8'))
        finally:
            watchdog.MAX_FAST_CRASHES = cap

    def test_the_loop_has_a_hard_round_limit(self):
        # A logic error must never turn the supervisor into an unbounded loop.
        cap = watchdog.MAX_ITERATIONS
        watchdog.MAX_ITERATIONS = 5
        try:
            self.state_value = (4242, True)
            self.live.add(4242)
            self.watchdog.wait = lambda seconds: None  # never yields, so only the cap can stop it
            self.assertEqual(self.watchdog.loop(), 0)
            self.assertIn('stopping after', (self.directory / 'lifecycle.log').read_text(encoding='utf-8'))
        finally:
            watchdog.MAX_ITERATIONS = cap

    def test_startup_writes_its_own_pid_for_the_doctor(self):
        self.state_value = (None, False)
        self.watchdog.loop(once=True)
        import os
        self.assertEqual((self.directory / 'watchdog.pid').read_text(encoding='ascii'), str(os.getpid()))


if __name__ == '__main__':
    unittest.main()
