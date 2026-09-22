// Version-sensitive adapter to the local Codex Desktop app-tools pipe.
// Only the five allowlisted tools are reachable. No network listener or credentials.
import net from 'node:net';
import fs from 'node:fs';
import readline from 'node:readline';
import { randomUUID } from 'node:crypto';

const allowed = new Set(['get_usage_limits', 'list_threads', 'read_thread', 'send_message_to_thread']);
const maximum = 8 * 1024 * 1024;
let selected = null;
let catalog = new Map();
let counter = 0;

function request(pipe, method, params, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const id = ++counter;
    let pending = Buffer.alloc(0);
    const socket = net.createConnection(pipe);
    const timer = setTimeout(() => finish(new Error('Desktop request timed out; delivery may be unknown')), timeout);
    function finish(error, result) {
      clearTimeout(timer); socket.destroy();
      error ? reject(error) : resolve(result);
    }
    socket.on('error', e => finish(e));
    socket.on('end', () => finish(new Error('Desktop connection closed')));
    socket.on('connect', () => {
      const body = Buffer.from(JSON.stringify({jsonrpc: '2.0', id, method, params}));
      const header = Buffer.alloc(4); header.writeUInt32LE(body.length);
      socket.write(Buffer.concat([header, body]));
    });
    socket.on('data', chunk => {
      pending = Buffer.concat([pending, chunk]);
      if (pending.length > maximum + 4) return finish(new Error('Desktop response too large'));
      while (pending.length >= 4) {
        const size = pending.readUInt32LE(0);
        if (size > maximum) return finish(new Error('Desktop response too large'));
        if (pending.length < size + 4) return;
        let result;
        try { result = JSON.parse(pending.subarray(4, size + 4).toString('utf8')); }
        catch { return finish(new Error('Invalid desktop response')); }
        pending = pending.subarray(size + 4);
        if (result.id !== id) continue;
        if (result.error) return finish(new Error(result.error.message));
        return finish(null, result.result);
      }
    });
  });
}

async function connect() {
  if (selected) return;
  const candidates = new Set();
  if (process.env.CODEX_APP_TOOLS_PIPE_PATH) candidates.add(process.env.CODEX_APP_TOOLS_PIPE_PATH);
  if (process.platform === 'win32') {
    for (const name of fs.readdirSync('\\\\.\\pipe\\')) {
      if (/^codex-browser-use-[a-f0-9-]+$/.test(name)) candidates.add('\\\\.\\pipe\\' + name);
    }
  }
  for (const pipe of candidates) {
    try {
      const result = await request(pipe, 'tools/list', {threadStartKind: 'all'}, 2000);
      const tools = new Map((result.tools || []).map(t => [t.name, t]));
      if ([...allowed].every(name => tools.has(name))) { selected = pipe; catalog = tools; return; }
    } catch { /* Other Codex surfaces may have a different catalog. */ }
  }
  throw new Error('Codex Desktop app-tools pipe unavailable; open Codex or update this adapter');
}

function unpack(result) {
  if (!result?.success) throw new Error('Desktop rejected: ' + (result?.contentItems || []).filter(c => c.type === 'inputText').map(c => c.text).join(' ').slice(0, 1000));
  const texts = (result.contentItems || []).filter(c => c.type === 'inputText').map(c => c.text);
  for (const text of texts) {
    try { return JSON.parse(text); } catch { /* Plain text is not a valid state snapshot. */ }
  }
  throw new Error('Desktop returned no structured result');
}

function compact(tool, value) {
  if (tool === 'read_thread') {
    return {thread: value.thread, turns: (value.turns || []).slice(0, 1).map(t => ({
      id: t.id, status: t.status, error: t.error, completedAt: t.completedAt, startedAt: t.startedAt
    }))};
  }
  if (tool === 'list_threads') {
    const keep = t => ({id: t.id, kind: t.kind, hostId: t.hostId, status: t.status, title: t.title});
    return {threads: (value.threads || []).map(keep), pinnedThreads: (value.pinnedThreads || []).map(keep),
      unavailableHosts: value.unavailableHosts, unavailableSources: value.unavailableSources};
  }
  if (tool === 'get_usage_limits') {
    const {ordinaryUsageAllowed, rateLimits, rateLimitsByLimitId} = value;
    return {ordinaryUsageAllowed, rateLimits, rateLimitsByLimitId};
  }
  return value;
}

const lines = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
for await (const line of lines) {
  let input;
  try {
    input = JSON.parse(line);
    if (!allowed.has(input.tool) || typeof input.owner !== 'string' || !input.owner) throw new Error('Invalid bridge request');
    await connect();
    const result = await request(selected, 'tools/call', {
      namespace: catalog.get(input.tool).namespace, tool: input.tool, arguments: input.args || {},
      threadId: input.owner, turnId: `quota-monitor-${randomUUID()}`, callId: `quota-monitor-${randomUUID()}`
    }, input.tool === 'send_message_to_thread' ? 30000 : 20000);
    process.stdout.write(JSON.stringify({id: input.id, result: compact(input.tool, unpack(result))}) + '\n');
  } catch (error) {
    selected = null;
    process.stdout.write(JSON.stringify({id: input?.id, error: String(error.message || error)}) + '\n');
  }
}
