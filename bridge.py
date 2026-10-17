"""Process-isolated desktop adapter; no account tokens are read."""
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import threading
from contextlib import closing


class NoContextError(RuntimeError):
    """No existing local root task can supply the desktop call context."""


class DesktopCallError(RuntimeError):
    """The desktop returned an explicit failure, rather than losing transport."""


def local_context_ids(directory=None):
    """Read IDs only from the newest local task index; never create a database.

    The index chooses a call context, not which task to resume. Eligibility and
    dispatch targets still come exclusively from fresh desktop tool responses.
    """
    root = Path(directory) if directory is not None else Path(os.environ.get('CODEX_HOME', Path.home()/'.codex'))
    try:
        indexes = [(int(p.stem.split('_')[1]), p) for p in root.glob('state_*.sqlite')
                   if p.stem.split('_')[1].isdigit()]
        if not indexes: return []
        path = max(indexes, key=lambda item: item[0])[1]
        # Do not fall back to an older index: it may still contain deleted IDs.
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            return [row[0] for row in db.execute(
                "SELECT id FROM threads WHERE archived=0 AND source NOT LIKE '%subagent%' "
                "ORDER BY updated_at DESC, id DESC LIMIT 32")]
    except (OSError, sqlite3.Error, ValueError):
        return []


class Desktop:
    def __init__(self, owner=None, contexts=None):
        # Accept the old argument for compatibility, but never bind to it.
        self.owner = None
        self.contexts = contexts if contexts is not None else local_context_ids
        self.context_rejections = {}
        self.context_sequence = 0
        self.process = None
        self.output = queue.Queue()
        self.counter = 0

    def close(self):
        if self.process:
            self.process.terminate()
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
            self.process = None

    def _start(self):
        self.close()
        node = os.environ.get('CODEX_MCP_NODE_PATH') or shutil.which('node')
        if not node:
            raise RuntimeError('Node.js not found')
        self.output = queue.Queue()
        self.process = subprocess.Popen([node, str(Path(__file__).with_name('desktop_bridge.mjs'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def pump(process, output):
            for line in process.stdout:
                output.put(line)
            output.put(None)
        threading.Thread(target=pump, args=(self.process,self.output),daemon=True).start()

    def call(self, tool, **args):
        if tool == 'send_message_to_thread':
            target = args.get('threadId')
            if not target: raise ValueError('Continuation target is required')
            # The target supplies its own call context. Never retry a send,
            # including a rejection: delivery may be ambiguous.
            return self._exchange(tool, target, **args)
        if tool not in ('get_usage_limits', 'list_threads', 'read_thread'):
            raise ValueError('Unsupported desktop tool')
        contexts = list(dict.fromkeys(self.contexts()))
        if not contexts: raise NoContextError('Waiting for an existing local Codex task')
        self.context_rejections = {key:value for key,value in self.context_rejections.items() if key in contexts}
        # Bound work per call, but rotate explicitly rejected contexts so a
        # valid fourth task cannot remain permanently hidden behind three bad IDs.
        contexts.sort(key=lambda owner:self.context_rejections.get(owner,0))
        if self.owner in contexts:
            contexts.remove(self.owner); contexts.insert(0, self.owner)
        for index, owner in enumerate(contexts[:3]):
            try:
                result = self._exchange(tool, owner, **args)
                self.owner = owner
                self.context_rejections.pop(owner,None)
                return result
            except DesktopCallError as error:
                self.owner = None
                # Only the generic routing rejection warrants trying another
                # context. Quota endpoint/network failures do not.
                if str(error) != 'Codex app tool request failed':raise
                self.context_sequence += 1
                self.context_rejections[owner] = self.context_sequence
                if index == min(3,len(contexts))-1:raise

    def _exchange(self, tool, owner, **args):
        if self.process is None or self.process.poll() is not None:
            self._start()
        self.counter += 1
        request = {'id':self.counter,'owner':owner,'tool':tool,'args':args}
        try:
            self.process.stdin.write(json.dumps(request)+'\n'); self.process.stdin.flush()
            line = self.output.get(timeout=50)
            if line is None: raise RuntimeError('Desktop bridge closed')
            response = json.loads(line)
            if response.get('id') != self.counter: raise RuntimeError('Mismatched desktop response')
            if 'error' in response: raise DesktopCallError(response['error'])
            return response['result']
        except (OSError, ValueError, queue.Empty):
            self.close()
            raise RuntimeError('Desktop bridge unavailable or timed out') from None
