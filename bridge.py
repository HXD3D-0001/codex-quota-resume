"""Process-isolated desktop adapter; no account tokens are read."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading


class Desktop:
    def __init__(self, owner):
        self.owner = owner
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
        if self.process is None or self.process.poll() is not None:
            self._start()
        self.counter += 1
        request = {'id':self.counter,'owner':self.owner,'tool':tool,'args':args}
        try:
            self.process.stdin.write(json.dumps(request)+'\n'); self.process.stdin.flush()
            line = self.output.get(timeout=50)
            if line is None: raise RuntimeError('Desktop bridge closed')
            response = json.loads(line)
            if response.get('id') != self.counter: raise RuntimeError('Mismatched desktop response')
            if 'error' in response: raise RuntimeError(response['error'])
            return response['result']
        except (OSError, ValueError, queue.Empty):
            self.close()
            raise RuntimeError('Desktop bridge unavailable or timed out') from None
