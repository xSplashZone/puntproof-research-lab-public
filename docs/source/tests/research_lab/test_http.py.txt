import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from server import Application, LabServer

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        base=Path(cls.temp.name)
        cls.app=Application(base/"lab",base/"absent_repo",base/"absent_pack",start_worker=False)
        cls.server=LabServer(("127.0.0.1",0),cls.app)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.port=cls.server.server_port
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
        cls.temp.cleanup()
    def request(self,path,method="GET",body=None,headers=None):
        c=http.client.HTTPConnection("127.0.0.1",self.port,timeout=10)
        c.request(method,path,body=body,headers=headers or {})
        r=c.getresponse()
        result=(r.status,dict(r.getheaders()),r.read())
        c.close()
        return result
    def session(self):
        status,headers,_=self.request("/")
        self.assertEqual(status,200)
        cookie=headers["Set-Cookie"].split(";")[0]
        status,_,raw=self.request("/api/session",headers={"Cookie":cookie})
        self.assertEqual(status,200)
        return {"Cookie":cookie,"X-CSRF-Token":json.loads(raw)["csrf"],
                "Origin":f"http://127.0.0.1:{self.port}","Content-Type":"application/json"}
    def test_auth_required(self):
        self.assertEqual(self.request("/api/state")[0],401)
    def test_rebinding_host_denied(self):
        self.assertEqual(self.request("/",headers={"Host":"attacker.invalid"})[0],403)
    def test_csrf_and_cross_origin_denied(self):
        headers=self.session();headers["Origin"]="https://attacker.invalid"
        self.assertEqual(self.request("/api/action","POST",'{"action":"pause"}',headers)[0],403)
        headers=self.session();headers.pop("X-CSRF-Token")
        self.assertEqual(self.request("/api/action","POST",'{"action":"pause"}',headers)[0],403)
    def test_rejected_post_drains_bounded_body_without_action(self):
        # Larger than a small socket buffer: premature close with unread bytes
        # previously hid the denial behind WinError 10053 on Windows.
        body=json.dumps({"action":"pause","padding":"x"*262144})
        cases=(
            ("origin", "/api/action", {"Origin":"https://attacker.invalid"},403),
            ("csrf", "/api/action", {"X-CSRF-Token":"wrong"},403),
            ("session", "/api/action", {"Cookie":"lab_session=wrong"},403),
            ("host", "/api/action", {"Host":"attacker.invalid"},403),
            ("path", "/not-an-action", {},404),
            ("content-type", "/api/action", {"Content-Type":"text/plain"},415),
        )
        for name,path,changed,expected in cases:
            with self.subTest(denial=name):
                self.app.store.set_setting("worker_paused",False)
                headers=self.session();headers.update(changed)
                status,response_headers,raw=self.request(path,"POST",body,headers)
                self.assertEqual(status,expected,raw)
                self.assertEqual(response_headers.get("Connection"),"close")
                self.assertIn("error",json.loads(raw))
                self.assertFalse(self.app.store.get_setting("worker_paused"))

    def test_state_real_roi_unknown(self):
        headers=self.session()
        status,_,raw=self.request("/api/state",headers=headers)
        state=json.loads(raw)
        self.assertEqual(status,200)
        self.assertEqual(state["overview"]["genuine_roi"],"ROI_NOT_ESTABLISHED")
        self.assertGreaterEqual(len(state["knowledge"]),30)
        self.assertIsNone(next(r for r in state["ledger"] if r["mode"]=="REAL")["roi"])
    def test_pause_resume_persist(self):
        h=self.session()
        for action,expected in (("pause",True),("resume",False)):
            self.assertEqual(self.request("/api/action","POST",json.dumps({"action":action}),h)[0],200)
            self.assertEqual(self.app.store.get_setting("worker_paused"),expected)
    def test_manual_refresh_only_overrides_idle_for_a_queued_connection(self):
        h=self.session();worker=self.app.worker;store=self.app.store
        store.set_setting('worker_manual_run_requested',False)
        store.set_setting('worker_paused',True)
        with patch.object(worker.operations,'fingerprint',return_value='authored-manual-refresh'),patch.object(worker,'wake') as wake:
            status,_,raw=self.request('/api/action','POST','{"action":"run_ready"}',h)
            queued=json.loads(raw)
            self.assertEqual(status,200,raw)
            self.assertEqual(queued['status'],'QUEUED')
            self.assertTrue(store.get_setting('worker_manual_run_requested'))
            self.assertTrue(store.get_setting('worker_paused'))
            wake.assert_called_once()
            wake.reset_mock()
            for existing in ('SUCCEEDED','RUNNING','FAILED'):
                with self.subTest(existing=existing):
                    with store.connect() as db:
                        db.execute('UPDATE lab_jobs SET status=? WHERE id=?',(existing,queued['id']))
                    store.set_setting('worker_manual_run_requested',False)
                    status,_,raw=self.request('/api/action','POST','{"action":"run_ready"}',h)
                    self.assertEqual(status,200,raw)
                    self.assertEqual(json.loads(raw),{'id':queued['id'],'status':existing})
                    self.assertFalse(store.get_setting('worker_manual_run_requested'))
                    wake.assert_not_called()
            self.assertEqual(len(store.rows("SELECT id FROM lab_jobs WHERE kind='connect'")),1)
        store.set_setting('worker_paused',False)
        self.assertTrue(worker.run_lock.acquire(blocking=False))
        worker.run_lock.release()
    def test_manual_refresh_is_nonblocking_while_worker_is_active(self):
        h=self.session();worker=self.app.worker;store=self.app.store
        store.set_setting('worker_manual_run_requested',False)
        self.assertTrue(worker.run_lock.acquire(blocking=False))
        try:
            with patch.object(worker.operations,'fingerprint') as fingerprint,patch.object(worker,'enqueue') as enqueue,patch.object(worker,'wake') as wake:
                status,_,raw=self.request('/api/action','POST','{"action":"run_ready"}',h)
                self.assertEqual(status,200,raw)
                self.assertEqual(json.loads(raw)['status'],'BUSY')
                self.assertFalse(store.get_setting('worker_manual_run_requested'))
                fingerprint.assert_not_called();enqueue.assert_not_called();wake.assert_not_called()
        finally:
            worker.run_lock.release()
        with patch.object(worker.operations,'fingerprint',side_effect=ValueError('Authored missing metadata')):
            self.assertEqual(self.request('/api/action','POST','{"action":"run_ready"}',h)[0],400)
        self.assertTrue(worker.run_lock.acquire(blocking=False))
        worker.run_lock.release()
    def test_settings_validated(self):
        h=self.session()
        self.assertEqual(self.request("/api/action","POST",json.dumps({"action":"settings","values":{"interval_seconds":0}}),h)[0],400)
        self.assertEqual(self.request("/api/action","POST",json.dumps({"action":"settings","values":{"interval_seconds":3600}}),h)[0],200)
    def test_knowledge_proposal_reaches_persistent_queue(self):
        h=self.session()
        concept=self.app.store.records("knowledge")[0]["id"]
        status,_,raw=self.request("/api/action","POST",json.dumps({"action":"propose","concept_id":concept}),h)
        self.assertEqual(status,200,raw)
        self.assertTrue(self.app.store.records("experiment"))
    def test_scientific_actions_blocked(self):
        h=self.session()
        for action in ("run_experiment","refit","forecast","promote","run_admitted"):
            status,_,raw=self.request("/api/action","POST",json.dumps({"action":action}),h)
            self.assertEqual(status,403,raw)
    def test_backup_download_and_traversal(self):
        h=self.session()
        status,_,raw=self.request("/api/action","POST",'{"action":"backup"}',h)
        self.assertEqual(status,200)
        self.assertEqual(self.request(json.loads(raw)["download"],headers=h)[0],200)
        self.assertEqual(self.request("/api/download/..%5Clab.sqlite3",headers=h)[0],400)
    def test_security_headers(self):
        _,headers,_=self.request("/")
        self.assertIn("frame-ancestors 'none'",headers["Content-Security-Policy"])
        self.assertIn("SameSite=Strict",headers["Set-Cookie"])

    def test_utf8_assets_and_json_survive_windows_edits(self):
        code,headers,raw=self.request('/app.js')
        self.assertEqual(code,200)
        self.assertIn('charset=utf-8',headers['Content-Type'])
        text=raw.decode('utf-8')
        self.assertIn("'Needs attention'",text)
        self.assertIn(chr(0x25cc),text)
        self.assertNotIn(chr(0xfffd),text)
        self.assertIn(chr(0xb7),text)
        self.assertNotIn(chr(0xe2)+chr(0x2014),text)
        self.assertNotIn(chr(0xc2)+chr(0xb7),text)
        _,_,raw=self.request('/api/state',headers=self.session())
        self.assertIn(chr(0x2014),json.loads(raw)['overview']['challenger'])
