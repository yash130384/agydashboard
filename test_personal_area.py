#!/usr/bin/env python3
"""
Test Suite for Personal Area ("Persönlicher Bereich") in LCARS System Dashboard.
Tests:
- Permission definition for 'personal' in PermissionsService
- Navigation to 'personal' via Cactus voice/text commands
- Left pillar structure: Personal accordion button and grouped sub-items
- Main content sections: section-personal with subnav and overview cards
- Direct HTTP endpoint GET / rendering personal area HTML components
"""

import unittest
from app import app, _process_cactus_prompt
from permissions_service import VALID_SECTIONS, PermissionsService


class TestPersonalArea(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def test_permissions_valid_sections(self):
        self.assertIn("personal", VALID_SECTIONS)
        perm = PermissionsService()
        status = perm.get_public_status()
        self.assertIsInstance(status.get("locked_sections"), list)

    def test_cactus_navigate_personal(self):
        prompts = [
            ("Gehe zu Persönlich", "personal"),
            ("Öffne persönlichen Bereich", "personal"),
            ("Persönlicher Bereich", "personal"),
            ("Wechsle zu Persönlich", "personal"),
            ("Zeige persönlichen Bereich", "personal"),
        ]
        for prompt, expected_sec in prompts:
            with self.subTest(prompt=prompt):
                res = _process_cactus_prompt(prompt, mode="test")
                self.assertTrue(res.get("success"), f"Failed for prompt: {prompt}")
                self.assertEqual(res.get("tool_call", {}).get("name"), "navigate_section")
                self.assertEqual(res.get("tool_call", {}).get("arguments", {}).get("target"), expected_sec)
                self.assertEqual(res.get("ui_action", {}).get("target"), expected_sec)

    def test_left_pillar_personal_group_rendered(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Check personal parent button in sidebar
        self.assertIn('id="btn-cat-personal"', html)
        self.assertIn('handlePersonalPillClick()', html)
        self.assertIn('id="personal-subpillar"', html)

        # Check grouped children remain present with their original IDs
        self.assertIn('id="btn-cat-fantasy"', html)
        self.assertIn('id="btn-cat-solar"', html)
        self.assertIn('id="btn-cat-homeassistant"', html)
        self.assertIn('id="btn-cat-cycle"', html)
        self.assertIn('id="btn-cat-pulsecast"', html)

    def test_main_personal_section_rendered(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Check section-personal exists
        self.assertIn('id="section-personal"', html)
        self.assertIn("LCARS PERSÖNLICHER BEREICH", html)

        # Check overview cards
        self.assertIn('id="personalFantasyTeam"', html)
        self.assertIn('id="personalSolarPv"', html)
        self.assertIn('id="personalHaStatus"', html)
        self.assertIn('id="personalCyclePartner"', html)
        self.assertIn('id="personalPulsecastStatus"', html)

        # Check subnav in personal child sections
        self.assertIn('id="section-fantasy"', html)
        self.assertIn('id="section-solar"', html)
        self.assertIn('id="section-homeassistant"', html)
        self.assertIn('id="section-cycle"', html)
        self.assertIn('id="section-pulsecast"', html)


if __name__ == "__main__":
    unittest.main()
