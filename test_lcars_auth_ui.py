import os
import sys
import unittest
import json

os.environ["FLASK_ENV"] = "testing"
import app as main_app
from user_service import UserService
from permissions_service import PermissionsService
from login_page import LOGIN_HTML

class TestLcarsAuthUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_app.USE_FLASK = True
        cls.flask_app = main_app.app
        cls.flask_app.config["TESTING"] = True
        cls.client = cls.flask_app.test_client()

    def setUp(self):
        self.client = self.flask_app.test_client()
        self.user_service = main_app.user_service
        self.permissions_service = main_app.permissions_service

    def test_login_page_html_contains_officer_and_access_key_tabs(self):
        """Test Requirement 1: Login Screen contains Officer ID and Access-Key form elements."""
        resp = self.client.get("/")
        # When unauthenticated, / serves the login screen
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("tabModeCredentials", html)
        self.assertIn("tabModeKey", html)
        self.assertIn("loginApiKey", html)
        self.assertIn("loginUsername", html)
        self.assertIn("loginPassword", html)
        self.assertIn("AUTORISIERUNG", html)

    def test_api_auth_login_with_api_key_and_invalid_credentials(self):
        """Test Requirement 1: Login with API-Key or invalid credentials."""
        # 1. Invalid username/password
        resp = self.client.post("/api/auth/login", json={"username": "nonexistent_officer", "password": "wrong"})
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertIn("error", data)

        # 2. Invalid API-Key
        resp = self.client.post("/api/auth/login", json={"api_key": "invalid_key_xyz"})
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertFalse(data["success"])

        # 3. Create user with API-Key
        key_user = "test_cadet_" + os.urandom(4).hex()
        raw_key = "lcars_testkey_" + os.urandom(8).hex()
        created = self.user_service.create_user(
            username=key_user,
            password="securePassword123",
            api_key=raw_key,
            allowed_services=["system"]
        )
        self.assertTrue(created.get("success"))

        # 4. Valid API-Key login
        resp = self.client.post("/api/auth/login", json={"api_key": raw_key})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["user"]["username"], key_user)
        self.assertIn("lcars_session", resp.headers.get("Set-Cookie", ""))

    def _login_as_cb(self):
        cb_user = self.user_service.get_user_by_username("cb")
        if not cb_user:
            self.user_service.create_user("cb", "superSecretCb123!", allowed_services=["*"])
        else:
            self.user_service.update_password(cb_user["id"], "superSecretCb123!")
        login_resp = self.client.post("/api/auth/login", json={
            "username": "cb",
            "password": "superSecretCb123!"
        })
        self.assertEqual(login_resp.status_code, 200)
        return login_resp

    def test_dashboard_header_user_status_and_logout(self):
        """Test Requirement 3: Header contains 'ANGEMELDET ALS:' user status and LOGOUT button."""
        self._login_as_cb()
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("topUserBadge", html)
        self.assertIn("ANGEMELDET ALS:", html)
        self.assertIn("topLogoutBtn", html)
        self.assertIn("lcarsLogout()", html)

    def test_dashboard_login_overlay_present(self):
        """Test Requirement 1: In-Dashboard LCARS Login Overlay modal present."""
        self._login_as_cb()
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("lcarsLoginOverlay", html)
        self.assertIn("overlayTabCredentials", html)
        self.assertIn("overlayTabKey", html)
        self.assertIn("submitLcarsLoginOverlay", html)

    def test_user_management_ui_admin_only_and_keys(self):
        """Test Requirement 2: User management only visible to admins (cb / super admin), API key management."""
        self._login_as_cb()
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("lcarsUserManagementSection", html)
        self.assertIn("lcarsUserMgmtRestrictedNotice", html)
        self.assertIn("formUserApiKey", html)
        self.assertIn("pwdModalApiKey", html)

        # Non-admin user session cannot access /api/users
        normal_user = "crew_member_" + os.urandom(4).hex()
        created = self.user_service.create_user(
            username=normal_user,
            password="memberPassword123",
            allowed_services=["config", "system"]
        )
        self.assertTrue(created.get("success"))

        # Login as normal user
        login_resp = self.client.post("/api/auth/login", json={
            "username": normal_user,
            "password": "memberPassword123"
        })
        self.assertEqual(login_resp.status_code, 200)

        # Attempt to access /api/users without command code -> Forbidden (403)
        users_resp = self.client.get("/api/users")
        self.assertEqual(users_resp.status_code, 403)

        # cb / super admin login can access /api/users
        admin_login = self.client.post("/api/auth/login", json={
            "username": "cb",
            "password": "0901_admin_password_or_whatever"
        })
        # If cb password isn't that, verify super admin check directly via command code or session
        cb_user = self.user_service.get_user_by_username("cb")
        if not cb_user:
            self.user_service.create_user("cb", "superSecretCb123!", allowed_services=["*"])
        else:
            self.user_service.update_password(cb_user["id"], "superSecretCb123!")

        admin_login = self.client.post("/api/auth/login", json={
            "username": "cb",
            "password": "superSecretCb123!"
        })
        self.assertEqual(admin_login.status_code, 200)

        # Now as cb, /api/users is authorized!
        users_resp = self.client.get("/api/users")
        self.assertEqual(users_resp.status_code, 200)
        data = users_resp.get_json()
        self.assertTrue(data["success"])

        # Test creating user with API Key via POST /api/users
        new_test_name = "cadet_key_" + os.urandom(4).hex()
        new_test_key = "lcars_cadet_" + os.urandom(8).hex()
        post_resp = self.client.post("/api/users", json={
            "username": new_test_name,
            "password": "cadetPassword123",
            "api_key": new_test_key,
            "allowed_services": ["personal", "solar", "cycle"]
        })
        self.assertEqual(post_resp.status_code, 201)
        created_user_id = post_resp.get_json()["user_id"]

        # Test updating key via POST /api/users/<id>/key
        key_resp = self.client.post(f"/api/users/{created_user_id}/key", json={})
        self.assertEqual(key_resp.status_code, 200)
        self.assertTrue(key_resp.get_json()["api_key"].startswith("lcars_"))

        # Test deleting key via DELETE /api/users/<id>/key
        del_key_resp = self.client.delete(f"/api/users/{created_user_id}/key")
        self.assertEqual(del_key_resp.status_code, 200)

        # Cleanup
        self.user_service.delete_user(created_user_id)

if __name__ == "__main__":
    unittest.main()
