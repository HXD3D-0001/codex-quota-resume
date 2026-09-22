import unittest
import tempfile,sys,json,threading,time
from pathlib import Path
import lifecycle
from lifecycle import (Lifecycle,Launcher,is_desktop_path,append_log,write_status,
                       serve,supervise,LOG_NAME)
from monitor import SingleInstance

class Child:
    def __init__(self):self.ended=False
    def poll(self):return 0 if self.ended else None

class _FakeProcess:
    def __init__(self):self.pid=4321
    def poll(self):return None

class _FakeLauncher:
    """Stands in for the overlay child so no window or subprocess is created."""
    def __init__(self,directory,root,command=None):
        self.directory,self.root=Path(directory),Path(root);self.started=0;self.stopped=0
    def start(self):
        (self.directory/'stop.request').unlink(missing_ok=True);self.started+=1;return _FakeProcess()
    def stop(self,child):self.stopped+=1

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.started=[];self.stopped=[]
        def start():
            child=Child();self.started.append(child);return child
        self.lifecycle=Lifecycle(start,self.stopped.append)
    def test_closed_start_open_close_reopen(self):
        for roots in [set(),{1},{1},set(),set(),{2}]:self.lifecycle.tick(roots)
        self.assertEqual(len(self.started),2)
        self.assertEqual(self.stopped,[self.started[0]])
    def test_multiple_windows_keep_running_until_last_app_exits(self):
        for roots in [{1},{1,2},{2}]:self.lifecycle.tick(roots)
        self.assertEqual(len(self.started),1);self.assertEqual(self.stopped,[])
        self.lifecycle.tick(set());self.assertEqual(len(self.stopped),1)
    def test_manual_exit_is_honored_until_next_app_launch(self):
        # A manual exit is recognised by the stop request the tray writes.
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            supervisor=Lifecycle(self.lifecycle.start,self.lifecycle.stop,root)
            supervisor.tick({1});self.started[0].ended=True
            (root/'lifecycle-stop.request').touch()
            supervisor.tick({1});supervisor.tick({1})
            self.assertEqual(len(self.started),1)
            supervisor.tick({2});self.assertEqual(len(self.started),2)
    def test_supervisor_exit_stops_owned_child(self):
        self.lifecycle.tick({1});self.lifecycle.close()
        self.assertEqual(self.stopped,self.started)

    def test_tray_stop_file_suppresses_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            supervisor=Lifecycle(self.lifecycle.start,self.lifecycle.stop,directory)
            supervisor.tick({1});self.started[0].ended=True
            (directory/'stop.request').touch()
            supervisor.tick({1});supervisor.tick({1})
            self.assertEqual(len(self.started),1)

    def test_serve_reads_stop_signals_from_state_not_source_directory(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)/'state';directory.mkdir()
            source=Path(temporary)/'source';source.mkdir()
            with patch('lifecycle.Lifecycle',wraps=Lifecycle) as factory, \
                 patch('lifecycle.Launcher',_FakeLauncher), \
                 patch('lifecycle.desktop_processes',return_value=set()):
                serve(directory,source,once=True)
            self.assertEqual(factory.call_args.args[2],directory)
    def test_cli_and_chatgpt_are_not_codex_desktop(self):
        self.assertTrue(is_desktop_path(r'C:\Program Files\WindowsApps\OpenAI.Codex_1_x64__test\app\ChatGPT.exe'))
        for path in [r'C:\OpenAI\Codex\bin\123\codex.exe',r'C:\WindowsApps\OpenAI.ChatGPT_1\ChatGPT.exe',r'C:\other\Codex.exe']:
            self.assertFalse(is_desktop_path(path))

    def test_a_crashed_child_is_restarted_while_codex_stays_open(self):
        # Regression: every child exit used to count as a manual dismissal, so
        # killing the overlay left the bar gone until Codex was reopened.
        supervisor=Lifecycle(self.lifecycle.start,self.lifecycle.stop,tempfile.gettempdir())
        supervisor.tick({1})
        self.started[0].ended=True
        supervisor.tick({1})
        self.assertEqual(len(self.started),2)
        self.assertEqual(self.stopped,[])

    def test_an_explicit_stop_is_honoured_for_the_rest_of_the_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'lifecycle-stop.request').touch()
            supervisor=Lifecycle(self.lifecycle.start,self.lifecycle.stop,root)
            supervisor.tick({1})
            self.started[0].ended=True
            for _ in range(3):supervisor.tick({1})
            self.assertEqual(len(self.started),1)
            supervisor.tick({2})  # a new Codex launch clears the dismissal
            self.assertEqual(len(self.started),2)

    def test_real_child_starts_stops_restarts_and_lock_prevents_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            command=[sys.executable,'-c',"from pathlib import Path; import time\nwhile not Path('stop.request').exists(): time.sleep(.05)"]
            launcher=Launcher(directory,directory,command)
            lifecycle=Lifecycle(launcher.start,launcher.stop)
            try:
                with SingleInstance(directory,name='lifecycle'):
                    with self.assertRaisesRegex(RuntimeError,'already running'):
                        with SingleInstance(directory,name='lifecycle'):pass
                    lifecycle.tick({10});first=lifecycle.child
                    self.assertIsNone(first.poll())
                    lifecycle.tick(set());self.assertIsNotNone(first.poll())
                    lifecycle.tick({11});second=lifecycle.child
                    self.assertNotEqual(first.pid,second.pid);self.assertIsNone(second.poll())
                    lifecycle.close();self.assertIsNotNone(second.poll())
            finally:lifecycle.close()


