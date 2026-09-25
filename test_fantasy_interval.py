#!/usr/bin/env python3
"""
Test suite for Fantasy Bot adjustable check intervals and kickoff slot triggers.
Tests:
- Interval hours setting (4, 8, 12, 24) and validation
- Rejection of invalid intervals
- Config persistence (ai_check_interval_hours, ai_check_interval)
- Kickoff slot calculation (T-20m)
- get_bot_status returns correct interval_hours, interval_seconds, and slot_checks_enabled
- REST API endpoint verification
- HTML presence of interval buttons (4H, 8H, 12H, 24H) and slot checks indicator
"""

import os
import json
import time
import tempfile
import shutil
import urllib.request
from espn_service import EspnFantasyClient

def test_intervals_and_kickoff():
    test_dir = tempfile.mkdtemp()
    try:
        config_path = os.path.join(test_dir, "config.json")
        init_cfg = {
            "espn_fantasy": {
                "enabled": True,
                "league_id": 378649793,
                "team_id": 17,
                "season_year": 2026,
                "swid": "{TEST}",
                "espn_s2": "TEST",
                "mode": "full",
                "poll_interval": 35,
                "ai_check_interval_hours": 4,
                "ai_check_interval": 14400
            }
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(init_cfg, f, indent=2)

        client = EspnFantasyClient(config_path=config_path)

        # 1. Default interval is 4 hours
        assert client.get_interval_hours() == 4, f"Expected 4, got {client.get_interval_hours()}"
        assert client.get_ai_interval() == 14400, f"Expected 14400, got {client.get_ai_interval()}"

        # 2. Test valid intervals: 8, 12, 24, 4
        for hours in [8, 12, 24, 4]:
            res = client.set_interval_hours(hours)
            assert res["success"] is True
            assert res["interval_hours"] == hours
            assert res["interval_seconds"] == hours * 3600
            assert client.get_interval_hours() == hours
            assert client.get_ai_interval() == hours * 3600

            # Verify persisted in config.json
            with open(config_path, "r", encoding="utf-8") as f:
                saved_cfg = json.load(f)["espn_fantasy"]
                assert saved_cfg["ai_check_interval_hours"] == hours
                assert saved_cfg["ai_check_interval"] == hours * 3600

        # 3. Test invalid interval rejection
        for invalid_val in [0, 1, 2, 5, 10, 48, -4, "abc"]:
            try:
                client.set_interval_hours(invalid_val)
                assert False, f"Expected ValueError for invalid interval {invalid_val}"
            except ValueError:
                pass

        # 4. Test bot_status reporting
        client._poller_running = True
        status = client.get_bot_status()
        assert status["interval_hours"] == 4
        assert status["interval_seconds"] == 14400
        assert status["slot_checks_enabled"] is True

        # 5. Test kickoff slots extraction & T-20min logic
        slots = client.get_upcoming_game_slots()
        assert isinstance(slots, list)
        assert len(slots) > 0, "Expected upcoming game slots"

        # Check that upcoming slots are in the future or within past 20 min
        now = time.time()
        for s in slots:
            assert s > (now - 1200)

        # Mock upcoming slot 15 minutes in future (T-20 is already active)
        client.get_upcoming_game_slots = lambda *args, **kwargs: [now + 900]
        status_imminent = client.get_bot_status()
        assert status_imminent["next_run_type"] == "kickoff_slot"
        assert status_imminent["next_run_in_seconds"] == 0
        assert "KI-CHECK LÄUFT" in status_imminent["next_run_text"]

        # Mock upcoming slot 35 minutes in future (T-20 check in 15 min)
        client._last_ai_check_ts = now - 3600
        client.get_upcoming_game_slots = lambda *args, **kwargs: [now + 2100]
        status_future = client.get_bot_status()
        assert status_future["next_run_type"] == "kickoff_slot"
        assert 890 <= status_future["next_run_in_seconds"] <= 910
        assert "Kickoff" in status_future["next_run_text"]

        print("[OK] All EspnFantasyClient interval and slot tests passed.")
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

def test_live_dashboard():
    # Test live endpoints on running agydashboard (port 5000)
    req = urllib.request.Request("http://127.0.0.1:5000/api/espn/settings")
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert data["interval_hours"] in (4, 8, 12, 24)
        assert data["slot_checks_enabled"] is True

    # Test POST /api/espn/settings
    req = urllib.request.Request(
        "http://127.0.0.1:5000/api/espn/settings",
        data=json.dumps({"interval_hours": 8}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert data["interval_hours"] == 8

    # Reset back to 4 hours
    req = urllib.request.Request(
        "http://127.0.0.1:5000/api/espn/settings",
        data=json.dumps({"interval_hours": 4}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert data["interval_hours"] == 4

    # Test GET /api/fantasy
    req = urllib.request.Request("http://127.0.0.1:5000/api/fantasy")
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert data["interval_hours"] == 4
        assert data["bot_status"]["slot_checks_enabled"] is True

    # Test HTML UI presence
    req = urllib.request.Request("http://127.0.0.1:5000/")
    with urllib.request.urlopen(req, timeout=5) as resp:
        html = resp.read().decode("utf-8")
        assert "btn-fantasy-interval-4" in html
        assert "btn-fantasy-interval-8" in html
        assert "btn-fantasy-interval-12" in html
        assert "btn-fantasy-interval-24" in html
        assert "fantasyBotSlotChecks" in html
        assert "fantasyAiIntervalStatus" in html

    print("[OK] All live API & UI verification tests passed.")

if __name__ == "__main__":
    test_intervals_and_kickoff()
    test_live_dashboard()
