"""Native window settings: acrylic background, sharp DPI rendering, no input capture."""
import ctypes as C
from ctypes import wintypes as W
import sys

LAYERED=0x00080000
TRANSPARENT=0x00000020
NOACTIVATE=0x08000000
TOOLWINDOW=0x00000080


def enable_dpi_awareness():
    if sys.platform=='win32':
        try:
            fn=C.windll.user32.SetProcessDpiAwarenessContext
            fn.argtypes=[C.c_void_p];fn.restype=W.BOOL
            return bool(fn(C.c_void_p(-4)))
        except AttributeError:return False
    return False


def configure_window(hwnd):
    if sys.platform!='win32':return {'click_through':False,'acrylic':False}
    user=C.windll.user32;dwm=C.windll.dwmapi
    get=user.GetWindowLongPtrW;get.argtypes=[W.HWND,C.c_int];get.restype=C.c_ssize_t
    set_=user.SetWindowLongPtrW;set_.argtypes=[W.HWND,C.c_int,C.c_ssize_t];set_.restype=C.c_ssize_t
    set_(hwnd,-20,get(hwnd,-20)|LAYERED|TRANSPARENT|NOACTIVATE|TOOLWINDOW)
    dwm.DwmSetWindowAttribute.argtypes=[W.HWND,W.DWORD,C.c_void_p,W.DWORD]
    dwm.DwmSetWindowAttribute.restype=C.c_long
    for key,value in [(20,1),(33,2)]:
        item=C.c_int(value);dwm.DwmSetWindowAttribute(hwnd,key,C.byref(item),C.sizeof(item))
    class Margins(C.Structure):_fields_=[(n,C.c_int) for n in ('left','right','top','bottom')]
    margins=Margins(-1,-1,-1,-1)
    dwm.DwmExtendFrameIntoClientArea.argtypes=[W.HWND,C.POINTER(Margins)]
    dwm.DwmExtendFrameIntoClientArea(hwnd,C.byref(margins))
    backdrop=C.c_int(3) # DWMSBT_TRANSIENTWINDOW: Desktop Acrylic on Windows 11.
    acrylic=dwm.DwmSetWindowAttribute(hwnd,38,C.byref(backdrop),4)==0
    if not acrylic and hasattr(user,'SetWindowCompositionAttribute'):
        class Accent(C.Structure):_fields_=[('state',C.c_int),('flags',C.c_int),('tint',W.DWORD),('animation',C.c_int)]
        class Data(C.Structure):_fields_=[('attribute',C.c_int),('data',C.c_void_p),('size',C.c_size_t)]
        accent=Accent(4,0,0xB826211B,0)
        data=Data(19,C.cast(C.pointer(accent),C.c_void_p),C.sizeof(accent))
        user.SetWindowCompositionAttribute.argtypes=[W.HWND,C.POINTER(Data)]
        acrylic=bool(user.SetWindowCompositionAttribute(hwnd,C.byref(data)))
    styles=get(hwnd,-20)
    return {'click_through':(styles&(LAYERED|TRANSPARENT|NOACTIVATE))==(LAYERED|TRANSPARENT|NOACTIVATE),
            'acrylic':acrylic,'styles':styles}


def window_dpi(hwnd):
    fn=C.windll.user32.GetDpiForWindow;fn.argtypes=[W.HWND];fn.restype=W.UINT
    return fn(hwnd)
