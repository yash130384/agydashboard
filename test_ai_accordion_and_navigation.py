#!/usr/bin/env python3
"""
Regression test suite for KI Accordion, direct child navigation,
Cactus voice routing, legacy aliases, and permissions in LCARS System Dashboard.
"""

import json
import re
import subprocess
import unittest
from app import app, _process_cactus_prompt
from permissions_service import VALID_SECTIONS, PermissionsService


class TestAiAccordionAndNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def test_left_pillar_ai_accordion_rendered(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Accordion parent
        self.assertIn('id="nav-ai-group"', html)
        self.assertIn('id="btn-cat-ai"', html)
        self.assertIn("handleAiPillClick()", html)
        self.assertIn('id="aiFoldIcon"', html)
        self.assertIn('id="ai-subpillar"', html)

        # 5 direct child items inside subpillar
        for child in ("9router", "hermes", "ide", "ai-info", "gemini_live"):
            with self.subTest(child=child):
                self.assertIn(f'id="btn-cat-{child}"', html)
                self.assertIn(f"switchCategory('{child}')", html)

        # Legacy fallback button exists and is hidden
        self.assertIn('id="btn-cat-agents"', html)
        self.assertIn('style="display: none;"', html)

    def test_main_ai_sections_and_subnav_rendered(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Main overview section and cards
        self.assertIn('id="section-ai"', html)
        self.assertIn("LCARS KÜNSTLICHE INTELLIGENZ // BEREICHSÜBERSICHT", html)
        self.assertIn("⚡ 9ROUTER COMM-LINK", html)
        self.assertIn("🤖 HERMES SYSTEM-AGENTEN", html)
        self.assertIn("🚀 ANTIGRAVITY IDE", html)
        self.assertIn("📊 KI-INFO &amp; TELEMETRIE", html)
        self.assertIn("📡 SUBRAUM COMM (CACTUS)", html)

        # Dedicated sub-sections exist
        for sec in ("9router", "hermes", "ide", "ai-info", "gemini_live"):
            with self.subTest(section=sec):
                self.assertIn(f'id="section-{sec}"', html)

    def test_permissions_valid_sections_and_ui_checkboxes(self):
        expected_ai_sections = {"ai", "9router", "hermes", "ide", "agents", "ai-info", "gemini_live"}
        self.assertTrue(expected_ai_sections.issubset(VALID_SECTIONS))

        perm = PermissionsService()
        status = perm.get_public_status()
        self.assertIsInstance(status.get("locked_sections"), list)

        # Verify UI checkboxes in HTML
        resp = self.client.get("/")
        html = resp.get_data(as_text=True)
        for sec in ("ai", "9router", "hermes", "ide", "ai-info", "gemini_live", "agents"):
            with self.subTest(perm_checkbox=sec):
                self.assertIn(f'id="permLock_{sec}"', html)
                self.assertIn(f'value="{sec}"', html)

    def test_cactus_voice_navigation_direct_and_aliases(self):
        prompts = [
            ("Gehe zu KI", "ai"),
            ("Öffne KI", "ai"),
            ("KI Bereich", "ai"),
            ("KI Übersicht", "ai"),
            ("Gehe zu 9Router", "9router"),
            ("Öffne 9Router", "9router"),
            ("9Router Comm", "9router"),
            ("Router Chat", "9router"),
            ("Gehe zu Hermes", "hermes"),
            ("Öffne Hermes", "hermes"),
            ("Hermes Agenten", "hermes"),
            ("System-Agenten", "hermes"),
            ("Gehe zu Antigravity", "ide"),
            ("Gehe zu Antigravity IDE", "ide"),
            ("Öffne IDE", "ide"),
            ("Entwicklungsumgebung", "ide"),
            ("Gehe zu KI Info", "ai-info"),
            ("Öffne KI-Info", "ai-info"),
            ("Neural Telemetrie", "ai-info"),
            ("Gehe zu Subraum Comm", "gemini_live"),
            ("Öffne Subraum", "gemini_live"),
            ("Cactus", "gemini_live"),
            # Legacy aliases
            ("Gehe zu Agenten", "agents"),
            ("Gehe zu KI-Agenten", "agents"),
            ("Gehe zu Chat", "9router"),
        ]
        for prompt, expected_target in prompts:
            with self.subTest(prompt=prompt, expected_target=expected_target):
                res = _process_cactus_prompt(prompt, mode="test")
                self.assertTrue(res.get("success"), f"Failed prompt: {prompt}")
                self.assertEqual(res.get("tool_call", {}).get("name"), "navigate_section")
                self.assertEqual(res.get("tool_call", {}).get("arguments", {}).get("target"), expected_target)
                self.assertEqual(res.get("ui_action", {}).get("target"), expected_target)

    def test_frontend_js_legacy_mapping_and_permission_guard(self):
        from app import DASHBOARD_HTML

        # Legacy category agents mapped to 9router in switchCategory
        self.assertIn("if (catId === 'agents') {", DASHBOARD_HTML)
        self.assertIn("catId = '9router';", DASHBOARD_HTML)

        # Legacy category agents mapped in normalizeCategoryTarget
        self.assertIn("'agents': '9router'", DASHBOARD_HTML)

        # Legacy alias hidden in applyPermissionsVisibility
        self.assertIn("if (secId === 'agents') {", DASHBOARD_HTML)
        self.assertIn("btn.style.display = 'none';", DASHBOARD_HTML)

    def test_javascript_syntax_with_node_check(self):
        from app import DASHBOARD_HTML

        scripts = re.findall(r"<script(?:\s+[^>]*)?>(.*?)</script>", DASHBOARD_HTML, re.DOTALL)
        self.assertGreaterEqual(len(scripts), 1)

        for i, s in enumerate(scripts):
            if not s.strip():
                continue
            cleaned = re.sub(r"\{\{.*?\}\}", '"test"', s)
            cleaned = re.sub(r"\{%.*?%\}", "", cleaned)
            proc = subprocess.run(
                ["node", "--check"],
                input=cleaned,
                text=True,
                capture_output=True,
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"Script tag {i} failed node --check: {proc.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
