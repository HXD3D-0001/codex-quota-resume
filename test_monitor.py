import tempfile
import unittest
from pathlib import Path
from test_core import usage, task
from monitor import Store, Monitor


class FakeDesktop:
    """Only replaces the external desktop; real state machine and SQLite run."""
    def __init__(self):
        self.usage=usage(); self.detail=task(); self.sent=[]; self.fail_send=False
        self.reads=0; self.change_on_recheck=False
    def call(self,tool,**args):
        if tool=='get_usage_limits': return self.usage
        if tool=='list_threads':
            return {'threads':[{'id':'thread-a','kind':'codex','hostId':'local','status':'idle'}]}
        if tool=='read_thread':
            self.reads+=1
            if self.change_on_recheck and self.reads % 2 == 0: return task('completed',turn='turn-b')
            return self.detail
        if tool=='send_message_to_thread':
            self.sent.append(args)
            if self.fail_send: raise TimeoutError('Response lost after delivery')
            return {'status':'sent'}
        raise AssertionError(tool)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)
        self.store=Store(self.path,now=100)
        self.desktop=FakeDesktop()
        self.monitor=Monitor(self.store,self.desktop,clock=lambda:120)
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def test_exhausted_then_recovered_sends_once_even_after_restart(self):
        self.monitor.tick()
        self.assertEqual(self.desktop.sent,[])
        self.desktop.usage=usage(0)
        self.monitor.tick();self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertEqual(self.desktop.sent[0]['threadId'],'thread-a')
        self.assertNotIn('model',self.desktop.sent[0])
        self.store.close();self.store=Store(self.path,now=130)
        Monitor(self.store,self.desktop,clock=lambda:130).tick()
        self.assertEqual(len(self.desktop.sent),1)
    def test_ambiguous_send_never_auto_retries(self):
        self.desktop.usage=usage(0);self.desktop.fail_send=True
        self.monitor.tick();self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertEqual(self.store.attempts()[0]['status'],'uncertain')
    def test_changed_task_before_dispatch_is_not_sent(self):
        self.desktop.usage=usage(0);self.desktop.change_on_recheck=True
        self.monitor.tick()
        self.assertEqual(self.desktop.sent,[])
    def test_paused_monitor_refreshes_but_does_not_send(self):
        self.store.set_enabled(False);self.desktop.usage=usage(0)
        report=self.monitor.tick()
        self.assertEqual(self.desktop.sent,[])
        self.assertEqual(report['usage']['windows'][0]['used'],0)
    def test_old_failure_needs_manual_mark(self):
        self.desktop.detail=task(ended=90);self.desktop.usage=usage(0)
        self.monitor.tick();self.assertEqual(self.desktop.sent,[])
        self.store.mark('thread-a','turn-a')
        self.monitor.tick();self.assertEqual(len(self.desktop.sent),1)
    def test_new_failed_turn_can_resume_again(self):
        self.desktop.usage=usage(0);self.monitor.tick()
        self.desktop.detail=task(turn='turn-b')
        self.monitor.tick();self.assertEqual(len(self.desktop.sent),2)
    def test_generic_throttle_is_not_usage_exhaustion(self):
        self.desktop.detail=task(error='429: too many requests');self.desktop.usage=usage(0)
        self.monitor.tick();self.assertEqual(self.desktop.sent,[])
    def test_corrupt_usage_keeps_last_display_but_never_sends(self):
        self.monitor.tick();self.desktop.usage={}
        self.monitor.tick();self.assertEqual(self.desktop.sent,[])
    def test_disable_between_checks_blocks_dispatch(self):
        self.desktop.usage=usage(0)
        original=self.desktop.call
        def call(tool,**args):
            result=original(tool,**args)
            if tool=='read_thread': self.store.set_enabled(False)
            return result
        self.desktop.call=call
        self.monitor.tick();self.assertEqual(self.desktop.sent,[])

if __name__=='__main__': unittest.main()
