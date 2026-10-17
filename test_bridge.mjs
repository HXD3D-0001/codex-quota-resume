import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import readline from 'node:readline';

for (const discovery of ['explicit', 'app-tools', 'browser-use']) {
test(`desktop transport handles split frames, privacy and exact target (${discovery})`, {skip: discovery!=='explicit' && process.platform!=='win32'}, async () => {
  const name=discovery==='explicit' ? `quota-test-${randomUUID()}` : `codex-${discovery}-${randomUUID()}`;
  const pipe=process.platform==='win32' ? `\\\\.\\pipe\\${name}` : `/tmp/${name}.sock`;
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
        if (message.params.callerSource !== 'codex') {
          const payload=Buffer.from(JSON.stringify({jsonrpc:'2.0',id:message.id,error:{code:-32602,message:'Invalid app tool request'}}));
          const prefix=Buffer.alloc(4);prefix.writeUInt32LE(payload.length);
          socket.write(Buffer.concat([prefix,payload]));
          return;
        }
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
  const env={...process.env};
  const args=[];
  if (discovery==='explicit') env.CODEX_APP_TOOLS_PIPE_PATH=pipe;
  else {
    delete env.CODEX_APP_TOOLS_PIPE_PATH;
    // Isolate discovery from the user's actual desktop pipes. All calls must
    // reach this test server, including the simulated continuation below.
    const bootstrap=`import fs from 'node:fs'; const original=fs.readdirSync; fs.readdirSync=function(path,...args){return path===${JSON.stringify('\\\\.\\pipe\\')} ? ${JSON.stringify([name,'unrelated-pipe'])} : original.call(this,path,...args);};`;
    args.push('--import',`data:text/javascript,${encodeURIComponent(bootstrap)}`);
  }
  args.push('desktop_bridge.mjs');
  const child=spawn(process.execPath,args,{env,stdio:['pipe','pipe','pipe']});
  try {
    const replies=readline.createInterface({input:child.stdout})[Symbol.asyncIterator]();
    child.stdin.write(JSON.stringify({id:1,owner:'manager',tool:'read_thread',args:{threadId:'target'}})+'\n');
    const first=JSON.parse((await replies.next()).value);
    assert.equal(first.error, undefined, 'current desktop must accept the app-tool request');
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
}
