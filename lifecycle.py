"""Small local launcher. Quota/UI only run while Codex Desktop is running."""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import time
from monitor import SingleInstance, state_directory


def is_desktop_path(path):
    path=PureWindowsPath(path)
    return (path.name.lower() in ('chatgpt.exe','codex.exe') and
            any(part.lower().startswith('openai.codex_') for part in path.parts))


def desktop_processes():
    """Inspect executable paths in our Windows session; never read command lines."""
    kernel=C.WinDLL('kernel32',use_last_error=True)
    class Entry(C.Structure):
        _fields_=[('size',W.DWORD),('usage',W.DWORD),('pid',W.DWORD),
                  ('heap',C.c_size_t),('module',W.DWORD),('threads',W.DWORD),
                  ('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),('exe',W.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=W.HANDLE
    kernel.Process32FirstW.argtypes=[W.HANDLE,C.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[W.HANDLE,C.POINTER(Entry)]
    kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];kernel.OpenProcess.restype=W.HANDLE
    kernel.CloseHandle.argtypes=[W.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
    kernel.ProcessIdToSessionId.argtypes=[W.DWORD,C.POINTER(W.DWORD)]
    session=W.DWORD();kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(),C.byref(session))
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot==C.c_void_p(-1).value:raise C.WinError(C.get_last_error())
    matched={}
    try:
        entry=Entry();entry.size=C.sizeof(entry)
        more=kernel.Process32FirstW(snapshot,C.byref(entry))
        while more:
            if entry.exe.lower() in ('chatgpt.exe','codex.exe'):
                other=W.DWORD()
                same=kernel.ProcessIdToSessionId(entry.pid,C.byref(other)) and other.value==session.value
                handle=kernel.OpenProcess(0x1000,False,entry.pid) if same else None
                if handle:
                    try:
                        length=W.DWORD(32768);buf=C.create_unicode_buffer(length.value)
                        if kernel.QueryFullProcessImageNameW(handle,0,buf,C.byref(length)) and is_desktop_path(buf.value):
                            matched[entry.pid]=entry.parent
                    finally:kernel.CloseHandle(handle)
            more=kernel.Process32NextW(snapshot,C.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    return {pid for pid,parent in matched.items() if parent not in matched}


class Lifecycle:
    def __init__(self,start,stop):
        self.start,self.stop=start,stop;self.child=None;self.previous=set();self.dismissed=False

    def tick(self,roots):
        roots=set(roots)
        if not roots or not roots.intersection(self.previous):self.dismissed=False
        if self.child and self.child.poll() is not None:
            self.child=None
            if roots.intersection(self.previous):self.dismissed=True
        if not roots and self.child:
            self.stop(self.child);self.child=None
        if roots and self.child is None and not self.dismissed:self.child=self.start()
        self.previous=roots

    def close(self):
        if self.child:self.stop(self.child);self.child=None


class Launcher:
    def __init__(self,directory,root,command=None):
        self.directory,self.root=Path(directory),Path(root)
        self.command=command or [str(Path(sys.executable).with_name('pythonw.exe')),str(self.root/'overlay.py')]
    def start(self):
        (self.directory/'stop.request').unlink(missing_ok=True)
        return subprocess.Popen(self.command,cwd=self.root,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    def stop(self,child):
        (self.directory/'stop.request').touch()
        try:child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            # Only the child launched by this supervisor and its own bridge.
            subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
            child.wait(timeout=3)


def run():
    directory=state_directory();root=Path(__file__).resolve().parent
    launcher=Launcher(directory,root)
    with SingleInstance(directory,name='lifecycle'):
        supervisor=Lifecycle(launcher.start,launcher.stop)
        try:
            while not (directory/'lifecycle-stop.request').exists():
                try:roots=desktop_processes()
                except OSError:
                    time.sleep(2);continue  # An inspection error is not an app exit.
                supervisor.tick(roots)
                report={'checked_at':time.time(),'desktop_running':bool(roots),
                        'monitor_running':bool(supervisor.child),'dismissed':supervisor.dismissed}
                temporary=directory/'lifecycle-status.tmp'
                temporary.write_text(json.dumps(report),encoding='utf-8')
                temporary.replace(directory/'lifecycle-status.json')
                time.sleep(2)
        finally:supervisor.close()


if __name__=='__main__':
    try:run()
    except RuntimeError as error:
        if 'already running' not in str(error):raise
