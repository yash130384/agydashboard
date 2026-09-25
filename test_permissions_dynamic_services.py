#!/usr/bin/env python3
"""
Test Suite: Dynamische Service-Berechtigungen & Super Admin / cb Autorisierung.
- Automatische Berechtigung für cb und Super Admin (admin / Master Administrator)
- Dynamische Aufnahme neuer Services in die Rechtevergabe
- Manuelle Rechtevergabe für reguläre Benutzer
- REST-API Endpunkte /api/users und /api/user-services
"""

import tempfile
import unittest
import os
from user_service import UserService
from auth_proxy import LcarsAuthProxy
from app import app


class TestDynamicPermissions(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.user_svc = UserService(db_path=self.temp_db.name)

    def tearDown(self):
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    def test_default_super_admins_created_and_authorized(self):
        # admin und cb müssen automatisch existieren
        admin = self.user_svc.get_user_by_username("admin")
        cb = self.user_svc.get_user_by_username("cb")
        self.assertIsNotNone(admin)
        self.assertIsNotNone(cb)
        self.assertTrue(self.user_svc.is_super_admin(admin))
        self.assertTrue(self.user_svc.is_super_admin(cb))

        # Beide müssen automatisch für jeden bestehenden und zukünftigen Service berechtigt sein
        self.assertTrue(self.user_svc.check_service_permission(admin, "pulsecast"))
        self.assertTrue(self.user_svc.check_service_permission(cb, "pulsecast"))
        self.assertTrue(self.user_svc.check_service_permission(admin, "brand_new_service_xyz"))
        self.assertTrue(self.user_svc.check_service_permission(cb, "brand_new_service_xyz"))

    def test_regular_user_access_control_on_new_service(self):
        # Regulärer Benutzer wird angelegt mit Rechten nur für pulsecast
        res = self.user_svc.create_user(
            username="crewman_tuvok",
            password="secure_password_123",
            display_name="Crewman Tuvok",
            allowed_services=["pulsecast"]
        )
        self.assertTrue(res.get("success"))
        uid = res["user_id"]
        user = self.user_svc.get_user(uid)

        self.assertFalse(self.user_svc.is_super_admin(user))
        self.assertTrue(self.user_svc.check_service_permission(user, "pulsecast"))
        # Kein Zugriff auf anderen Service oder neuen Service
        self.assertFalse(self.user_svc.check_service_permission(user, "matter"))
        self.assertFalse(self.user_svc.check_service_permission(user, "brand_new_service"))

        # Neuer Service kommt und wird registriert
        ok = self.user_svc.register_service(
            key="brand_new_service",
            name="Brand New Service",
            subdomain="bns",
            port=9999,
            desc="Test Service"
        )
        self.assertTrue(ok)

        # cb und Super Admin sind automatisch berechtigt
        admin = self.user_svc.get_user_by_username("admin")
        cb = self.user_svc.get_user_by_username("cb")
        self.assertTrue(self.user_svc.check_service_permission(admin, "brand_new_service"))
        self.assertTrue(self.user_svc.check_service_permission(cb, "brand_new_service"))

        # Regulärer Nutzer ist weiterhin NICHT berechtigt
        user = self.user_svc.get_user(uid)
        self.assertFalse(self.user_svc.check_service_permission(user, "brand_new_service"))

        # Rechte werden vergeben
        up_res = self.user_svc.update_user(uid, allowed_services=["pulsecast", "brand_new_service"])
        self.assertTrue(up_res.get("success"))
        updated_user = self.user_svc.get_user(uid)
        self.assertTrue(self.user_svc.check_service_permission(updated_user, "brand_new_service"))

    def test_dynamic_service_registration_in_auth_proxy(self):
        proxy = LcarsAuthProxy(user_svc=self.user_svc)
        proxy.register_subdomain(subdomain="cool", target_port=4321, service_key="cool_svc", name="Cool Service")

        services = self.user_svc.get_services()
        self.assertIn("cool_svc", services)
        self.assertEqual(services["cool_svc"]["port"], 4321)
        self.assertEqual(services["cool_svc"]["subdomain"], "cool")


class TestFlaskServiceApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_api_user_services_endpoint(self):
        resp = self.client.get("/api/user-services")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        keys = [s["key"] for s in data.get("services", [])]
        self.assertIn("pulsecast", keys)

    def test_api_users_includes_services(self):
        resp = self.client.get("/api/users", headers={"X-Command-Code": "0901"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("services", data)
        self.assertIn("users", data)


if __name__ == "__main__":
    unittest.main()
