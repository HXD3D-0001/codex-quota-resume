import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import readline from 'node:readline';

test('desktop transport handles split frames, strips content and preserves exact resume target', async () => {
  const pipe=process.platform==='win32' ? `\\\\.\\pipe\\quota-test-${randomUUID()}` : `/tmp/quota-test-${randomUUID()}.sock`;
  const calls=[];
  const sockets=new Set();
  const server=net.createServer(socket=>{
    sockets.add(socket);socket.on('close',()=>sockets.delete(socket));
    let data=Buffer.alloc(0);
    socket.on('data',chunk=>{
      data=Buffer.concat([data,chunk]);
      if(data.length<4 || data.length<data.readUInt32LE(0)+4)return;
      const message=JSON.parse(data.subarray(4).toString());
      let result;
      if(message.method==='tools/list')result={tools:['get_usage_limits','list_threads','read_thread','send_message_to_thread'].map(name=>({name,namespace:'codex_app'}))};
      else {
        calls.push(message.params);
        const value=message.params.tool==='read_thread' ? {thread:{id:'target'},turns:[{id:'t',status:'failed',items:[{text:'PRIVATE'}]}]} : {status:'sent'};
        result={success:true,contentItems:[{type:'inputText',text:JSON.stringify(value)}]};
      }
      const payload=Buffer.from(JSON.stringify({jsonrpc:'2.0',id:message.id,result}));
      const prefix=Buffer.alloc(4);prefix.writeUInt32LE(payload.length);
      socket.write(prefix.subarray(0,2));
      setImmediate(()=>socket.write(Buffer.concat([prefix.subarray(2),payload])));
    });
  });
  await new Promise(resolve=>server.listen(pipe,resolve));
  const child=spawn(process.execPath,['desktop_bridge.mjs'],{env:{...process.env,CODEX_APP_TOOLS_PIPE_PATH:pipe},stdio:['pipe','pipe','pipe']});
  try {
    const replies=readline.createInterface({input:child.stdout})[Symbol.asyncIterator]();
    child.stdin.write(JSON.stringify({id:1,owner:'manager',tool:'read_thread',args:{threadId:'target'}})+'\n');
    const first=JSON.parse((await replies.next()).value);
    assert.equal(first.result.turns[0].id,'t');
    assert.equal(JSON.stringify(first).includes('PRIVATE'),false);
    child.stdin.write(JSON.stringify({id:2,owner:'manager',tool:'send_message_to_thread',args:{threadId:'target',prompt:'continue'}})+'\n');
    const second=JSON.parse((await replies.next()).value);
    assert.equal(second.result.status,'sent');
    assert.equal(calls[1].arguments.threadId,'target');
    assert.equal(calls[1].threadId,'manager');
    assert.equal(calls[1].arguments.model,undefined);
    child.stdin.write(JSON.stringify({id:3,owner:'manager',tool:'consume_usage_reset'})+'\n');
    const third=JSON.parse((await replies.next()).value);
    assert.ok(third.error);assert.equal(calls.length,2);
  } finally {
    child.kill();for(const socket of sockets)socket.destroy();
    await new Promise(resolve=>server.close(resolve));
  }
});
