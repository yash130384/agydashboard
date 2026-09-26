#!/usr/bin/env python3
"""
Comprehensive QA & Integration Test Suite for LCARS Auth & Permission System.
Task: t_fc5fff9c

Covers:
1. Login with password and with key (valid/invalid, session cookie / header / Bearer / query).
2. Category access restriction (restricted test user can only see and access assigned categories;
   access to locked categories / APIs returns 401/403).
3. cb / Super Admin full access to all categories and services.
4. Logout and session invalidation across cookie, header, and body.
"""

import os
import sys
import json
import time
import secrets
import unittest
import warnings

# Suppress minor async/resource warnings for clean test output
warnings.filterwarnings("ignore", category=ResourceWarning)
os.environ["FLASK_ENV"] = "testing"

import app as main_app
from user_service import user_service, UserService, DEFAULT_SESSION_DURATION, REMEMBER_SESSION_DURATION
from permissions_service import permissions_service, VALID_SECTIONS, DASHBOARD_CATEGORIES


class BaseAuthQATestCase(unittest.TestCase):
    """Base class providing test client and tracked user cleanup."""

    @classmethod
    def setUpClass(cls):
        main_app.USE_FLASK = True
        cls.flask_app = main_app.app
        cls.flask_app.config["TESTING"] = True
        cls.client = cls.flask_app.test_client()
        cls.user_service = main_app.user_service
        cls.permissions_service = main_app.permissions_service
        # Make sure default admins exist
        cls.user_service.ensure_default_admins()

    def setUp(self):
        self.client = self.flask_app.test_client()
        self._created_user_ids = []

    def tearDown(self):
        for uid in self._created_user_ids:
            try:
                self.user_service.delete_user(uid)
            except Exception:
                pass

    def create_test_user(self, username=None, password="TestPassword123!",
                         allowed_services=None, api_key=None, is_active=True,
                         display_name="QA Test User", notes=""):
        if not username:
            username = f"qa_{secrets.token_hex(4)}"
        if allowed_services is None:
            allowed_services = ["system"]
        res = self.user_service.create_user(
            username=username,
            password=password,
            display_name=display_name,
            allowed_services=allowed_services,
            notes=notes,
            api_key=api_key,
        )
        self.assertTrue(res.get("success"), f"Failed to create test user: {res}")
        user_id = res["user_id"]
        self._created_user_ids.append(user_id)
        if not is_active:
            self.user_service.update_user(user_id, is_active=False)
        return {
            "id": user_id,
            "username": username,
            "password": password,
            "allowed_services": allowed_services,
            "api_key": api_key,
        }


