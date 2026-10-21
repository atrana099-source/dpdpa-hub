import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ADMIN_PASSWORD", "TestPassw0rd-123")

from app import create_app, db  # noqa: E402
import manage  # noqa: E402

PW = "TestPassw0rd-123"


class Base(unittest.TestCase):
    def setUp(self):
        from app import auth, views_dsr
        auth._failures.clear()
        views_dsr._hits.clear()
        self.tmp = tempfile.mkdtemp()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "DATABASE_URL": "sqlite:///" + os.path.join(self.tmp, "t.db")})
        with self.app.app_context():
            manage.cmd_init()
        self.c = self.app.test_client()

    def token(self, client=None):
        client = client or self.c
        with client.session_transaction() as s:
            return s.get("csrf")

    def login(self, user="admin", pw=PW, client=None):
        client = client or self.c
        client.get("/login")
        return client.post("/login", data={"username": user, "password": pw, "csrf_token": self.token(client)})

    def post(self, url, data=None, client=None, **kw):
        client = client or self.c
        client.get("/login") if not self.token(client) else None
        d = dict(data or {})
        d["csrf_token"] = self.token(client)
        return client.post(url, data=d, **kw)

    def add_user(self, name, role):
        with self.app.app_context():
            from app.auth import create_user
            create_user(name, "Another-Pass-123", role)


class TestAuth(Base):
    def test_requires_login(self):
        self.assertEqual(self.c.get("/").status_code, 302)
        self.assertEqual(self.c.get("/e/gaps").status_code, 302)

    def test_login_ok_and_bad(self):
        self.assertEqual(self.login(pw="wrong-password").status_code, 401)
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.c.get("/").status_code, 200)

    def test_csrf_required(self):
        self.login()
        r = self.c.post("/e/gaps/new", data={"code": "G-1", "finding": "x"})
        self.assertEqual(r.status_code, 400)

    def test_lockout(self):
        self.c.get("/login")
        for _ in range(6):
            r = self.post("/login", {"username": "admin", "password": "nope-nope-nope"})
        self.assertEqual(r.status_code, 429)

    def test_roles(self):
        self.add_user("view", "viewer")
        self.add_user("mgr", "manager")
        v = self.app.test_client()
        self.login("view", "Another-Pass-123", v)
        self.assertEqual(v.get("/e/gaps").status_code, 200)
        self.assertEqual(self.post("/e/gaps/new", {"code": "G-9", "finding": "x"}, v).status_code, 403)
        self.assertEqual(v.get("/users").status_code, 403)
        self.assertEqual(v.get("/audit").status_code, 403)
        m = self.app.test_client()
        self.login("mgr", "Another-Pass-123", m)
        self.assertEqual(m.get("/e/gaps/new").status_code, 200)
        self.assertEqual(m.get("/users").status_code, 403)
        self.assertEqual(self.post("/consent/apikeys", {"action": "create", "name": "x"}, m).status_code, 403)


class TestRegisters(Base):
    def test_crud_and_export(self):
        self.login()
        r = self.post("/e/gaps/new", {"code": "G-100", "finding": "Test finding", "priority": "High", "status": "Open", "department": "HR"})
        self.assertEqual(r.status_code, 302)
        self.assertIn(b"G-100", self.c.get("/e/gaps?q=test+finding").data)
        self.assertIn(b"G-100", self.c.get("/e/gaps/export.csv").data)
        with self.app.app_context():
            rid = db.scalar("SELECT id FROM gaps WHERE code = 'G-100'")
        self.post(f"/e/gaps/{rid}", {"code": "G-100", "finding": "Edited finding", "priority": "Low", "status": "Closed"})
        self.assertIn(b"Edited finding", self.c.get(f"/e/gaps/{rid}").data)
        self.post(f"/e/gaps/{rid}/delete")
        self.assertNotIn(b"G-100", self.c.get("/e/gaps").data)

    def test_import_tracker(self):
        with self.app.app_context():
            manage.cmd_seed_tracker()
            self.assertGreater(db.scalar("SELECT COUNT(*) FROM gaps"), 40)
            self.assertEqual(db.scalar("SELECT COUNT(*) FROM departments"), 13)
        self.login()
        self.assertEqual(self.c.get("/").status_code, 200)

    def test_csv_import(self):
        import io
        self.login()
        csvdata = "ID,Department,Theme,Finding,Type,Priority,DPDP reference,Source,Action ID,Status,Target date,Named owner,Evidence / remarks\n"
        csvdata = "code,department,theme,finding,type,priority,dpdp_ref,source,action_code,status,target_date,owner,remarks\nG-7,HR,Retention,Imported finding,Gap,High,,,,Open,,,\n"
        r = self.post("/e/gaps/import", {"file": (io.BytesIO(csvdata.encode()), "g.csv")}, content_type="multipart/form-data")
        self.assertIn(r.status_code, (200, 302))
        with self.app.app_context():
            self.assertEqual(db.scalar("SELECT COUNT(*) FROM gaps WHERE code = 'G-7'"), 1)


