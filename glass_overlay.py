"""Compact Qt glass overlay. All window input passes to the app underneath."""
import json
from pathlib import Path
import sys
import threading
import time
from monitor import SingleInstance, state_directory, run_worker
from glass_native import enable_dpi_awareness, configure_window, window_dpi
from ui_model import display_state

for directory in (state_directory()/'ui-runtime',Path(__file__).parent/'.runtime'/'ui'):
    if directory.exists():sys.path.insert(0,str(directory))
from PySide6.QtCore import Qt,QTimer,QRectF
from PySide6.QtGui import QColor,QFont,QPainter,QPen,QLinearGradient,QCursor,QIcon,QPixmap
from PySide6.QtWidgets import QApplication,QWidget,QSystemTrayIcon,QMenu

WIDTH,HEIGHT=360,38

class GlassBar(QWidget):
    def __init__(self,app,live=True):
        super().__init__()
        self.app=app;self.live=live
        self.setWindowTitle('Codex Quota Resume')
        self.setWindowFlags(Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|
                            Qt.WindowTransparentForInput|Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(WIDTH,HEIGHT)
        self.expanded=True;self.pinned=False;self.grace=time.monotonic()+5
        self.report={};self.view=display_state({},time.time());self.last_read=0
        self.native={};self.tray=None
        if live:self.build_tray()
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick)
        if live:self.timer.start(100)

    def build_tray(self):
        icon=QPixmap(32,32);icon.fill(Qt.transparent)
        paint=QPainter(icon);paint.setRenderHint(QPainter.Antialiasing)
        paint.setPen(Qt.NoPen);paint.setBrush(QColor('#182333'));paint.drawRoundedRect(1,1,30,30,9,9)
        paint.setPen(QPen(QColor('#6ee7b7'),3));paint.drawArc(7,7,18,18,45*16,280*16);paint.end()
        self.tray=QSystemTrayIcon(QIcon(icon),self)
        menu=QMenu();self.menu=menu
        self.menu_status=menu.addAction('正在连接');self.menu_status.setEnabled(False)
        menu.addSeparator()
        from control import execute
        menu.addAction('立即刷新',lambda:execute('refresh'))
        self.enabled_action=menu.addAction('自动续跑');self.enabled_action.setCheckable(True)
        self.enabled_action.triggered.connect(lambda enabled:execute('enable' if enabled else 'pause'))
        self.pin_action=menu.addAction('常驻显示');self.pin_action.setCheckable(True)
        self.pin_action.triggered.connect(self.set_pinned)
        menu.addSeparator();menu.addAction('退出',self.app.quit)
        menu.aboutToShow.connect(self.refresh_menu)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.reveal() if reason==QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def refresh_menu(self):
        self.menu_status.setText(self.view['status'])
        self.enabled_action.setChecked(bool(self.report.get('enabled')))
    def set_pinned(self,value):self.pinned=value;self.tick()
    def reveal(self):self.grace=time.monotonic()+8;self.tick()
    def showEvent(self,event):
        super().showEvent(event);self.native=configure_window(int(self.winId()))

    def read_report(self):
        try:self.report=json.loads((state_directory()/'usage.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        self.view=display_state(self.report,time.time())
        if self.tray:
            groups=self.view['groups']
            self.tray.setToolTip(f"Codex · 5h {groups[0]['balance']} · 7d {groups[1]['balance']}\n{self.view['status']}\n显示条支持鼠标穿透；右键托盘可操作")

    def tick(self):
        if (state_directory()/'stop.request').exists():self.app.quit();return
        now=time.monotonic()
        if now-self.last_read>=1:self.read_report();self.last_read=now
        screen=self.app.primaryScreen().geometry();cursor=QCursor.pos()
        center=screen.x()+screen.width()//2
        near=abs(cursor.x()-center)<=WIDTH//2 and screen.y()<=cursor.y()<=screen.y()+HEIGHT+14
        expanded=self.pinned or near or now<self.grace
        width,height=(WIDTH,HEIGHT) if expanded else (64,4)
        if expanded!=self.expanded:self.expanded=expanded;self.setFixedSize(width,height)
        self.move(center-width//2,screen.y()+(7 if expanded else 1))
        if not self.isVisible():self.show()
        self.update()

    def text_font(self,pixels,weight=QFont.Normal):
        font=QFont('Segoe UI');font.setPixelSize(pixels);font.setWeight(weight)
        font.setHintingPreference(QFont.PreferFullHinting);return font

    def paintEvent(self,event):
        paint=QPainter(self);paint.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing)
        rect=QRectF(self.rect()).adjusted(.5,.5,-.5,-.5)
        if not self.expanded:
            paint.setPen(Qt.NoPen);paint.setBrush(QColor(self.view['color']));paint.drawRoundedRect(rect,2,2);return
        # Background transparency does not reduce text opacity.
        gradient=QLinearGradient(0,0,0,HEIGHT);alpha=105 if self.native.get('acrylic') else 225
        gradient.setColorAt(0,QColor(32,41,56,alpha));gradient.setColorAt(1,QColor(16,22,33,alpha+20))
        paint.setBrush(gradient);paint.setPen(QPen(QColor(230,240,255,48),1));paint.drawRoundedRect(rect,12,12)
        paint.setPen(Qt.NoPen);paint.setBrush(QColor(self.view['color']));paint.drawEllipse(QRectF(10,16,5,5))
        for index,group in enumerate(self.view['groups']):
            x=25+index*170
            paint.setFont(self.text_font(11,QFont.Medium));paint.setPen(QColor('#a9b8cd'))
            paint.drawText(QRectF(x,0,24,HEIGHT),Qt.AlignVCenter,group['label'])
            paint.setFont(self.text_font(14,QFont.DemiBold));paint.setPen(QColor('#f3f7fc'))
            paint.drawText(QRectF(x+25,0,51,HEIGHT),Qt.AlignVCenter,group['balance'])
            paint.setFont(self.text_font(11));paint.setPen(QColor('#b8c7db'))
            paint.drawText(QRectF(x+80,0,76,HEIGHT),Qt.AlignVCenter,'↻ '+group['countdown'])
        paint.setPen(QPen(QColor(207,221,243,40),1));paint.drawLine(183,11,183,27)

    def diagnostics(self):
        self.native=configure_window(int(self.winId()))
        return {**self.native,'logical_size':[self.width(),self.height()],
                'device_pixel_ratio':self.devicePixelRatioF(),'dpi':window_dpi(int(self.winId())),
                'input_transparent_flag':bool(self.windowFlags()&Qt.WindowTransparentForInput),
                'takes_focus':not bool(self.windowFlags()&Qt.WindowDoesNotAcceptFocus)}

def main():
    if '--once' in sys.argv:
        from control import execute
        if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
        print(json.dumps(execute('probe'),ensure_ascii=False,indent=2));return 0
    enable_dpi_awareness();app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False)
    with SingleInstance(state_directory()):
        stop=threading.Event();worker=threading.Thread(target=run_worker,args=(stop,),daemon=True);worker.start()
        bar=GlassBar(app);bar.tick()
        (state_directory()/'ui-status.json').write_text(json.dumps(bar.diagnostics()),encoding='utf-8')
        try:return app.exec()
        finally:
            stop.set();worker.join(timeout=55)
            if bar.tray:bar.tray.hide()
