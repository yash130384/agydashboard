#!/usr/bin/env python3
"""
Unit and integration tests for granular category permissions & authorization.
Task t_3ff22b01:
1. Rechte pro Benutzer/Rolle konfigurierbar (Kategorien & Sektionen)
2. Backend-Schutz: Endpunkte der Sektionen pruefen aktive Session & Sektions-Rechte
3. UI-Anpassung: In LCARS-Navigationsleiste nur berechtigte Kategorien aktiv/sichtbar
4. cb & Super Admin besitzen implizit Rechte fuer alle Sektionen
"""

import json
import unittest
from unittest.mock import patch

from app import app
from permissions_service import permissions_service
from user_service import user_service


class TestGranularCategoryPermissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

        # Create test users with different permission profiles
        # 1. Admin / cb -> Implicit full access
        # 2. restricted_user -> Only "solar" and "fantasy"
        # 3. ai_only_user -> Only "ai"
        # 4. no_access_user -> Empty permissions
        if user_service:
            for un in ("test_solar_user", "test_ai_user", "test_zero_user"):
                existing = user_service.get_user_by_username(un)
                if existing:
                    user_service.delete_user(existing["id"])

            r1 = user_service.create_user(
                username="test_solar_user",
                password="password123",
                display_name="Solar & Fantasy Officer",
                allowed_services=["solar", "fantasy"]
            )
            cls.solar_user_id = r1.get("user_id")

            r2 = user_service.create_user(
                username="test_ai_user",
                password="password123",
                display_name="AI Specialist",
                allowed_services=["ai"]
            )
            cls.ai_user_id = r2.get("user_id")

            r3 = user_service.create_user(
                username="test_zero_user",
                password="password123",
                display_name="No Perms User",
                allowed_services=[]
            )
            cls.zero_user_id = r3.get("user_id")

    @classmethod
    def tearDownClass(cls):
        if user_service:
            if hasattr(cls, "solar_user_id") and cls.solar_user_id:
                user_service.delete_user(cls.solar_user_id)
            if hasattr(cls, "ai_user_id") and cls.ai_user_id:
                user_service.delete_user(cls.ai_user_id)
            if hasattr(cls, "zero_user_id") and cls.zero_user_id:
                user_service.delete_user(cls.zero_user_id)

    def test_01_super_admin_implicit_permissions(self):
        """cb and super admin have implicit permissions for all sections."""
        cb_user = {"username": "cb", "allowed_services": []}
        admin_user = {"username": "admin", "allowed_services": []}
        super_admin_role = {"username": "somebody", "role": "admin", "allowed_services": []}

        self.assertTrue(permissions_service.is_super_admin(cb_user))
        self.assertTrue(permissions_service.is_super_admin(admin_user))
        self.assertTrue(permissions_service.is_super_admin(super_admin_role))

        sections_to_test = ["system", "services", "ai", "personal", "solar", "cycle", "pulsecast", "devteam"]
        for sec in sections_to_test:
            self.assertTrue(permissions_service.check_permission(cb_user, sec))
            self.assertTrue(permissions_service.check_permission(admin_user, sec))

        allowed = permissions_service.get_allowed_sections(cb_user)
        self.assertIn("*", allowed)
        for sec in sections_to_test:
            self.assertIn(sec, allowed)

    def test_02_granular_user_permission_checking(self):
        """Standard user only has access to allowed sections and their parent/children."""
        solar_user = {"username": "test_solar_user", "allowed_services": ["solar", "fantasy"]}
        self.assertTrue(permissions_service.check_permission(solar_user, "solar"))
        self.assertTrue(permissions_service.check_permission(solar_user, "fantasy"))
        # personal is parent container, so user with child permission has access to personal
        self.assertTrue(permissions_service.check_permission(solar_user, "personal"))

        # But NOT allowed to ai, cycle, pulsecast, devteam, system
        self.assertFalse(permissions_service.check_permission(solar_user, "cycle"))
        self.assertFalse(permissions_service.check_permission(solar_user, "pulsecast"))
        self.assertFalse(permissions_service.check_permission(solar_user, "ai"))
        self.assertFalse(permissions_service.check_permission(solar_user, "devteam"))

        # AI parent grants access to ai sub-sections
        ai_user = {"username": "test_ai_user", "allowed_services": ["ai"]}
        self.assertTrue(permissions_service.check_permission(ai_user, "ai"))
        self.assertTrue(permissions_service.check_permission(ai_user, "hermes"))
        self.assertTrue(permissions_service.check_permission(ai_user, "9router"))
        self.assertFalse(permissions_service.check_permission(ai_user, "solar"))
        self.assertFalse(permissions_service.check_permission(ai_user, "cycle"))

    def test_03_backend_endpoint_protection(self):
        """Backend endpoints return 403 when session user lacks section rights."""
        # Authenticate as solar user
        login_resp = self.client.post("/api/auth/login", json={
            "username": "test_solar_user",
            "password": "password123"
        })
        self.assertEqual(login_resp.status_code, 200)
        session_id = login_resp.get_json()["session_id"]
        headers = {"Authorization": f"Bearer {session_id}"}

        # Solar endpoint should be accessible (or 200/503 depending on ha_service, but NOT 403)
        solar_resp = self.client.get("/api/solar/data", headers=headers)
        self.assertNotEqual(solar_resp.status_code, 403)

        # Fantasy endpoint should be accessible (not 403)
        fantasy_resp = self.client.get("/api/fantasy", headers=headers)
        self.assertNotEqual(fantasy_resp.status_code, 403)

        # Cycle endpoint MUST return 403 Forbidden for solar user
        cycle_resp = self.client.get("/api/cycle/partners", headers=headers)
        self.assertEqual(cycle_resp.status_code, 403)

        # Devteam endpoint MUST return 403 Forbidden for solar user
        devteam_resp = self.client.get("/api/devteam/tasks", headers=headers)
        self.assertEqual(devteam_resp.status_code, 403)

        # Hermes profiles MUST return 403 Forbidden for solar user
        hermes_resp = self.client.get("/api/hermes/profiles", headers=headers)
        self.assertEqual(hermes_resp.status_code, 403)

    def test_04_backend_endpoint_cb_and_admin_allowed(self):
        """cb (super admin) has full access to all protected endpoints."""
        c = app.test_client()
        login_res = c.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        token = login_res.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Devteam
        resp_dev = c.get("/api/devteam/tasks", headers=headers)
        self.assertNotEqual(resp_dev.status_code, 403)

        # Cycle
        resp_cyc = c.get("/api/cycle/partners", headers=headers)
        self.assertNotEqual(resp_cyc.status_code, 403)

        # Services stop
        resp_srv = c.post("/api/services/stop", json={"pid": 999999}, headers=headers)
        self.assertNotEqual(resp_srv.status_code, 403)

    def test_05_ui_rendered_with_user_allowed_sections(self):
        """Dashboard HTML injects current_user_json with allowed_sections and JS isUserAllowedSection."""
        # 1. Without session, returns login HTML
        c = app.test_client()
        r_unauth = c.get("/")
        self.assertEqual(r_unauth.status_code, 200)
        self.assertIn("LCARS ACCESS AUTHORIZATION", r_unauth.data.decode("utf-8"))

        # 2. With solar user session
        login_resp = c.post("/api/auth/login", json={
            "username": "test_solar_user",
            "password": "password123"
        })
        self.assertEqual(login_resp.status_code, 200)

        r_auth = c.get("/")
        self.assertEqual(r_auth.status_code, 200)
        html = r_auth.data.decode("utf-8")

        self.assertIn("var currentLcarsUser =", html)
        self.assertIn("test_solar_user", html)
        self.assertIn("function isUserAllowedSection", html)
        self.assertIn("function applyPermissionsVisibility", html)
        self.assertIn("solar", html)
        self.assertIn("fantasy", html)


if __name__ == "__main__":
    unittest.main()
