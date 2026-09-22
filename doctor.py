"""Preflight and cross-checks for the installed monitor.

Answers one question: will the overlay start with Codex and keep running?
Every check is read-only and injectable, so it can be tested without Windows.
"""
import ctypes as C
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from monitor import state_directory

TASK_NAME = 'CodexQuotaResumeListener'
REDIRECT_PACKAGE_PREFIX = 'AppData\\Local\\Packages\\OpenAI.'
HEARTBEAT_STALE_SECONDS = 15
PYSIDE_MARKER = Path('PySide6') / 'QtWidgets.pyd'


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000


def _wait_handle(kernel, pid):
    """Return a handle to process ``pid`` that supports waiting, or None."""
    # PROCESS_QUERY_LIMITED_INFORMATION alone does not grant SYNCHRONIZE, so a
    # wait on such a handle fails and every live process looks dead. Ask for
    # both, and fall back to PROCESS_QUERY_INFORMATION when denied.
    for access in (PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, 0x0400 | SYNCHRONIZE):
        handle = kernel.OpenProcess(access, False, pid)
        if handle:
            return handle
    return None


def process_alive(pid):
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [C.c_uint32, C.c_int, C.c_uint32]
    kernel.OpenProcess.restype = C.c_void_p
    kernel.CloseHandle.argtypes = [C.c_void_p]
    kernel.WaitForSingleObject.argtypes = [C.c_void_p, C.c_uint32]
    kernel.WaitForSingleObject.restype = C.c_uint32
    handle = _wait_handle(kernel, pid)
    if not handle:
        return False
    try:
        # WAIT_TIMEOUT (258) means the process object is still alive.
        return kernel.WaitForSingleObject(handle, 0) == 0x00000102
    finally:
        kernel.CloseHandle(handle)


def read_pid(path):
    try:
        text = Path(path).read_text(encoding='ascii').strip()
        return int(text) if text.isdigit() else None
    except (OSError, ValueError):
        return None


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def powershell_path():
    import shutil
    return shutil.which('powershell.exe') or shutil.which('powershell')


