"""Native read-only window/style assertions plus a render of our own widget."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from glass_native import enable_dpi_awareness
enable_dpi_awareness()
from glass_overlay import QApplication,GlassBar,display_state
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter,QColor
import ctypes as C
from ctypes import wintypes as W
import json,time

class PatternSurface(QWidget):
    def paintEvent(self,event):
        paint=QPainter(self);paint.fillRect(self.rect(),QColor('#203651'))
        for x in range(0,self.width(),8):
            paint.fillRect(x,0,4,self.height(),QColor('#b4cfed'))

app=QApplication([])
under=PatternSurface();under.setWindowTitle('Quota overlay input test surface');under.setGeometry(250,250,450,150);under.show()
bar=GlassBar(app,live=False)
bar.report={'connected':True,'enabled':True,'usage':{'observed_at':time.time(),'windows':[
    {'used':3,'minutes':300,'reset':time.time()+12345},
    {'used':30,'minutes':10080,'reset':time.time()+345678}]}}
bar.view=display_state(bar.report,time.time());bar.move(270,270);bar.show();app.processEvents()
user=C.windll.user32
user.GetWindowLongPtrW.argtypes=[W.HWND,C.c_int];user.GetWindowLongPtrW.restype=C.c_ssize_t
required=0x00080000|0x00000020|0x08000000
# Inspect the production show path before diagnostics can repair native flags.
actual_styles=user.GetWindowLongPtrW(int(bar.winId()),-20)
assert actual_styles & required == required, hex(actual_styles)
status=bar.diagnostics()
assert status['click_through'] and status['input_transparent_flag'] and not status['takes_focus'],status
assert status['logical_size']==[360,38]
assert status['acrylic'],status
assert status.get('acrylic_backend')=='accent', 'layered overlay needs the accent glass backend'
# Query Windows hit testing without moving/clicking the user's mouse.
point=W.POINT()
user=C.windll.user32
user.ClientToScreen.argtypes=[W.HWND,C.POINTER(W.POINT)]
user.WindowFromPoint.argtypes=[W.POINT];user.WindowFromPoint.restype=W.HWND
point.x=int(80*bar.devicePixelRatioF());point.y=int(18*bar.devicePixelRatioF())
user.ClientToScreen(int(bar.winId()),C.byref(point))
hit=user.WindowFromPoint(point)
status['hit_test_skips_overlay']=hit!=int(bar.winId())
status['hit_test_reaches_underlying_window']=hit==int(under.winId())
assert status['hit_test_skips_overlay'] and status['hit_test_reaches_underlying_window'],status
output=Path(sys.argv[1]) if len(sys.argv)>1 else Path('.runtime/ui-preview.png')
output.parent.mkdir(parents=True,exist_ok=True);bar.grab().save(str(output))
# Capture the compositor result over our pattern, not just Qt's alpha surface.
app.processEvents();time.sleep(.3);app.processEvents()
bar.screen().grabWindow(0,bar.x(),bar.y(),bar.width(),bar.height()).save(str(output.with_name(output.stem+'-glass.png')))
# Collapsing must remove the native window, including its DWM backdrop.
user.IsWindowVisible.argtypes=[W.HWND];user.IsWindowVisible.restype=W.BOOL
for _ in range(3):
    bar.set_expanded(False);app.processEvents()
    assert not bar.isVisible() and not user.IsWindowVisible(int(bar.winId()))
    assert bar.timer.isActive() is False  # This test uses live=False.
    bar.set_expanded(True);app.processEvents()
    assert bar.isVisible() and user.IsWindowVisible(int(bar.winId()))
    assert bar.width()==360 and bar.height()==38
    actual_styles=user.GetWindowLongPtrW(int(bar.winId()),-20)
    assert actual_styles & required == required, hex(actual_styles)
    assert user.WindowFromPoint(point)==int(under.winId()), 'reshown overlay intercepts input'
status['hide_show_cycles']=3
print(json.dumps(status))
bar.close();under.close();app.processEvents()