class SelfHealingTests(unittest.TestCase):
    """The listener used to die silently and only come back at the next logon."""

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.directory=Path(self.temporary.name)
        self.real_launcher,self.real_processes=lifecycle.Launcher,lifecycle.desktop_processes
        lifecycle.Launcher=_FakeLauncher
        lifecycle.desktop_processes=lambda: {1}
    def tearDown(self):
        lifecycle.Launcher,lifecycle.desktop_processes=self.real_launcher,self.real_processes
        self.temporary.cleanup()
    def log_text(self):
        path=self.directory/LOG_NAME
        return path.read_text(encoding='utf-8') if path.exists() else ''

    def test_crash_is_logged_and_the_listener_comes_back_by_itself(self):
        patches=(lifecycle.RESTART_DELAY_SECONDS,lifecycle.MAX_FAST_CRASHES)
        lifecycle.RESTART_DELAY_SECONDS,lifecycle.MAX_FAST_CRASHES=0.05,10
        calls=[]
        def flaky():
            calls.append(1)
            if len(calls)==1:raise RuntimeError('injected crash')
        try:
            restarts=supervise(self.directory,self.directory,serve_once=flaky)
        finally:
            lifecycle.RESTART_DELAY_SECONDS,lifecycle.MAX_FAST_CRASHES=patches
        self.assertEqual(restarts,1)
        self.assertEqual(len(calls),2)
        self.assertIn('restarting listener',self.log_text())
        self.assertIn('RuntimeError',self.log_text())

    def test_requested_stop_is_not_treated_as_a_crash(self):
        (self.directory/'lifecycle-stop.request').touch()
        calls=[]
        def once():calls.append(1)
        self.assertEqual(supervise(self.directory,self.directory,serve_once=once),0)
        self.assertEqual(len(calls),1)
        self.assertIn('stop requested',self.log_text())

    def test_repeated_fast_crashes_stop_instead_of_spinning(self):
        patches=(lifecycle.RESTART_DELAY_SECONDS,lifecycle.MAX_FAST_CRASHES)
        lifecycle.RESTART_DELAY_SECONDS,lifecycle.MAX_FAST_CRASHES=0.01,3
        calls=[]
        def broken():
            calls.append(1);raise RuntimeError('always broken')
        try:
            supervise(self.directory,self.directory,serve_once=broken)
        finally:
            lifecycle.RESTART_DELAY_SECONDS,lifecycle.MAX_FAST_CRASHES=patches
        self.assertEqual(len(calls),3)  # initial run plus the capped retries
        self.assertIn('giving up',self.log_text())

    def test_serve_publishes_a_heartbeat_and_supervises_the_child(self):
        errors=[]
        def worker():
            try:serve(self.directory,self.directory)
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=worker,daemon=True);thread.start()
        status={}
        for _ in range(100):
            try:status=json.loads((self.directory/'lifecycle-status.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):time.sleep(0.05);continue
            if status.get('monitor_running'):break
            time.sleep(0.05)
        (self.directory/'lifecycle-stop.request').touch()
        thread.join(timeout=10)
        self.assertEqual(errors,[])
        self.assertTrue(status.get('desktop_running'))
        self.assertTrue(status.get('monitor_running'))
        self.assertEqual(status.get('pid'),__import__('os').getpid())
        self.assertLess(abs(status['last_heartbeat']-time.time()),30)

    def test_a_stale_stop_request_does_not_kill_the_next_launch(self):
        # Regression: an old stop.request made the overlay quit right after
        # Codex was opened, which looked exactly like "the bar never appears".
        (self.directory/'stop.request').touch()
        (self.directory/'lifecycle-stop.request').touch()
        errors=[]
        def worker():
            try:serve(self.directory,self.directory)
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=worker,daemon=True);thread.start()
        started=False
        for _ in range(100):
            try:started=json.loads((self.directory/'lifecycle-status.json').read_text(encoding='utf-8')).get('monitor_running',False)
            except (OSError,ValueError):pass
            if started:break
            time.sleep(0.05)
        (self.directory/'lifecycle-stop.request').touch()
        thread.join(timeout=10)
        self.assertEqual(errors,[])
        self.assertTrue(started,'the child must start despite a stale stop.request')
        self.assertFalse((self.directory/'stop.request').exists())

    def test_logging_survives_an_unwritable_directory(self):
        blocked=self.directory/'blocked'
        blocked.write_text('not a directory',encoding='utf-8')
        self.assertFalse(append_log(blocked,'ignored'))

    def test_log_rotates_instead_of_growing_forever(self):
        patches=lifecycle.LOG_MAX_BYTES
        lifecycle.LOG_MAX_BYTES=200
        try:
            for index in range(60):append_log(self.directory,'line %d'%index)
        finally:
            lifecycle.LOG_MAX_BYTES=patches
        self.assertTrue((self.directory/(LOG_NAME+'.1')).exists())
        self.assertLess((self.directory/LOG_NAME).stat().st_size,400)

    def test_status_file_is_written_atomically(self):
        write_status(self.directory,{'checked_at':1,'last_heartbeat':1})
        self.assertEqual(json.loads((self.directory/'lifecycle-status.json').read_text())['checked_at'],1)
        self.assertFalse((self.directory/'lifecycle-status.tmp').exists())


if __name__=='__main__':unittest.main()