def known_folder(name, powershell=None):
    """Real shell folder path, bypassing MSIX/AppContainer environment redirection."""
    shell = powershell or powershell_path()
    if not shell:
        return None
    try:
        done = subprocess.run(
            [shell, '-NoProfile', '-NonInteractive', '-Command',
             "[Environment]::GetFolderPath('" + name + "')"],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        return None
    value = (done.stdout or '').strip()
    return value or None


def scheduled_task_state(name=TASK_NAME, powershell=None):
    """Registered state and last result of the logon task; never raises."""
    shell = powershell or powershell_path()
    if not shell:
        return {'registered': False, 'state': None, 'last_result': None}
    script = ("$t=Get-ScheduledTask -TaskName '" + name + "' -ErrorAction SilentlyContinue;"
              "if($t){$i=$t|Get-ScheduledTaskInfo;'{0}|{1}' -f $t.State,$i.LastTaskResult}else{'MISSING'}")
    try:
        done = subprocess.run(
            [shell, '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        return {'registered': False, 'state': None, 'last_result': None,
                'error': 'scheduled task query failed'}
    text = (done.stdout or '').strip()
    if not text or text.upper().startswith('MISSING'):
        return {'registered': False, 'state': None, 'last_result': None}
    state, _, result = text.partition('|')
    return {'registered': True, 'state': state or None, 'last_result': result or None}


def _state_dir_check(state, runtime_dirs):
    """Detect MSIX redirection and a UI runtime that is unreachable from the real path."""
    local = str(os.environ.get('LOCALAPPDATA', ''))
    real = known_folder('LocalApplicationData')
    redirected = bool(local and real and os.path.normcase(local) != os.path.normcase(real))
    with_runtime = [str(p) for p in runtime_dirs if (Path(p) / PYSIDE_MARKER).is_file()]
    return {'id': 'state_dir', 'ok': not redirected and bool(with_runtime),
            'detail': {'env_localappdata': local or None, 'real_localappdata': real,
                       'redirected': redirected,
                       'runtime_dirs_checked': [str(p) for p in runtime_dirs],
                       'runtime_dirs_with_pyside6': with_runtime,
                       'state_dir': str(state)}}


def _codex_running(probe=None):
    """Whether Codex Desktop is running right now, or None when unknown."""
    if probe is None:
        try:
            from lifecycle import desktop_processes
            return bool(desktop_processes())
        except Exception:
            return None
    try:
        return bool(probe())
    except Exception:
        return None


def _listener_check(state, alive=None, now=None, codex=None):
    now = time.time() if now is None else now
    pid = read_pid(Path(state) / 'lifecycle.pid')
    watchdog = read_pid(Path(state) / 'watchdog.pid')
    status = read_json(Path(state) / 'lifecycle-status.json')
    heartbeat = status.get('last_heartbeat') or status.get('checked_at')
    age = None if heartbeat is None else round(now - float(heartbeat), 1)
    # Without a pid there is nothing to probe; asking would report a dead
    # process for a listener that was simply never started.
    is_alive = False if pid is None else (process_alive(pid) if alive is None else bool(alive(pid)))
    # A registered watchdog that is gone is its own failure: nothing would bring
    # the listener back if it died next.
    watchdog_alive = None if watchdog is None else (
        process_alive(watchdog) if alive is None else bool(alive(watchdog)))
    running = _codex_running(codex)
    # Order matters: a live listener with a fresh heartbeat is healthy whether
    # or not Codex happens to be open.
    if is_alive and age is not None and 0 <= age <= HEARTBEAT_STALE_SECONDS:
        ok, reason = True, None
    elif running is False:
        # The listener is supposed to exit with Codex. Nothing to fix here, and
        # the logon task starts it again on the next launch.
        ok, reason = True, 'not running because Codex is closed'
    elif heartbeat is None and running is None:
        ok, reason = False, 'no heartbeat and Codex state unknown'
    else:
        ok, reason = False, 'listener is not watching while Codex is running'
    if ok and watchdog_alive is False:
        ok, reason = False, f'watchdog pid {watchdog} is gone; nothing would restart the listener'
    return {'id': 'listener', 'ok': ok,
            'detail': {'pid': pid, 'alive': is_alive, 'heartbeat_age_seconds': age,
                       'watchdog_pid': watchdog, 'watchdog_alive': watchdog_alive,
                       'codex_running': running, 'reason': reason,
                       'desktop_running': status.get('desktop_running'),
                       'monitor_running': status.get('monitor_running'),
                       'dismissed': status.get('dismissed')}}


def _monitor_check(state, process=None):
    check = process or process_alive
    pid = read_pid(Path(state) / 'monitor.pid')
    report = read_json(Path(state) / 'usage.json')
    checked = report.get('checked_at')
    age = None if checked is None else round(time.time() - float(checked), 1)
    alive = check(pid)
    # Monitoring is expected to be idle while Codex is closed, so a missing
    # monitor is informative rather than a failure.
    return {'id': 'monitor', 'ok': True,
            'detail': {'pid': pid, 'alive': alive, 'report_age_seconds': age,
                       'connected': report.get('connected'), 'enabled': report.get('enabled'),
                       'candidates': len(report.get('candidates') or []),
                       'attempts': len(report.get('attempts') or []),
                       'ui_error_log': (Path(state) / 'ui-error.log').exists()}}


def _startup_check(state, task=None, task_query=None):
    query = task_query or scheduled_task_state
    if task is not None:
        info = task
    else:
        try:
            info = query()
        except Exception as error:  # a probe must never break the report
            info = {'registered': False, 'error': type(error).__name__}
    legacy = []
    startup = known_folder('Startup')
    if startup:
        for name in ('Codex Quota Resume.lnk',):
            if (Path(startup) / name).exists():
                legacy.append(str(Path(startup) / name))
    registered = bool(info.get('registered'))
    return {'id': 'startup', 'ok': registered,
            'detail': {**info, 'task_name': TASK_NAME, 'legacy_shortcuts': legacy}}


def _runtime_check(executable_dir, lookup):
    """The interpreter may be running from a directory that is not on PATH."""
    problems = []
    details = {}
    for name in ('pythonw.exe', 'python.exe', 'node.exe'):
        found = lookup(name)
        if not found:
            local = Path(executable_dir) / name
            found = str(local) if local.is_file() else None
        details[name] = found
        if not found:
            problems.append(name)
    return {'id': 'runtime', 'ok': not problems,
            'detail': {'executable_dir': str(executable_dir), 'found': details,
                       'missing': problems}}


def _logs_check(state):
    path = Path(state) / 'ui-error.log'
    if path.exists():
        try:
            stat = path.stat()
            return {'id': 'logs', 'ok': False,
                    'detail': {'ui_error_log': str(path), 'modified_at': stat.st_mtime,
                               'bytes': stat.st_size}}
        except OSError:
            pass
    return {'id': 'logs', 'ok': True, 'detail': {'ui_error_log': None}}


def checks(state=None, root=None, probes=None):
    """Assemble every health check. ``probes`` overrides individual dependencies.

    A probe that raises must not silently pass: the failing check is reported
    and the exit code stays non-zero, because an unknown answer is not a
    healthy answer.
    """
    probes = probes or {}
    state = Path(state) if state is not None else state_directory()
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    import shutil

    lookup = probes.get('lookup') or shutil.which
    runtime_dirs = [state / 'ui-runtime', root / '.runtime' / 'ui']
    builders = [
        ('state_dir', lambda: _state_dir_check(state, runtime_dirs)),
        ('runtime', lambda: _runtime_check(probes.get('executable_dir') or Path(sys.executable).parent, lookup)),
        ('startup', lambda: _startup_check(state, probes.get('task'), probes.get('task_query'))),
        ('listener', lambda: _listener_check(state, probes.get('alive'), probes.get('now'),
                                            probes.get('codex_running'))),
        ('monitor', lambda: _monitor_check(state, probes.get('process'))),
        ('logs', lambda: _logs_check(state)),
    ]
    results = []
    for name, build in builders:
        try:
            results.append(build())
        except Exception as error:
            results.append({'id': name, 'ok': False,
                            'detail': {'error': type(error).__name__, 'message': str(error)[:200]}})
    return results


def problems(results=None):
    return [item for item in (results if results is not None else checks()) if not item['ok']]


def summary(results=None):
    results = results if results is not None else checks()
    return {'ok': not problems(results), 'checks': results,
            'problems': [item['id'] for item in problems(results)]}


def main(argv=None, probes=None):
    import argparse
    parser = argparse.ArgumentParser(description='Check whether Codex quota monitoring can start and stay running')
    parser.add_argument('--state', help='override the state directory')
    arguments = parser.parse_args(argv)
    report = summary(checks(state=arguments.state, probes=probes))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
