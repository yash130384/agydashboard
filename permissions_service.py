#!/usr/bin/env python3
"""
Permissions & Command Code Service for LCARS System Dashboard.
Manages access control, command code verification, and locked sections.
Initial code: 0901.
"""

import json
import os
import sys
import threading

DEFAULT_COMMAND_CODE = "0901"
DEFAULT_LOCKED_SECTIONS = ["cycle", "pulsecast", "gemini_live", "devteam"]
VALID_SECTIONS = [
    "system",
    "services",
    "agents",
    "ai-info",
    "config",
    "fantasy",
    "solar",
    "homeassistant",
    "cycle",
    "pulsecast",
    "gemini_live",
    "devteam",
]



class PermissionsService:
    def __init__(self, config_path=None):
        if not config_path:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        self.config_path = config_path
        self.lock = threading.RLock()
        self.command_code = DEFAULT_COMMAND_CODE
        self.locked_sections = list(DEFAULT_LOCKED_SECTIONS)
        self._load_config()

    def _load_config(self):
        with self.lock:
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "permissions" in data:
                            p_data = data["permissions"]
                            if isinstance(p_data, dict):
                                self.command_code = str(p_data.get("command_code", DEFAULT_COMMAND_CODE)).strip()
                                locked = p_data.get("locked_sections")
                                if isinstance(locked, list):
                                    self.locked_sections = [str(s).strip() for s in locked if str(s).strip() in VALID_SECTIONS]
                except Exception as e:
                    print(f"[WARN] PermissionsService: Fehler beim Laden von config.json: {e}", file=sys.stderr)

    def _save_to_disk(self):
        try:
            disk_data = {}
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        disk_data = json.load(f)
                        if not isinstance(disk_data, dict):
                            disk_data = {}
                except Exception:
                    disk_data = {}

            disk_data["permissions"] = {
                "command_code": self.command_code,
                "locked_sections": self.locked_sections,
            }

            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(disk_data, f, indent=2)
            return True
        except Exception as e:
            print(f"[WARN] PermissionsService: Fehler beim Speichern von config.json: {e}", file=sys.stderr)
            return False

    def get_public_status(self):
        with self.lock:
            return {
                "locked_sections": list(self.locked_sections),
                "has_code": bool(self.command_code),
            }

    def verify_code(self, code):
        if not code:
            return False
        with self.lock:
            return str(code).strip() == self.command_code

    def update_permissions(self, current_code, new_code=None, locked_sections=None):
        with self.lock:
            if not self.verify_code(current_code):
                return {"success": False, "error": "Ungültiger Command Code (Autorisierung fehlgeschlagen)"}

            if new_code is not None:
                new_str = str(new_code).strip()
                if len(new_str) >= 3:
                    self.command_code = new_str
                else:
                    return {"success": False, "error": "Neuer Command Code muss mindestens 3 Zeichen lang sein"}

            if locked_sections is not None:
                if isinstance(locked_sections, list):
                    self.locked_sections = [str(s).strip() for s in locked_sections if str(s).strip() in VALID_SECTIONS]

            ok = self._save_to_disk()
            if ok:
                return {
                    "success": True,
                    "locked_sections": list(self.locked_sections),
                    "message": "Rechtekonfiguration erfolgreich gespeichert.",
                }
            return {"success": False, "error": "Fehler beim Schreiben der Konfiguration auf Datenträger"}


permissions_service = PermissionsService()
