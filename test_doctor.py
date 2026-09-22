import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import doctor


class DoctorTests(unittest.TestCase):
    """Every check is exercised with injected probes, so no Windows state is needed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / 'state'
        self.root = Path(self.tmp.name) / 'repo'
        (self.root / 'scripts').mkdir(parents=True)
        self.state.mkdir(parents=True)
        # Keep the checks hermetic: the real shell API must not decide whether
        # the host machine counts as redirected.
        self.real_known_folder = doctor.known_folder
        doctor.known_folder = lambda name: os.environ.get('LOCALAPPDATA', '')
    def tearDown(self):
        doctor.known_folder = self.real_known_folder
        self.tmp.cleanup()

    def marker(self, directory):
        path = Path(directory) / doctor.PYSIDE_MARKER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x')

    def results(self, **probes):
        base = {'lookup': lambda name: 'C:\\fake\\' + name, 'task': {'registered': True, 'state': 'Ready'},
                'alive': lambda pid: True, 'process': lambda pid: True, 'now': 1000,
                'executable_dir': self.root, 'codex_running': lambda: True}
        base.update(probes)
        return {item['id']: item for item in doctor.checks(state=self.state, root=self.root, probes=base)}

    def write_heartbeat(self, age):
        (self.state / 'lifecycle-status.json').write_text(
            json.dumps({'checked_at': 1000 - age, 'last_heartbeat': 1000 - age, 'desktop_running': True,
                        'monitor_running': True}), encoding='utf-8')
        (self.state / 'lifecycle.pid').write_text('4242', encoding='ascii')

    def test_missing_ui_runtime_is_reported(self):
        self.assertFalse(self.results()['state_dir']['ok'])
        self.marker(self.state / 'ui-runtime')
        self.assertTrue(self.results()['state_dir']['ok'])

    def test_repo_local_runtime_also_counts(self):
        self.marker(self.root / '.runtime' / 'ui')
        self.assertTrue(self.results()['state_dir']['ok'])

    def test_msix_redirected_state_dir_is_not_green(self):
        self.marker(self.state / 'ui-runtime')
        doctor.known_folder = lambda name: 'C:\\Users\\x\\AppData\\Local'
        previous = os.environ.get('LOCALAPPDATA')
        os.environ['LOCALAPPDATA'] = 'C:\\Users\\x\\AppData\\Local\\Packages\\OpenAI.Codex_x\\LocalCache\\Local'
        try:
            result = self.results()['state_dir']
        finally:
            if previous is None:
                os.environ.pop('LOCALAPPDATA', None)
            else:
                os.environ['LOCALAPPDATA'] = previous
        self.assertFalse(result['ok'])
        self.assertTrue(result['detail']['redirected'])

    def test_dead_listener_with_stale_heartbeat_fails(self):
        self.write_heartbeat(age=1)
        self.assertTrue(self.results()['listener']['ok'])
        stale = self.results(now=1000 + 120)['listener']
        self.assertFalse(stale['ok'])
        dead = self.results(alive=lambda pid: False)['listener']
        self.assertFalse(dead['ok'])
        self.assertEqual(dead['detail']['pid'], 4242)

    def test_missing_pid_file_is_not_treated_as_alive(self):
        (self.state / 'lifecycle-status.json').write_text(
            json.dumps({'last_heartbeat': 999}), encoding='utf-8')
        result = self.results(alive=lambda pid: self.fail('alive() must not be asked without a pid'))['listener']
        self.assertFalse(result['ok'])
        self.assertIsNone(result['detail']['pid'])

    def test_closed_codex_explains_an_absent_listener(self):
        # The listener is meant to exit with Codex, so this is not a fault.
        (self.state / 'lifecycle-status.json').write_text(
            json.dumps({'last_heartbeat': 100, 'desktop_running': False}), encoding='utf-8')
        result = self.results(alive=lambda pid: False, codex_running=lambda: False)['listener']
        self.assertTrue(result['ok'])
        self.assertEqual(result['detail']['reason'], 'not running because Codex is closed')

    def test_live_listener_is_healthy_even_before_codex_opens(self):
        # Regression: a closed Codex used to mask a perfectly healthy listener.
        self.write_heartbeat(age=1)
        result = self.results(codex_running=lambda: False)['listener']
        self.assertTrue(result['ok'])
        self.assertIsNone(result['detail']['reason'])

    def test_real_codex_process_scan_is_used_and_never_raises(self):
        # No probe supplied: the check must consult the live snapshot itself.
        self.write_heartbeat(age=1)
        result = self.results(codex_running=None)['listener']
        self.assertIn(result['detail']['codex_running'], (True, False, None))
        self.assertTrue(result['ok'])  # a live listener with a fresh heartbeat

    def test_dead_listener_while_codex_runs_is_a_problem(self):
        self.write_heartbeat(age=600)
        result = self.results(alive=lambda pid: False, codex_running=lambda: True)['listener']
        self.assertTrue(result['detail']['codex_running'])
        self.assertFalse(result['ok'])

    def test_unknown_codex_state_without_heartbeat_is_reported(self):
        result = self.results(codex_running=lambda: (_ for _ in ()).throw(OSError('no snapshot')))['listener']
        self.assertFalse(result['ok'])
        self.assertIsNone(result['detail']['codex_running'])
        self.assertIn('unknown', result['detail']['reason'])

    def test_unregistered_startup_task_is_a_problem(self):
        self.marker(self.state / 'ui-runtime')
        self.write_heartbeat(age=1)
        results = self.results(task={'registered': False})
        self.assertFalse(results['startup']['ok'])
        self.assertIn('startup', doctor.summary(list(results.values()))['problems'])

    def test_task_query_failure_never_breaks_the_report(self):
        def boom():
            raise OSError('schtasks unavailable')
        results = doctor.checks(state=self.state, root=self.root, probes={
            'lookup': lambda name: 'C:\\fake\\' + name, 'task_query': boom,
            'alive': lambda pid: True, 'process': lambda pid: True, 'now': 1000})
        startup = [item for item in results if item['id'] == 'startup'][0]
        self.assertFalse(startup['ok'])
        self.assertEqual(startup['detail']['error'], 'OSError')

    def test_ui_error_log_is_surfaced(self):
        self.assertTrue(self.results()['logs']['ok'])
        (self.state / 'ui-error.log').write_text('Traceback', encoding='utf-8')
        self.assertFalse(self.results()['logs']['ok'])

    def test_monitor_is_informative_not_blocking(self):
        (self.state / 'usage.json').write_text(json.dumps(
            {'checked_at': 990, 'connected': True, 'enabled': True, 'candidates': [{'key': 'a'}],
             'attempts': []}), encoding='utf-8')
        monitor = self.results()['monitor']
        self.assertTrue(monitor['ok'])
        self.assertEqual(monitor['detail']['candidates'], 1)

    def test_a_probe_that_raises_is_reported_and_not_swallowed(self):
        # Regression: a probe raising inside _startup_check made the whole
        # report read "ok" while the startup check had never run.
        def boom(name):
            raise NameError('probe signature mismatch')
        real = doctor.known_folder
        doctor.known_folder = boom
        try:
            results = {item['id']: item for item in doctor.checks(
                state=self.state, root=self.root,
                probes={'lookup': lambda n: 'x', 'task': {'registered': True},
                        'alive': lambda pid: True, 'process': lambda pid: True,
                        'now': 1000, 'executable_dir': self.root})}
        finally:
            doctor.known_folder = real
        self.assertFalse(results['startup']['ok'])
        self.assertEqual(results['startup']['detail']['error'], 'NameError')
        self.assertIn('startup', doctor.summary(list(results.values()))['problems'])

    def test_summary_lists_only_failing_checks(self):
        self.marker(self.state / 'ui-runtime')
        self.write_heartbeat(age=1)
        report = doctor.summary(list(self.results().values()))
        self.assertTrue(report['ok'])
        self.assertEqual(report['problems'], [])

    def test_missing_runtime_is_reported_with_location(self):
        self.marker(self.state / 'ui-runtime')
        result = self.results(lookup=lambda name: None,
                              executable_dir=self.root)['runtime']
        self.assertFalse(result['ok'])
        self.assertEqual(result['detail']['missing'], ['pythonw.exe', 'python.exe', 'node.exe'])


class ProcessLivenessTests(unittest.TestCase):
    """Regression: the check asked for SYNCHRONIZE-less access, so every
    process looked dead and a healthy listener was reported as broken."""

    def test_own_process_is_alive(self):
        self.assertTrue(doctor.process_alive(os.getpid()))

    def test_live_child_is_alive_and_a_finished_child_is_not(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            self.assertTrue(doctor.process_alive(child.pid))
        finally:
            child.kill(); child.wait(timeout=10)
        self.assertFalse(doctor.process_alive(child.pid))

    def test_nonsense_pids_are_not_alive(self):
        for value in (None, 0, -1, '', 'abc', 999999999):
            self.assertFalse(doctor.process_alive(value), value)


if __name__ == '__main__':
    unittest.main()
