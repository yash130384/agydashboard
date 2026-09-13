#!/usr/bin/env python3
"""
Home Assistant Integration Service for LCARS System Dashboard.
Verwaltet Verbindung, Authentifizierung (Token & Benutzer/Passwort Login-Flow),
Raum- und Geräte-Abfragen sowie Service-Steuerung über die Home Assistant REST API.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

DEFAULT_HA_CONFIG = {
    "enabled": True,
    "url": "http://127.0.0.1:8123",
    "token": "",
    "username": "",
    "password": "",
    "refresh_token": "",
    "token_expires_at": 0,
    "name": "Assistant"
}

# Raum-Icons Zuordnung
AREA_ICONS = {
    "wohnzimmer": "🛋️",
    "living_room": "🛋️",
    "kuche": "🍳",
    "kueche": "🍳",
    "kitchen": "🍳",
    "schlafzimmer": "🛏️",
    "bedroom": "🛏️",
    "bad": "🚿",
    "badezimmer": "🚿",
    "bathroom": "🚿",
    "buro": "💻",
    "bureau": "💻",
    "office": "💻",
    "arbeitszimmer": "💻",
    "flur": "🚪",
    "hallway": "🚪",
    "korridor": "🚪",
    "garten": "🌳",
    "garden": "🌳",
    "balkon": "🪴",
    "balcony": "🪴",
    "terrasse": "☀️",
    "keller": "📦",
    "basement": "📦",
    "garage": "🚗"
}

# Domain Icons
DOMAIN_ICONS = {
    "light": "💡",
    "switch": "🔌",
    "climate": "🌡️",
    "cover": "🪟",
    "media_player": "🎵",
    "vacuum": "🤖",
    "fan": "💨",
    "lock": "🔒",
    "button": "🔘",
    "scene": "🎬",
    "script": "📜",
    "number": "🔢",
    "select": "📋",
    "sensor": "📊",
    "binary_sensor": "👁️",
    "input_boolean": "🔘",
    "input_number": "🔢",
    "input_select": "📋",
    "input_button": "🔘",
    "remote": "📱",
    "person": "👤",
    "sun": "☀️",
    "weather": "🌤️"
}

CONTROLLABLE_DOMAINS = {
    "light", "switch", "climate", "cover", "media_player", "vacuum",
    "fan", "lock", "button", "scene", "script", "number", "select",
    "input_boolean", "input_number", "input_select", "input_button", "remote"
}

class HomeAssistantService:
    def __init__(self, config_path=None):
        self.config_path = config_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        self.config = self._load_config()

    def _load_config(self):
        cfg = dict(DEFAULT_HA_CONFIG)
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "home_assistant" in data:
                        ha_data = data["home_assistant"]
                        if isinstance(ha_data, dict):
                            cfg.update(ha_data)
            except Exception as e:
                print(f"[WARN] HomeAssistantService: Fehler beim Laden von config.json: {e}", file=sys.stderr)
        return cfg

    def _save_to_file(self, ha_cfg):
        current_data = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    current_data = json.load(f)
                    if not isinstance(current_data, dict):
                        current_data = {}
            except Exception as e:
                print(f"[WARN] HomeAssistantService: Konnte bestehende config.json nicht lesen: {e}", file=sys.stderr)

        current_data["home_assistant"] = ha_cfg
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(current_data, f, indent=2)
            self.config = ha_cfg
            return True
        except Exception as e:
            print(f"[ERROR] HomeAssistantService: Fehler beim Schreiben von config.json: {e}", file=sys.stderr)
            return False

    def get_config(self, safe=True):
        self.config = self._load_config()
        cfg = dict(self.config)
        token = cfg.get("token", "")
        cfg["has_token"] = bool(token and len(token.strip()) > 10)
        has_creds = bool(cfg.get("username") and cfg.get("password"))
        cfg["has_creds"] = has_creds
        cfg["configured"] = bool(cfg.get("enabled") and cfg.get("url") and (cfg["has_token"] or has_creds))
        if safe:
            if token:
                if len(token) > 16:
                    cfg["token_masked"] = token[:8] + "..." + token[-6:]
                else:
                    cfg["token_masked"] = "********"
            else:
                cfg["token_masked"] = ""
            # Don't expose plain password to client
            if cfg.get("password"):
                cfg["password"] = "********"
        return cfg

    def login_with_credentials(self, url, username, password):
        base_url = url.rstrip("/")
        client_id = "http://127.0.0.1:5000/"

        try:
            # 1. Flow init
            req = urllib.request.Request(
                f"{base_url}/auth/login_flow",
                data=json.dumps({
                    "client_id": client_id,
                    "handler": ["homeassistant", None],
                    "redirect_uri": client_id
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                flow_id = data.get("flow_id")
                if not flow_id:
                    return {"success": False, "error": "Login-Flow konnte nicht gestartet werden."}

            # 2. Submit credentials
            req2 = urllib.request.Request(
                f"{base_url}/auth/login_flow/{flow_id}",
                data=json.dumps({
                    "client_id": client_id,
                    "username": username,
                    "password": password
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req2, timeout=8) as resp2:
                data2 = json.loads(resp2.read().decode("utf-8"))
                code = data2.get("result")
                if not code:
                    errors = data2.get("errors", {})
                    err_msg = errors.get("base") or "Ungültiger Benutzername oder Passwort."
                    return {"success": False, "error": f"Login fehlgeschlagen: {err_msg}"}

            # 3. Request token
            token_post_data = urllib.parse.urlencode({
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id
            }).encode("utf-8")
            req3 = urllib.request.Request(
                f"{base_url}/auth/token",
                data=token_post_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST"
            )
            with urllib.request.urlopen(req3, timeout=8) as resp3:
                tok_data = json.loads(resp3.read().decode("utf-8"))
                access_token = tok_data.get("access_token")
                refresh_token = tok_data.get("refresh_token", "")
                expires_in = tok_data.get("expires_in", 1800)
                if not access_token:
                    return {"success": False, "error": "Kein Access-Token erhalten."}

                return {
                    "success": True,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_expires_at": time.time() + expires_in - 60
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            return {"success": False, "error": f"HTTP {e.code}: {err_body}"}
        except Exception as e:
            return {"success": False, "error": f"Fehler beim Verbinden: {str(e)}"}

    def refresh_access_token(self):
        url = self.config.get("url", "").rstrip("/")
        refresh_token = self.config.get("refresh_token", "")
        if not url or not refresh_token:
            return False

        try:
            token_post_data = urllib.parse.urlencode({
                "grant_type": "refresh_token",
                "client_id": "http://127.0.0.1:5000/",
                "refresh_token": refresh_token
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{url}/auth/token",
                data=token_post_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                tok_data = json.loads(resp.read().decode("utf-8"))
                new_access = tok_data.get("access_token")
                expires_in = tok_data.get("expires_in", 1800)
                if new_access:
                    self.config["token"] = new_access
                    self.config["token_expires_at"] = time.time() + expires_in - 60
                    self._save_to_file(self.config)
                    return True
        except Exception as e:
            print(f"[WARN] HomeAssistantService: Token Refresh fehlgeschlagen: {e}", file=sys.stderr)
        return False

    def save_config(self, updates):
        current_cfg = self._load_config()

        if "enabled" in updates:
            current_cfg["enabled"] = bool(updates["enabled"])
        if "url" in updates and updates["url"]:
            current_cfg["url"] = str(updates["url"]).strip().rstrip("/")
        if "name" in updates and updates["name"]:
            current_cfg["name"] = str(updates["name"]).strip()

        username = updates.get("username", "").strip()
        password = updates.get("password", "").strip()
        token = updates.get("token", "").strip()

        # If user/password changed or provided, try login flow
        if username and password and password != "********":
            current_cfg["username"] = username
            current_cfg["password"] = password
            login_res = self.login_with_credentials(current_cfg["url"], username, password)
            if login_res.get("success"):
                current_cfg["token"] = login_res["access_token"]
                current_cfg["refresh_token"] = login_res["refresh_token"]
                current_cfg["token_expires_at"] = login_res["token_expires_at"]
                print("[INFO] HomeAssistantService: Login via Benutzer/Passwort erfolgreich.")
            else:
                print(f"[WARN] HomeAssistantService: Login via Benutzer/Passwort fehlgeschlagen: {login_res.get('error')}")

        if token and not token.startswith("...") and "..." not in token and token != "********":
            current_cfg["token"] = token
            # Direct token takes precedence
            current_cfg["token_expires_at"] = 0

        success = self._save_to_file(current_cfg)
        if not success:
            return {"success": False, "error": "Konnte Konfiguration nicht speichern."}

        return {"success": True, "config": self.get_config()}

    def _make_request(self, endpoint, method="GET", data=None, url_override=None, token_override=None):
        base_url = (url_override or self.config.get("url", "http://127.0.0.1:8123")).rstrip("/")

        # Check if token needs refresh
        if token_override is None:
            expires_at = self.config.get("token_expires_at", 0)
            if expires_at and time.time() >= expires_at:
                self.refresh_access_token()

        token = token_override if token_override is not None else self.config.get("token", "")

        url = f"{base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_data = resp.read().decode("utf-8")
                try:
                    return json.loads(resp_data), resp.status
                except json.JSONDecodeError:
                    return resp_data, resp.status
        except urllib.error.HTTPError as e:
            # If 401 and we have a refresh_token, try refreshing once
            if e.code == 401 and token_override is None and self.config.get("refresh_token"):
                if self.refresh_access_token():
                    return self._make_request(endpoint, method=method, data=data)
            err_body = e.read().decode("utf-8", errors="ignore")
            return {"error": f"HTTP {e.code}: {e.reason}", "details": err_body}, e.code
        except urllib.error.URLError as e:
            return {"error": f"Verbindungsfehler: {e.reason}"}, 503
        except Exception as e:
            return {"error": f"Unerwarteter Fehler: {str(e)}"}, 500

    def test_connection(self, url=None, token=None, username=None, password=None):
        if not url:
            url = self.config.get("url", "")
        if not url:
            return {"success": False, "error": "Keine Home Assistant URL angegeben."}

        # If username and password provided and not masked, try login flow
        if username and password and password != "********":
            login_res = self.login_with_credentials(url, username, password)
            if not login_res.get("success"):
                return {"success": False, "error": f"Login fehlgeschlagen: {login_res.get('error')}"}
            token = login_res["access_token"]

        if token is None or token == "" or "..." in token:
            token = self.config.get("token", "")

        if not token:
            return {"success": False, "error": "Kein Zugangs-Token oder Login angegeben."}

        res, code = self._make_request("/api/", "GET", url_override=url, token_override=token)
        if code == 200 and isinstance(res, dict) and res.get("message") == "API running.":
            return {"success": True, "message": "Verbindung erfolgreich hergestellt (API running).", "code": code}
        elif code == 401:
            return {"success": False, "error": "Authentifizierungsfehler: Token ungültig oder abgelaufen (401 Unauthorized).", "code": code}
        elif isinstance(res, dict) and "error" in res:
            return {"success": False, "error": res["error"], "code": code}
        else:
            return {"success": False, "error": f"Unerwartete Antwort: Status {code}", "code": code}

    def get_rooms_and_entities(self):
        cfg = self.get_config()
        if not cfg.get("configured"):
            return {
                "success": False,
                "configured": False,
                "error": "Home Assistant ist noch nicht konfiguriert oder deaktiviert.",
                "areas": [],
                "unassigned": [],
                "stats": {"total_entities": 0, "areas_count": 0, "active_count": 0}
            }

        # 1. Fetch all entity states
        states_resp, code = self._make_request("/api/states")
        if code != 200 or not isinstance(states_resp, list):
            err_msg = states_resp.get("error", f"Fehler beim Abrufen der Zustände (Code {code})") if isinstance(states_resp, dict) else f"Fehler Code {code}"
            return {
                "success": False,
                "configured": True,
                "error": err_msg,
                "areas": [],
                "unassigned": [],
                "stats": {"total_entities": 0, "areas_count": 0, "active_count": 0}
            }

        states_map = {s["entity_id"]: s for s in states_resp if isinstance(s, dict) and "entity_id" in s}

        # 2. Fetch areas and their entity mappings via Jinja template
        tmpl = "{% set ns = namespace(res=[]) %}{% for a in areas() %}{% set ns.res = ns.res + [dict(id=a, name=area_name(a), entities=area_entities(a))] %}{% endfor %}{{ ns.res | tojson }}"
        areas_resp, tmpl_code = self._make_request("/api/template", method="POST", data={"template": tmpl})

        areas_list = []
        assigned_entity_ids = set()

        if tmpl_code == 200 and isinstance(areas_resp, list):
            for a in areas_resp:
                if not isinstance(a, dict):
                    continue
                area_id = a.get("id", "")
                area_name = a.get("name") or area_id.title()
                raw_eids = a.get("entities", [])
                area_entities = []

                for eid in raw_eids:
                    st = states_map.get(eid)
                    if st:
                        assigned_entity_ids.add(eid)
                        formatted = self._format_entity(st)
                        area_entities.append(formatted)

                # Sort entities: controllable first, then alphabetically
                area_entities.sort(key=lambda x: (0 if x["controllable"] else 1, x["domain"], x["friendly_name"].lower()))

                icon = AREA_ICONS.get(area_id.lower(), AREA_ICONS.get(area_name.lower(), "🚪"))
                active_entities = sum(1 for e in area_entities if e["state"] in ("on", "open", "playing", "cleaning"))

                areas_list.append({
                    "id": area_id,
                    "name": area_name,
                    "icon": icon,
                    "entities": area_entities,
                    "entity_count": len(area_entities),
                    "active_count": active_entities
                })

        # 3. Unassigned entities (filter out noisy internals unless useful)
        unassigned_entities = []
        for eid, st in states_map.items():
            if eid in assigned_entity_ids:
                continue
            domain = eid.split(".")[0]
            # Exclude internal noisy domains from unassigned list
            if domain in ("conversation", "zone", "event", "tts", "update", "image", "device_tracker"):
                continue
            formatted = self._format_entity(st)
            unassigned_entities.append(formatted)

        unassigned_entities.sort(key=lambda x: (0 if x["controllable"] else 1, x["domain"], x["friendly_name"].lower()))
        unassigned_active = sum(1 for e in unassigned_entities if e["state"] in ("on", "open", "playing", "cleaning"))

        # Global stats
        total_entities = len(states_map)
        total_active = sum(a["active_count"] for a in areas_list) + unassigned_active
        controllable_count = sum(1 for s in states_map.values() if s.get("entity_id", "").split(".")[0] in CONTROLLABLE_DOMAINS)

        return {
            "success": True,
            "configured": True,
            "name": cfg.get("name", "Assistant"),
            "url": cfg.get("url", ""),
            "areas": areas_list,
            "unassigned": unassigned_entities,
            "unassigned_active": unassigned_active,
            "stats": {
                "total_entities": total_entities,
                "areas_count": len(areas_list),
                "active_count": total_active,
                "controllable_count": controllable_count
            },
            "timestamp": time.strftime("%H:%M:%S")
        }

    def _format_entity(self, raw):
        eid = raw.get("entity_id", "")
        domain = eid.split(".")[0] if "." in eid else "unknown"
        attrs = raw.get("attributes", {})
        state = raw.get("state", "unknown")

        friendly_name = attrs.get("friendly_name") or eid
        icon = attrs.get("icon")
        if not icon or not icon.startswith("mdi:"):
            icon = DOMAIN_ICONS.get(domain, "⚙️")

        controllable = domain in CONTROLLABLE_DOMAINS

        ctrl_details = {
            "domain": domain,
            "can_toggle": domain in ("light", "switch", "input_boolean", "fan"),
            "has_brightness": domain == "light" and ("brightness" in attrs or "supported_color_modes" in attrs),
            "brightness": attrs.get("brightness"),
            "brightness_pct": round(attrs.get("brightness", 0) / 255 * 100) if attrs.get("brightness") is not None else None,
            "color_temp": attrs.get("color_temp_kelvin") or attrs.get("color_temp"),
            "min_color_temp": attrs.get("min_color_temp_kelvin", 2200),
            "max_color_temp": attrs.get("max_color_temp_kelvin", 6500),
            "rgb_color": attrs.get("rgb_color"),
            "temperature": attrs.get("temperature"),
            "current_temperature": attrs.get("current_temperature"),
            "hvac_modes": attrs.get("hvac_modes", []),
            "current_position": attrs.get("current_position"),
            "volume_level": attrs.get("volume_level"),
            "volume_pct": round(attrs.get("volume_level", 0) * 100) if attrs.get("volume_level") is not None else None,
            "is_volume_muted": attrs.get("is_volume_muted"),
            "media_title": attrs.get("media_title"),
            "media_artist": attrs.get("media_artist"),
            "battery_level": attrs.get("battery_level") or attrs.get("battery"),
            "unit": attrs.get("unit_of_measurement", ""),
            "device_class": attrs.get("device_class", ""),
            "min_val": attrs.get("min"),
            "max_val": attrs.get("max"),
            "step_val": attrs.get("step", 1),
            "options": attrs.get("options", [])
        }

        return {
            "entity_id": eid,
            "domain": domain,
            "state": state,
            "friendly_name": friendly_name,
            "icon": icon,
            "controllable": controllable,
            "controls": ctrl_details,
            "last_changed": raw.get("last_changed", "")
        }

    def call_service(self, domain, service, service_data):
        cfg = self.get_config()
        if not cfg.get("configured"):
            return {"success": False, "error": "Home Assistant ist nicht konfiguriert."}

        endpoint = f"/api/services/{domain}/{service}"
        res, code = self._make_request(endpoint, method="POST", data=service_data)
        if code in (200, 201):
            return {"success": True, "result": res, "code": code}
        else:
            err = res.get("error", str(res)) if isinstance(res, dict) else str(res)
            return {"success": False, "error": err, "code": code}

# Singleton Instance
ha_service = HomeAssistantService()

if __name__ == "__main__":
    print("Testing HomeAssistantService with user/password...")
    t = ha_service.test_connection(url="http://127.0.0.1:8123", username="cb", password="mymaajen")
    print("Test result:", t)
