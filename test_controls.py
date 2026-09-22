import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


class MCPTests(unittest.TestCase):
    def test_real_stdio_server_status_and_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
                      {'jsonrpc':'2.0','method':'notifications/initialized'},
                      {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                      {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'quota_resume_control','arguments':{'action':'pause'}}},
                      {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'quota_resume_status'}}]
            run=subprocess.run([sys.executable,'mcp_server.py'],input='\n'.join(map(json.dumps,requests))+'\n',
                               text=True,encoding='utf-8',capture_output=True,
                               env=dict(os.environ,CODEX_QUOTA_RESUME_STATE=tmp),timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)
            rows=[json.loads(line) for line in run.stdout.splitlines()]
            self.assertEqual([r['id'] for r in rows],[1,2,3,4])
            self.assertEqual(rows[0]['result']['capabilities'],{'tools':{}})
            self.assertEqual(len(rows[1]['result']['tools']),2)
            status=json.loads(rows[3]['result']['content'][0]['text'])
            self.assertFalse(status['enabled'])
            self.assertTrue((Path(tmp)/'refresh.request').exists())

    def test_stdio_rejects_unknown_action(self):
        request={'id':1,'method':'tools/call','params':{'name':'quota_resume_control','arguments':{'action':'init'}}}
        run=subprocess.run([sys.executable,'mcp_server.py'],input=json.dumps(request)+'\n',text=True,capture_output=True,timeout=10)
        self.assertTrue(json.loads(run.stdout)['result']['isError'])


class DoctorCommandTests(unittest.TestCase):
    """The CLI surface must work end to end, not just as an imported function."""

    # Probes keep the report independent of whatever is registered on this host
    # while still exercising doctor.main -> checks end to end. The trailing
    # SystemExit is what makes `python -c` propagate the exit code at all.
    preamble = ('import doctor\n'
                'doctor.known_folder = lambda *a, **k: None\n'
                'def finish():\n'
                '    raise SystemExit(doctor.main(argv=[]))\n')
    def run_doctor(self, tmp, body):
        return subprocess.run([sys.executable, '-c', self.preamble + body],
                              text=True, encoding='utf-8', capture_output=True, timeout=120,
                              env=dict(os.environ, CODEX_QUOTA_RESUME_STATE=tmp))

    def dead_pid(self):
        """A pid that is certainly not running, so liveness is unambiguous."""
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait(timeout=30)
        return child.pid

    def test_doctor_runs_from_the_cli_and_reports_failing_checks(self):
        # Regression: doctor.main() reused control.py's argv and crashed with
        # "unrecognized arguments: doctor" while every unit test stayed green.
        with tempfile.TemporaryDirectory() as tmp:
            run = self.run_doctor(tmp,
                'doctor.scheduled_task_state = lambda name=None, powershell=None: '
                '{"registered": False, "state": None, "last_result": None}\n'
                'doctor._codex_running = lambda probe=None: True\n'
                'finish()')
            self.assertNotIn('unrecognized arguments', run.stderr)
            report = json.loads(run.stdout)
            self.assertIn('checks', report)
            self.assertFalse(report['ok'])
            self.assertIn('startup', report['problems'])
            self.assertIn('listener', report['problems'])
            self.assertEqual(run.returncode, 1)

    def test_doctor_exit_code_is_zero_when_every_check_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A genuinely live pid plus a reachable UI runtime, so the real
            # liveness check and the real file checks both run.
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
            try:
                state = Path(tmp)
                now = time.time()
                (state / 'lifecycle.pid').write_text(str(child.pid), encoding='ascii')
                (state / 'lifecycle-status.json').write_text(
                    json.dumps({'checked_at': now, 'last_heartbeat': now}), encoding='utf-8')
                marker = state / 'ui-runtime' / 'PySide6' / 'QtWidgets.pyd'
                marker.parent.mkdir(parents=True, exist_ok=True); marker.write_bytes(b'x')
                run = self.run_doctor(tmp,
                    'doctor.scheduled_task_state = lambda name=None, powershell=None: '
                    '{"registered": True, "state": "Ready", "last_result": 0}\n'
                    'finish()')
                report = json.loads(run.stdout)
                self.assertEqual(report['problems'], [], report)
                self.assertEqual(run.returncode, 0)
            finally:
                child.kill(); child.wait(timeout=10)

    def test_doctor_distinguishes_a_dead_listener_from_a_closed_codex(self):
        # The same state must be a problem while Codex runs and fine when it does
        # not; conflating the two is what hid the failure for hours.
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'lifecycle.pid').write_text(str(self.dead_pid()), encoding='ascii')
            (state / 'lifecycle-status.json').write_text(
                json.dumps({'last_heartbeat': time.time()}), encoding='utf-8')
            dead = self.run_doctor(tmp,
                'doctor.scheduled_task_state = lambda name=None, powershell=None: '
                '{"registered": True, "state": "Ready", "last_result": 0}\n'
                'doctor._codex_running = lambda probe=None: True\n'
                'finish()')
            self.assertEqual(json.loads(dead.stdout)['problems'], ['listener'])
            closed = self.run_doctor(tmp,
                'doctor.scheduled_task_state = lambda name=None, powershell=None: '
                '{"registered": True, "state": "Ready", "last_result": 0}\n'
                'doctor._codex_running = lambda probe=None: False\n'
                'finish()')
            self.assertEqual(json.loads(closed.stdout)['problems'], [])


if __name__=='__main__':unittest.main()
