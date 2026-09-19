#!/usr/bin/env python3
"""
LCARS Auth & User Management Integration Test Suite for agydashboard.
Tests:
- UserService CRUD operations, PBKDF2 hashing, sessions, and audit logging
- Flask Authentication endpoints (/login, /api/auth/login, /api/auth/logout, /api/auth/me)
- Flask User Management API endpoints (/api/users, /api/users/<id>, /api/users/<id>/password, /api/users/audit-log)
- LcarsAuthProxy subdomain resolution, authentication check, and redirect logic
"""

import json
import os
import tempfile
import time
import unittest

from app import app
from auth_proxy import LcarsAuthProxy
from login_page import LOGIN_HTML
from user_service import UserService


class TestUserService(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.user_svc = UserService(db_path=self.temp_db.name)

    def tearDown(self):
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    def test_user_creation_and_auth(self):
        res = self.user_svc.create_user(
            username="test_officer",
            password="federation_pass_123",
            display_name="Lt. Officer",
            allowed_services=["pulsecast"],
            notes="Engineering test user"
        )
        self.assertTrue(res.get("success"), f"create_user failed: {res}")
        user_id = res["user_id"]

        # Duplicate rejection
        dup = self.user_svc.create_user("test_officer", "other_pass_123")
        self.assertFalse(dup.get("success"))

        # Auth success
        auth = self.user_svc.authenticate("test_officer", "federation_pass_123")
        self.assertTrue(auth.get("success"))
        self.assertEqual(auth["user"]["username"], "test_officer")
        self.assertEqual(auth["user"]["allowed_services"], ["pulsecast"])

        # Auth failure (wrong password)
        bad_auth = self.user_svc.authenticate("test_officer", "wrong_pass")
        self.assertFalse(bad_auth.get("success"))

        # Auth failure (nonexistent user)
        no_auth = self.user_svc.authenticate("unknown_user", "pass")
        self.assertFalse(no_auth.get("success"))

    def test_session_lifecycle(self):
        res = self.user_svc.create_user("session_user", "password123")
        uid = res["user_id"]

        session_id = self.user_svc.create_session(uid, duration_seconds=3600)
        self.assertTrue(bool(session_id))

        valid = self.user_svc.validate_session(session_id)
        self.assertIsNotNone(valid)
        self.assertEqual(valid["username"], "session_user")

        # Delete session
        deleted = self.user_svc.delete_session(session_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.user_svc.validate_session(session_id))

    def test_password_update_and_session_invalidation(self):
        res = self.user_svc.create_user("pwd_user", "initial_pass123")
        uid = res["user_id"]

        session_id = self.user_svc.create_session(uid, duration_seconds=3600)
        self.assertIsNotNone(self.user_svc.validate_session(session_id))

        # Update password
        up_res = self.user_svc.update_password(uid, "updated_pass456")
        self.assertTrue(up_res.get("success"))

        # Old password must fail
        old_auth = self.user_svc.authenticate("pwd_user", "initial_pass123")
        self.assertFalse(old_auth.get("success"))

        # New password must succeed
        new_auth = self.user_svc.authenticate("pwd_user", "updated_pass456")
        self.assertTrue(new_auth.get("success"))

        # Existing sessions should have been invalidated
        self.assertIsNone(self.user_svc.validate_session(session_id))

    def test_user_permissions(self):
        admin_user = {"allowed_services": ["*"]}
        regular_user = {"allowed_services": ["pulsecast", "cups"]}

        self.assertTrue(self.user_svc.check_service_permission(admin_user, "telemetryvault"))
        self.assertTrue(self.user_svc.check_service_permission(admin_user, "headroom"))

        self.assertTrue(self.user_svc.check_service_permission(regular_user, "pulsecast"))
        self.assertTrue(self.user_svc.check_service_permission(regular_user, "cups"))
        self.assertFalse(self.user_svc.check_service_permission(regular_user, "telemetryvault"))

    def test_audit_logging(self):
        self.user_svc.log_audit("ensign_picard", "WARP_CORE_ACCESS", target_service="warp", details="Level 1 diagnostics")
        logs = self.user_svc.get_audit_log(limit=5)
        self.assertTrue(len(logs) >= 1)
        found = any(l["username"] == "ensign_picard" and l["event"] == "WARP_CORE_ACCESS" for l in logs)
        self.assertTrue(found)


class TestFlaskAuthEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()
        cls.test_username = f"officer_unit_{int(time.time())}"
        cls.test_password = "unit_test_password_47"

    def test_01_login_page_renders(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"LCARS ACCESS AUTHORIZATION", resp.data)

    def test_02_auth_me_unauthenticated(self):
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data.get("authenticated"))
        self.assertIsNone(data.get("user"))

    def test_03_users_endpoint_unauthorized(self):
        resp = self.client.get("/api/users")
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertFalse(data.get("success"))

    def test_04_create_user_with_command_code(self):
        payload = {
            "username": self.test_username,
            "password": self.test_password,
            "display_name": "Test Commander",
            "allowed_services": ["pulsecast", "telemetryvault"],
            "notes": "Automated unit test officer"
        }
        resp = self.client.post(
            "/api/users",
            json=payload,
            headers={"X-Command-Code": "0901"}
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.__class__.created_user_id = data["user_id"]

    def test_05_list_users_with_command_code(self):
        resp = self.client.get("/api/users", headers={"X-Command-Code": "0901"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        usernames = [u["username"] for u in data.get("users", [])]
        self.assertIn(self.test_username, usernames)

    def test_06_login_success(self):
        payload = {
            "username": self.test_username,
            "password": self.test_password,
            "remember": False,
            "return_to": "/dashboard"
        }
        resp = self.client.post("/api/auth/login", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("redirect_url"), "/dashboard")

        # Verify session cookie was set
        cookie = self.client.get_cookie("lcars_session")
        self.assertIsNotNone(cookie)
        self.assertTrue(bool(cookie.value))

    def test_07_auth_me_authenticated(self):
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data["user"]["username"], self.test_username)

    def test_08_update_user(self):
        uid = getattr(self.__class__, "created_user_id", None)
        self.assertIsNotNone(uid)
        payload = {
            "display_name": "Captain Test",
            "is_active": True,
            "allowed_services": ["*"],
            "notes": "Promoted to Captain"
        }
        resp = self.client.put(
            f"/api/users/{uid}",
            json=payload,
            headers={"X-Command-Code": "0901"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

    def test_09_update_user_password(self):
        uid = getattr(self.__class__, "created_user_id", None)
        self.assertIsNotNone(uid)
        payload = {"new_password": "captain_new_secure_pwd_47"}
        resp = self.client.post(
            f"/api/users/{uid}/password",
            json=payload,
            headers={"X-Command-Code": "0901"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

    def test_10_audit_log_endpoint(self):
        resp = self.client.get("/api/users/audit-log?limit=10", headers={"X-Command-Code": "0901"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertIsInstance(data.get("audit_log"), list)

    def test_11_logout(self):
        resp = self.client.post("/api/auth/logout", json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

        # Verify auth/me is now unauthenticated
        me_resp = self.client.get("/api/auth/me")
        self.assertFalse(me_resp.get_json().get("authenticated"))

    def test_12_delete_user(self):
        uid = getattr(self.__class__, "created_user_id", None)
        self.assertIsNotNone(uid)
        resp = self.client.delete(f"/api/users/{uid}", headers={"X-Command-Code": "0901"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))


class TestLcarsAuthProxy(unittest.TestCase):
    def setUp(self):
        self.proxy = LcarsAuthProxy()

    def test_subdomain_resolution(self):
        sub, info = self.proxy.resolve_subdomain("cast.pimmel.site")
        self.assertEqual(sub, "cast")
        self.assertEqual(info["port"], 3000)
        self.assertEqual(info["service"], "pulsecast")

        sub, info = self.proxy.resolve_subdomain("tele.pimmel.site:443")
        self.assertEqual(sub, "tele")
        self.assertEqual(info["port"], 8000)

        sub, info = self.proxy.resolve_subdomain("port.pimmel.site")
        self.assertEqual(sub, "port")
        self.assertEqual(info["port"], 631)

        sub, info = self.proxy.resolve_subdomain("unregistered.pimmel.site")
        self.assertIsNone(sub)
        self.assertIsNone(info)

    def test_register_dynamic_subdomain(self):
        self.proxy.register_subdomain("solar", 8080, "solar_hub", "Solar Hub")
        sub, info = self.proxy.resolve_subdomain("solar.pimmel.site")
        self.assertEqual(sub, "solar")
        self.assertEqual(info["port"], 8080)
        self.assertEqual(info["service"], "solar_hub")


if __name__ == "__main__":
    unittest.main(verbosity=2)
