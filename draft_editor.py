"""Local-only recovery drafts. This dialog never contacts the desktop service."""
from pathlib import Path
import sys
import time
import uuid

from monitor import Store, state_directory

for directory in (state_directory() / 'ui-runtime', Path(__file__).parent / '.runtime' / 'ui'):
    if directory.exists():
        sys.path.insert(0, str(directory))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QHBoxLayout, QLabel,
                              QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)


def local_titles(thread_ids):
    from bridge import local_task_titles
    return local_task_titles(thread_ids)


class DraftEditor(QDialog):
    """Protect selected/unsaved tasks; draft text stays outside reports."""

    def __init__(self, store=None, title_loader=None):
        # Never parent this to GlassBar: its native input/focus flags are special.
        super().__init__(None)
        self.store = store if store is not None else Store()
        self.owns_store = store is None
        self.title_loader = title_loader or local_titles
        self.token = uuid.uuid4().hex
        self.current_key = None
        self.lease_keys = set()
        self.cache = {}
        self.candidates = set()
        self.claimed_keys = set()
        self.unprotected_keys = set()
        self.entries = {}
        self.loading = False
        self.finished_cleanup = False
        self.setWindowTitle('补充恢复提示词 · Codex')
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setMinimumSize(560, 440)
        self.resize(680, 540)
        font = QFont('Microsoft YaHei UI', 10)
        self.setFont(font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        title = QLabel('补充恢复提示词')
        heading_font = QFont(font)
        heading_font.setPixelSize(21)
        heading_font.setWeight(QFont.DemiBold)
        title.setFont(heading_font)
        layout.addWidget(title)
        hint = QLabel('在本机暂存补充要求；额度恢复后，随对应任务的恢复消息发送一次。\n这里只编辑提示词，不会启用已暂停的自动续跑。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        picker_row = QHBoxLayout()
        self.task_picker = QComboBox()
        self.task_picker.setMinimumContentsLength(24)
        self.task_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.task_picker.setAccessibleName('选择等待恢复的任务')
        picker_row.addWidget(self.task_picker, 1)
        refresh = QPushButton('刷新列表')
        refresh.clicked.connect(self.refresh_tasks)
        picker_row.addWidget(refresh)
        layout.addLayout(picker_row)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_label.setMinimumHeight(44)
        layout.addWidget(self.status_label)
        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName('本任务的补充恢复提示词')
        self.editor.setPlaceholderText('例如：先检查上次运行结果，再继续；请按我补充的新要求调整……')
        layout.addWidget(self.editor, 1)
        self.count_label = QLabel('0 / 20000 字符')
        layout.addWidget(self.count_label)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton('复制文本')
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.editor.toPlainText()))
        self.clear_button = QPushButton('清除暂存')
        self.clear_button.clicked.connect(self.clear_current)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch()
        close_button = QPushButton('关闭')
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        self.save_button = QPushButton('保存并等待恢复')
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save_current)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.task_picker.currentIndexChanged.connect(self.select_task)
        self.editor.textChanged.connect(self.text_changed)
        self.finished.connect(self.cleanup)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh_tasks)
        self.refresh_tasks()
        self.timer.start()

    def remember_current(self):
        if self.current_key in self.cache:
            self.cache[self.current_key]['text'] = self.editor.toPlainText()

    def dirty_keys(self):
        self.remember_current()
        return [key for key, item in self.cache.items() if item['text'] != item['saved']]

    def release_lease(self, key=None):
        for leased_key in list(self.lease_keys) if key is None else [key]:
            if leased_key not in self.lease_keys:
                continue
            try:
                self.store.end_edit(leased_key, self.token)
            except Exception:
                pass  # A failed release expires naturally after 30 seconds.
            self.lease_keys.discard(leased_key)

    def refresh_tasks(self):
        self.remember_current()
        try:
            report = self.store.last_report()
            rows = self.store.draft_rows()
            candidates = {item['key']: item for item in report.get('candidates', [])
                          if item.get('key') and item.get('thread_id')}
            keys = list(candidates)
            keys.extend(row['key'] for row in rows if row['key'] not in keys)
            keys.extend(key for key in self.cache if key not in keys)
            self.claimed_keys = {key for key in keys if self.store.claimed(key)}
            # A consumed candidate is irrelevant unless there is saved/local text to recover.
            keys = [key for key in keys if key not in self.claimed_keys
                    or key in self.cache or any(row['key'] == key for row in rows)]
            self.candidates = set(candidates) - self.claimed_keys
            titles = self.title_loader([key.rsplit(':', 1)[0] for key in keys])
        except Exception:
            self.disable_editing('无法读取本机任务列表。文本仍保留在窗口中，可复制后重试刷新。')
            return
        for key in list(self.lease_keys):
            item = self.cache.get(key)
            dirty = bool(item and item['text'] != item['saved'])
            if key not in self.candidates or (key != self.current_key and not dirty):
                self.release_lease(key)
        self.unprotected_keys = set()
        for key, item in self.cache.items():
            if key in self.candidates and key != self.current_key and item['text'] != item['saved']:
                try:
                    self.store.begin_edit(key, self.token, time.time())
                    self.lease_keys.add(key)
                except Exception:
                    self.lease_keys.discard(key)
                    self.unprotected_keys.add(key)
        self.entries = {key: titles.get(key.rsplit(':', 1)[0]) or '未命名任务' for key in keys}
        previous = self.current_key
        self.task_picker.blockSignals(True)
        self.task_picker.clear()
        for key in keys:
            thread, _, turn = key.rpartition(':')
            suffix = ' · 已发送或状态待确认' if key in self.claimed_keys else (' · 旧记录' if key not in self.candidates else '')
            label = f'{self.entries[key]}  ·  {thread[-8:]} / {turn[-8:]}{suffix}'
            self.task_picker.addItem(label, key)
            self.task_picker.setItemData(self.task_picker.count() - 1, f'{self.entries[key]}\n任务 {thread}\n轮次 {turn}', Qt.ToolTipRole)
        index = keys.index(previous) if previous in keys else (0 if keys else -1)
        self.task_picker.setCurrentIndex(index)
        self.task_picker.blockSignals(False)
        self.select_task(index)

    def select_task(self, index):
        key = self.task_picker.itemData(index) if index >= 0 else None
        if key != self.current_key:
            self.remember_current()
            previous = self.cache.get(self.current_key)
            if not (self.current_key in self.candidates and previous
                    and previous['text'] != previous['saved']):
                self.release_lease(self.current_key)
            self.current_key = key
            # Establish display ownership before any fallible state/lease read.
            # The old task was remembered above; an unread new task starts blank.
            self.loading = True
            self.editor.setPlainText(self.cache[key]['text'] if key in self.cache else '')
            self.loading = False
        if not key:
            self.loading = True
            self.editor.clear()
            self.loading = False
            self.disable_editing('暂时没有等待恢复的任务。请在任务因额度暂停后刷新列表；额度用尽时也可离线打开此窗口。')
            return
        try:
            claimed = self.store.claimed(key)
        except Exception:
            self.disable_editing('无法核对任务状态。文本仍保留，可复制后重试刷新。')
            return
        lease_error = False
        active = not claimed and key in self.candidates
        if active:
            try:
                self.store.begin_edit(key, self.token, time.time())
                self.lease_keys.add(key)
            except Exception:
                lease_error = True
                self.lease_keys.discard(key)
        else:
            self.release_lease(key)
        if key not in self.cache:
            # Active text needs a lease; stale/claimed text is read-only recovery.
            self.loading = True
            self.editor.clear()
            self.loading = False
            if lease_error:
                self.disable_editing('该任务正在恢复或已在其他窗口编辑，请稍后刷新。')
                return
            try:
                text = self.store.draft(key)
            except Exception:
                self.disable_editing('暂存文本读取失败，请刷新重试。')
                return
            self.cache[key] = {'text': text, 'saved': text}
        if self.editor.toPlainText() != self.cache[key]['text']:
            self.loading = True
            self.editor.setPlainText(self.cache[key]['text'])
            self.loading = False
        if claimed:
            self.release_lease(key)
            self.disable_editing('该轮次已发送或发送状态待确认。仅可复制保留的文本；不会自动重试。')
        elif lease_error:
            self.disable_editing('编辑保护暂时不可用：文本仍保留，可复制。请刷新重试；当前内容尚未保存。')
        elif key not in self.candidates:
            self.editor.setReadOnly(True)
            self.save_button.setEnabled(False)
            self.clear_button.setEnabled(True)
            self.status_label.setText('这是旧轮次：任务可能已进入新一轮或不再等待恢复。文本不会自动转给新一轮；可复制后重新选择任务。')
        else:
            self.editor.setReadOnly(False)
            self.save_button.setEnabled(len(self.editor.toPlainText()) <= 20000)
            self.clear_button.setEnabled(True)
            self.status_label.setText('正在编辑此任务，暂缓它的自动恢复。切换后，尚未保存的任务继续受保护；保存或关闭后释放。其他任务照常。')
        if self.unprotected_keys:
            self.status_label.setText('有其他未保存任务的编辑保护暂时失效。文本仍留在窗口中，请切换检查并复制备份。\n' + self.status_label.text())
        self.update_count()

    def disable_editing(self, message):
        self.editor.setReadOnly(True)
        self.save_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.status_label.setText(message)
        self.update_count()

    def text_changed(self):
        if not self.loading:
            self.remember_current()
        self.update_count()

    def update_count(self):
        length = len(self.editor.toPlainText())
        dirty = bool(self.current_key in self.cache and
                     self.editor.toPlainText() != self.cache[self.current_key]['saved'])
        self.count_label.setText(f'{length} / 20000 字符' + (' · 尚未保存' if dirty else ''))
        palette = QPalette(self.palette())
        if length > 20000:
            dark = palette.color(QPalette.Window).lightness() < 128
            palette.setColor(QPalette.WindowText, QColor('#ffaaa0' if dark else '#a4261b'))
        self.count_label.setPalette(palette)
        if not self.editor.isReadOnly():
            self.save_button.setEnabled(length <= 20000)
        self.copy_button.setEnabled(bool(length))

    def save_current(self):
        # Reconcile a newly published turn before deciding which key can save.
        self.refresh_tasks()
        key = self.current_key
        if not key or key not in self.candidates or self.editor.isReadOnly():
            return
        text = self.editor.toPlainText()
        try:
            self.store.begin_edit(key, self.token, time.time())
            self.lease_keys.add(key)
            self.store.save_draft(key, text, time.time())
        except Exception:
            self.status_label.setText('未能保存：任务可能已恢复、正在发送或被其他编辑器占用。文本仍保留在本窗口，请复制或刷新后重试。')
            return
        self.cache[key] = {'text': text if text.strip() else '', 'saved': text if text.strip() else ''}
        if not text.strip():
            self.editor.clear()
        self.release_lease(key)
        self.update_count()
        if not self.dirty_keys():
            self.accept()
        else:
            next_key = self.dirty_keys()[0]
            self.task_picker.setCurrentIndex(self.task_picker.findData(next_key))
            self.status_label.setText('上一任务已保存并释放编辑保护。已切换到另一个尚未保存的任务，请继续保存。')

    def clear_current(self):
        key = self.current_key
        if not key or not self.clear_button.isEnabled():
            return
        if QMessageBox.question(self, '清除暂存', '清除当前轮次的暂存文本及本窗口修改？',
                                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        try:
            self.store.begin_edit(key, self.token, time.time())
            self.lease_keys.add(key)
            self.store.save_draft(key, '', time.time())
        except Exception:
            self.status_label.setText('未能清除：任务可能已开始恢复。文本仍保留，可复制后刷新重试。')
            return
        self.cache[key] = {'text': '', 'saved': ''}
        self.editor.clear()
        self.release_lease(key)
        if not self.dirty_keys():
            self.accept()
        else:
            self.task_picker.setCurrentIndex(self.task_picker.findData(self.dirty_keys()[0]))
            self.status_label.setText('上一任务的暂存已清除。已切换到另一个尚未保存的任务。')

    def reject(self):
        if self.dirty_keys():
            choice = QMessageBox.warning(self, '还有未保存的修改',
                '部分任务的修改尚未保存。关闭会丢弃这些修改，已保存的暂存文本会保留。\n请返回各任务保存，或先复制需要的文本。',
                QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if choice != QMessageBox.Discard:
                return
        super().reject()

    def closeEvent(self, event):
        self.reject()
        if self.isVisible():
            event.ignore()
        else:
            event.accept()

    def cleanup(self, result=None):
        if self.finished_cleanup:
            return
        self.finished_cleanup = True
        self.timer.stop()
        self.release_lease()
        if self.owns_store:
            self.store.close()
