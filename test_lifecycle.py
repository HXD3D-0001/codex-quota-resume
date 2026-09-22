import unittest
import tempfile,sys
from pathlib import Path
from lifecycle import Lifecycle,Launcher,is_desktop_path
from monitor import SingleInstance

class Child:
    def __init__(self):self.ended=False
    def poll(self):return 0 if self.ended else None

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
        self.lifecycle.tick({1});self.started[0].ended=True
        self.lifecycle.tick({1});self.lifecycle.tick({1})
        self.assertEqual(len(self.started),1)
        self.lifecycle.tick({2});self.assertEqual(len(self.started),2)
    def test_supervisor_exit_stops_owned_child(self):
        self.lifecycle.tick({1});self.lifecycle.close()
        self.assertEqual(self.stopped,self.started)
    def test_cli_and_chatgpt_are_not_codex_desktop(self):
        self.assertTrue(is_desktop_path(r'C:\Program Files\WindowsApps\OpenAI.Codex_1_x64__test\app\ChatGPT.exe'))
        for path in [r'C:\OpenAI\Codex\bin\123\codex.exe',r'C:\WindowsApps\OpenAI.ChatGPT_1\ChatGPT.exe',r'C:\other\Codex.exe']:
            self.assertFalse(is_desktop_path(path))

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

if __name__=='__main__':unittest.main()
