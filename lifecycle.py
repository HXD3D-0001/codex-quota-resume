"""Small local launcher. Quota/UI only run while Codex Desktop is running.

The launcher supervises itself: an unexpected crash is logged and the loop is
retried with backoff, so a single bad tick no longer ends startup sync until
the next logon. The logon scheduled task adds a second layer by restarting the
process if it is killed outright.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import time
from monitor import SingleInstance, state_directory

LOG_NAME = 'lifecycle.log'
LOG_MAX_BYTES = 1_000_000
HEARTBEAT_SECONDS = 2
RESTART_DELAY_SECONDS = 30
MAX_RESTART_DELAY_SECONDS = 300
CRASH_WINDOW_SECONDS = 60
MAX_FAST_CRASHES = 10


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


def append_log(directory, message, now=None):
    """Append one line, rotating first when the log grows past the cap.

    Logging must never be the reason the launcher stops, so every failure is
    swallowed after the first attempt to roll the file over.
    """
    directory=Path(directory)
    path=directory/LOG_NAME
    stamp=time.strftime('%Y-%m-%dT%H:%M:%S',time.localtime(time.time() if now is None else now))
    try:
        directory.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.stat().st_size>LOG_MAX_BYTES:
            rotated=directory/(LOG_NAME+'.1')
            try:rotated.unlink()
            except OSError:pass
            path.replace(rotated)
        with path.open('a',encoding='utf-8') as handle:
            handle.write(f'{stamp} {message}\n')
        return True
    except OSError:
        return False


def write_status(directory, report):
    """Publish the heartbeat atomically; a stale file is never half written."""
    temporary=Path(directory)/'lifecycle-status.tmp'
    temporary.write_text(json.dumps(report),encoding='utf-8')
    temporary.replace(Path(directory)/'lifecycle-status.json')


class Lifecycle:
    def __init__(self,start,stop,root=None):
        self.start,self.stop=start,stop
        self.root=None if root is None else Path(root)
        self.child=None;self.previous=set();self.dismissed=False

    def requested_stop(self):
        """True when a stop was asked for; a crash must not look like one."""
        return bool(self.root) and any((self.root/name).exists()
                                      for name in ('stop.request','lifecycle-stop.request'))

    def tick(self,roots):
        roots=set(roots)
        if not roots or not roots.intersection(self.previous):self.dismissed=False
        if self.child and self.child.poll() is not None:
            # A child that died on its own has to come back. Only an explicit
            # stop request keeps it closed for the rest of this Codex session,
            # which is what the tray "exit" action writes.
            if roots.intersection(self.previous) and self.requested_stop():self.dismissed=True
            self.child=None
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


def serve(directory,root,once=False,fail_after=None):
    """One supervision pass. ``fail_after`` exists only for tests."""
    directory,root=Path(directory),Path(root)
    launcher=Launcher(directory,root)
    supervisor=Lifecycle(launcher.start,launcher.stop,directory)
    ticks=0
    with SingleInstance(directory,name='lifecycle'):
        # A task or logon launch outranks an old stop request. Leaving one
        # behind made the overlay quit the moment Codex was next opened.
        (directory/'stop.request').unlink(missing_ok=True)
        (directory/'lifecycle-stop.request').unlink(missing_ok=True)
        append_log(directory,'listener started')
        try:
            while not (directory/'lifecycle-stop.request').exists():
                try:roots=desktop_processes()
                except OSError:
                    time.sleep(2);continue  # An inspection error is not an app exit.
                supervisor.tick(roots)
                write_status(directory,{'checked_at':time.time(),'last_heartbeat':time.time(),
                                        'pid':os.getpid(),'desktop_running':bool(roots),
                                        'monitor_running':bool(supervisor.child),
                                        'dismissed':supervisor.dismissed})
                ticks+=1
                if fail_after is not None and ticks>=fail_after:
                    raise RuntimeError('injected listener failure')
                if once:return
                time.sleep(HEARTBEAT_SECONDS)
        except BaseException as error:
            append_log(directory,f'listener stopping: {type(error).__name__}: {error}')
            raise
        finally:supervisor.close()
    append_log(directory,'listener exit requested')


def supervise(directory,root,serve_once=None):
    """Run the listener forever, retrying unexpected crashes with backoff.

    Returns the number of restarts performed; a requested stop ends the loop
    without restarting.
    """
    directory=Path(directory)
    serve_once=serve_once or (lambda: serve(directory,root))
    delay=RESTART_DELAY_SECONDS
    restarts=0
    fast=0
    while True:
        started=time.monotonic()
        try:
            serve_once()
            if (directory/'lifecycle-stop.request').exists():
                append_log(directory,'stop requested; supervisor exiting')
            return restarts
        except BaseException as error:
            lifetime=time.monotonic()-started
            if isinstance(error,(KeyboardInterrupt,SystemExit)):
                append_log(directory,f'listener interrupted after {lifetime:.1f}s')
                return restarts
            if lifetime>=CRASH_WINDOW_SECONDS:
                # A crash after real work is not a crash loop.
                delay=RESTART_DELAY_SECONDS;fast=0
            fast+=1
            append_log(directory,f'restarting listener in {delay}s after {type(error).__name__} '
                                  f'(ran {lifetime:.1f}s, restart #{restarts+1}, fast crash #{fast})')
            if fast>=MAX_FAST_CRASHES:
                # A permanently broken child would otherwise spin forever; this
                # is reported instead of silently retrying every few minutes.
                append_log(directory,f'giving up after {fast} fast crashes; run scripts/start.ps1 to retry')
                return restarts
            time.sleep(delay)
            restarts+=1
            delay=min(delay*2,MAX_RESTART_DELAY_SECONDS)


def run():
    directory=state_directory();root=Path(__file__).resolve().parent
    supervise(directory,root)


if __name__=='__main__':
    try:run()
    except RuntimeError as error:
        if 'already running' in str(error):
            append_log(state_directory(),'another listener already holds the lock; exiting')
        else:raise
