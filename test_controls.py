import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MCPTests(unittest.TestCase):
    def test_real_stdio_server_status_and_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
                      {'jsonrpc':'2.0','method':'notifications/initialized'},
                      {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                      {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'quota_resume_control','arguments':{'action':'pause'}}},
                      {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'quota_resume_status'}}]
            run=subprocess.run([sys.executable,'mcp_server.py'],input='\n'.join(map(json.dumps,requests))+'\n',
                               text=True,encoding='utf-8',capture_output=True,
                               env=dict(os.environ,CODEX_QUOTA_RESUME_STATE=tmp),timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)
            rows=[json.loads(line) for line in run.stdout.splitlines()]
            self.assertEqual([r['id'] for r in rows],[1,2,3,4])
            self.assertEqual(rows[0]['result']['capabilities'],{'tools':{}})
            self.assertEqual(len(rows[1]['result']['tools']),2)
            status=json.loads(rows[3]['result']['content'][0]['text'])
            self.assertFalse(status['enabled'])
            self.assertTrue((Path(tmp)/'refresh.request').exists())

    def test_stdio_rejects_unknown_action(self):
        request={'id':1,'method':'tools/call','params':{'name':'quota_resume_control','arguments':{'action':'init'}}}
        run=subprocess.run([sys.executable,'mcp_server.py'],input=json.dumps(request)+'\n',text=True,capture_output=True,timeout=10)
        self.assertTrue(json.loads(run.stdout)['result']['isError'])

if __name__=='__main__':unittest.main()
