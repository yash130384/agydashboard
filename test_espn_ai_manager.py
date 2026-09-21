#!/usr/bin/env python3
"""
Unit tests for ESPN Fantasy Football AI Manager in agydashboard.
Tests mode switching, safeguard validation (lock, eligibility, IR),
proposals lifecycle (create, apply, dismiss), and Flask API endpoints.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Import EspnFantasyClient from espn_service
from espn_service import EspnFantasyClient


class TestEspnAiManager(unittest.TestCase):
    def setUp(self):
        # Create an isolated temporary directory for test config and data
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
                "mode": "manual",
                "poll_interval": 35
            }
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.initial_config, f, indent=2)

        # Initialize client with test config
        self.client = EspnFantasyClient(config_path=self.config_path)

        # Synthetic roster entries for safeguard testing
        self.sample_roster_entries = [
            {
                "lineupSlotId": 2,
                "playerPoolEntry": {
                    "lineupLocked": False,
                    "player": {
                        "id": 1001,
                        "fullName": "Active Runningback",
                        "injuryStatus": "ACTIVE",
                        "eligibleSlots": [2, 3, 20, 23]
                    }
                }
            },
            {
                "lineupSlotId": 4,
                "playerPoolEntry": {
                    "lineupLocked": True,
                    "player": {
                        "id": 1002,
                        "fullName": "Locked Receiver",
                        "injuryStatus": "ACTIVE",
                        "eligibleSlots": [4, 5, 20, 23]
                    }
                }
            },
            {
                "lineupSlotId": 4,
                "playerPoolEntry": {
                    "lineupLocked": False,
                    "player": {
                        "id": 1003,
                        "fullName": "Injured Receiver",
                        "injuryStatus": "OUT",
                        "eligibleSlots": [4, 5, 20, 21, 23]
                    }
                }
            },
            {
                "lineupSlotId": 20,
                "playerPoolEntry": {
                    "lineupLocked": False,
                    "player": {
                        "id": 1004,
                        "fullName": "Bench Receiver",
                        "injuryStatus": "ACTIVE",
                        "eligibleSlots": [4, 5, 20, 23]
                    }
                }
            },
            {
                "lineupSlotId": 20,
                "playerPoolEntry": {
                    "lineupLocked": False,
                    "player": {
                        "id": 1005,
                        "fullName": "IR Candidate Active",
                        "injuryStatus": "ACTIVE",
                        "eligibleSlots": [2, 3, 20, 21, 23]
                    }
                }
            }
        ]

    def tearDown(self):
        # Clean up temporary directory
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # =========================================================================
    # 1. TEST MODE: get_mode and set_mode
    # =========================================================================
    def test_get_mode_default(self):
        """Prüft, dass der Standardmodus aus der Konfiguration geladen wird."""
        self.assertEqual(self.client.get_mode(), "manual")

    def test_set_mode_valid(self):
        """Prüft Umschalten auf 'semi' und 'full' sowie Persistenz in config.json."""
        # Umschalten auf SEMI
        res_semi = self.client.set_mode("semi")
        self.assertTrue(res_semi.get("success"))
        self.assertEqual(res_semi.get("mode"), "semi")
        self.assertEqual(self.client.get_mode(), "semi")

        # Prüfung der physischen config.json
        with open(self.config_path, "r", encoding="utf-8") as f:
            saved_cfg = json.load(f)
        self.assertEqual(saved_cfg.get("espn_fantasy", {}).get("mode"), "semi")

        # Umschalten auf FULL
        res_full = self.client.set_mode("full")
        self.assertTrue(res_full.get("success"))
        self.assertEqual(res_full.get("mode"), "full")
        self.assertEqual(self.client.get_mode(), "full")

        # Umschalten zurück auf MANUAL
        res_manual = self.client.set_mode("manual")
        self.assertTrue(res_manual.get("success"))
        self.assertEqual(res_manual.get("mode"), "manual")
        self.assertEqual(self.client.get_mode(), "manual")

    def test_set_mode_case_and_strip(self):
        """Prüft Groß-/Kleinschreibung und Leerzeichen beim Modus-Wechsel."""
        res = self.client.set_mode("  SEMI  ")
        self.assertEqual(res.get("mode"), "semi")
        self.assertEqual(self.client.get_mode(), "semi")

    def test_set_mode_invalid(self):
        """Prüft, dass ungültige Modi abgelehnt werden und einen ValueError werfen."""
        with self.assertRaises(ValueError):
            self.client.set_mode("autonomous_sky_net")
        with self.assertRaises(ValueError):
            self.client.set_mode("")
        # Modus bleibt unverändert
        self.assertEqual(self.client.get_mode(), "manual")

    # =========================================================================
    # 2. TEST SAFEGUARDS: validate_roster_move
    # =========================================================================
    def test_validate_empty_items(self):
        """Prüft Abweisung leerer oder ungültiger Move-Listen."""
        val1 = self.client.validate_roster_move([], raw_entries=self.sample_roster_entries)
        self.assertFalse(val1["valid"])

        val2 = self.client.validate_roster_move(None, raw_entries=self.sample_roster_entries)
        self.assertFalse(val2["valid"])

    def test_validate_unknown_player(self):
        """Prüft Abweisung von Spielern, die nicht im Roster vorhanden sind."""
        items = [{"playerId": 9999, "toLineupSlotId": 2}]
        val = self.client.validate_roster_move(items, raw_entries=self.sample_roster_entries)
        self.assertFalse(val["valid"])
        self.assertIn("nicht im Kader", val["error"])

    def test_validate_lock_check(self):
        """Prüft Safeguard 1: Spieler mit lineupLocked == True dürfen nicht bewegt werden."""
        items = [
            {
                "playerId": 1002,  # Locked Receiver
                "fromLineupSlotId": 4,
                "toLineupSlotId": 20
            }
        ]
        val = self.client.validate_roster_move(items, raw_entries=self.sample_roster_entries)
        self.assertFalse(val["valid"])
        self.assertIn("gelockt", val["error"].lower())

    def test_validate_slot_eligibility_check(self):
        """Prüft Safeguard 2: Spieler dürfen nur auf berechtigte Slots (eligibleSlots) gesetzt werden."""
        # Active Runningback (eligibleSlots: [2, 3, 20, 23]) -> Ziel Slot 0 (QB) ist unzulässig
        items_invalid = [
            {
                "playerId": 1001,
                "fromLineupSlotId": 2,
                "toLineupSlotId": 0
            }
        ]
        val_invalid = self.client.validate_roster_move(items_invalid, raw_entries=self.sample_roster_entries)
        self.assertFalse(val_invalid["valid"])
        self.assertIn("unzulässig", val_invalid["error"])

        # Gültiger Ziel-Slot: FLEX (23) oder BENCH (20)
        items_valid = [
            {
                "playerId": 1001,
                "fromLineupSlotId": 2,
                "toLineupSlotId": 23
            }
        ]
        val_valid = self.client.validate_roster_move(items_valid, raw_entries=self.sample_roster_entries)
        self.assertTrue(val_valid["valid"])
        self.assertIsNone(val_valid["error"])

    def test_validate_ir_check(self):
        """Prüft Safeguard 3: Nur Spieler mit OUT oder INJURY_RESERVE dürfen auf Slot 21 (IR)."""
        # Gesunder Spieler (ACTIVE) auf Slot 21 -> MUSS abgewiesen werden
        items_active_to_ir = [
            {
                "playerId": 1005,
                "fromLineupSlotId": 20,
                "toLineupSlotId": 21
            }
        ]
        val_active = self.client.validate_roster_move(items_active_to_ir, raw_entries=self.sample_roster_entries)
        self.assertFalse(val_active["valid"])
        self.assertIn("IR-Slot", val_active["error"])

        # Verletzter Spieler (OUT) auf Slot 21 -> Gültig
        items_injured_to_ir = [
            {
                "playerId": 1003,
                "fromLineupSlotId": 4,
                "toLineupSlotId": 21
            }
        ]
        val_injured = self.client.validate_roster_move(items_injured_to_ir, raw_entries=self.sample_roster_entries)
        self.assertTrue(val_injured["valid"])
        self.assertIsNone(val_injured["error"])

    def test_validate_valid_swap(self):
        """Prüft einen vollständigen, gültigen Start/Sit Swap zweier Spieler."""
        items = [
            {
                "playerId": 1004,  # Bench Receiver (from 20 to 4)
                "type": "LINEUP",
                "fromLineupSlotId": 20,
                "toLineupSlotId": 4
            },
            {
                "playerId": 1003,  # Injured Receiver (from 4 to 20)
                "type": "LINEUP",
                "fromLineupSlotId": 4,
                "toLineupSlotId": 20
            }
        ]
        val = self.client.validate_roster_move(items, raw_entries=self.sample_roster_entries)
        self.assertTrue(val["valid"])
        self.assertIsNone(val["error"])

    # =========================================================================
    # 3. TEST PROPOSALS: Erstellung, Apply und Dismiss
    # =========================================================================
    def test_proposals_lifecycle(self):
        """Prüft Hinzufügen, Auslesen, Verwerfen und Anwenden von Vorschlägen."""
        # Initial: Keine Vorschläge
        self.assertEqual(self.client.get_proposals(), [])
        self.assertIsNone(self.client.get_active_proposal())

        proposal = {
            "id": "prop_test_001",
            "created_at": int(time.time()),
            "week": 2,
            "status": "pending",
            "reason": "Receiver verletzt, Tausch gegen Bankspieler empfohlen.",
            "confidence": 0.92,
            "projected_gain": 8.4,
            "player_in_name": "Bench Receiver",
            "player_out_name": "Injured Receiver",
            "moves": [
                {
                    "playerId": 1004,
                    "type": "LINEUP",
                    "fromLineupSlotId": 20,
                    "toLineupSlotId": 4
                },
                {
                    "playerId": 1003,
                    "type": "LINEUP",
                    "fromLineupSlotId": 4,
                    "toLineupSlotId": 20
                }
            ]
        }

        # 1. Hinzufügen
        added = self.client.add_proposal(proposal)
        self.assertEqual(added["id"], "prop_test_001")

        # 2. Auslesen
        props = self.client.get_proposals()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["id"], "prop_test_001")

        active = self.client.get_active_proposal()
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], "prop_test_001")

        # 3. Dismiss Test mit separatem Vorschlag
        prop_dismiss = {
            "id": "prop_test_dismiss",
            "created_at": int(time.time()),
            "week": 2,
            "status": "pending",
            "reason": "Test Dismiss",
            "moves": [{"playerId": 1001, "toLineupSlotId": 23}]
        }
        self.client.add_proposal(prop_dismiss)
        self.assertEqual(len(self.client.get_proposals(only_pending=True)), 2)

        # Verwerfen
        res_dismiss = self.client.dismiss_proposal("prop_test_dismiss")
        self.assertTrue(res_dismiss)
        # Nicht mehr als pending gelistet
        pending_props = self.client.get_proposals(only_pending=True)
        self.assertEqual(len(pending_props), 1)
        self.assertEqual(pending_props[0]["id"], "prop_test_001")

        # Verwerfen einer ungültigen ID
        self.assertFalse(self.client.dismiss_proposal("non_existent_id"))

        # 4. Apply Test mit gemockter ESPN API Ausführung
        with patch.object(self.client, "execute_roster_transaction", return_value={"success": True, "data": {"status": "ok"}}), \
             patch.object(self.client, "validate_roster_move", return_value={"valid": True, "error": None}), \
             patch.object(self.client, "fetch", return_value={}), \
             patch.object(self.client, "send_telegram_notification", return_value=True):

            res_apply = self.client.apply_proposal("prop_test_001")
            self.assertTrue(res_apply.get("success"))
            self.assertIn("erfolgreich", res_apply.get("message"))

            # Status in proposals.json muss auf 'applied' stehen
            all_props = self.client.get_proposals()
            applied_prop = next((p for p in all_props if p["id"] == "prop_test_001"), None)
            self.assertIsNotNone(applied_prop)
            self.assertEqual(applied_prop["status"], "applied")

            # Es gibt keinen offenen (pending) Vorschlag mehr
            self.assertIsNone(self.client.get_active_proposal())

    def test_apply_proposal_safeguard_failure(self):
        """Prüft, dass apply_proposal abbricht, wenn Safeguards fehlschlagen."""
        proposal = {
            "id": "prop_bad_safeguard",
            "status": "pending",
            "moves": [{"playerId": 1002, "toLineupSlotId": 20}]
        }
        self.client.add_proposal(proposal)

        with patch.object(self.client, "validate_roster_move", return_value={"valid": False, "error": "Spieler gelockt"}):
            res = self.client.apply_proposal("prop_bad_safeguard")
            self.assertFalse(res.get("success"))
            self.assertIn("Safeguard-Prüfung fehlgeschlagen", res.get("error"))

    # =========================================================================
    # 4. TEST FLASK API ENDPOINTS
    # =========================================================================
    def test_flask_endpoints(self):
        """Prüft die Flask API-Endpunkte in app.py über den Flask TestClient."""
        from app import app
        app.config["TESTING"] = True
        client = app.test_client()

        # 1. GET /api/espn/mode
        resp = client.get("/api/espn/mode")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn(data.get("mode"), ("manual", "semi", "full"))

        # 2. POST /api/espn/mode
        resp_post = client.post("/api/espn/mode", json={"mode": "semi"})
        self.assertEqual(resp_post.status_code, 200)
        data_post = resp_post.get_json()
        self.assertEqual(data_post.get("status"), "ok")
        self.assertEqual(data_post.get("mode"), "semi")

        # Ungültiger Modus
        resp_inv = client.post("/api/espn/mode", json={"mode": "bad_mode"})
        self.assertEqual(resp_inv.status_code, 400)

        # Zurücksetzen auf manual
        client.post("/api/espn/mode", json={"mode": "manual"})

        # 3. GET /api/espn/proposals
        resp_props = client.get("/api/espn/proposals")
        self.assertEqual(resp_props.status_code, 200)
        data_props = resp_props.get_json()
        self.assertEqual(data_props.get("status"), "ok")
        self.assertIsInstance(data_props.get("proposals"), list)

        # 4. POST /api/espn/proposals/<id>/dismiss für ungültige ID
        resp_dis = client.post("/api/espn/proposals/invalid_id_999/dismiss")
        self.assertEqual(resp_dis.status_code, 404)

        # 5. POST /api/espn/analyze (Mocking analyze_roster_with_ai)
        with patch("espn_service.espn_client.analyze_roster_with_ai", return_value={"status": "ok", "mode": "manual", "proposals_count": 0}):
            resp_ana = client.post("/api/espn/analyze")
            self.assertEqual(resp_ana.status_code, 200)
            data_ana = resp_ana.get_json()
            self.assertEqual(data_ana.get("status"), "ok")


if __name__ == "__main__":
    unittest.main()
