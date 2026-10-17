"""Click only a disposable button in another process and restore the cursor."""
import ctypes as C
from ctypes import wintypes as W
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from glass_native import enable_dpi_awareness
enable_dpi_awareness()
from glass_overlay import QApplication,GlassBar
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton

if '--target' in sys.argv:
    directory=Path(sys.argv[-1]);app=QApplication([])
    button=QPushButton('Disposable click target');button.setGeometry(300,400,440,100)
    button.clicked.connect(lambda:(directory/'clicked').write_text('clicked'))
    button.show();app.processEvents()
    (directory/'ready').write_text(str(int(button.winId())))
    QTimer.singleShot(10000,app.quit)
    sys.exit(app.exec())

user=C.windll.user32
user.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
user.WindowFromPoint.argtypes=[W.POINT];user.WindowFromPoint.restype=W.HWND
user.SendMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];user.SendMessageW.restype=C.c_ssize_t
user.SetForegroundWindow.argtypes=[W.HWND]
app=QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    directory=Path(tmp);child=subprocess.Popen([sys.executable,__file__,'--target',tmp])
    old_cursor=W.POINT();user.GetCursorPos(C.byref(old_cursor))
    user.GetForegroundWindow.restype=W.HWND;old_foreground=user.GetForegroundWindow()
    bar=None
    try:
        deadline=time.monotonic()+5
        while not (directory/'ready').exists() and time.monotonic()<deadline:
            app.processEvents();time.sleep(.05)
        assert (directory/'ready').exists(),'target failed to start'
        target=int((directory/'ready').read_text());rect=W.RECT();user.GetWindowRect(target,C.byref(rect))
        point=W.POINT((rect.left+rect.right)//2,(rect.top+rect.bottom)//2)
        bar=GlassBar(app,live=False)
        scale=bar.devicePixelRatioF()
        bar.move(round(point.x/scale-bar.width()/2),round(point.y/scale-bar.height()/2))
        bar.show();app.processEvents();time.sleep(.2);app.processEvents()
        hwnd=int(bar.winId())
        hit_test=user.SendMessageW(hwnd,0x0084,0,(point.y<<16)|(point.x&0xffff))
        assert hit_test==-1, f'WM_NCHITTEST must reject overlay input, got {hit_test}'
        assert user.WindowFromPoint(point)==target,'overlay covers cross-process button'
        for _ in range(3):
            bar.hide();app.processEvents();bar.show();app.processEvents()
            assert user.WindowFromPoint(point)==target,'reshown overlay covers button'
        user.SetForegroundWindow(target)
        user.SetCursorPos(point.x,point.y)
        user.mouse_event(0x0002,0,0,0,0);user.mouse_event(0x0004,0,0,0,0)
        deadline=time.monotonic()+2
        while not (directory/'clicked').exists() and time.monotonic()<deadline:
            app.processEvents();time.sleep(.05)
        assert (directory/'clicked').exists(),'underlying process did not receive click'
        print(json.dumps({'cross_process_click_received':True,'hide_show_cycles':3,'hit_test':hit_test}))
    finally:
        user.SetCursorPos(old_cursor.x,old_cursor.y)
        if old_foreground:user.SetForegroundWindow(old_foreground)
        if bar:bar.close()
        child.terminate();child.wait(timeout=5)
