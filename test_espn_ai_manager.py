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
    # 2. TEST RISK LEVEL (1-5 Validierung & Persistenz)
    # =========================================================================
    def test_get_risk_level_default(self):
        """Prüft, dass der Standard-Risikolevel 3 (Ausgewogen) ist."""
        self.assertEqual(self.client.get_risk_level(), 3)

    def test_set_risk_level_valid(self):
        """Prüft das Setzen aller gültigen Risiko-Stufen (1 bis 5) inkl. Persistenz in config.json."""
        for lvl in (1, 2, 3, 4, 5):
            res = self.client.set_risk_level(lvl)
            self.assertTrue(res.get("success"))
            self.assertEqual(res.get("risk_level"), lvl)
            self.assertEqual(self.client.get_risk_level(), lvl)

            # Physische Prüfung in config.json
            with open(self.config_path, "r", encoding="utf-8") as f:
                saved_cfg = json.load(f)
            self.assertEqual(saved_cfg.get("espn_fantasy", {}).get("risk_level"), lvl)

        # Auch numerische Strings wie "4" müssen akzeptiert und als int konvertiert werden
        res_str = self.client.set_risk_level("4")
        self.assertTrue(res_str.get("success"))
        self.assertEqual(res_str.get("risk_level"), 4)
        self.assertEqual(self.client.get_risk_level(), 4)

    def test_set_risk_level_invalid(self):
        """Prüft, dass Werte außerhalb 1-5 sowie ungültige Typen abgewiesen werden."""
        self.client.set_risk_level(3)

        invalid_values = [0, -1, 6, 10, 99, "0", "6", "-2", "ultra", "", None, [3], {"lvl": 3}]
        for val in invalid_values:
            with self.assertRaises(ValueError, msg=f"Erwarteter ValueError für Eingabe: {val}"):
                self.client.set_risk_level(val)

        # Level muss unverändert 3 bleiben
        self.assertEqual(self.client.get_risk_level(), 3)

    # =========================================================================
    # 3. TEST FLASH TOGGLE (flash_enabled & trigger_flash)
    # =========================================================================
    def test_get_flash_enabled_default(self):
        """Prüft, dass Flash-Signale standardmäßig aktiviert (True) sind."""
        self.assertTrue(self.client.get_flash_enabled())

    def test_set_flash_enabled_toggle(self):
        """Prüft das Umschalten von flash_enabled (Boolean und Strings) inkl. Persistenz."""
        # 1. Deaktivieren via bool
        res_off = self.client.set_flash_enabled(False)
        self.assertTrue(res_off.get("success"))
        self.assertFalse(res_off.get("flash_enabled"))
        self.assertFalse(self.client.get_flash_enabled())

        with open(self.config_path, "r", encoding="utf-8") as f:
            saved_cfg = json.load(f)
        self.assertFalse(saved_cfg.get("espn_fantasy", {}).get("flash_enabled"))

        # 2. Wieder aktivieren via bool
        res_on = self.client.set_flash_enabled(True)
        self.assertTrue(res_on.get("success"))
        self.assertTrue(res_on.get("flash_enabled"))
        self.assertTrue(self.client.get_flash_enabled())

        # 3. String Parsing: "false", "0", "no", "off" -> False
        for false_val in ("false", "False", "0", "off", "no"):
            self.client.set_flash_enabled(false_val)
            self.assertFalse(self.client.get_flash_enabled())

        # 4. String Parsing: "true", "1", "yes", "on" -> True
        for true_val in ("true", "True", "1", "on", "yes"):
            self.client.set_flash_enabled(true_val)
            self.assertTrue(self.client.get_flash_enabled())

    def test_trigger_flash_disabled(self):
        """Prüft, dass trigger_flash sofort False liefert und übersprungen wird, wenn deaktiviert."""
        self.client.set_flash_enabled(False)
        # Wenn deaktiviert, darf trigger_flash sofort False zurückgeben ohne HA-Aufruf
        res = self.client.trigger_flash()
        self.assertFalse(res)

    # =========================================================================
    # 4. TEST SETTINGS BUNDLE & AI STATS
    # =========================================================================
    def test_get_and_update_settings(self):
        """Prüft get_settings und update_settings für gebündelte Abfragen und Aktualisierungen."""
        settings = self.client.get_settings()
        self.assertEqual(settings.get("status"), "ok")
        self.assertEqual(settings.get("mode"), "manual")
        self.assertEqual(settings.get("risk_level"), 3)
        self.assertTrue(settings.get("flash_enabled"))

        # update_settings mit partiellen & vollständigen Parametern
        updated = self.client.update_settings(mode="semi", risk_level=5, flash_enabled=False)
        self.assertEqual(updated.get("status"), "ok")
        self.assertEqual(updated.get("mode"), "semi")
        self.assertEqual(updated.get("risk_level"), 5)
        self.assertFalse(updated.get("flash_enabled"))

        self.assertEqual(self.client.get_mode(), "semi")
        self.assertEqual(self.client.get_risk_level(), 5)
        self.assertFalse(self.client.get_flash_enabled())

        # Ungültiger Parameter muss ValueError werfen
        with self.assertRaises(ValueError):
            self.client.update_settings(risk_level=99)

    def test_get_ai_stats(self):
        """Prüft das Abrufen der KI-Tokenstatistiken und Modellangaben."""
        stats = self.client.get_ai_stats()
        self.assertEqual(stats.get("status"), "ok")
        self.assertIn("model", stats)
        self.assertIn("last_token_usage", stats)
        usage = stats["last_token_usage"]
        self.assertIn("prompt_tokens", usage)
        self.assertIn("completion_tokens", usage)
        self.assertIn("total_tokens", usage)
        self.assertIsInstance(usage["total_tokens"], int)

    # =========================================================================
    # 5. TEST SAFEGUARDS: validate_roster_move
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

    def test_flask_settings_endpoint(self):
        """Prüft den Flask-Endpunkt /api/espn/settings (GET und POST) inkl. Validierung."""
        from app import app
        app.config["TESTING"] = True
        client = app.test_client()

        # Ursprüngliche Einstellungen sichern
        orig_resp = client.get("/api/espn/settings")
        self.assertEqual(orig_resp.status_code, 200)
        orig_data = orig_resp.get_json()
        self.assertEqual(orig_data.get("status"), "ok")
        self.assertIn("mode", orig_data)
        self.assertIn("risk_level", orig_data)
        self.assertIn("flash_enabled", orig_data)

        try:
            # 1. POST /api/espn/settings: Gültiges Update aller Parameter
            post_resp = client.post("/api/espn/settings", json={
                "mode": "semi",
                "risk_level": 4,
                "flash_enabled": False
            })
            self.assertEqual(post_resp.status_code, 200)
            post_data = post_resp.get_json()
            self.assertEqual(post_data.get("status"), "ok")
            self.assertEqual(post_data.get("mode"), "semi")
            self.assertEqual(post_data.get("risk_level"), 4)
            self.assertFalse(post_data.get("flash_enabled"))

            # 2. GET /api/espn/settings: Verifikation der neuen Einstellungen
            get_resp = client.get("/api/espn/settings")
            self.assertEqual(get_resp.status_code, 200)
            get_data = get_resp.get_json()
            self.assertEqual(get_data.get("mode"), "semi")
            self.assertEqual(get_data.get("risk_level"), 4)
            self.assertFalse(get_data.get("flash_enabled"))

            # 3. POST /api/espn/settings: Ungültiges risk_level (> 5) muss HTTP 400 liefern
            resp_too_high = client.post("/api/espn/settings", json={"risk_level": 6})
            self.assertEqual(resp_too_high.status_code, 400)
            self.assertEqual(resp_too_high.get_json().get("status"), "error")

            # 4. POST /api/espn/settings: Ungültiges risk_level (< 1) muss HTTP 400 liefern
            resp_too_low = client.post("/api/espn/settings", json={"risk_level": 0})
            self.assertEqual(resp_too_low.status_code, 400)
            self.assertEqual(resp_too_low.get_json().get("status"), "error")

            # 5. POST /api/espn/settings: Nicht-numerischer String als risk_level muss HTTP 400 liefern
            resp_str_inv = client.post("/api/espn/settings", json={"risk_level": "maximum_risk"})
            self.assertEqual(resp_str_inv.status_code, 400)
            self.assertEqual(resp_str_inv.get_json().get("status"), "error")

        finally:
            # Zustand sauber wiederherstellen
            client.post("/api/espn/settings", json={
                "mode": orig_data.get("mode", "manual"),
                "risk_level": orig_data.get("risk_level", 3),
                "flash_enabled": orig_data.get("flash_enabled", True)
            })

    def test_flask_ai_stats_endpoint(self):
        """Prüft den Flask-Endpunkt /api/espn/ai-stats (GET)."""
        from app import app
        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/api/espn/ai-stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("model", data)
        self.assertIn("last_token_usage", data)
        usage = data["last_token_usage"]
        self.assertIn("prompt_tokens", usage)
        self.assertIn("completion_tokens", usage)
        self.assertIn("total_tokens", usage)
        self.assertIsInstance(usage["total_tokens"], int)


if __name__ == "__main__":
    unittest.main()
