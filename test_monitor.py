import tempfile
import unittest
from pathlib import Path
from test_core import usage, task, weekly_usage
from monitor import Store, Monitor


class FakeDesktop:
    """Only replaces the external desktop; real state machine and SQLite run."""
    def __init__(self):
        self.usage=usage(); self.detail=task(); self.sent=[]; self.fail_send=False
        self.reads=0; self.change_on_recheck=False
        self.list_status='idle'
    def call(self,tool,**args):
        if tool=='get_usage_limits': return self.usage
        if tool=='list_threads':
            return {'threads':[{'id':'thread-a','kind':'codex','hostId':'local','status':self.list_status}]}
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

    def test_weekly_only_recovery_after_plan_change_sends_once(self):
        self.desktop.usage=weekly_usage(100,False)
        self.monitor.tick();self.assertEqual(self.desktop.sent,[])
        self.desktop.usage=weekly_usage(40,True)
        self.monitor.tick();self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertEqual(self.desktop.sent[0]['threadId'],'thread-a')

    def test_switching_from_pro_weekly_only_back_to_plus_checks_both_windows(self):
        self.desktop.usage=weekly_usage(40,True)
        report=self.monitor.tick()
        self.assertTrue(report['usage']['weekly_only'])
        self.assertEqual(len(self.desktop.sent),1)
        self.desktop.detail=task(turn='plus-turn')
        for short,weekly in [(100,20),(0,100)]:
            self.desktop.usage=usage(short,weekly)
            self.desktop.usage['ordinaryUsageAllowed']=True
            self.desktop.usage['rateLimitsByLimitId']['codex']['planType']='plus'
            report=self.monitor.tick()
            self.assertFalse(report['usage']['weekly_only'])
            self.assertEqual(len(self.desktop.sent),1)
        self.desktop.usage=usage(0,20)
        self.desktop.usage['rateLimitsByLimitId']['codex']['planType']='plus'
        self.monitor.tick();self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),2)
        self.assertEqual({row['key'] for row in self.store.attempts()},
                         {'thread-a:turn-a','thread-a:plus-turn'})

    def test_reset_credit_recovery_is_polled_before_original_reset_time(self):
        # A credit is redeemed independently of the previously advertised timer.
        clock=[120.0];queried=[]
        self.monitor.clock=lambda:clock[0]
        original=self.desktop.call
        def call(tool,**args):
            if tool=='get_usage_limits':
                queried.append(clock[0])
                return usage(100 if clock[0]<123 else 0,weekly=20,reset=900)
            return original(tool,**args)
        self.desktop.call=call
        report=self.monitor.tick()
        self.assertEqual(self.desktop.sent,[])
        self.assertLessEqual(report['next_poll_seconds'],5)
        clock[0]+=report['next_poll_seconds']
        self.monitor.tick();self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertEqual(self.desktop.sent[0]['threadId'],'thread-a')
        self.assertLess(queried[-1],900)

    def test_multiple_waiting_tasks_drain_without_another_normal_poll_interval(self):
        self.desktop.usage=usage(0)
        original=self.desktop.call
        def call(tool,**args):
            if tool=='list_threads':
                return {'threads':[{'id':key,'kind':'codex'} for key in ['thread-a','thread-b']]}
            if tool=='read_thread':
                detail=task();detail['thread']['id']=args['threadId']
                return detail
            return original(tool,**args)
        self.desktop.call=call
        report=self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertLessEqual(report['next_poll_seconds'],1)
        self.monitor.tick();self.monitor.tick()
        self.assertEqual([sent['threadId'] for sent in self.desktop.sent],['thread-a','thread-b'])

    def test_pending_failure_keeps_fast_polling_after_temporary_quota_read_error(self):
        self.monitor.tick()
        original=self.desktop.call
        def call(tool,**args):
            if tool=='get_usage_limits':raise TimeoutError('temporary desktop error')
            return original(tool,**args)
        self.desktop.call=call
        report=self.monitor.tick()
        self.assertFalse(report['connected'])
        self.assertEqual(self.desktop.sent,[])
        self.assertLessEqual(report['next_poll_seconds'],5)
        report=self.monitor.tick()
        self.assertLessEqual(report['next_poll_seconds'],5)

    def test_idle_or_paused_monitor_keeps_normal_polling(self):
        self.desktop.detail=task('completed');self.desktop.usage=usage(0)
        self.assertEqual(self.monitor.tick()['next_poll_seconds'],30)
        self.desktop.detail=task();self.store.set_enabled(False)
        self.assertEqual(self.monitor.tick()['next_poll_seconds'],30)

    def test_initial_quota_read_error_still_detects_waiting_task_without_using_cached_quota(self):
        from core import normalize_usage
        self.store.report({'usage':normalize_usage(usage(0),119),'candidates':[]})
        original=self.desktop.call
        def call(tool,**args):
            if tool=='get_usage_limits':raise TimeoutError()
            return original(tool,**args)
        self.desktop.call=call
        report=self.monitor.tick()
        self.assertFalse(report['connected'])
        self.assertEqual(len(report['candidates']),1)
        self.assertEqual(report['candidates'][0]['thread_id'],'thread-a')
        self.assertLessEqual(report['next_poll_seconds'],5)
        self.assertEqual(self.desktop.sent,[])
    def test_no_remaining_local_task_waits_without_sending(self):
        from bridge import NoContextError
        def call(tool,**args):raise NoContextError('No local task')
        self.desktop.call=call
        report=self.monitor.tick()
        self.assertFalse(report['connected'])
        self.assertTrue(report['waiting_for_context'])
        self.assertEqual(report['context_mode'],'automatic')
        self.assertEqual(self.desktop.sent,[])
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
    def test_supplementary_user_turn_replaces_old_waiting_turn(self):
        self.monitor.tick()
        self.desktop.detail=task(turn='supplementary-user-turn')
        self.monitor.tick()
        self.desktop.usage=usage(0)
        self.monitor.tick();self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertEqual([a['key'] for a in self.store.attempts()],['thread-a:supplementary-user-turn'])
    def test_user_supplement_during_final_quota_read_defers_old_turn(self):
        self.desktop.usage=usage(0)
        original=self.desktop.call;usage_reads=[]
        def call(tool,**args):
            result=original(tool,**args)
            if tool=='get_usage_limits':
                usage_reads.append(1)
                if len(usage_reads)==2:self.desktop.detail=task(turn='supplementary-user-turn')
            return result
        self.desktop.call=call
        self.monitor.tick()
        self.assertEqual(self.desktop.sent,[])
        self.monitor.tick();self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertEqual([a['key'] for a in self.store.attempts()],['thread-a:supplementary-user-turn'])
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
    def test_pause_during_final_task_read_blocks_dispatch(self):
        self.desktop.usage=usage(0)
        original=self.desktop.call;reads=[]
        def call(tool,**args):
            result=original(tool,**args)
            if tool=='read_thread':
                reads.append(1)
                if len(reads)==3:self.store.set_enabled(False)
            return result
        self.desktop.call=call
        self.monitor.tick()
        self.assertEqual(self.desktop.sent,[])
        self.assertEqual(self.store.attempts(),[])
    def test_final_task_read_cannot_outlive_fresh_quota(self):
        self.desktop.usage=usage(0)
        now=[120.0];self.monitor.clock=lambda:now[0]
        original=self.desktop.call;reads=[]
        def call(tool,**args):
            result=original(tool,**args)
            if tool=='read_thread':
                reads.append(1)
                if len(reads)==3:now[0]+=61
            return result
        self.desktop.call=call
        self.monitor.tick()
        self.assertEqual(self.desktop.sent,[])
        self.assertEqual(self.store.attempts(),[])

    def test_error_badge_does_not_hide_a_quota_failed_task(self):
        self.desktop.list_status='error'
        self.desktop.detail['thread']['status']={'type':'systemError'}
        self.monitor.tick()
        self.desktop.usage=usage(0)
        report=self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertEqual(report['attempts'][0]['status'],'sent')

    def test_one_unreadable_task_does_not_block_another(self):
        self.desktop.usage=usage(0)
        original=self.desktop.call
        def call(tool,**args):
            if tool=='list_threads':
                return {'threads':[{'id':key,'kind':'codex'} for key in ['broken','thread-a']]}
            if tool=='read_thread' and args['threadId']=='broken':raise TimeoutError()
            return original(tool,**args)
        self.desktop.call=call
        report=self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertEqual(report['inspections'][0]['reason'],'read_failed')

    def test_background_worker_recovers_after_reset_credit_without_manual_refresh(self):
        from unittest.mock import patch
        from monitor import run_worker
        clock=[120.0];queried=[]
        class Timer:
            def is_set(self):return clock[0]>=127
            def wait(self,seconds):clock[0]+=seconds;return self.is_set()
        original=self.desktop.call
        def call(tool,**args):
            if tool=='get_usage_limits':
                queried.append(clock[0])
                return usage(0 if clock[0]>=123 else 100,reset=900)
            return original(tool,**args)
        self.desktop.call=call;self.desktop.close=lambda:None
        with patch('monitor.Store',lambda:Store(self.path)), \
             patch('bridge.Desktop',lambda:self.desktop), \
             patch('monitor.Monitor',lambda store,desktop:Monitor(store,desktop,clock=lambda:clock[0])), \
             patch('monitor.time.monotonic',lambda:clock[0]):
            run_worker(Timer())
        self.assertEqual(queried[:3],[120,125,125])
        self.assertEqual(len(self.desktop.sent),1)
        self.assertEqual(self.desktop.sent[0]['threadId'],'thread-a')
        self.assertFalse((self.path/'refresh.request').exists())

    def test_background_worker_queries_at_reset_without_manual_refresh(self):
        from unittest.mock import patch
        from monitor import run_worker
        clock=[197.0];queried=[]
        class Timer:
            def is_set(self):return clock[0]>=202
            def wait(self,seconds):clock[0]+=seconds;return self.is_set()
        original=self.desktop.call
        def call(tool,**args):
            if tool=='get_usage_limits':
                queried.append(clock[0])
                return usage(0 if clock[0]>=200 else 100,reset=500 if clock[0]>=200 else 200)
            return original(tool,**args)
        self.desktop.call=call;self.desktop.close=lambda:None
        self.desktop.list_status='error'
        self.desktop.detail['thread']['status']={'type':'systemError'}
        with patch('monitor.Store',lambda:Store(self.path)), \
             patch('bridge.Desktop',lambda:self.desktop), \
             patch('monitor.Monitor',lambda store,desktop:Monitor(store,desktop,clock=lambda:clock[0])), \
             patch('monitor.time.monotonic',lambda:clock[0]):
            run_worker(Timer())
        self.assertEqual(queried[:3],[197,200,200])
        self.assertEqual(len(self.desktop.sent),1)
        self.assertFalse((self.path/'refresh.request').exists())

if __name__=='__main__': unittest.main()
