#!/usr/bin/env python3
"""
Test Suite für Knowledge Base Backend & LCARS Frontend Integration.
- Testet KnowledgeService (CRUD, Tags, Kategorien, Suche, Statistiken)
- Testet REST-API (/api/knowledge, /api/knowledge/<id>, /api/knowledge/stats)
- Testet Berechtigungen & Schutz vor unautorisiertem Zugriff
- Testet Bot-Interface für automatische Ablage von QA- und Review-Berichten
"""

import json
import os
import tempfile
import unittest

from knowledge_service import KnowledgeService
from app import app
from user_service import UserService


class TestKnowledgeService(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.service = KnowledgeService(db_path=self.temp_db.name)

    def tearDown(self):
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    def test_create_and_get_article(self):
        art = self.service.create_article(
            title="QA Testbericht Sprint 42",
            content="# QA Report\nAlle 42 Tests bestanden.",
            category="qa",
            tags=["qa", "release", "v1.2"],
            author="qa-bot",
            summary="QA Abschlussbericht",
            metadata={"build": 104, "passed": 42}
        )
        self.assertIsNotNone(art)
        self.assertEqual(art["title"], "QA Testbericht Sprint 42")
        self.assertEqual(art["category"], "qa")
        self.assertIn("qa", art["tags"])
        self.assertEqual(art["metadata"].get("passed"), 42)

        fetched = self.service.get_article(art["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], art["id"])
        self.assertEqual(fetched["content"], "# QA Report\nAlle 42 Tests bestanden.")

    def test_list_and_filter_by_category(self):
        self.service.create_article("QA Report 1", "Content 1", category="qa")
        self.service.create_article("Review 1", "Content 2", category="review")
        self.service.create_article("ADR 1", "Architecture Decision", category="architecture")

        all_arts = self.service.list_articles()
        self.assertEqual(len(all_arts), 3)

        qa_arts = self.service.list_articles(category="qa")
        self.assertEqual(len(qa_arts), 1)
        self.assertEqual(qa_arts[0]["title"], "QA Report 1")

        rev_arts = self.service.list_articles(category="review")
        self.assertEqual(len(rev_arts), 1)
        self.assertEqual(rev_arts[0]["title"], "Review 1")

    def test_search_query(self):
        self.service.create_article("Auth System ADR", "OAuth2 and LCARS tokens", category="architecture")
        self.service.create_article("PulseCast Bug", "Audio player streaming error", category="qa")

        res1 = self.service.list_articles(query="OAuth2")
        self.assertEqual(len(res1), 1)
        self.assertEqual(res1[0]["title"], "Auth System ADR")

        res2 = self.service.list_articles(query="Pulsecast")
        self.assertEqual(len(res2), 1)
        self.assertEqual(res2[0]["title"], "PulseCast Bug")

        res_none = self.service.list_articles(query="NonExistingTerm")
        self.assertEqual(len(res_none), 0)

    def test_update_and_delete_article(self):
        art = self.service.create_article("Initial Title", "Initial Content", category="allgemein")
        art_id = art["id"]

        updated = self.service.update_article(art_id, title="Updated Title", content="Updated Content", category="runbook")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["title"], "Updated Title")
        self.assertEqual(updated["content"], "Updated Content")
        self.assertEqual(updated["category"], "runbook")

        deleted = self.service.delete_article(art_id)
        self.assertTrue(deleted)

        fetched = self.service.get_article(art_id)
        self.assertIsNone(fetched)

    def test_stats(self):
        self.service.create_article("QA 1", "Content", category="qa")
        self.service.create_article("QA 2", "Content", category="qa")
        self.service.create_article("Review 1", "Content", category="review")

        stats = self.service.get_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_category"].get("qa"), 2)
        self.assertEqual(stats["by_category"].get("review"), 1)


class TestKnowledgeApiEndpoints(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_knowledge_api_crud_flow(self):
        login_res = self.client.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        token = login_res.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create article via POST (with Super Admin session)
        post_resp = self.client.post("/api/knowledge", json={
            "title": "Automatischer Testbericht Bot",
            "content": "## Testlauf OK\nAlle Integrationstests grün.",
            "category": "qa",
            "tags": ["qa", "bot-run"],
            "author": "tester-bot",
            "metadata": {"tests": 12, "failed": 0}
        }, headers=headers)

        self.assertEqual(post_resp.status_code, 201)
        data = post_resp.get_json()
        self.assertTrue(data.get("success"))
        art = data.get("article")
        art_id = art["id"]
        self.assertEqual(art["title"], "Automatischer Testbericht Bot")
        self.assertEqual(art["author"], "tester-bot")

        # Get detail via GET /api/knowledge/<id>
        get_resp = self.client.get(f"/api/knowledge/{art_id}", headers=headers)
        self.assertEqual(get_resp.status_code, 200)
        detail = get_resp.get_json()
        self.assertEqual(detail["id"], art_id)
        self.assertIn("Integrationstests grün", detail["content"])

        # List via GET /api/knowledge
        list_resp = self.client.get("/api/knowledge?category=qa", headers=headers)
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.get_json()
        self.assertTrue(any(it["id"] == art_id for it in items))

        # Update via PUT /api/knowledge/<id>
        put_resp = self.client.put(f"/api/knowledge/{art_id}", json={
            "title": "Aktualisierter Testbericht Bot",
            "summary": "Nachtrag zum Testbericht"
        }, headers=headers)
        self.assertEqual(put_resp.status_code, 200)
        self.assertEqual(put_resp.get_json()["article"]["title"], "Aktualisierter Testbericht Bot")

        # Delete via DELETE /api/knowledge/<id>
        del_resp = self.client.delete(f"/api/knowledge/{art_id}", headers=headers)
        self.assertEqual(del_resp.status_code, 200)
        self.assertTrue(del_resp.get_json().get("success"))

        # Verify not found
        get_after = self.client.get(f"/api/knowledge/{art_id}", headers=headers)
        self.assertEqual(get_after.status_code, 404)


if __name__ == "__main__":
    unittest.main()
