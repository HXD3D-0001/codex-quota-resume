import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import bridge


class IndexFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def index(self, version=5, rows=()):
        path = self.root / f'state_{version}.sqlite'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT, archived INTEGER, updated_at INTEGER)')
            db.executemany('INSERT INTO threads VALUES (?,?,?,?)', rows)
        return path

    def contexts(self):
        resolver = getattr(bridge, 'local_context_ids', None)
        self.assertTrue(callable(resolver), 'Background context discovery must not require a management chat')
        return resolver(self.root)

class ContextIndexTests(IndexFixture, unittest.TestCase):
    def test_missing_index_does_not_create_database(self):
        self.assertEqual(self.contexts(), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_only_existing_unarchived_root_tasks_are_selected(self):
        path = self.index(rows=[('old', 'vscode', 0, 1), ('new', 'vscode', 0, 4),
                                ('archived', 'vscode', 1, 5),
                                ('child', '{"subagent":{"other":"guardian"}}', 0, 6)])
        before = path.read_bytes()
        self.assertEqual(self.contexts(), ['new', 'old'])
        self.assertEqual(path.read_bytes(), before)

    def test_newest_index_is_authoritative_even_when_empty(self):
        self.index(5, [('deleted', 'vscode', 0, 1)])
        self.index(10)
        self.assertEqual(self.contexts(), [])

    def test_corrupt_newest_index_does_not_resurrect_older_tasks(self):
        self.index(5, [('deleted', 'vscode', 0, 1)])
        (self.root / 'state_10.sqlite').write_bytes(b'not sqlite')
        self.assertEqual(self.contexts(), [])
    def test_titles_are_read_only_and_limited_to_requested_tasks(self):
        path=self.index(rows=[('a','vscode',0,1),('b','vscode',0,2)])
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('ALTER TABLE threads ADD COLUMN title TEXT')
            db.execute('UPDATE threads SET title=id || ? ',(' title',))
        self.assertTrue(callable(getattr(bridge,'local_task_titles',None)), 'Task picker needs unambiguous local task titles')
        before=path.read_bytes()
        self.assertEqual(bridge.local_task_titles(['b'],self.root),{'b':'b title'})
        self.assertEqual(path.read_bytes(),before)


class AutomaticContextTests(IndexFixture, unittest.TestCase):
    def desktop(self):
        return bridge.Desktop(contexts=lambda: self.contexts())

    def test_deleting_previous_context_switches_without_restarting(self):
        path = self.index(rows=[('first', 'vscode', 0, 2), ('second', 'vscode', 0, 1)])
        desktop = self.desktop()
        calls = []
        def exchange(tool, owner, **args):
            calls.append(owner)
            return {}
        with patch.object(desktop, '_exchange', side_effect=exchange):
            desktop.call('get_usage_limits')
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('DELETE FROM threads WHERE id=?', ('first',))
            desktop.call('list_threads', limit=30)
        self.assertEqual(calls, ['first', 'second'])

    def test_read_context_rejection_can_fall_back_to_another_task(self):
        self.index(rows=[('gone', 'vscode', 0, 2), ('valid', 'vscode', 0, 1)])
        desktop = self.desktop()
        def exchange(tool, owner, **args):
            if owner == 'gone': raise bridge.DesktopCallError('Codex app tool request failed')
            return {'ok': True}
        with patch.object(desktop, '_exchange', side_effect=exchange) as request:
            self.assertEqual(desktop.call('get_usage_limits'), {'ok': True})
            self.assertEqual(request.call_count, 2)

    def test_empty_index_waits_then_discovers_new_task(self):
        path = self.index()
        desktop = self.desktop()
        with patch.object(desktop, '_exchange', return_value={}) as request:
            with self.assertRaises(bridge.NoContextError): desktop.call('get_usage_limits')
            request.assert_not_called()
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('INSERT INTO threads VALUES (?,?,?,?)', ('new', 'vscode', 0, 1))
            desktop.call('get_usage_limits')
            self.assertEqual(request.call_args.args, ('get_usage_limits', 'new'))

    def test_send_uses_target_context_and_never_retries_lost_response(self):
        self.index(rows=[('unrelated', 'vscode', 0, 1)])
        desktop = self.desktop()
        with patch.object(desktop, '_exchange', side_effect=TimeoutError('unknown delivery')) as request:
            with self.assertRaises(TimeoutError):
                desktop.call('send_message_to_thread', threadId='target', hostId='local', prompt='continue')
        request.assert_called_once_with('send_message_to_thread', 'target', threadId='target', hostId='local', prompt='continue')

    def test_legacy_owner_is_not_required_or_reused(self):
        self.index(rows=[('valid', 'vscode', 0, 1)])
        desktop = bridge.Desktop('deleted-manager', contexts=lambda: self.contexts())
        with patch.object(desktop, '_exchange', return_value={}) as request:
            desktop.call('get_usage_limits')
        self.assertEqual(request.call_args.args, ('get_usage_limits', 'valid'))

    def test_transport_failure_is_not_retried_as_another_context(self):
        self.index(rows=[('one', 'vscode', 0, 2), ('two', 'vscode', 0, 1)])
        desktop = self.desktop()
        with patch.object(desktop, '_exchange', side_effect=TimeoutError('pipe unavailable')) as request:
            with self.assertRaises(TimeoutError): desktop.call('get_usage_limits')
        self.assertEqual(request.call_count, 1)
    def test_bounded_retries_rotate_past_three_stale_contexts(self):
        self.index(rows=[(name,'vscode',0,stamp) for name,stamp in
                         [('stale-1',4),('stale-2',3),('stale-3',2),('valid',1)]])
        desktop=self.desktop();calls=[]
        def exchange(tool,owner,**args):
            calls.append(owner)
            if owner!='valid':raise bridge.DesktopCallError('Codex app tool request failed')
            return {'ok':True}
        with patch.object(desktop,'_exchange',side_effect=exchange):
            with self.assertRaises(bridge.DesktopCallError):desktop.call('get_usage_limits')
            self.assertEqual(desktop.call('get_usage_limits'),{'ok':True})
        self.assertEqual(calls,['stale-1','stale-2','stale-3','valid'])


if __name__ == '__main__': unittest.main()
