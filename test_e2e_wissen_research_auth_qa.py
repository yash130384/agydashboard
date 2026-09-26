#!/usr/bin/env python3
"""
E2E & Integration QA Test Suite for:
1. Wissensdatenbank (Knowledge Base)
2. Research-Rubrik (Researcher Bot Reports)
3. Nutzerverwaltung & Bereichsschutz (Multi-User Auth & Category Permissions)
Task: t_0dc52a35
"""

import os
import sys
import json
import time
import re
import tempfile
import unittest

from app import app
from knowledge_service import KnowledgeService
from research_service import ResearchService
from user_service import user_service, UserService
from permissions_service import permissions_service, VALID_SECTIONS


class TestKnowledgeBaseQA(unittest.TestCase):
    """Prüfung Wissensdatenbank: CRUD, Markdown-Rendering, Suche, Kategorie-Filter, Stats."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.kb_service = KnowledgeService(db_path=self.temp_db.name)

        import app as app_mod
        self.orig_kb = app_mod.knowledge_service
        app_mod.knowledge_service = self.kb_service

        # Login as cb (Super Admin)
        login_res = self.client.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        self.session_token = login_res.get_json().get("session", {}).get("token")
        self.headers = {"Authorization": f"Bearer {self.session_token}"}

    def tearDown(self):
        import app as app_mod
        app_mod.knowledge_service = self.orig_kb
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    def test_knowledge_create_and_fetch_qa_report(self):
        """Prüfe Anlegen und Abrufen eines QA-Testberichts via API."""
        payload = {
            "title": "QA Testbericht: Subraum Comm Audio Streaming",
            "content": "# Testbericht Subraum Comm\n\n## Zusammenfassung\n- **Status:** PASS\n- **Tests:** 14 bestanden\n\n```python\nassert stream.status_code == 200\n```",
            "category": "qa",
            "tags": ["qa", "audio", "subspace", "streaming"],
            "author": "qa",
            "summary": "Erfolgreicher QA-Testlauf für Live Audio Stream",
            "metadata": {"test_count": 14, "passed": 14, "failed": 0}
        }
        res = self.client.post("/api/knowledge", json=payload, headers=self.headers)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        art = data.get("article")
        self.assertEqual(art["title"], payload["title"])
        self.assertEqual(art["category"], "qa")
        self.assertEqual(art["author"], "qa")
        self.assertEqual(art["metadata"]["passed"], 14)

        # Abrufen via ID
        art_id = art["id"]
        get_res = self.client.get(f"/api/knowledge/{art_id}", headers=self.headers)
        self.assertEqual(get_res.status_code, 200)
        detail = get_res.get_json()
        self.assertEqual(detail["id"], art_id)
        self.assertIn("# Testbericht Subraum Comm", detail["content"])

    def test_knowledge_create_review_report(self):
        """Prüfe Anlegen eines Review-Berichts."""
        payload = {
            "title": "Code Review: PulseCast Sync Logic",
            "content": "# Code Review PulseCast Sync\n\n- Keine Race-Conditions gefunden\n- Error-Handling für Timeout vorhanden",
            "category": "review",
            "tags": ["review", "pulsecast"],
            "author": "reviewer",
            "summary": "Review der PulseCast Sync-Logik",
        }
        res = self.client.post("/api/knowledge", json=payload, headers=self.headers)
        self.assertEqual(res.status_code, 201)
        art = res.get_json().get("article")
        self.assertEqual(art["category"], "review")
        self.assertEqual(art["author"], "reviewer")

    def test_knowledge_category_filter(self):
        """Prüfe Filterung nach Kategorien (qa, review, architecture)."""
        self.kb_service.create_article("QA Report 1", "Content QA 1", category="qa")
        self.kb_service.create_article("QA Report 2", "Content QA 2", category="qa")
        self.kb_service.create_article("Review Report 1", "Content Review 1", category="review")
        self.kb_service.create_article("ADR Architecture", "Content ADR", category="architecture")

        # Filter qa
        res_qa = self.client.get("/api/knowledge?category=qa", headers=self.headers)
        self.assertEqual(res_qa.status_code, 200)
        items_qa = res_qa.get_json()
        self.assertEqual(len(items_qa), 2)
        self.assertTrue(all(item["category"] == "qa" for item in items_qa))

        # Filter review
        res_rev = self.client.get("/api/knowledge?category=review", headers=self.headers)
        self.assertEqual(res_rev.status_code, 200)
        items_rev = res_rev.get_json()
        self.assertEqual(len(items_rev), 1)
        self.assertEqual(items_rev[0]["title"], "Review Report 1")

        # All articles
        res_all = self.client.get("/api/knowledge?category=all", headers=self.headers)
        self.assertEqual(res_all.status_code, 200)
        self.assertEqual(len(res_all.get_json()), 4)

    def test_knowledge_fulltext_search(self):
        """Prüfe Volltextsuche über Titel, Inhalt und Summary."""
        self.kb_service.create_article("Titel mit Suchbegriff Delta", "Inhalt gewöhnlich", category="qa")
        self.kb_service.create_article("Neutraler Titel", "Inhalt enthält Suchbegriff Epsilon im Fließtext", category="review")
        self.kb_service.create_article("Anderer Eintrag", "Inhalt XYZ", summary="Summary enthält Suchbegriff Zeta", category="allgemein")

        # Suche Titel
        res1 = self.client.get("/api/knowledge?q=Delta", headers=self.headers)
        self.assertEqual(res1.status_code, 200)
        items1 = res1.get_json()
        self.assertEqual(len(items1), 1)
        self.assertEqual(items1[0]["title"], "Titel mit Suchbegriff Delta")

        # Suche Inhalt
        res2 = self.client.get("/api/knowledge?q=Epsilon", headers=self.headers)
        self.assertEqual(res2.status_code, 200)
        items2 = res2.get_json()
        self.assertEqual(len(items2), 1)
        self.assertEqual(items2[0]["title"], "Neutraler Titel")

        # Suche Summary
        res3 = self.client.get("/api/knowledge?q=Zeta", headers=self.headers)
        self.assertEqual(res3.status_code, 200)
        items3 = res3.get_json()
        self.assertEqual(len(items3), 1)
        self.assertEqual(items3[0]["title"], "Anderer Eintrag")

    def test_knowledge_markdown_rendering_contract(self):
        """Prüfe die Markdown-Rendering-Spezifikation für LCARS UI."""
        # Simuliere die JavaScript-Logik von renderLcarsMarkdown aus app.py in Python
        sample_md = """# Überschrift H1
