import tempfile
import threading
import unittest
from pathlib import Path

from monitor import Store, Monitor, CONTINUE_PROMPT
from test_core import task, usage
from test_monitor import FakeDesktop


class DraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.store=Store(self.path,now=100)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def require_drafts(self):
        self.assertTrue(callable(getattr(self.store,'save_draft',None)), 'Local recovery prompts must be saved independently of quota')
    def test_multiple_task_drafts_persist_independently_without_report_content(self):
        self.require_drafts()
        self.store.save_draft('a:turn','任务 A：先修复输入校验。',120)
        self.store.save_draft('b:turn','任务 B：更新说明文档。',120)
        other=Store(self.path)
        try:
            self.assertEqual(other.draft('a:turn'),'任务 A：先修复输入校验。')
            self.assertEqual(other.draft('b:turn'),'任务 B：更新说明文档。')
            self.assertEqual({r['key'] for r in other.draft_rows()},{'a:turn','b:turn'})
            self.assertNotIn('任务 A',str(other.draft_rows()))
            other.save_draft('a:turn','  ',121)
            self.assertEqual(self.store.draft('a:turn'),'')
            self.assertEqual(self.store.draft('b:turn'),'任务 B：更新说明文档。')
        finally:other.close()
    def test_draft_cannot_be_changed_after_dispatch_is_claimed(self):
        self.require_drafts()
        self.store.save_draft('a:turn','original',120)
        self.assertTrue(self.store.claim('a:turn','a',120))
        with self.assertRaises(ValueError):self.store.save_draft('a:turn','too late',121)
        self.store.finish('a:turn','uncertain',122)
        self.assertEqual(self.store.draft('a:turn'),'original')
        self.store.finish('a:turn','sent',123)
        self.assertEqual(self.store.draft('a:turn'),'')
    def test_editing_lease_blocks_only_corresponding_task_and_expires(self):
        self.require_drafts()
        self.store.begin_edit('a:turn','editor',120)
        self.assertFalse(self.store.claim('a:turn','a',121))
        self.assertFalse(self.store.claim('a:new-turn','a',121))
        self.assertTrue(self.store.claim('b:turn','b',121))
        self.assertTrue(self.store.claim('a:turn','a',151))
        with self.assertRaises(ValueError):self.store.begin_edit('a:turn','editor',152)
    def test_editor_tokens_cannot_release_or_replace_another_editor(self):
        self.require_drafts()
        self.store.begin_edit('a:turn','one',120)
        with self.assertRaises(ValueError):self.store.begin_edit('a:turn','two',121)
        self.store.end_edit('a:turn','two')
        self.assertFalse(self.store.claim('a:turn','a',122))
        self.store.begin_edit('a:turn','one',140)
        self.assertFalse(self.store.claim('a:turn','a',151))
        self.store.end_edit('a:turn','one')
        self.assertTrue(self.store.claim('a:turn','a',152))
    def test_lease_and_dispatch_cannot_both_win_across_connections(self):
        self.require_drafts()
        gate=threading.Barrier(2);results=[];errors=[]
        def action(edit):
            store=Store(self.path)
            try:
                gate.wait(timeout=5)
                if edit:
                    try:store.begin_edit('a:turn','editor',120);results.append(True)
                    except ValueError:results.append(False)
                else:results.append(store.claim('a:turn','a',120))
            except Exception as e:errors.append(type(e).__name__)
            finally:store.close()
        threads=[threading.Thread(target=action,args=(edit,)) for edit in (True,False)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=10)
        self.assertEqual(errors,[])
        self.assertEqual(sorted(results),[False,True])
    def test_size_and_key_validation_do_not_damage_existing_draft(self):
        self.require_drafts()
        self.store.save_draft('a:turn','keep',120)
        with self.assertRaises(ValueError):self.store.save_draft('a:turn','x'*20001,121)
        with self.assertRaises(ValueError):self.store.save_draft('not-a-turn','text',121)
        self.assertEqual(self.store.draft('a:turn'),'keep')


class DraftDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.store=Store(self.path,now=100);self.desktop=FakeDesktop()
        self.monitor=Monitor(self.store,self.desktop,clock=lambda:120)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def save(self,key,text):
        self.assertTrue(callable(getattr(self.store,'save_draft',None)), 'Recovery must include locally queued supplementary prompts')
        self.store.save_draft(key,text,119)
    def test_two_tasks_receive_only_their_own_prompt_once(self):
        original=self.desktop.call
        def call(tool,**args):
            if tool=='list_threads':return {'threads':[{'id':x,'kind':'codex'} for x in ['a','b']]}
            if tool=='read_thread':
                detail=task();detail['thread']['id']=args['threadId'];return detail
            return original(tool,**args)
        self.desktop.call=call
        self.save('a:turn-a','FOR_A');self.save('b:turn-a','FOR_B')
        self.monitor.tick();self.assertEqual(self.desktop.sent,[])
        self.desktop.usage=usage(0)
        self.monitor.tick();self.monitor.tick();self.monitor.tick()
        self.assertEqual([x['threadId'] for x in self.desktop.sent],['a','b'])
        self.assertIn('FOR_A',self.desktop.sent[0]['prompt']);self.assertNotIn('FOR_B',self.desktop.sent[0]['prompt'])
        self.assertIn('FOR_B',self.desktop.sent[1]['prompt']);self.assertNotIn('FOR_A',self.desktop.sent[1]['prompt'])
        self.assertTrue(self.desktop.sent[0]['prompt'].startswith(CONTINUE_PROMPT))
        self.assertEqual(self.store.draft_rows(),[])
    def test_new_turn_never_receives_previous_turn_draft(self):
        self.save('thread-a:turn-a','OLD_REQUIREMENT')
        self.desktop.detail=task(turn='turn-b');self.desktop.usage=usage(0)
        self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertNotIn('OLD_REQUIREMENT',self.desktop.sent[0]['prompt'])
        self.assertEqual(self.store.draft('thread-a:turn-a'),'OLD_REQUIREMENT')
    def test_unknown_delivery_keeps_prompt_and_never_retries(self):
        self.save('thread-a:turn-a','LOCAL_NOTE')
        self.desktop.usage=usage(0);self.desktop.fail_send=True
        self.monitor.tick();self.monitor.tick()
        self.assertEqual(len(self.desktop.sent),1)
        self.assertEqual(self.store.draft('thread-a:turn-a'),'LOCAL_NOTE')
        self.assertEqual(self.store.attempts()[0]['status'],'uncertain')
    def test_editing_draft_blocks_resume_until_saved_and_released(self):
        self.save('thread-a:turn-a','before edit')
        self.desktop.usage=usage(0);self.store.begin_edit('thread-a:turn-a','editor',119)
        self.monitor.tick();self.assertEqual(self.desktop.sent,[])
        self.store.save_draft('thread-a:turn-a','after edit',120)
        self.store.end_edit('thread-a:turn-a','editor')
        self.monitor.tick()
        self.assertIn('after edit',self.desktop.sent[0]['prompt'])
        self.assertNotIn('before edit',self.desktop.sent[0]['prompt'])
    def test_editing_one_task_does_not_block_another_task(self):
        original=self.desktop.call
        def call(tool,**args):
            if tool=='list_threads':return {'threads':[{'id':x,'kind':'codex'} for x in ['a','b']]}
            if tool=='read_thread':
                detail=task();detail['thread']['id']=args['threadId'];return detail
            return original(tool,**args)
        self.desktop.call=call;self.desktop.usage=usage(0)
        self.save('a:turn-a','FOR_A');self.save('b:turn-a','FOR_B')
        self.store.begin_edit('a:turn-a','editor',119)
        self.monitor.tick()
        self.assertEqual([x['threadId'] for x in self.desktop.sent],['b'])
        self.assertEqual(self.store.draft('a:turn-a'),'FOR_A')
        self.store.end_edit('a:turn-a','editor');self.monitor.tick()
        self.assertEqual([x['threadId'] for x in self.desktop.sent],['b','a'])
    def test_draft_neither_authorizes_completed_task_nor_overrides_pause(self):
        self.save('thread-a:turn-a','LOCAL_NOTE')
        self.desktop.detail=task('completed');self.desktop.usage=usage(0)
        self.monitor.tick();self.assertEqual(self.desktop.sent,[])
        self.desktop.detail=task();self.store.set_enabled(False)
        self.monitor.tick();self.assertEqual(self.desktop.sent,[])
        self.assertEqual(self.store.draft('thread-a:turn-a'),'LOCAL_NOTE')
    def test_private_draft_text_never_enters_status_report(self):
        self.save('thread-a:turn-a','PRIVATE_LOCAL_SUPPLEMENT')
        report=self.monitor.tick()
        self.assertNotIn('PRIVATE_LOCAL_SUPPLEMENT',str(report))
        self.assertNotIn('PRIVATE_LOCAL_SUPPLEMENT',(self.path/'usage.json').read_text(encoding='utf-8'))


if __name__=='__main__':unittest.main()
