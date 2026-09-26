#!/usr/bin/env python3
"""
Unit tests for ESPN Fantasy Bot Log & Telemetry in agydashboard.
Tests:
- Bot status and countdown calculations in manual, semi, and full modes
- Logging and rationale capture in German for checks, moves, and proposals
- De-duplication of consecutive identical roster checks
- GET /api/espn/history returning bot_status and decision history
- DOM verification of Bot status bar and activity log card in section-fantasy
"""

import os
import json
import time
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from espn_service import EspnFantasyClient
from app import app


class TestEspnBotLog(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")
        self.initial_config = {
            "espn_fantasy": {
                "enabled": True,
                "league_id": 378649793,
                "team_id": 17,
                "season_year": 2026,
                "swid": "{TEST-SWID-123}",
                "espn_s2": "TEST-ESPN-S2-ABC",
                "mode": "full",
                "poll_interval": 35,
                "ai_check_interval": 120
            }
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.initial_config, f, indent=2)

        self.client = EspnFantasyClient(config_path=self.config_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_bot_status_full_mode(self):
        status = self.client.get_bot_status()
        self.assertIn("mode", status)
        self.assertEqual(status["mode"], "full")
        self.assertEqual(status["interval_seconds"], 120)
        self.assertIn("last_run_datetime", status)
        self.assertIn("next_run_text", status)

    def test_get_bot_status_manual_mode(self):
        self.client.set_mode("manual")
        status = self.client.get_bot_status()
        self.assertEqual(status["mode"], "manual")
        self.assertFalse(status["is_active"])
        self.assertIn("Pausiert", status["next_run_text"])

    def test_log_decision_and_deduplication(self):
        # 1. Log first ROSTER_CHECK
        e1 = self.client.log_decision(
            "ROSTER_CHECK",
            "Kader analysiert: Aufstellung optimal, keine Änderungen nötig",
            success=True,
            metadata={"reason": "Alle Starter aktiv", "base_details": "Kader analysiert: Aufstellung optimal, keine Änderungen nötig"}
        )
        self.assertEqual(e1["action"], "ROSTER_CHECK")
        self.assertIn("optimal", e1["details"])

        # 2. Log second identical ROSTER_CHECK -> should update in-place with repeat_count=2
        e2 = self.client.log_decision(
            "ROSTER_CHECK",
            "Kader analysiert: Aufstellung optimal, keine Änderungen nötig",
            success=True,
            metadata={"reason": "Alle Starter aktiv", "base_details": "Kader analysiert: Aufstellung optimal, keine Änderungen nötig"}
        )
        history = self.client.get_decision_log(limit=10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["metadata"].get("repeat_count"), 2)
        self.assertIn("x2", history[0]["details"])

        # 3. Log a different action -> creates new entry
        self.client.log_decision(
            "AUTO_MOVE_EXECUTED",
            "Aufstellung autonom angepasst: Mitchell für Robinson eingewechselt",
            success=True,
            metadata={"reason": "Höherer Floor", "player_in": "Mitchell", "player_out": "Robinson"}
        )
        history2 = self.client.get_decision_log(limit=10)
        self.assertEqual(len(history2), 2)
        self.assertEqual(history2[-1]["action"], "AUTO_MOVE_EXECUTED")
        self.assertEqual(history2[-1]["metadata"]["reason"], "Höherer Floor")

    def test_analyze_roster_logging_no_moves(self):
        # Mock fetch to return roster and mock 9Router to return no moves
        sample_data = {
            "status": "ok",
            "team_name": "Test Team",
            "current_week": 1,
            "roster": [
                {
                    "player_id": 101, "name": "Player 1", "position": "RB", "pro_team": "KC",
                    "slot": "RB", "slot_id": 2, "is_starter": True, "injury": "ACTIVE",
                    "projected": 15.0, "actual": 0.0, "is_locked": False, "eligible_slots": [2, 20]
                }
            ],
            "matchup": {"my_team": {"score": 0, "projected": 100, "win_prob": 50}}
        }
        with patch.object(self.client, "fetch", return_value=sample_data), \
             patch.object(self.client, "_call_9router", return_value=json.dumps({
                 "assessment": "Alle Starter optimal aufgestellt. Kein Handlungsbedarf.",
                 "recommended_moves": [],
                 "confidence": 0.98
             })):
            res = self.client.analyze_roster_with_ai(force=True)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["proposals_count"], 0)

            history = self.client.get_decision_log(limit=5)
            last = history[-1]
            self.assertEqual(last["action"], "ROSTER_CHECK")
            self.assertIn("optimal", last["details"])
            self.assertEqual(last["metadata"]["reason"], "Alle Starter optimal aufgestellt. Kein Handlungsbedarf.")

    def test_api_espn_history_endpoint(self):
        test_client = app.test_client()
        resp = test_client.get("/api/espn/history")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("bot_status", data)
        self.assertIn("history", data)
        self.assertIsInstance(data["history"], list)

    def test_ui_dom_elements_present(self):
        test_client = app.test_client()
        test_client.set_cookie("lcars_session", "0901")
        resp = test_client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")

        # Telemetry & Status bar elements
        self.assertIn('id="fantasyBotStatusBar"', html)
        self.assertIn('id="fantasyBotStatusBadge"', html)
        self.assertIn('id="fantasyBotLastRun"', html)
        self.assertIn('id="fantasyBotNextRun"', html)
        self.assertIn('id="fantasyBotLastAction"', html)
        self.assertIn('id="fantasyBotLastReason"', html)
        self.assertIn('id="btnFantasyRunNow"', html)

        # Log & Decision Card elements
        self.assertIn('id="fantasyBotLogCard"', html)
        self.assertIn('id="fantasyLogCountBadge"', html)
        self.assertIn('id="fantasyDecisionLogTableBody"', html)
        self.assertIn('filterFantasyLog', html)
        self.assertIn('triggerFantasyBotCheck', html)


if __name__ == "__main__":
    unittest.main()