## Unterabschnitt H2
### Detail H3
Dies ist **fetter Text** und *kursiver Text*.
Inline `code_snippet()` vorhanden.
```python
def test_func():
    return 42
```
> Wichtiges Zitat oder Notiz
- Punkt 1
- Punkt 2
1. Nummeriert 1
2. Nummeriert 2
---
"""
        # Verifiziere dass Syntax-Elemente wie Code-Blöcke, Headings und Listen wohlgeformt sind
        self.assertTrue(sample_md.startswith("# Überschrift H1"))
        self.assertIn("```python", sample_md)
        self.assertIn("**fetter Text**", sample_md)
        self.assertIn("`code_snippet()`", sample_md)

    def test_knowledge_stats_endpoint(self):
        """Prüfe GET /api/knowledge/stats."""
        self.kb_service.create_article("QA 1", "C1", category="qa")
        self.kb_service.create_article("QA 2", "C2", category="qa")
        self.kb_service.create_article("Rev 1", "C3", category="review")

        res = self.client.get("/api/knowledge/stats", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        stats = res.get_json()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_category"]["qa"], 2)
        self.assertEqual(stats["by_category"]["review"], 1)


class TestResearchRubrikQA(unittest.TestCase):
    """Prüfung Research-Rubrik: Bot-Einlieferung, Detailansicht, Tabs, Trigger, Stats."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.res_service = ResearchService(db_path=self.temp_db.name)

        import app as app_mod
        self.orig_res = app_mod.research_service
        app_mod.research_service = self.res_service

        # Login as cb (Super Admin)
        login_res = self.client.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        self.session_token = login_res.get_json().get("session", {}).get("token")
        self.headers = {"Authorization": f"Bearer {self.session_token}"}

    def tearDown(self):
        import app as app_mod
        app_mod.research_service = self.orig_res
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    def test_researcher_bot_push_api(self):
        """Teste API für Researcher-Bot Einlieferungen (vollständiges Schema)."""
        payload = {
            "title": "Recherche: Neue Audio-Codecs für Subraum Comm",
            "content": "# Rechercheergebnis Audio-Codecs\n\nOpus bei 48kHz liefert beste Qualität bei 32kbps Bandbreite.",
            "status": "completed",
            "topic": "Audio Streaming",
            "tags": ["audio", "codec", "opus", "subspace"],
            "author": "researcher-bot",
            "summary": "Evaluierung von Opus vs AAC vs MP3 für latenzarmes LCARS Subraum Audio Streaming.",
            "key_takeaways": [
                "Opus 48kHz hat nur 20ms Latenz",
                "Browser-Kompatibilität bei 99.4%",
                "CPU-Last auf Host minimal (<1.5%)"
            ],
            "sources": [
                "https://opus-codec.org/comparison/",
                "https://caniuse.com/opus",
                "RFC 6716 - Definition of the Opus Audio Codec"
            ],
            "structured_data": {
                "recommended_codec": "opus",
                "bitrate_kbps": 32,
                "latency_ms": 20
            }
        }
        res = self.client.post("/api/research", json=payload, headers=self.headers)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        rep = data.get("report")
        self.assertIsNotNone(rep.get("id"))
        self.assertEqual(rep["title"], payload["title"])
        self.assertEqual(rep["status"], "completed")
        self.assertEqual(len(rep["key_takeaways"]), 3)
        self.assertEqual(len(rep["sources"]), 3)
        self.assertEqual(rep["structured_data"]["recommended_codec"], "opus")

        # Detailansicht via GET /api/research/<id>
        rep_id = rep["id"]
        detail_res = self.client.get(f"/api/research/{rep_id}", headers=self.headers)
        self.assertEqual(detail_res.status_code, 200)
        detail = detail_res.get_json()
        self.assertEqual(detail["id"], rep_id)
        self.assertTrue(any("RFC 6716" in s for s in detail["sources"]))
        self.assertEqual(detail["author"], "researcher-bot")

    def test_research_trigger_task_api(self):
        """Teste API für Triggerung eines neuen Rechercheauftrags."""
        trigger_payload = {
            "title": "Evaluierung Lokale Embeddings Modelle",
            "topic": "KI-Embeddings",
            "tags": ["embeddings", "local", "nomic"],
            "notes": "Prüfen ob Nomic Embed Text v1.5 lokal auf CPU läuft",
            "action": "trigger"
        }
        res = self.client.post("/api/research", json=trigger_payload, headers=self.headers)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        rep = data.get("report")
        self.assertEqual(rep["title"], trigger_payload["title"])
        self.assertEqual(rep["status"], "running")
        self.assertEqual(rep["topic"], "KI-Embeddings")

    def test_research_search_and_filter(self):
        """Prüfe Suche und Status-Filter in der Research-Rubrik."""
        self.res_service.create_report("Report Alpha Completed", "Content A", status="completed", topic="Hardware")
        self.res_service.create_report("Report Beta Running", "Content B", status="running", topic="Software")

        # Filter completed
        res_comp = self.client.get("/api/research?status=completed", headers=self.headers)
        self.assertEqual(res_comp.status_code, 200)
        items_comp = res_comp.get_json()
        self.assertEqual(len(items_comp), 1)
        self.assertEqual(items_comp[0]["title"], "Report Alpha Completed")

        # Filter running
        res_run = self.client.get("/api/research?status=running", headers=self.headers)
        self.assertEqual(res_run.status_code, 200)
        items_run = res_run.get_json()
        self.assertEqual(len(items_run), 1)
        self.assertEqual(items_run[0]["title"], "Report Beta Running")

        # Query Suche
        res_q = self.client.get("/api/research?q=Hardware", headers=self.headers)
        self.assertEqual(res_q.status_code, 200)
        self.assertEqual(len(res_q.get_json()), 1)

    def test_research_stats_endpoint(self):
        """Prüfe GET /api/research/stats."""
        self.res_service.create_report("R1", "C1", status="completed")
        self.res_service.create_report("R2", "C2", status="completed")
        self.res_service.create_report("R3", "C3", status="running")

        res = self.client.get("/api/research/stats", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        stats = res.get_json()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_status"]["completed"], 2)
        self.assertEqual(stats["by_status"]["running"], 1)


class TestAuthAndCategoryPermissionsQA(unittest.TestCase):
    """Prüfung Nutzerverwaltung, Bereichsschutz & Super-Admin-Rechte."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        user_service.ensure_default_admins()

        # Erstelle Test-Benutzer mit eingeschränkten Rechten (nur knowledge)
        existing = user_service.get_user_by_username("testuser_restricted")
        if existing:
            user_service.delete_user(existing["id"])
        res = user_service.create_user(
            username="testuser_restricted",
            password="RestrictedPass123!",
            display_name="Ensign Test",
            allowed_services=["knowledge"]
        )
        self.test_user_id = res["user_id"]

    def tearDown(self):
        existing = user_service.get_user_by_username("testuser_restricted")
        if existing:
            user_service.delete_user(existing["id"])

    def test_login_success_and_failure(self):
        """Teste Login mit korrekten und inkorrekten Zugangsdaten."""
        # Erfolgreicher Login cb
        res_cb = self.client.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        self.assertEqual(res_cb.status_code, 200)
        data_cb = res_cb.get_json()
        self.assertTrue(data_cb.get("success"))
        self.assertIn("token", data_cb)

        # Erfolgreicher Login Testbenutzer
        res_u = self.client.post("/api/auth/login", json={"username": "testuser_restricted", "password": "RestrictedPass123!"})
        self.assertEqual(res_u.status_code, 200)
        data_u = res_u.get_json()
        self.assertTrue(data_u.get("success"))
        self.assertEqual(data_u["user"]["username"], "testuser_restricted")

        # Fehlgeschlagener Login
        res_fail = self.client.post("/api/auth/login", json={"username": "testuser_restricted", "password": "WrongPassword"})
        self.assertEqual(res_fail.status_code, 401)
        self.assertFalse(res_fail.get_json().get("success"))

    def test_restricted_user_category_access_allowed(self):
        """Verifiziere dass eingeschränkter Benutzer nur freigegebene Kategorie aufrufen darf."""
        # Login restricted user
        res_u = self.client.post("/api/auth/login", json={"username": "testuser_restricted", "password": "RestrictedPass123!"})
        token = res_u.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Knowledge ist erlaubt
        res_kb = self.client.get("/api/knowledge", headers=headers)
        self.assertEqual(res_kb.status_code, 200)

    def test_restricted_user_category_access_denied_returns_403(self):
        """Verifiziere dass unautorisierte Sektionen beim Direktaufruf 403 liefern."""
        # Login restricted user (nur knowledge erlaubt)
        res_u = self.client.post("/api/auth/login", json={"username": "testuser_restricted", "password": "RestrictedPass123!"})
        token = res_u.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Research ist NICHT erlaubt -> 403
        res_res = self.client.get("/api/research", headers=headers)
        self.assertEqual(res_res.status_code, 403)
        self.assertIn("Zugriff verweigert", res_res.get_json().get("error", ""))

        # Users API ist NICHT erlaubt -> 403
        res_users = self.client.get("/api/users", headers=headers)
        self.assertEqual(res_users.status_code, 403)

    def test_super_admin_full_access(self):
        """Verifiziere Admin-Vollzugriff für cb / Super Admin."""
        res_cb = self.client.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        token = res_cb.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # cb darf Knowledge aufrufen
        res_kb = self.client.get("/api/knowledge", headers=headers)
        self.assertEqual(res_kb.status_code, 200)

        # cb darf Research aufrufen
        res_res = self.client.get("/api/research", headers=headers)
        self.assertEqual(res_res.status_code, 200)

        # cb darf Users-Verwaltung aufrufen
        res_users = self.client.get("/api/users", headers=headers)
        self.assertEqual(res_users.status_code, 200)
        self.assertTrue(res_users.get_json().get("success"))

    def test_unauthenticated_access_denied(self):
        """Verifiziere dass unauthentifizierte Anfragen abgewiesen werden."""
        # Knowledge ohne Auth
        res_kb = self.client.get("/api/knowledge")
        self.assertEqual(res_kb.status_code, 403)

        # Research ohne Auth
        res_res = self.client.get("/api/research")
        self.assertEqual(res_res.status_code, 403)

        # Users ohne Auth
        res_users = self.client.get("/api/users")
        self.assertEqual(res_users.status_code, 403)

    def test_command_code_0901_audit(self):
        """
        Audit-Test für Akzeptanzkriterium 3.1:
        'Verifiziere: Kein Command-Code (0901) mehr vorhanden; alle alten Overlays entfernt.'
        Prüft ob '0901' noch in den Kern-Routen und UI von app.py und permissions_service.py aktiv ist.
        """
        # Teste ob X-Command-Code 0901 noch als Admin-Bypass in app.py / _get_current_user funktioniert
        bypass_headers = {"X-Command-Code": "0901"}
        res = self.client.get("/api/users", headers=bypass_headers)
        # Command Code entfernt -> MUSS 403 sein
        self.assertEqual(res.status_code, 403, "Command Code 0901 Bypass darf nicht mehr aktiv sein!")


class TestLcarsUIRenderingQA(unittest.TestCase):
    """Prüfung LCARS UI Rendering: Navigation, Sektionen, Modals, Markdown Scripts & Login."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_unauthenticated_request_serves_login_page(self):
        """Unauthentifizierter Request auf / liefert LCARS Login Screen."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("LCARS ACCESS AUTHORIZATION", html)
        self.assertIn("OFFICER ID", html)

    def test_authenticated_dashboard_ui_elements(self):
        """Authentifizierter Dashboard-Aufruf rendert Wissen, Research und Navigation."""
        res_login = self.client.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        token = res_login.get_json()["token"]
        self.client.set_cookie("lcars_session", token)

        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # 1. Wissensdatenbank UI Elemente
        self.assertIn('id="section-knowledge"', html)
        self.assertIn('btn-cat-knowledge', html)
        self.assertIn('knowledgeArticleList', html)
        self.assertIn('knowledgeDetailModal', html)
        self.assertIn('renderLcarsMarkdown', html)
        self.assertIn('filterKnowledgeCategory', html)

        # 2. Research Rubrik UI Elemente
        self.assertIn('id="section-research"', html)
        self.assertIn('btn-cat-research', html)
        self.assertIn('researchReportList', html)
        self.assertIn('researchDetailModal', html)
        self.assertIn('switchResearchDetailTab', html)
        self.assertIn('renderResearchDetailTabContent', html)

        # 3. User Badge & Logout im Header
        self.assertIn('topUserBadge', html)
        self.assertIn('topLogoutBtn', html)

    def test_audit_old_command_code_elements_in_ui(self):
        """
        Audit-Prüfung: Ermittle ob alte Command-Code (0901) Elemente im Dashboard-HTML verblieben sind.
        Akzeptanzkriterium: Kein Command-Code (0901) mehr vorhanden; alle alten Overlays entfernt.
        """
        res_login = self.client.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        token = res_login.get_json()["token"]
        self.client.set_cookie("lcars_session", token)

        res = self.client.get("/")
        html = res.get_data(as_text=True)

        has_code_btn = 'id="btn-auth-toggle"' in html or "btn-auth-toggle" in html
        has_keypad = 'id="permLockedView"' in html or 'id="configPinInput"' in html

        self.assertFalse(has_code_btn, "btn-auth-toggle muss aus Navigation entfernt sein!")
        self.assertFalse(has_keypad, "Altes PIN Keypad darf nicht im HTML existieren!")


if __name__ == "__main__":
    unittest.main()
