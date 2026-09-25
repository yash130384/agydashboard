#!/usr/bin/env python3
"""
Test Suite for Dashboard Context & UI Navigation Tools in Cactus / Subraum Comm.
Tests:
- Deterministic resolution of navigation commands ('Gehe zu Solar', 'Öffne Services', etc.)
- Deterministic resolution of pagination commands ('Nächste Seite', 'Vorherige Seite', etc.)
- Deterministic resolution of UI actions ('Zurück', 'Vor', 'Aktualisieren', 'Vollbild')
- UI Context awareness (active section, pagination bounds, visible navigation)
- API endpoint /api/cactus/process execution with context
- API endpoint /api/cactus/tools metadata
- API endpoint /api/cactus/status tools metadata
- Pass-through of smart home commands ('Decke 2 aus') to Cactus NeedleAgent
"""

import unittest
from app import app, _process_cactus_prompt


class TestCactusUiNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.auth_headers = {"X-Command-Code": "0901"}

    def test_navigate_section_direct(self):
        prompts = [
            ("Gehe zu Solar", "solar"),
            ("Öffne Solar", "solar"),
            ("Wechsle zu System", "system"),
            ("Zeige Services", "services"),
            ("Öffne Einstellungen", "config"),
            ("Gehe zu PulseCast", "pulsecast"),
            ("Mediathek öffnen", "pulsecast"),
            ("Zeige Dev-Team", "devteam"),
            ("Öffne Home Assistant", "homeassistant"),
            ("Gehe zu Fantasy", "fantasy"),
            ("Gehe zu Persönlich", "personal"),
            ("Öffne persönlichen Bereich", "personal"),
            ("Gehe zu KI", "ai"),
            ("Gehe zu 9Router", "9router"),
            ("Gehe zu Hermes", "hermes"),
            ("Gehe zu Antigravity", "ide"),
            ("Gehe zu KI Info", "ai-info"),
            ("Gehe zu Subraum Comm", "gemini_live"),
        ]
        for prompt, expected_sec in prompts:
            with self.subTest(prompt=prompt):
                res = _process_cactus_prompt(prompt, mode="test")
                self.assertTrue(res.get("success"), f"Failed for prompt: {prompt}")
                self.assertEqual(res.get("tool_call", {}).get("name"), "navigate_section")
                self.assertEqual(res.get("tool_call", {}).get("arguments", {}).get("target"), expected_sec)
                self.assertEqual(res.get("ui_action", {}).get("type"), "navigate")
                self.assertEqual(res.get("ui_action", {}).get("target"), expected_sec)
                self.assertEqual(res.get("confidence"), 100.0)

    def test_paginate_direct(self):
        # Next page tests
        next_prompts = ["Nächste Seite", "Eine Seite vor", "Blättere weiter", "Seite weiter"]
        for prompt in next_prompts:
            with self.subTest(prompt=prompt):
                res = _process_cactus_prompt(prompt, mode="test")
                self.assertTrue(res.get("success"))
                self.assertEqual(res.get("tool_call", {}).get("name"), "paginate")
                self.assertEqual(res.get("tool_call", {}).get("arguments", {}).get("direction"), "next")
                self.assertEqual(res.get("ui_action", {}).get("delta"), 1)
                self.assertEqual(res.get("confidence"), 100.0)

        # Previous page tests
        prev_prompts = ["Vorherige Seite", "Eine Seite zurück", "Blättere zurück", "Seite zurück"]
        for prompt in prev_prompts:
            with self.subTest(prompt=prompt):
                res = _process_cactus_prompt(prompt, mode="test")
                self.assertTrue(res.get("success"))
                self.assertEqual(res.get("tool_call", {}).get("name"), "paginate")
                self.assertEqual(res.get("tool_call", {}).get("arguments", {}).get("direction"), "prev")
                self.assertEqual(res.get("ui_action", {}).get("delta"), -1)
                self.assertEqual(res.get("confidence"), 100.0)

    def test_ui_actions_direct(self):
        action_tests = [
            ("Zurück", "back"),
            ("Gehe zurück", "back"),
            ("Vor", "forward"),
            ("Gehe vor", "forward"),
            ("Aktualisieren", "refresh"),
            ("Neu laden", "refresh"),
            ("Aktualisiere die Ansicht", "refresh"),
            ("Vollbild", "fullscreen"),
        ]
        for prompt, expected_action in action_tests:
            with self.subTest(prompt=prompt):
                res = _process_cactus_prompt(prompt, mode="test")
                self.assertTrue(res.get("success"))
                self.assertEqual(res.get("tool_call", {}).get("name"), "ui_action")
                self.assertEqual(res.get("tool_call", {}).get("arguments", {}).get("action"), expected_action)
                self.assertEqual(res.get("ui_action", {}).get("action"), expected_action)
                self.assertEqual(res.get("confidence"), 100.0)

    def test_context_awareness(self):
        # 1. Pagination context with next page available
        ctx_pagination = {
            "active_section": "pulsecast",
            "active_section_title": "LCARS PULSECAST // MEDIA & DOWNLOAD HUB",
            "pagination": {
                "has_pagination": True,
                "current_page": 2,
                "total_pages": 6,
                "has_next": True,
                "has_prev": True
            }
        }
        res_next = _process_cactus_prompt("Nächste Seite", mode="test", context=ctx_pagination)
        self.assertIn("3 von 6", res_next.get("message", ""))

        res_prev = _process_cactus_prompt("Vorherige Seite", mode="test", context=ctx_pagination)
        self.assertIn("1 von 6", res_prev.get("message", ""))

        # 2. Pagination boundary: already on last page
        ctx_last_page = {
            "pagination": {
                "has_pagination": True,
                "current_page": 6,
                "total_pages": 6,
                "has_next": False,
                "has_prev": True
            }
        }
        res_boundary = _process_cactus_prompt("Nächste Seite", mode="test", context=ctx_last_page)
        self.assertIn("letzten Seite", res_boundary.get("message", ""))

        # 3. Active section title in refresh action
        ctx_refresh = {
            "active_section": "solar",
            "active_section_title": "LCARS ENERGIE-MANAGEMENT // BALKONSOLAR"
        }
        res_refresh = _process_cactus_prompt("Aktualisieren", mode="test", context=ctx_refresh)
        self.assertIn("BALKONSOLAR", res_refresh.get("message", ""))

    def test_cactus_process_api_with_ui_navigation(self):
        resp = self.client.post(
            "/api/cactus/process",
            json={
                "prompt": "Gehe zu Solar",
                "mode": "test",
                "context": {
                    "active_section": "system",
                    "visible_navigation": [{"id": "solar", "label": "SOLAR"}]
                }
            },
            headers=self.auth_headers
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("tool_call", {}).get("name"), "navigate_section")
        self.assertEqual(data.get("ui_action", {}).get("target"), "solar")
        self.assertIn("BALKONSOLAR", data.get("message", ""))

    def test_cactus_tools_endpoint(self):
        resp = self.client.get("/api/cactus/tools")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        tools = [t["name"] for t in data.get("tools", [])]
        self.assertIn("navigate_section", tools)
        self.assertIn("paginate", tools)
        self.assertIn("ui_action", tools)
        self.assertIn("control_light", tools)
        self.assertIn("get_weather", tools)

    def test_status_endpoint_tools(self):
        resp = self.client.get("/api/cactus/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        tools = data.get("tools", [])
        self.assertIn("navigate_section", tools)
        self.assertIn("paginate", tools)
        self.assertIn("ui_action", tools)

    def test_home_assistant_command_pass_through(self):
        res = _process_cactus_prompt("Decke 2 aus", mode="test")
        self.assertIn("mode", res)
        self.assertEqual(res.get("tool_call", {}).get("name"), "control_light")
        self.assertEqual(res.get("entity_id"), "light.decke2")

    def test_unknown_section_navigation_fails_gracefully(self):
        # Explicit navigation intent to an invalid section should fail deterministically
        # and NOT fall back to Home Assistant light control
        invalid_prompts = [
            "Gehe zu Holodeck",
            "Öffne UnbekannteSektion",
            "Wechsle zu Matrix"
        ]
        for prompt in invalid_prompts:
            with self.subTest(prompt=prompt):
                res = _process_cactus_prompt(prompt, mode="test")
                self.assertFalse(res.get("success"))
                self.assertIn("nicht im LCARS Dashboard gefunden", res.get("message", ""))
                self.assertNotEqual((res.get("tool_call") or {}).get("name"), "control_light")
                self.assertEqual(res.get("action"), "navigate_failed")


if __name__ == "__main__":
    unittest.main()
