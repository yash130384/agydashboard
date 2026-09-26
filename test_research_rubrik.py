#!/usr/bin/env python3
"""
Test Suite für Research Rubrik Backend, API & LCARS Integration.
- Testet ResearchService (CRUD, Tags, Status, Key Takeaways, Quellen, Suche, Stats, Bot Trigger)
- Testet REST-API Endpunkte (/api/research, /api/research/<id>, /api/research/stats)
- Testet Rechte- und Zugriffskontrolle (403 bei unberechtigtem Zugriff)
- Testet Integration mit Researcher-Worker (POST durch Researcher-Bot)
"""

import json
import os
import tempfile
import unittest

from research_service import ResearchService
from app import app
from user_service import UserService


class TestResearchService(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.service = ResearchService(db_path=self.temp_db.name)

    def tearDown(self):
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    def test_create_and_get_report(self):
        rep = self.service.create_report(
            title="Evaluation LLM Voice Routing",
            content="# Research Report\nGemini Live vs Whisper.",
            status="completed",
            topic="Speech Models",
            tags=["ai", "voice", "gemini"],
            author="researcher",
            summary="Benchmark Sprachmodelle",
            key_takeaways=["Latenz unter 300ms", "Streaming-WebSockets überlegen"],
            sources=["https://arxiv.org/abs/12345", "https://google.github.io"],
            structured_data={"latency_ms": 280, "score": 9.2}
        )
        self.assertIsNotNone(rep)
        self.assertEqual(rep["title"], "Evaluation LLM Voice Routing")
        self.assertEqual(rep["status"], "completed")
        self.assertEqual(len(rep["key_takeaways"]), 2)
        self.assertEqual(len(rep["sources"]), 2)
        self.assertEqual(rep["structured_data"].get("score"), 9.2)

        fetched = self.service.get_report(rep["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], rep["id"])
        self.assertEqual(fetched["content"], "# Research Report\nGemini Live vs Whisper.")

    def test_list_and_filter_by_status(self):
        self.service.create_report("Report 1", "Content 1", status="completed")
        self.service.create_report("Report 2", "Content 2", status="running")
        self.service.create_report("Report 3", "Content 3", status="planned")

        all_reports = self.service.list_reports()
        self.assertEqual(len(all_reports), 3)

        completed = self.service.list_reports(status="completed")
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["title"], "Report 1")

        running = self.service.list_reports(status="running")
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0]["title"], "Report 2")

    def test_search_reports(self):
        self.service.create_report("Solar Inverter Protocol", "CAN bus documentation", topic="Solar")
        self.service.create_report("Subspace Audio Architecture", "Opus codec analysis", topic="Media")

        res_solar = self.service.list_reports(query="Inverter")
        self.assertEqual(len(res_solar), 1)
        self.assertEqual(res_solar[0]["title"], "Solar Inverter Protocol")

        res_opus = self.service.list_reports(query="Opus")
        self.assertEqual(len(res_opus), 1)
        self.assertEqual(res_opus[0]["title"], "Subspace Audio Architecture")

        res_none = self.service.list_reports(query="UnknownWord123")
        self.assertEqual(len(res_none), 0)

    def test_update_and_delete_report(self):
        rep = self.service.create_report("Initial Title", "Initial Content", status="running")
        rep_id = rep["id"]

        updated = self.service.update_report(
            rep_id,
            title="Final Title",
            content="Final Analysis",
            status="completed",
            key_takeaways=["Final Point 1"]
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["title"], "Final Title")
        self.assertEqual(updated["status"], "completed")
        self.assertEqual(updated["key_takeaways"], ["Final Point 1"])

        deleted = self.service.delete_report(rep_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.service.get_report(rep_id))

    def test_stats(self):
        self.service.create_report("R1", "C1", status="completed")
        self.service.create_report("R2", "C2", status="completed")
        self.service.create_report("R3", "C3", status="running")

        stats = self.service.get_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_status"].get("completed"), 2)
        self.assertEqual(stats["by_status"].get("running"), 1)


class TestResearchAPI(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        app.config["TESTING"] = True
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.service = ResearchService(db_path=self.temp_db.name)

        # Patch global research_service in app
        import app as app_module
        self._orig_research_service = app_module.research_service
        app_module.research_service = self.service

    def tearDown(self):
        import app as app_module
        app_module.research_service = self._orig_research_service
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    def test_api_list_and_stats(self):
        login_res = self.client.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        token = login_res.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        self.service.create_report("API Test 1", "Content 1", status="completed")

        resp = self.client.get("/api/research", headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "API Test 1")

        stats_resp = self.client.get("/api/research/stats", headers=headers)
        self.assertEqual(stats_resp.status_code, 200)
        stats = stats_resp.get_json()
        self.assertEqual(stats["total"], 1)

    def test_api_create_and_get_detail(self):
        login_res = self.client.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        token = login_res.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "title": "Researcher Push: Edge AI",
            "content": "# Edge AI\nEvaluation of on-device LLMs.",
            "status": "completed",
            "topic": "Edge Hardware",
            "tags": ["edge", "npu", "coral"],
            "author": "researcher-bot",
            "summary": "Edge AI Benchmark für Raspberry Pi / ODN Hub",
            "key_takeaways": ["Hailo NPU liefert 26 TOPS", "Whisper Small läuft mit 12x Realtime"],
            "sources": ["https://hailo.ai", "https://raspberrypi.com"],
            "structured_data": {"device": "rpi5", "tops": 26}
        }
        create_resp = self.client.post("/api/research", json=payload, headers=headers)
        self.assertEqual(create_resp.status_code, 201)
        created = create_resp.get_json()
        self.assertTrue(created.get("success"))
        rep_id = created["report"]["id"]

        detail_resp = self.client.get(f"/api/research/{rep_id}", headers=headers)
        self.assertEqual(detail_resp.status_code, 200)
        report = detail_resp.get_json()
        self.assertEqual(report["title"], "Researcher Push: Edge AI")
        self.assertEqual(len(report["key_takeaways"]), 2)
        self.assertEqual(len(report["sources"]), 2)
        self.assertEqual(report["structured_data"].get("tops"), 26)

    def test_api_trigger_research_action(self):
        login_res = self.client.post("/api/auth/login", json={"username": "cb", "password": "09010901"})
        token = login_res.get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        trigger_payload = {
            "title": "Untersuchung Neue Audio-Codecs für Subraum",
            "topic": "Audio Streaming",
            "tags": ["audio", "opus", "flac"],
            "notes": "Prüfen ob Opus 48kHz im Browser ohne Transcoding läuft.",
            "action": "trigger"
        }
        resp = self.client.post("/api/research", json=trigger_payload, headers=headers)
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        rep = data.get("report")
        self.assertEqual(rep["title"], "Untersuchung Neue Audio-Codecs für Subraum")
        self.assertEqual(rep["status"], "running")

    def test_api_forbidden_without_auth_or_permission(self):
        # Without headers or permission when locked
        from permissions_service import permissions_service
        with permissions_service.lock:
            orig_locked = list(permissions_service.locked_sections)
            if "research" not in permissions_service.locked_sections:
                permissions_service.locked_sections.append("research")

        try:
            resp = self.client.get("/api/research")
            self.assertEqual(resp.status_code, 403)
        finally:
            with permissions_service.lock:
                permissions_service.locked_sections = orig_locked


if __name__ == "__main__":
    unittest.main()