class TestConsent(Base):
    def test_flow_and_chain(self):
        from app import consent_service as cs
        with self.app.app_context():
            r1 = cs.record_consent("a@example.com", "guest-booking", ["BOOKING", "MARKETING"])
            r2 = cs.record_consent("+91 98765 43210", "guest-booking", ["BOOKING"])
            self.assertTrue(cs.has_consent("a@example.com", "MARKETING"))
            cs.withdraw_consent("a@example.com", "guest-booking", ["MARKETING"])
            self.assertFalse(cs.has_consent("a@example.com", "MARKETING"))
            self.assertTrue(cs.has_consent("a@example.com", "BOOKING"))
            self.assertTrue(cs.has_consent("9876543210", "BOOKING"))
            self.assertTrue(cs.verify_chain()["ok"])
            self.assertEqual(r1["receipt_id"][:3], "CR-")
            # no raw identifier stored
            row = db.one("SELECT * FROM consent_events WHERE id = 1")
            self.assertNotIn("a@example.com", " ".join(str(v) for v in row.values()))
            with self.assertRaises(cs.ConsentError):
                cs.record_consent("a@example.com", "guest-booking", ["NOT_A_PURPOSE"])
            # tamper
            db.execute("UPDATE consent_events SET granted = 'BOOKING,MARKETING,KYC' WHERE id = 2")
            res = cs.verify_chain()
            self.assertFalse(res["ok"])
            self.assertEqual(res["first_bad_id"], 2)

    def test_hosted_page(self):
        self.assertEqual(self.c.get("/c/guest-booking").status_code, 200)
        r = self.post("/c/guest-booking", {"principal_ref": "x@example.com", "purpose": ["BOOKING", "FEEDBACK"]})
        self.assertIn(b"CR-", r.data)
        m = re.search(rb"/receipt/(CR-[A-Z0-9]+)", r.data)
        self.assertEqual(self.c.get("/receipt/" + m.group(1).decode()).status_code, 200)
        r = self.post("/withdraw/guest-booking", {"principal_ref": "x@example.com"})
        self.assertIn(b"withdrawn", r.data)
        self.assertEqual(self.c.get("/c/missing").status_code, 404)

    def test_notice_versioning(self):
        self.login()
        with self.app.app_context():
            nid = db.scalar("SELECT id FROM notices WHERE slug = 'guest-booking'")
        self.assertEqual(self.post(f"/consent/notices/{nid}", {"title": "Changed", "body": "x", "purpose_codes": "BOOKING"}).status_code, 403)
        self.post(f"/consent/notices/{nid}/newversion")
        with self.app.app_context():
            new = db.one("SELECT * FROM notices WHERE version = 2")
            self.assertEqual(new["status"], "Draft")
        self.post(f"/consent/notices/{new['id']}/publish")
        with self.app.app_context():
            self.assertEqual(db.one("SELECT status FROM notices WHERE version = 1")["status"], "Superseded")
            self.assertEqual(db.one("SELECT status FROM notices WHERE version = 2")["status"], "Published")

    def test_api(self):
        self.login()
        r = self.post("/consent/apikeys", {"action": "create", "name": "t"})
        key = re.search(rb"dpk_[A-Za-z0-9_\-]+", r.data).group(0).decode()
        h = {"X-API-Key": key}
        api = self.app.test_client()
        self.assertEqual(api.post("/api/v1/consents", json={}).status_code, 401)
        r = api.post("/api/v1/consents", json={"principal_ref": "p@example.com", "notice_slug": "guest-booking", "granted": ["BOOKING"]}, headers=h)
        self.assertEqual(r.status_code, 201)
        rid = r.get_json()["receipt_id"]
        self.assertTrue(api.get("/api/v1/consents/check?principal_ref=p@example.com&purpose=BOOKING", headers=h).get_json()["allowed"])
        self.assertFalse(api.get("/api/v1/consents/check?principal_ref=p@example.com&purpose=MARKETING", headers=h).get_json()["allowed"])
        self.assertEqual(api.get("/api/v1/receipts/" + rid, headers=h).status_code, 200)
        r = api.post("/api/v1/consents/withdraw", json={"principal_ref": "p@example.com", "notice_slug": "guest-booking"}, headers=h)
        self.assertEqual(r.status_code, 201)
        self.assertFalse(api.get("/api/v1/consents/check?principal_ref=p@example.com&purpose=BOOKING", headers=h).get_json()["allowed"])
        r = api.post("/api/v1/dsr", json={"type": "Erasure", "name": "P", "contact": "p@example.com"}, headers=h)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(api.post("/api/v1/consents", json={"principal_ref": "p", "notice_slug": "nope", "granted": []}, headers=h).status_code, 400)

    def test_public_api_cors(self):
        self.app.config["CORS_ORIGINS"] = "https://www.example.com"
        r = self.c.get("/api/v1/public/notices/guest-booking", headers={"Origin": "https://www.example.com"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("Access-Control-Allow-Origin"), "https://www.example.com")
        r = self.c.get("/api/v1/public/notices/guest-booking", headers={"Origin": "https://evil.example"})
        self.assertNotEqual(r.headers.get("Access-Control-Allow-Origin"), "https://evil.example")


class TestDSR(Base):
    def test_flow(self):
        r = self.post("/dsr", {"type": "Erasure", "name": "Guest", "contact": "g@example.com", "identifier": "g@example.com", "description": "Erase"})
        ref = re.search(rb"DSR-\d{4}-\d{4}", r.data).group(0).decode()
        r = self.post("/dsr/status", {"ref": ref, "contact": "g@example.com"})
        self.assertIn(ref.encode(), r.data)
        r = self.post("/dsr/status", {"ref": ref, "contact": "other@example.com"})
        self.assertIn(b"No request found", r.data)
        from app import consent_service as cs
        with self.app.app_context():
            cs.record_consent("g@example.com", "guest-booking", ["BOOKING", "MARKETING"])
        self.login()
        with self.app.app_context():
            rid = db.scalar("SELECT id FROM dsr_requests WHERE ref = ?", (ref,))
        self.post(f"/requests/{rid}", {"action": "withdraw_all"})
        with self.app.app_context():
            self.assertFalse(cs.has_consent("g@example.com", "BOOKING"))
        self.post(f"/requests/{rid}", {"action": "update", "status": "Completed", "identity_verified": "1", "closure_note": "done"})
        with self.app.app_context():
            row = db.one("SELECT * FROM dsr_requests WHERE id = ?", (rid,))
            self.assertEqual(row["status"], "Completed")
            self.assertTrue(row["closed_at"])

    def test_overdue(self):
        from app.views_dsr import create_request, overdue
        with self.app.app_context():
            _, rid = create_request("Access (summary of my data)", "A", "a@example.com", "", "")
            old = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            db.execute("UPDATE dsr_requests SET due_at = ? WHERE id = ?", (old, rid))
            self.assertTrue(overdue(db.one("SELECT * FROM dsr_requests WHERE id = ?", (rid,))))

    def test_validation(self):
        r = self.post("/dsr", {"type": "Bogus", "name": "", "contact": ""})
        self.assertNotIn(b"DSR-", r.data)


class TestIncidents(Base):
    def test_timer(self):
        from app.views_breach import timers
        now = datetime.now(timezone.utc)
        f = lambda h: (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(timers({"aware_at": f(1), "board_detailed_at": None, "board_initial_at": None})["state"], "ok")
        self.assertEqual(timers({"aware_at": f(60), "board_detailed_at": None, "board_initial_at": None})["state"], "urgent")
        self.assertEqual(timers({"aware_at": f(80), "board_detailed_at": None, "board_initial_at": None})["state"], "overdue")
        self.assertEqual(timers({"aware_at": f(80), "board_detailed_at": f(1), "board_initial_at": f(70)})["state"], "filed")

    def test_flow(self):
        self.login()
        ist_old = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30) - timedelta(hours=80)).strftime("%Y-%m-%dT%H:%M")
        r = self.post("/incidents/new", {"title": "Wrong recipient", "aware_at": ist_old, "severity": "High"})
        self.assertEqual(r.status_code, 302)
        with self.app.app_context():
            i = db.one("SELECT * FROM incidents")
            self.assertEqual(i["aware_at"][11:16], (datetime.strptime(ist_old, "%Y-%m-%dT%H:%M") - timedelta(hours=5, minutes=30)).strftime("%H:%M"))   # IST to UTC
        self.assertIn(b"overdue", self.c.get("/incidents").data.lower())
        self.post(f"/incidents/{i['id']}", {"action": "board_initial"})
        self.post(f"/incidents/{i['id']}", {"action": "board_detailed"})
        self.assertIn(b"Detailed report filed", self.c.get(f"/incidents/{i['id']}").data)
        self.assertEqual(self.c.get(f"/incidents/{i['id']}/report").status_code, 200)
        self.assertEqual(self.post("/incidents/new", {"title": "", "aware_at": ""}).status_code, 200)


class TestSecurity(Base):
    def test_headers_and_escape(self):
        r = self.c.get("/dsr")
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.login()
        self.post("/e/gaps/new", {"code": "G-X", "finding": "<script>alert(1)</script>"})
        self.assertNotIn(b"<script>alert(1)</script>", self.c.get("/e/gaps").data)

    def test_prod_requires_secret(self):
        os.environ["APP_ENV"] = "production"
        try:
            with self.assertRaises(RuntimeError):
                create_app({"DATABASE_URL": "sqlite:///" + os.path.join(self.tmp, "p.db"), "SECRET_KEY": "dev-only-change-me"})
        finally:
            del os.environ["APP_ENV"]


if __name__ == "__main__":
    unittest.main()
