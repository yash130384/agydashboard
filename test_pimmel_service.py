#!/usr/bin/env python3
"""
Test- und Verifikationsskript für pimmel_service.py
Prüft:
- SSH-Zugang zu PiMMEL
- One-Shot Telemetrie-Abruf
- SQLite Rsync & Parsing
- Methoden get_status(), get_history(), get_9router_data(), get_logs()
- JSON-Serialisierbarkeit aller Rückgabewerte
"""

import json
import sys
import time
from pimmel_service import pimmel_service, parse_9router_sqlite


def run_tests():
    print("=== TEST 1: Initialer Zustand & Konfiguration ===")
    print(f"Host: {pimmel_service.ssh_host} ({pimmel_service.ip})")
    print(f"Cache DB Path: {pimmel_service.cache_db_path}")

    print("\n=== TEST 2: Manueller Telemetrie-Poll über SSH ===")
    pimmel_service._poll_host_telemetry()
    status = pimmel_service.get_status()
    print("Status Result:")
    print(json.dumps(status, indent=2))
    assert status["node"]["hostname"] == "PiMMEL", "Hostname muss PiMMEL sein"
    assert status["node"]["online"] is True, "Node muss online sein"
    assert "cpu" in status["host_metrics"], "host_metrics muss cpu enthalten"
    assert "ram" in status["host_metrics"], "host_metrics muss ram enthalten"
    assert "disk" in status["host_metrics"], "host_metrics muss disk enthalten"
    assert "temp" in status["host_metrics"], "host_metrics muss temp enthalten"
    assert "services" in status["host_metrics"], "host_metrics muss services enthalten"
    print("✓ Test 2 erfolgreich!")

    print("\n=== TEST 3: Manueller SQLite Rsync & Parsing ===")
    pimmel_service._sync_sqlite_db()
    data_9r = pimmel_service.get_9router_data()
    print("9Router Data Status:", data_9r.get("status"))
    print("Totals:", json.dumps(data_9r.get("totals"), indent=2))
    assert data_9r.get("status") in ("online", "ok"), "9Router Status muss online/ok sein"
    assert data_9r.get("totals", {}).get("requests", 0) > 0, "Requests müssen > 0 sein"
    assert len(data_9r.get("recent_history", [])) > 0, "recent_history muss Einträge haben"
    print("✓ Test 3 erfolgreich!")

    print("\n=== TEST 4: Sensor-Historie (Ringbuffer) ===")
    hist = pimmel_service.get_history(range_param="1h")
    print(f"History Range: {hist['range']}, Samples count: {hist['count']}")
    assert "samples" in hist, "history muss samples enthalten"
    print("✓ Test 4 erfolgreich!")

    print("\n=== TEST 5: PM2 Log-Abruf ===")
    logs_res = pimmel_service.get_logs(lines=20)
    print(f"Log Zeilen erhalten: {logs_res['count']}")
    print("Log Preview:\n" + "\n".join(logs_res["logs"].strip().split("\n")[-5:]))
    assert logs_res["count"] > 0, "Logs müssen Einträge enthalten"
    print("✓ Test 5 erfolgreich!")

    print("\n=== TEST 6: JSON Serialisierungsprüfung ===")
    for name, obj in [
        ("get_status", pimmel_service.get_status()),
        ("get_history", pimmel_service.get_history()),
        ("get_9router_data", pimmel_service.get_9router_data()),
        ("get_logs", pimmel_service.get_logs(10))
    ]:
        serialized = json.dumps(obj)
        assert len(serialized) > 0
        print(f"✓ {name} ist valide JSON-serialisierbar ({len(serialized)} Bytes)")

    print("\n==========================================")
    print("ALLE TESTS FÜR pimmel_service.py BESTANDEN!")
    print("==========================================")


if __name__ == "__main__":
    run_tests()
