"""Small stdio MCP control surface; daemon lifetime is independent of MCP."""
import json
import sys
from control import execute

TOOLS=[
    {'name':'quota_resume_status','description':'Read the quota monitor status, pending candidates and delivery attempts.','inputSchema':{'type':'object','properties':{},'additionalProperties':False}},
    {'name':'quota_resume_control','description':'Enable/pause the quota monitor, request a refresh, or explicitly mark/unmark a task for continuation when quota is available. Mark only when the user asked to continue that exact task.','inputSchema':{'type':'object','properties':{'action':{'type':'string','enum':['enable','pause','refresh','mark','unmark']},'thread_id':{'type':'string'}},'required':['action'],'additionalProperties':False}}
]


def handle(message):
    method=message.get('method');params=message.get('params',{})
    if method=='initialize':return {'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'codex-quota-resume','version':'0.1.0'}}
    if method=='ping':return {}
    if method=='tools/list':return {'tools':TOOLS}
    if method=='tools/call':
        try:
            name=params.get('name');args=params.get('arguments',{})
            if name=='quota_resume_status':result=execute('status')
            elif name=='quota_resume_control' and args.get('action') in ('enable','pause','refresh','mark','unmark'):
                result=execute(args['action'],args.get('thread_id'))
            else:raise ValueError('Unknown tool or action')
            return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}]}
        except Exception as error:return {'isError':True,'content':[{'type':'text','text':str(error)}]}
    raise ValueError('Method not found')


def main():
    sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
    for line in sys.stdin:
        try:
            message=json.loads(line)
            if 'id' not in message:continue
            response={'jsonrpc':'2.0','id':message['id'],'result':handle(message)}
        except Exception:
            response={'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid request'}}
        print(json.dumps(response,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
