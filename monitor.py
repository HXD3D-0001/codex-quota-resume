"""Persistent, quota-independent monitor. Only sends on verified eligibility."""
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

from core import normalize_usage, quota_ready, next_poll_delay, candidate_key

CONTINUE_PROMPT = ('额度恢复助手：请继续本任务之前已经获得授权但尚未完成的工作，'
                   '先核对当前文件、进程及上一次执行结果，从中断处接着完成，避免重复已完成的操作。'
                   '保留本任务的原有目标、模型设置和权限边界。'
                   '如果已经完成、用户已要求停止，或必须等待用户提供信息/批准，请停止并说明。')


def state_directory():
    return Path(os.environ.get('CODEX_QUOTA_RESUME_STATE',
                str(Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'CodexQuotaResume')))


class Store:
    def __init__(self, directory=None, now=None):
        self.directory = Path(directory) if directory is not None else state_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'state.sqlite', timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS marks (thread_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
                key TEXT PRIMARY KEY, thread_id TEXT NOT NULL, status TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL);
        ''')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',('activated_at',str(time.time() if now is None else now)))
            self.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',('enabled','true'))

    def close(self): self.db.close()
    def get(self,key,default=None):
        row=self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0] if row else default
    def set(self,key,value):
        with self.db:self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,str(value)))
    def enabled(self):return self.get('enabled')=='true'
    def set_enabled(self,value):self.set('enabled','true' if value else 'false')
    def mark(self,thread,turn):
        with self.db:self.db.execute('INSERT OR REPLACE INTO marks VALUES (?,?)',(thread,turn))
    def unmark(self,thread):
        with self.db:self.db.execute('DELETE FROM marks WHERE thread_id=?',(thread,))
    def marked(self,thread):
        row=self.db.execute('SELECT turn_id FROM marks WHERE thread_id=?',(thread,)).fetchone()
        return row[0] if row else None
    def claimed(self,key):return self.db.execute('SELECT 1 FROM attempts WHERE key=?',(key,)).fetchone() is not None
    def claim(self,key,thread,now):
        with self.db:
            cursor=self.db.execute('INSERT OR IGNORE INTO attempts VALUES (?,?,?,?,?)',(key,thread,'dispatching',now,now))
            return cursor.rowcount==1
    def finish(self,key,status,now):
        with self.db:self.db.execute('UPDATE attempts SET status=?,updated_at=? WHERE key=?',(status,now,key))
    def attempts(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM attempts ORDER BY created_at DESC LIMIT 50')]
    def report(self,value):
        target=self.directory/'usage.json'
        temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(target)
    def last_report(self):
        try:return json.loads((self.directory/'usage.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}


class Monitor:
    def __init__(self,store,desktop,clock=time.time):
        self.store,self.desktop,self.clock=store,desktop,clock

    def detail(self,thread):
        return self.desktop.call('read_thread',threadId=thread,turnLimit=1,
                                 includeOutputs=False,maxOutputCharsPerItem=1)

    def tick(self):
        now=self.clock()
        report={'checked_at':now,'enabled':self.store.enabled(),'connected':False,
                'candidates':[],'attempts':self.store.attempts()}
        try:
            data=normalize_usage(self.desktop.call('get_usage_limits'),self.clock())
            report.update(usage=data,connected=True)
            listing=self.desktop.call('list_threads',limit=30)
            rows=listing.get('threads',[])+listing.get('pinnedThreads',[])
            # The desktop supplies at most 30 recent unpinned tasks; pinned tasks are complete.
            report['scan_limit']=30
            report['scan_may_be_truncated']=len(listing.get('threads',[]))>=30
            unique={row['id']:row for row in rows if row.get('kind')=='codex' and row.get('hostId','local')=='local'}
            report['scanned']=len(unique)
            activated=float(self.store.get('activated_at'))
            for thread,row in unique.items():
                if row.get('status')!='idle':continue
                detail=self.detail(thread)
                marked=self.store.marked(thread)
                if marked and detail.get('turns') and detail['turns'][0].get('id')!=marked:
                    self.store.unmark(thread);marked=None
                key=candidate_key(detail,marked,activated)
                if not key or self.store.claimed(key):continue
                report['candidates'].append({'thread_id':thread,'key':key})
                if not self.store.enabled() or not quota_ready(data,self.clock()):continue
                # Check latest turn again immediately before dispatch. New user work wins.
                latest=self.detail(thread)
                if candidate_key(latest,self.store.marked(thread),activated)!=key:continue
                fresh=normalize_usage(self.desktop.call('get_usage_limits'),self.clock())
                report['usage']=fresh
                if not quota_ready(fresh,self.clock()) or not self.store.enabled():continue
                if not self.store.claim(key,thread,self.clock()):continue
                try:
                    response=self.desktop.call('send_message_to_thread',threadId=thread,hostId='local',prompt=CONTINUE_PROMPT)
                    # Any returned failure stays uncertain; never turn an ambiguous response into a resend.
                    if response.get('error') or response.get('status') in ('failed','error','rejected'):
                        raise RuntimeError('Desktop did not confirm dispatch')
                    self.store.finish(key,'sent',self.clock())
                    self.store.unmark(thread)
                except Exception:
                    self.store.finish(key,'uncertain',self.clock())
                # One per tick avoids a burst of competing continuations.
                break
        except Exception as error:
            report['connected']=False
            report['error']=type(error).__name__  # No raw desktop messages/session data in logs.
            report.setdefault('usage',self.store.last_report().get('usage',{}))
        report['enabled']=self.store.enabled()
        report['attempts']=self.store.attempts()
        report['next_poll_seconds']=next_poll_delay(report.get('usage',{}),self.clock()) if report['connected'] else 15
        self.store.report(report)
        return report


class SingleInstance:
    def __init__(self,directory):
        self.directory=Path(directory);self.handle=None
    def __enter__(self):
        import msvcrt
        self.directory.mkdir(parents=True,exist_ok=True)
        self.handle=(self.directory/'monitor.lock').open('a+b')
        self.handle.seek(0);self.handle.write(b'0');self.handle.flush();self.handle.seek(0)
        try:msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            self.handle.close();self.handle=None
            raise RuntimeError('Quota monitor is already running') from None
        (self.directory/'monitor.pid').write_text(str(os.getpid()),encoding='ascii')
        return self
    def __exit__(self,*args):
        if self.handle:
            import msvcrt
            self.handle.seek(0);msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1);self.handle.close()
            (self.directory/'monitor.pid').unlink(missing_ok=True)


def run_worker(stop=None):
    from bridge import Desktop
    stop=stop or threading.Event()
    store=Store();desktop=Desktop(store.get('owner',''))
    try:
        while not stop.is_set():
            if (store.directory/'stop.request').exists():break
            report=Monitor(store,desktop).tick()
            # Short local waits make explicit refresh/exit responsive without extra network calls.
            deadline=time.monotonic()+report['next_poll_seconds']
            while not stop.wait(min(1,max(0,deadline-time.monotonic()))):
                refresh=store.directory/'refresh.request'
                if refresh.exists():refresh.unlink(missing_ok=True);break
                if (store.directory/'stop.request').exists() or time.monotonic()>=deadline:break
    finally:
        desktop.close();store.close()