class TestLoginWithPassword(BaseAuthQATestCase):
    """Test 1.1: Password Login (valid/invalid, cookie/header, remember me, return_to)."""

    def test_login_password_valid_credentials(self):
        """Valid password login returns 200, session_id, user info, Set-Cookie, and Bearer header."""
        user = self.create_test_user(allowed_services=["solar", "services"])

        resp = self.client.post("/api/auth/login", json={
            "username": user["username"],
            "password": user["password"]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("session_id", data)
        self.assertIn("token", data)
        self.assertEqual(data["session_id"], data["token"])
        self.assertEqual(data["user"]["username"], user["username"])
        self.assertEqual(data["user"]["allowed_services"], ["solar", "services"])

        # Check Set-Cookie
        set_cookie = resp.headers.get("Set-Cookie", "")
        self.assertIn("lcars_session=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Path=/", set_cookie)

        # Check response headers
        self.assertEqual(resp.headers.get("X-Session-ID"), data["session_id"])
        self.assertEqual(resp.headers.get("Authorization"), f"Bearer {data['session_id']}")

        # Verify session exists in DB
        self.assertIsNotNone(data)
        assert data is not None
        db_session = self.user_service.validate_session(data["session_id"])
        self.assertIsNotNone(db_session)
        assert db_session is not None
        self.assertEqual(db_session["username"], user["username"])

    def test_login_password_remember_me_durations(self):
        """remember=False uses DEFAULT_SESSION_DURATION; remember=True uses REMEMBER_SESSION_DURATION."""
        user = self.create_test_user()

        # remember=False
        resp_std = self.client.post("/api/auth/login", json={
            "username": user["username"],
            "password": user["password"],
            "remember": False
        })
        self.assertEqual(resp_std.status_code, 200)
        data_std = resp_std.get_json() or {}
        sid_std = data_std.get("session_id", "")
        sess_std = self.user_service.validate_session(sid_std)
        self.assertIsNotNone(sess_std)
        assert sess_std is not None
        now_ts = int(time.time())
        # Default ~24h (86400s)
        self.assertTrue(sess_std["expires_at"] - now_ts <= DEFAULT_SESSION_DURATION + 10)
        self.assertTrue(sess_std["expires_at"] - now_ts >= DEFAULT_SESSION_DURATION - 60)

        # remember=True
        resp_rem = self.client.post("/api/auth/login", json={
            "username": user["username"],
            "password": user["password"],
            "remember": True
        })
        self.assertEqual(resp_rem.status_code, 200)
        data_rem = resp_rem.get_json() or {}
        sid_rem = data_rem.get("session_id", "")
        sess_rem = self.user_service.validate_session(sid_rem)
        self.assertIsNotNone(sess_rem)
        assert sess_rem is not None
        # Remember ~30d (2592000s)
        self.assertTrue(sess_rem["expires_at"] - now_ts <= REMEMBER_SESSION_DURATION + 10)
        self.assertTrue(sess_rem["expires_at"] - now_ts >= REMEMBER_SESSION_DURATION - 60)

    def test_login_password_return_to_url(self):
        """return_to parameter in request is returned as redirect_url."""
        user = self.create_test_user()
        resp = self.client.post("/api/auth/login", json={
            "username": user["username"],
            "password": user["password"],
            "return_to": "/#cat-solar"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("redirect_url"), "/#cat-solar")

    def test_login_password_wrong_password_fails(self):
        """Wrong password returns 401 and descriptive error."""
        user = self.create_test_user()
        resp = self.client.post("/api/auth/login", json={
            "username": user["username"],
            "password": "incorrect_password_xyz"
        })
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertFalse(data.get("success"))
        self.assertIn("error", data)

    def test_login_password_nonexistent_user_fails(self):
        """Non-existent username returns 401."""
        resp = self.client.post("/api/auth/login", json={
            "username": "ghost_cadet_99999",
            "password": "some_password"
        })
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertFalse(data.get("success"))

    def test_login_password_deactivated_account_fails(self):
        """Deactivated user account returns 401 and indicates account is deactivated."""
        user = self.create_test_user(is_active=False)
        resp = self.client.post("/api/auth/login", json={
            "username": user["username"],
            "password": user["password"]
        })
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertFalse(data.get("success"))
        self.assertIn("deaktiviert", data.get("error", "").lower())


class TestLoginWithApiKey(BaseAuthQATestCase):
    """Test 1.2: API Key Login (valid/invalid, payload formats, headers, Bearer)."""

    def test_login_api_key_via_json_body(self):
        """Valid API key in JSON body 'api_key' creates session and sets cookie."""
        raw_key = f"lcars_key_{secrets.token_hex(8)}"
        user = self.create_test_user(api_key=raw_key, allowed_services=["ai", "solar"])

        resp = self.client.post("/api/auth/login", json={"api_key": raw_key})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["user"]["username"], user["username"])
        self.assertIn("lcars_session=", resp.headers.get("Set-Cookie", ""))
        self.assertIsNotNone(data.get("session_id"))

    def test_login_api_key_via_json_key_alias(self):
        """Valid API key in JSON body 'key' alias also authenticates."""
        raw_key = f"lcars_key_{secrets.token_hex(8)}"
        user = self.create_test_user(api_key=raw_key)

        resp = self.client.post("/api/auth/login", json={"key": raw_key})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["user"]["username"], user["username"])

    def test_login_api_key_via_x_api_key_header(self):
        """Valid API key in X-API-Key request header authenticates."""
        raw_key = f"lcars_key_{secrets.token_hex(8)}"
        user = self.create_test_user(api_key=raw_key)

        resp = self.client.post("/api/auth/login", headers={"X-API-Key": raw_key}, json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["user"]["username"], user["username"])

    def test_login_api_key_invalid_rejected(self):
        """Invalid API key returns 401."""
        resp = self.client.post("/api/auth/login", json={"api_key": "lcars_invalid_key_99999"})
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertFalse(data.get("success"))

    def test_login_api_key_deactivated_user_rejected(self):
        """API key of a deactivated user returns 401."""
        raw_key = f"lcars_key_{secrets.token_hex(8)}"
        self.create_test_user(api_key=raw_key, is_active=False)

        resp = self.client.post("/api/auth/login", json={"api_key": raw_key})
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertFalse(data.get("success"))


class TestSessionCookieAndHeaderAuth(BaseAuthQATestCase):
    """Test 1.3: Session-Cookie & Header Authentication across endpoints."""

    def test_auth_via_session_cookie(self):
        """Valid session cookie authenticates request to /api/auth/me."""
        user = self.create_test_user()
        sid = self.user_service.create_session(user["id"])

        self.client.set_cookie("lcars_session", sid)
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data["user"]["username"], user["username"])

    def test_auth_via_bearer_session_token(self):
        """Authorization: Bearer <session_id> authenticates /api/auth/me."""
        user = self.create_test_user()
        sid = self.user_service.create_session(user["id"])

        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {sid}"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data["user"]["username"], user["username"])

    def test_auth_via_x_session_id_header(self):
        """X-Session-ID header authenticates /api/auth/me."""
        user = self.create_test_user()
        sid = self.user_service.create_session(user["id"])

        resp = self.client.get("/api/auth/me", headers={"X-Session-ID": sid})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data["user"]["username"], user["username"])

    def test_direct_api_key_auth_via_bearer(self):
        """Direct API key in Authorization: Bearer <key> authenticates without session."""
        raw_key = f"lcars_key_{secrets.token_hex(8)}"
        user = self.create_test_user(api_key=raw_key)

        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {raw_key}"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data["user"]["username"], user["username"])

    def test_direct_api_key_auth_via_x_api_key(self):
        """Direct API key in X-API-Key header authenticates without session."""
        raw_key = f"lcars_key_{secrets.token_hex(8)}"
        user = self.create_test_user(api_key=raw_key)

        resp = self.client.get("/api/auth/me", headers={"X-API-Key": raw_key})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data["user"]["username"], user["username"])

    def test_direct_api_key_auth_via_query_param(self):
        """Direct API key in query ?key=<key> authenticates without session."""
        raw_key = f"lcars_key_{secrets.token_hex(8)}"
        user = self.create_test_user(api_key=raw_key)

        resp = self.client.get(f"/api/auth/me?key={raw_key}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data["user"]["username"], user["username"])

    def test_invalid_session_token_rejected(self):
        """Bogus session token returns authenticated=False."""
        resp = self.client.get("/api/auth/me", headers={"Authorization": "Bearer bogus_session_xyz123"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data.get("authenticated"))
        self.assertIsNone(data.get("user"))


class TestCategoryAccessRestriction(BaseAuthQATestCase):
    """Test 2: Category Access Restriction (left categories visibility, 401/403 on forbidden APIs)."""

    def test_restricted_user_allowed_sections_in_me_endpoint(self):
        """Restricted user with only ['solar', 'pulsecast'] only gets allowed sections."""
        user = self.create_test_user(allowed_services=["solar", "pulsecast"])
        sid = self.user_service.create_session(user["id"])

        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {sid}"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["authenticated"])
        self.assertFalse(data["user"]["is_super_admin"])

        allowed = data["user"]["allowed_sections"]
        # 'solar' and 'pulsecast' are allowed
        self.assertIn("solar", allowed)
        self.assertIn("pulsecast", allowed)
        # 'personal' is their parent container, so it's also allowed
        self.assertIn("personal", allowed)

        # Disallowed categories must NOT be in allowed_sections
        for forbidden in ["ai", "hermes", "9router", "ide", "cycle", "devteam", "config", "services", "system"]:
            self.assertNotIn(forbidden, allowed)

    def test_restricted_user_backend_forbidden_endpoints_return_403(self):
        """Access to forbidden categories/APIs returns 403 Forbidden for restricted user."""
        user = self.create_test_user(allowed_services=["solar", "pulsecast"])
        sid = self.user_service.create_session(user["id"])
        headers = {"Authorization": f"Bearer {sid}"}

        # 1. Allowed category endpoints should NOT return 403
        solar_resp = self.client.get("/api/solar/data", headers=headers)
        self.assertNotEqual(solar_resp.status_code, 403)

        pulsecast_resp = self.client.get("/api/pulsecast/status", headers=headers)
        self.assertNotEqual(pulsecast_resp.status_code, 403)

        # 2. Forbidden categories MUST return 403 Forbidden
        cycle_resp = self.client.get("/api/cycle/partners", headers=headers)
        self.assertEqual(cycle_resp.status_code, 403)
        self.assertIn("nicht berechtigt", cycle_resp.get_json().get("error", ""))

        devteam_resp = self.client.get("/api/devteam/tasks", headers=headers)
        self.assertEqual(devteam_resp.status_code, 403)

        hermes_resp = self.client.get("/api/hermes/profiles", headers=headers)
        self.assertEqual(hermes_resp.status_code, 403)

        fantasy_resp = self.client.get("/api/fantasy", headers=headers)
        self.assertEqual(fantasy_resp.status_code, 403)

        services_resp = self.client.post("/api/services/stop", json={"pid": 999999}, headers=headers)
        self.assertEqual(services_resp.status_code, 403)

        # User management requires super admin or command code
        users_resp = self.client.get("/api/users", headers=headers)
        self.assertEqual(users_resp.status_code, 403)

    def test_category_hierarchy_parent_and_child_inheritance(self):
        """Parent category grants access to all children; specific child does not grant siblings."""
        # User A has parent 'ai' -> gets ai, 9router, hermes, ide, gemini_live, ai-info
        user_ai = self.create_test_user(allowed_services=["ai"])
        sid_ai = self.user_service.create_session(user_ai["id"])
        resp_ai = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {sid_ai}"})
        allowed_ai = resp_ai.get_json()["user"]["allowed_sections"]

        self.assertIn("ai", allowed_ai)
        self.assertIn("hermes", allowed_ai)
        self.assertIn("9router", allowed_ai)
        self.assertIn("ide", allowed_ai)
        self.assertIn("gemini_live", allowed_ai)
        self.assertNotIn("solar", allowed_ai)
        self.assertNotIn("cycle", allowed_ai)

        # User B has only child 'hermes' -> gets hermes and container 'ai', but NOT '9router' or 'ide'
        user_hermes = self.create_test_user(allowed_services=["hermes"])
        sid_hermes = self.user_service.create_session(user_hermes["id"])
        resp_hermes = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {sid_hermes}"})
        allowed_hermes = resp_hermes.get_json()["user"]["allowed_sections"]

        self.assertIn("hermes", allowed_hermes)
        self.assertIn("ai", allowed_hermes)
        self.assertNotIn("9router", allowed_hermes)
        self.assertNotIn("ide", allowed_hermes)

    def test_ui_renders_category_visibility_script(self):
        """Dashboard HTML contains user permissions JSON and applyPermissionsVisibility() DOM logic."""
        user = self.create_test_user(allowed_services=["solar", "fantasy"])
        sid = self.user_service.create_session(user["id"])

        self.client.set_cookie("lcars_session", sid)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Verification of DOM permission injection
        self.assertIn("var currentLcarsUser =", html)
        self.assertIn(user["username"], html)
        self.assertIn("function isUserAllowedSection", html)
        self.assertIn("function applyPermissionsVisibility", html)

        # Verification that buttons are checked by id in client-side visibility logic
        self.assertIn("btn-cat-system", html)
        self.assertIn("btn-cat-services", html)
        self.assertIn("btn-cat-config", html)
        self.assertIn("btn-cat-devteam", html)
        self.assertIn("nav-ai-group", html)
        self.assertIn("nav-personal-group", html)

    def test_unauthenticated_request_serves_login_screen_not_dashboard(self):
        """Unauthenticated GET / serves the LCARS login screen, not the internal dashboard."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("AUTORISIERUNG", html)
        self.assertIn("tabModeCredentials", html)
        self.assertNotIn("var currentLcarsUser =", html)


class TestSuperAdminAndCbFullAccess(BaseAuthQATestCase):
    """Test 3: cb / Super Admin Full Access to all categories and services."""

    def test_cb_user_implicit_super_admin_status(self):
        """Username 'cb' has implicit super admin status with full permissions (*)."""
        cb_user = self.user_service.get_user_by_username("cb")
        self.assertIsNotNone(cb_user, "Default user 'cb' must exist")
        assert cb_user is not None

        sid = self.user_service.create_session(cb_user["id"])
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {sid}"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["authenticated"])
        self.assertTrue(data["user"]["is_super_admin"])
        self.assertIn("*", data["user"]["allowed_sections"])

    def test_cb_user_can_access_all_protected_endpoints(self):
        """Session for 'cb' has access to all protected categories and user management without 403."""
        cb_user = self.user_service.get_user_by_username("cb")
        self.assertIsNotNone(cb_user)
        assert cb_user is not None
        sid = self.user_service.create_session(cb_user["id"])
        headers = {"Authorization": f"Bearer {sid}"}

        # Devteam
        dev_resp = self.client.get("/api/devteam/tasks", headers=headers)
        self.assertNotEqual(dev_resp.status_code, 403)

        # Cycle
        cyc_resp = self.client.get("/api/cycle/partners", headers=headers)
        self.assertNotEqual(cyc_resp.status_code, 403)

        # Hermes
        hermes_resp = self.client.get("/api/hermes/profiles", headers=headers)
        self.assertNotEqual(hermes_resp.status_code, 403)

        # Fantasy
        fan_resp = self.client.get("/api/fantasy", headers=headers)
        self.assertNotEqual(fan_resp.status_code, 403)

        # Solar
        sol_resp = self.client.get("/api/solar/data", headers=headers)
        self.assertNotEqual(sol_resp.status_code, 403)

        # Services stop (missing param -> 400, but NOT 403)
        srv_resp = self.client.post("/api/services/stop", json={}, headers=headers)
        self.assertEqual(srv_resp.status_code, 400)

        # User management /api/users
        users_resp = self.client.get("/api/users", headers=headers)
        self.assertEqual(users_resp.status_code, 200)
        self.assertTrue(users_resp.get_json().get("success"))

    def test_cb_default_api_key_full_access(self):
        """Default API key 'lcars_cb_sec_token_0901' gives full super admin access."""
        headers = {"X-API-Key": "lcars_cb_sec_token_0901"}
        resp = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["authenticated"])
        self.assertTrue(data["user"]["is_super_admin"])
        self.assertEqual(data["user"]["username"], "cb")

        # Can access user management directly via header
        users_resp = self.client.get("/api/users", headers=headers)
        self.assertEqual(users_resp.status_code, 200)

    def test_generic_super_admin_via_allowed_services_wildcard(self):
        """User with allowed_services=['*'] is recognized as super admin."""
        admin_user = self.create_test_user(allowed_services=["*"], display_name="Admin Officer")
        sid = self.user_service.create_session(admin_user["id"])

        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {sid}"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["user"]["is_super_admin"])
        self.assertIn("*", data["user"]["allowed_sections"])

        # Can access user management
        users_resp = self.client.get("/api/users", headers={"Authorization": f"Bearer {sid}"})
        self.assertEqual(users_resp.status_code, 200)

    def test_command_code_0901_admin_override(self):
        """X-Command-Code 0901 is removed and must return 403."""
        headers = {"X-Command-Code": "0901"}

        users_resp = self.client.get("/api/users", headers=headers)
        self.assertEqual(users_resp.status_code, 403)


class TestLogoutAndSessionInvalidation(BaseAuthQATestCase):
    """Test 4: Logout and Session Invalidation (cookie, Bearer, X-Session-ID, DB purge)."""

    def test_logout_via_session_cookie(self):
        """Logout via session cookie clears cookie, deletes session in DB, invalidates access."""
        user = self.create_test_user()
        sid = self.user_service.create_session(user["id"])

        # Confirm session is valid
        self.client.set_cookie("lcars_session", sid)
        me_resp = self.client.get("/api/auth/me")
        self.assertEqual(me_resp.status_code, 200)
        self.assertTrue(me_resp.get_json()["authenticated"])

        # POST /api/auth/logout
        logout_resp = self.client.post("/api/auth/logout")
        self.assertEqual(logout_resp.status_code, 200)
        data = logout_resp.get_json()
        self.assertTrue(data.get("success"))

        # Verify Set-Cookie clears lcars_session (Expires in 1970 or Max-Age=0)
        set_cookie = logout_resp.headers.get("Set-Cookie", "")
        self.assertIn("lcars_session=", set_cookie)
        self.assertTrue("Expires=" in set_cookie or "Max-Age=0" in set_cookie)

        # Verify session deleted from database
        self.assertIsNone(self.user_service.validate_session(sid))

        # Subsequent call with previous session must be unauthenticated
        after_me = self.client.get("/api/auth/me")
        self.assertFalse(after_me.get_json()["authenticated"])

    def test_logout_via_bearer_header(self):
        """Logout with Authorization: Bearer <session_id> invalidates session."""
        user = self.create_test_user()
        sid = self.user_service.create_session(user["id"])

        logout_resp = self.client.post("/api/auth/logout", headers={"Authorization": f"Bearer {sid}"})
        self.assertEqual(logout_resp.status_code, 200)
        self.assertTrue(logout_resp.get_json()["success"])

        # Session is destroyed in DB
        self.assertIsNone(self.user_service.validate_session(sid))

        # Endpoint rejects old token
        after_me = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {sid}"})
        self.assertFalse(after_me.get_json()["authenticated"])

    def test_logout_via_x_session_id_header(self):
        """Logout with X-Session-ID header invalidates session."""
        user = self.create_test_user()
        sid = self.user_service.create_session(user["id"])

        logout_resp = self.client.post("/api/auth/logout", headers={"X-Session-ID": sid})
        self.assertEqual(logout_resp.status_code, 200)
        self.assertTrue(logout_resp.get_json()["success"])

        self.assertIsNone(self.user_service.validate_session(sid))

    def test_logout_via_json_payload(self):
        """Logout with JSON {'session_id': ...} invalidates session."""
        user = self.create_test_user()
        sid = self.user_service.create_session(user["id"])

        logout_resp = self.client.post("/api/auth/logout", json={"session_id": sid})
        self.assertEqual(logout_resp.status_code, 200)
        self.assertTrue(logout_resp.get_json()["success"])

        self.assertIsNone(self.user_service.validate_session(sid))

    def test_expired_session_cleanup(self):
        """user_service.cleanup_expired_sessions() removes past sessions."""
        user = self.create_test_user()
        # Create session with -100 seconds duration (already expired)
        sid = self.user_service.create_session(user["id"], duration_seconds=-100)

        # validate_session cleans up on access
        self.assertIsNone(self.user_service.validate_session(sid))

        # Or batch cleanup
        sid2 = self.user_service.create_session(user["id"], duration_seconds=-100)
        deleted_count = self.user_service.cleanup_expired_sessions()
        self.assertGreaterEqual(deleted_count, 1)


if __name__ == "__main__":
    unittest.main()
