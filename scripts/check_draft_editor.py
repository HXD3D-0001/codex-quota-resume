"""Offline UI checks: disposable Store, synthetic tasks, no desktop calls."""
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from draft_editor import DraftEditor
from monitor import Store
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    keys = ['synthetic-task-a:synthetic-turn-a', 'synthetic-task-b:synthetic-turn-b']
    with tempfile.TemporaryDirectory(prefix='quota-draft-ui-') as temporary, ExitStack() as cleanup:
        store = Store(temporary)
        cleanup.callback(store.close)
        store.set_enabled(False)
        report = {'enabled': False, 'connected': True, 'candidates': [
            {'thread_id': key.split(':')[0], 'key': key} for key in keys]}
        store.report(report)
        titles = lambda ids: {task: '等待恢复的示例任务' for task in ids}
        dialog = DraftEditor(store=store, title_loader=titles)
        dialog.show()
        app.processEvents()
        assert dialog.parent() is None
        assert not dialog.windowFlags() & Qt.WindowTransparentForInput
        assert not dialog.windowFlags() & Qt.WindowDoesNotAcceptFocus
        assert not dialog.testAttribute(Qt.WA_TransparentForMouseEvents)
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            from glass_native import TRANSPARENT, NOACTIVATE
            user = ctypes.windll.user32
            user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            native_style = user.GetWindowLongPtrW(int(dialog.winId()), -20)
            assert not native_style & TRANSPARENT, 'editor HWND must accept clicks'
            assert not native_style & NOACTIVATE, 'editor HWND must accept focus'
        assert not dialog.editor.isReadOnly()
        dialog.editor.setFocus()
        app.processEvents()
        assert dialog.editor.hasFocus()
        from PySide6.QtTest import QTest
        QTest.keyClicks(dialog.editor, 'task A: keep this work')
        assert 'task A' in dialog.editor.toPlainText()
        first_text = dialog.editor.toPlainText()
        claimed = store.claimed
        def fail_b(key):
            if key == keys[1]:
                raise OSError('synthetic state-read failure')
            return claimed(key)
        with patch.object(store, 'claimed', side_effect=fail_b):
            dialog.task_picker.setCurrentIndex(1)
            assert dialog.editor.toPlainText() == '', 'unread B must not display A text'
            dialog.refresh_tasks()
        dialog.refresh_tasks()
        assert dialog.cache[keys[0]]['text'] == first_text
        assert dialog.cache[keys[1]]['text'] == ''
        assert not store.claim(keys[0], 'synthetic-task-a', time.time()), 'unsaved background task must remain protected'
        dialog.refresh_tasks()
        assert not store.claim(keys[0], 'synthetic-task-a', time.time()), 'refresh must renew background protection'
        dialog.editor.setPlainText('任务 B：仅修改第二个任务。')
        dialog.task_picker.setCurrentIndex(0)
        assert dialog.editor.toPlainText() == first_text
        with patch.object(store, 'claimed', side_effect=fail_b):
            dialog.task_picker.setCurrentIndex(1)
            assert dialog.editor.toPlainText() == '任务 B：仅修改第二个任务。', 'failed selection must still display B-owned text'
            dialog.refresh_tasks()
        dialog.refresh_tasks()
        assert dialog.cache[keys[0]]['text'] == first_text
        assert dialog.cache[keys[1]]['text'] == '任务 B：仅修改第二个任务。'
        dialog.task_picker.setCurrentIndex(0)
        dialog.save_current()
        assert store.draft(keys[0]) == first_text
        assert store.claim(keys[0], 'synthetic-task-a', time.time()), 'saving A must release A while B remains dirty'
        with store.db:
            store.db.execute('DELETE FROM attempts WHERE key=?', (keys[0],))
        dialog.task_picker.setCurrentIndex(1)
        dialog.save_current()
        assert store.draft(keys[1]) == '任务 B：仅修改第二个任务。'
        assert not dialog.isVisible()
        assert not store.enabled(), 'editing must not enable automatic resume'
        assert store.claim(keys[0], 'synthetic-task-a', time.time())
        store.finish(keys[0], 'test-reset', time.time())
        # Remove only this synthetic attempt to allow the reopen scenario below.
        with store.db:
            store.db.execute('DELETE FROM attempts WHERE key=?', (keys[0],))

        reopened = DraftEditor(store=store, title_loader=titles)
        reopened.show()
        app.processEvents()
        assert reopened.editor.toPlainText() == first_text
        assert not store.claim(keys[0], 'synthetic-task-a', time.time()), 'selected task must hold a lease'
        assert store.claim(keys[1], 'synthetic-task-b', time.time()), 'other task must remain available'
        store.finish(keys[1], 'uncertain', time.time())
        reopened.task_picker.setCurrentIndex(1)
        assert reopened.editor.isReadOnly()
        assert '状态待确认' in reopened.status_label.text()
        assert store.draft(keys[1]) == '任务 B：仅修改第二个任务。'
        reopened.task_picker.setCurrentIndex(0)
        reopened.editor.setPlainText(first_text + '\n尚未保存的补充')
        with patch.object(store, 'save_draft', side_effect=ValueError('synthetic save failure')):
            reopened.save_current()
        assert '未能保存' in reopened.status_label.text()
        assert '尚未保存' in reopened.editor.toPlainText()
        with patch.object(QMessageBox, 'warning', return_value=QMessageBox.Cancel):
            reopened.close()
        assert reopened.isVisible(), 'cancel close must preserve unsaved edits'
        reopened.refresh_tasks()
        assert '尚未保存' in reopened.editor.toPlainText()
        # A newer failed turn must not inherit old text.
        report['candidates'][0]['key'] = 'synthetic-task-a:synthetic-new-turn'
        store.report(report)
        reopened.refresh_tasks()
        assert reopened.current_key == keys[0]
        assert not reopened.save_button.isEnabled()
        assert reopened.editor.isReadOnly()
        assert '新一轮' in reopened.status_label.text()
        assert store.claim('synthetic-task-a:synthetic-new-turn', 'synthetic-task-a', time.time()), 'viewing stale text must not block newer turn'
        with store.db:
            store.db.execute('DELETE FROM attempts WHERE key=?', ('synthetic-task-a:synthetic-new-turn',))
        assert '尚未保存' in reopened.editor.toPlainText()
        output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.runtime/draft-editor-preview.png')
        output.parent.mkdir(parents=True, exist_ok=True)
        app.processEvents()
        assert reopened.grab().save(str(output))
        # Restore the candidate to demonstrate recovery without losing text.
        report['candidates'][0]['key'] = keys[0]
        store.report(report)
        reopened.refresh_tasks()
        assert not reopened.editor.isReadOnly()
        assert '尚未保存' in reopened.editor.toPlainText()
        app.processEvents()
        assert reopened.grab().save(str(output.with_name(output.stem + '-active.png')))
        original_palette = QPalette(reopened.palette())
        dark_palette = QPalette(original_palette)
        for role, color in ((QPalette.Window, '#202124'), (QPalette.WindowText, '#f1f3f4'),
                            (QPalette.Base, '#292a2d'), (QPalette.Text, '#f1f3f4'),
                            (QPalette.Button, '#35363a'), (QPalette.ButtonText, '#f1f3f4'),
                            (QPalette.PlaceholderText, '#bdc1c6')):
            dark_palette.setColor(role, QColor(color))
        reopened.setPalette(dark_palette)
        reopened.update_count()
        assert not reopened.status_label.styleSheet()
        assert reopened.status_label.palette().color(QPalette.WindowText) == QColor('#f1f3f4')
        assert reopened.count_label.palette().color(QPalette.WindowText) == QColor('#f1f3f4')
        retained_text = reopened.editor.toPlainText()
        reopened.editor.setPlainText('x' * 20001)
        assert not reopened.save_button.isEnabled()
        assert reopened.count_label.palette().color(QPalette.WindowText) == QColor('#ffaaa0')
        reopened.editor.setPlainText(retained_text)
        assert reopened.count_label.palette().color(QPalette.WindowText) == QColor('#f1f3f4')
        app.processEvents()
        assert reopened.grab().save(str(output.with_name(output.stem + '-dark.png')))
        reopened.setPalette(original_palette)
        reopened.update_count()
        reopened.save_current()
        assert not reopened.isVisible()
        assert not store.enabled()
        # Parentless dialog singleton is owned by the bar without inheriting flags.
        import draft_editor
        from glass_overlay import GlassBar
        bar = GlassBar(app, live=False)
        original_editor = draft_editor.DraftEditor
        with patch.object(draft_editor, 'DraftEditor', side_effect=lambda: original_editor(store=store, title_loader=titles)):
            bar.open_draft_editor()
            first_window = bar.draft_editor
            bar.open_draft_editor()
            assert bar.draft_editor is first_window
            assert first_window.parent() is None
            assert bar.windowFlags() & Qt.WindowTransparentForInput
            assert bar.windowFlags() & Qt.WindowDoesNotAcceptFocus
            assert not first_window.windowFlags() & Qt.WindowTransparentForInput
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
                first_window.clear_current()
            assert store.draft(keys[0]) == ''
            first_window.close()
            assert bar.draft_editor is None
        bar.close()
        empty = Store(Path(temporary) / 'empty')
        cleanup.callback(empty.close)
        no_tasks = DraftEditor(store=empty, title_loader=titles)
        assert not no_tasks.save_button.isEnabled()
        assert '没有等待恢复' in no_tasks.status_label.text()
        no_tasks.reject()
    print(json.dumps({'offline': True, 'two_task_drafts': True, 'reopen': True,
                      'focusable': True, 'stale_text_preserved': True,
                      'save_failure_preserves_text': True, 'claimed_copy_only': True,
                      'per_task_lease': True, 'tray_dialog_singleton': True,
                      'native_editor_focus_and_input': sys.platform == 'win32',
                      'selection_error_keeps_task_ownership': True, 'palette_readable': True,
                      'clear_saved_draft': True, 'empty_state': True,
                      'automatic_resume_stays_paused': True, 'screenshot': str(output.resolve())}))


if __name__ == '__main__':
    main()
