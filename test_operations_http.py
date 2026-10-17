"""Local synthetic HTTP contracts. Never calls Gemini or reads production keys."""
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import operations as ops
import openai_compatible_server as server


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.patches=[]
        def fake_run(coro):
            coro.close()
            return {'id':'synthetic','model':'gemini-3-pro','choices':[{'message':{'content':'OK'}}]}
        state=SimpleNamespace(args=SimpleNamespace(api_key='master',default_model='gemini-3-pro',strict_pro=False,quiet=True,request_timeout=480),
                              clients=[('synthetic',object())],run_async=fake_run)
        for p in [patch.object(ops,'DATA',Path(self.tmp.name)),patch.object(server,'STATE',state),
                  patch.object(server,'GATE',ops.AdmissionGate()),patch.object(server,'model_names',return_value=['gemini-3-pro','gemini-4-argon'])]:
            p.start();self.addCleanup(p.stop)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.OpenAICompatibleHandler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.key=ops.create_key('client',['gemini-3-pro'],1,5)['key']

    def stop(self):
        self.http.shutdown();self.http.server_close();self.thread.join()

    def call(self,path,token=None,body=None):
        req=urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}'+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+(token or self.key),'Content-Type':'application/json'})
        try:r=urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=3)
        except urllib.error.HTTPError as exc:r=exc
        with r:return r.status,json.load(r)

    def test_scoped_models_and_no_admin_access(self):
        code,value=self.call('/v1/models');self.assertEqual(code,200)
        self.assertEqual([m['id'] for m in value['data']],['gemini-3-pro'])
        self.assertEqual(self.call('/health')[0],403)
        self.assertEqual(self.call('/internal/operations',body={'action':'drain'})[0],403)
        self.assertEqual(self.call('/v1/models',token='bad')[0],401)

    def test_request_scope_drain_resume_and_trace(self):
        payload={'model':'gemini-4-argon','messages':[{'role':'user','content':'synthetic'}]}
        code,result=self.call('/v1/chat/completions',body=payload)
        self.assertEqual(code,403);self.assertFalse(result['error']['submitted'])
        self.assertEqual(self.call('/internal/operations','master',{'action':'drain'})[0],200)
        payload['model']='gemini-3-pro'
        self.assertEqual(self.call('/v1/chat/completions',body=payload)[0],503)
        self.assertEqual(self.call('/internal/operations','master',{'action':'resume'})[0],200)
        self.assertEqual(self.call('/v1/chat/completions',body=payload)[0],200)
        events=ops.diagnostics()['events']
        self.assertTrue(any(e['stage']=='completed' for e in events))
        self.assertNotIn('synthetic',json.dumps(events))


if __name__=='__main__':unittest.main()
