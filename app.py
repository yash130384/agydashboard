#!/usr/bin/env python3
"""
LCARS System Dashboard & Webserver Discovery Engine (Star Trek LCARS Interface)
Lauscht auf Port 5000 (bind 0.0.0.0) und bietet:
- Star Trek LCARS Fullscreen Benutzeroberfläche (Vorlage: https://www.thelcars.com/)
- 5 Hauptkategorien ohne Nummern: SYSTEM, SERVICES, KI-AGENTEN, KI-INFO, CONFIG
- System & 24h Sensor-Verlauf in einer gemeinsamen Kategorie (SYSTEM)
- Web-Services & 5-Minuten Webserver-Scanner in einer gemeinsamen Kategorie (SERVICES)
- 6 umschaltbare LCARS Farbmodi (Classic, Nemesis Blue, Lower Decks, Lower Decks PADD, Picard, Voyager)
- Automatischer Red Alert Alarm bei Schwellwert-Überschreitung (CPU, RAM, Disk, Temp > 1 Min) oder Ausfall des 9Router Gateways
- Keine horizontalen Scrollbalken (alle Inhalte responsive und bildschirmgerecht aufbereitet)
- Zuverlässig initialisierte Chart.js Diagramme für Systemverlauf und KI-Modell-Verbrauch
- 5-Minuten Hintergrund-Scanner für alle laufenden Prozesse mit LAN-, Tailscale- und Cloudflared-Adressen
- Echtes Stardate und Live-Vitals im oberen LCARS-Terminal-Rahmen
"""

import atexit
import datetime
import glob
import gzip
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque

try:
    import yaml
except ImportError:
    yaml = None

try:
    import requests
except ImportError:
    requests = None


# Automatische Installation von psutil falls nicht vorhanden
try:
    import psutil
except ImportError:
    print("[INFO] psutil nicht gefunden. Installiere psutil via pip...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil"])
        import psutil
        print("[OK] psutil erfolgreich installiert.")
    except Exception as e:
        print(f"[WARN] Installation von psutil fehlgeschlagen: {e}. Verwende Fallback-Metriken.")
        psutil = None

# Baseline CPU Initialisierung
if psutil:
    try:
        psutil.cpu_percent(interval=None)
    except Exception:
        pass

# Flask Import mit Fallback zu http.server
try:
    from flask import Flask, jsonify, render_template_string, request, send_from_directory, Response, stream_with_context, redirect, make_response
    USE_FLASK = True
except ImportError:
    print("[INFO] Flask nicht installiert, verwende Python Standardbibliothek (http.server).")
    USE_FLASK = False
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ESPN Fantasy Service Import
try:
    from espn_service import espn_client
except Exception as _espn_err:
    espn_client = None
    print(f"[WARN] espn_service konnte nicht importiert werden: {_espn_err}", file=sys.stderr)

# Home Assistant Service Import
try:
    from ha_service import ha_service
except Exception as _ha_err:
    ha_service = None
    print(f"[WARN] ha_service konnte nicht importiert werden: {_ha_err}", file=sys.stderr)

# Permissions Service Import
try:
    from permissions_service import permissions_service
except Exception as _perm_err:
    permissions_service = None
    print(f"[WARN] permissions_service konnte nicht importiert werden: {_perm_err}", file=sys.stderr)

# Cycle Tracking Service Import
try:
    from cycle_service import cycle_service
except Exception as _cycle_err:
    cycle_service = None
    print(f"[WARN] cycle_service konnte nicht importiert werden: {_cycle_err}", file=sys.stderr)

# LCARS Central User & Session Service Import
try:
    from user_service import user_service
    if user_service and not user_service.get_user_by_username("admin"):
        user_service.create_user("admin", "09010901", display_name="Master Administrator", allowed_services=["*"], notes="Master Admin")
except Exception as _user_err:
    user_service = None
    print(f"[WARN] user_service konnte nicht importiert werden: {_user_err}", file=sys.stderr)

# Pimmel Remote Node Service Import
try:
    from pimmel_service import pimmel_service
except Exception as _pimmel_err:
    pimmel_service = None
    print(f"[WARN] pimmel_service konnte nicht importiert werden: {_pimmel_err}", file=sys.stderr)

# LCARS Login Interface HTML Import
try:
    from login_page import LOGIN_HTML
except Exception as _login_err:
    LOGIN_HTML = None
    print(f"[WARN] login_page konnte nicht importiert werden: {_login_err}", file=sys.stderr)

# LCARS Auth Reverse Proxy Import
try:
    from auth_proxy import auth_proxy
except Exception as _proxy_err:
    auth_proxy = None
    print(f"[WARN] auth_proxy konnte nicht importiert werden: {_proxy_err}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Basis Service-Registry für Web-Services
# ---------------------------------------------------------------------------
SERVICE_REGISTRY = {
    "dashboard": {
        "name": "agydashboard",
        "title": "System Dashboard",
        "icon": "📟",
        "port": 5000,
        "description": "Flask System Dashboard (Eigenes)",
        "allow_external": True,
        "cf_key": "dashboard",
        "cf_url": "https://dash.pimmel.site",
        "tailscale_url": "http://pimmel.tail3a782b.ts.net:5000",
        "lan_url": "http://192.168.31.210:5000",
    },
    "telemetry": {
        "name": "TelemetryVault",
        "title": "TelemetryVault",
        "icon": "🏎️",
        "port": 8000,
        "description": "ACC Telemetry (uvicorn)",
        "allow_external": True,
        "cf_key": None,
        "cf_url": "https://tele.pimmel.site",
        "tailscale_url": "http://pimmel.tail3a782b.ts.net:8000",
        "lan_url": "http://192.168.31.210:8000",
    },
    "xdcc": {
        "name": "xdcc-load-cast",
        "title": "xdcc-load-cast",
        "icon": "🚀",
        "port": 3000,
        "description": "node server.js",
        "allow_external": True,
        "cf_key": "xdcc",
        "cf_url": "https://cast.pimmel.site",
        "tailscale_url": "http://pimmel.tail3a782b.ts.net:3000",
        "lan_url": "http://192.168.31.210:3000",
    },
    "9router": {
        "name": "9Router",
        "title": "9Router AI Router",
        "icon": "⚙️",
        "port": 20128,
        "description": "FREE AI Router & Token Saver",
        "allow_external": True,
        "cf_key": None,
        "cf_url": "https://ai.pimmel.site",
        "tailscale_url": "http://pimmel.tail3a782b.ts.net:20128",
        "lan_url": "http://192.168.31.210:20128",
    },
    "headroom": {
        "name": "Headroom",
        "title": "Headroom AI Compression",
        "icon": "🧠",
        "port": 8787,
        "description": "Context Compression for AI Agents",
        "allow_external": True,
        "cf_key": None,
        "cf_url": "https://head.pimmel.site",
        "tailscale_url": "http://pimmel.tail3a782b.ts.net:8787",
        "lan_url": "http://192.168.31.210:8787",
    },
    "homeassistant": {
        "name": "Home Assistant",
        "title": "Home Assistant",
        "icon": "🏠",
        "port": 8123,
        "description": "Open Source Home Automation",
        "allow_external": True,
        "cf_key": None,
        "cf_url": "https://ha.pimmel.site",
        "tailscale_url": "http://pimmel.tail3a782b.ts.net:8123",
        "lan_url": "http://192.168.31.210:8123",
    },
    "matter": {
        "name": "Python Matter Server",
        "title": "Matter Server",
        "icon": "🔌",
        "port": 5580,
        "description": "Python Matter Server (WebSockets)",
        "allow_external": True,
        "cf_key": None,
        "cf_url": "https://mat.pimmel.site",
        "tailscale_url": "http://pimmel.tail3a782b.ts.net:5580",
        "lan_url": "http://192.168.31.210:5580",
    },
    "cups": {
        "name": "CUPS",
        "title": "CUPS Druckerdienst",
        "icon": "🖨️",
        "port": 631,
        "description": "Netzwerkdrucker Verwaltung",
        "allow_external": True,
        "cf_key": None,
        "cf_url": "https://port.pimmel.site",
        "tailscale_url": "http://pimmel.tail3a782b.ts.net:631",
        "lan_url": "http://192.168.31.210:631",
    },
    "postgres": {
        "name": "PostgreSQL 17",
        "title": "PostgreSQL 17",
        "icon": "🗄️",
        "port": 5432,
        "description": "PostgreSQL Datenbank (nur localhost)",
        "allow_external": False,
        "cf_key": None,
        "cf_url": None,
        "tailscale_url": None,
        "lan_url": None,
    },
}


# ---------------------------------------------------------------------------
# Formatierungs- & System-Hilfsfunktionen
# ---------------------------------------------------------------------------
def format_tokens(n):
    if not isinstance(n, (int, float)):
        return "0"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def format_usd(n):
    if not isinstance(n, (int, float)):
        return "$0.00"
    if abs(n) < 0.005 and n != 0:
        return f"${n:.4f}"
    return f"${n:.2f}"


def format_uptime(seconds):
    try:
        s = int(seconds)
        days = s // 86400
        hours = (s % 86400) // 3600
        minutes = (s % 3600) // 60
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0 or days > 0:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)
    except Exception:
        return "N/A"


def get_temperature():
    thermal_paths = [
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
    ]
    for path in thermal_paths:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    content = f.read().strip()
                    temp_c = float(content) / 1000.0
                    return round(temp_c, 1), f"{temp_c:.1f} °C"
        except Exception:
            pass

    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"], stderr=subprocess.DEVNULL, timeout=1).decode("utf-8")
        m = re.search(r"temp=([\d\.]+)", out)
        if m:
            temp_c = float(m.group(1))
            return round(temp_c, 1), f"{temp_c:.1f} °C"
    except Exception:
        pass

    if psutil and hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            for name, entries in temps.items():
                if entries:
                    temp_c = entries[0].current
                    return round(temp_c, 1), f"{temp_c:.1f} °C"
        except Exception:
            pass

    return None, "N/A"


def calculate_stardate(dt=None):
    """Berechnet das echte Star Trek Stardate gemäß offizieller LCARS-Formel (thelcars.com)."""
    if dt is None:
        dt = datetime.datetime.now()
    current_hour = dt.hour
    date_for_calc = dt
    if current_hour == 0:
        date_for_calc = dt + datetime.timedelta(days=1)
    first_number = date_for_calc.year - 1946
    start_of_year = datetime.datetime(date_for_calc.year, 1, 1)
    diff_days = (date_for_calc - start_of_year).days + 1
    calculated_second_number = int(diff_days * 2.732)
    second_number = f"{calculated_second_number:03d}"
    if current_hour == 0:
        final_number = "0"
    elif 1 <= current_hour <= 9:
        final_number = f"{current_hour:02d}"
    elif 10 <= current_hour <= 11:
        final_number = str(current_hour)
    elif 12 <= current_hour <= 21:
        h12 = current_hour % 12
        if h12 == 0:
            h12 = 12
        final_number = str(h12)
    else:  # 22 <= current_hour <= 23
        final_number = str(current_hour)
    return f"{first_number}{second_number}.{final_number}"


_throttled_cache = (False, "0x0")
_throttled_cache_ts = 0
_throttled_lock = threading.Lock()


def get_throttled_status(cache_ttl=15.0):
    global _throttled_cache, _throttled_cache_ts
    now = time.time()
    with _throttled_lock:
        if now - _throttled_cache_ts < cache_ttl:
            return _throttled_cache

    try:
        out = subprocess.check_output(["vcgencmd", "get_throttled"], stderr=subprocess.DEVNULL, timeout=1).decode("utf-8")
        m = re.search(r"throttled=(0x[0-9a-fA-F]+)", out)
        if m:
            val_hex = m.group(1)
            val = int(val_hex, 16)
            is_active = bool(val & 0x1)
            res = (is_active, val_hex)
            with _throttled_lock:
                _throttled_cache = res
                _throttled_cache_ts = now
            return res
    except Exception:
        pass
    return False, "0x0"


def get_ram_metrics():
    if psutil:
        try:
            vm = psutil.virtual_memory()
            return {
                "percent": round(vm.percent, 1),
                "used_gb": round((vm.total - vm.available) / (1024**3), 2),
                "total_gb": round(vm.total / (1024**3), 2),
                "available_gb": round(vm.available / (1024**3), 2),
            }
        except Exception:
            pass

    try:
        meminfo = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = parts[1].strip()
        total_kb = float(meminfo.get("MemTotal", "0 kB").split()[0])
        avail_kb = float(meminfo.get("MemAvailable", meminfo.get("MemFree", "0 kB")).split()[0])
        used_kb = max(0, total_kb - avail_kb)
        pct = round((used_kb / total_kb) * 100, 1) if total_kb else 0.0
        return {
            "percent": pct,
            "used_gb": round(used_kb / (1024**2), 2),
            "total_gb": round(total_kb / (1024**2), 2),
            "available_gb": round(avail_kb / (1024**2), 2),
        }
    except Exception:
        return {"percent": 0.0, "used_gb": 0.0, "total_gb": 0.0, "available_gb": 0.0}


def get_disk_metrics():
    if psutil:
        try:
            du = psutil.disk_usage("/")
            return {
                "percent": round(du.percent, 1),
                "used_gb": round(du.used / (1024**3), 2),
                "total_gb": round(du.total / (1024**3), 2),
                "free_gb": round(du.free / (1024**3), 2),
            }
        except Exception:
            pass

    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = max(0, total - free)
        percent = round((used / total) * 100, 1) if total else 0.0
        return {
            "percent": percent,
            "used_gb": round(used / (1024**3), 2),
            "total_gb": round(total / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
        }
    except Exception:
        return {"percent": 0.0, "used_gb": 0.0, "total_gb": 0.0, "free_gb": 0.0}


_lan_ip_cache = None
_lan_ip_cache_ts = 0
_lan_ip_lock = threading.Lock()


def get_lan_ip(cache_ttl=60.0):
    global _lan_ip_cache, _lan_ip_cache_ts
    now = time.time()
    with _lan_ip_lock:
        if _lan_ip_cache and (now - _lan_ip_cache_ts < cache_ttl):
            return _lan_ip_cache

    ip = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            with _lan_ip_lock:
                _lan_ip_cache = ip
                _lan_ip_cache_ts = now
            return ip
    except Exception:
        pass
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True, timeout=1).strip()
        for ip_part in out.split():
            if ip_part.startswith("192.168.") or ip_part.startswith("10.") or ip_part.startswith("172."):
                with _lan_ip_lock:
                    _lan_ip_cache = ip_part
                    _lan_ip_cache_ts = now
                return ip_part
    except Exception:
        pass
    fallback_ip = "192.168.31.210"
    with _lan_ip_lock:
        _lan_ip_cache = fallback_ip
        _lan_ip_cache_ts = now
    return fallback_ip



def get_tailscale_info():
    try:
        res = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            dns = data.get("Self", {}).get("DNSName", "").rstrip(".")
            ips = data.get("Self", {}).get("TailscaleIPs", [])
            ip = ips[0] if ips else None
            return {
                "domain": dns or (ip if ip else "localhost"),
                "ip": ip,
                "active": True,
            }
    except Exception:
        pass
    return {"domain": "pimmel.tail3a782b.ts.net", "ip": "100.88.215.98", "active": True}


_cloudflared_urls_cache = None
_cloudflared_urls_cache_ts = 0
_cloudflared_urls_lock = threading.Lock()


def get_cloudflared_urls(cache_ttl=5.0):
    global _cloudflared_urls_cache, _cloudflared_urls_cache_ts
    now = time.time()
    with _cloudflared_urls_lock:
        if _cloudflared_urls_cache is not None and (now - _cloudflared_urls_cache_ts < cache_ttl):
            return _cloudflared_urls_cache

    urls = {
        "dashboard": "https://dash.pimmel.site",
        "xdcc": "https://cast.pimmel.site",
        "telemetry": "https://tele.pimmel.site",
        "9router": "https://ai.pimmel.site",
        "headroom": "https://head.pimmel.site",
        "homeassistant": "https://ha.pimmel.site",
        "matter": "https://mat.pimmel.site",
    }
    if "cf_tunnel_manager" in globals() and cf_tunnel_manager:
        all_cf = cf_tunnel_manager.get_all_urls()
        for p, u in all_cf.items():
            urls[str(p)] = u

    with _cloudflared_urls_lock:
        _cloudflared_urls_cache = urls
        _cloudflared_urls_cache_ts = now
    return urls


_service_status_cache = {}
_service_status_lock = threading.Lock()


def check_service_status(port=8000, host="127.0.0.1", timeout=0.2, max_age=4.0):
    # Das Dashboard selbst läuft immer, wenn dieser Code ausgeführt wird
    if port == 5000:
        return True
    now = time.time()
    cache_key = (host, port)
    with _service_status_lock:
        cached = _service_status_cache.get(cache_key)
        if cached and (now - cached["timestamp"] < max_age):
            return cached["online"]

    online = False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            online = True
    except Exception:
        online = False

    with _service_status_lock:
        _service_status_cache[cache_key] = {"online": online, "timestamp": now}
    return online


# ---------------------------------------------------------------------------
# Cloudflare Named-Tunnel-Manager (pimmel.site)
# ---------------------------------------------------------------------------
STATIC_PORT_SUBDOMAINS = {
    5000: "dash",
    8123: "ha",
    20128: "ai",
    3000: "cast",
    8000: "tele",
    5580: "mat",
    8787: "head",
    631: "port",
}
DIRECT_AUTH_PORTS = {5000, 8123, 20128}


class CloudflaredNamedTunnelManager:
    """Verwaltet Cloudflare Named Tunnel (pimmel-tunnel) für entdeckte Webdienste.
    Konfiguriert Ingress-Regeln in /home/cb/.cloudflared/config.yml und registriert
    2-4 Buchstaben DNS-Subdomains auf *.pimmel.site.
    """

    def __init__(
        self,
        config_path="/home/cb/.cloudflared/config.yml",
        binary_path="/home/cb/bin/cloudflared",
        tunnel_name="pimmel-tunnel",
        base_domain="pimmel.site",
    ):
        self.config_path = config_path
        self.binary_path = (
            binary_path
            if (os.path.isfile(binary_path) and os.access(binary_path, os.X_OK))
            else (shutil.which("cloudflared") or "cloudflared")
        )
        self.tunnel_name = tunnel_name
        self.base_domain = base_domain
        self._lock = threading.RLock()
        self._restart_lock = threading.RLock()
        self._last_restart_ts = 0.0
        self._url_cache = {}  # port -> "https://<subdomain>.pimmel.site"
        self._load_config_cache()
        self.kill_legacy_quick_tunnels()

    def kill_legacy_quick_tunnels(self):
        """Beendet alte trycloudflare Quick-Tunnel-Prozesse."""
        if not psutil:
            return
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    cmd = proc.info.get("cmdline") or []
                    cmd_str = " ".join(cmd)
                    if "cloudflared" in cmd_str and ("--url" in cmd_str or "trycloudflare.com" in cmd_str):
                        print(f"[CLOUDFLARED] Beende alten Quick-Tunnel PID {proc.pid}: {cmd_str[:60]}...", flush=True)
                        proc.terminate()
                        try:
                            proc.wait(timeout=1.0)
                        except Exception:
                            proc.kill()
                except Exception:
                    pass
        except Exception as e:
            print(f"[WARN] Fehler beim Beenden alter Quick-Tunnel: {e}", file=sys.stderr)

    def _load_config_cache(self):
        """Liest bestehende Mappings aus config.yml in den internen Cache."""
        for p, sub in STATIC_PORT_SUBDOMAINS.items():
            self._url_cache[p] = f"https://{sub}.{self.base_domain}"

        if not os.path.exists(self.config_path) or not yaml:
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            ingress = data.get("ingress", [])
            for rule in ingress:
                hostname = rule.get("hostname", "")
                service = rule.get("service", "")
                if hostname and service:
                    m = re.search(r":(\d+)$", service)
                    if m:
                        port = int(m.group(1))
                        if port == 5050:
                            sub = hostname.split(".")[0]
                            for sp, sname in STATIC_PORT_SUBDOMAINS.items():
                                if sname == sub:
                                    self._url_cache[sp] = f"https://{hostname}"
                                    break
                            if auth_proxy and sub in auth_proxy.subdomain_map:
                                p_auth = auth_proxy.subdomain_map[sub]["port"]
                                self._url_cache[p_auth] = f"https://{hostname}"
                        else:
                            if port not in self._url_cache or hostname.startswith("dash."):
                                self._url_cache[port] = f"https://{hostname}"
        except Exception as e:
            print(f"[WARN] Fehler beim Laden von {self.config_path}: {e}", file=sys.stderr)

    def get_existing_hostnames(self) -> set:
        """Gibt alle in config.yml konfigurierten Hostnames zurück."""
        hostnames = set()
        if not os.path.exists(self.config_path) or not yaml:
            return hostnames
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for rule in data.get("ingress", []):
                h = rule.get("hostname")
                if h:
                    hostnames.add(h.lower().strip())
        except Exception:
            pass
        return hostnames

    def generate_subdomain(self, port: int, process_name: str = "", title: str = "") -> str:
        """Erzeugt ein eindeutiges 2-4 Zeichen Kürzel (^[a-z0-9]{2,4}$)."""
        if port in STATIC_PORT_SUBDOMAINS:
            return STATIC_PORT_SUBDOMAINS[port]

        existing_hosts = self.get_existing_hostnames()
        existing_subdomains = {h.split(".")[0] for h in existing_hosts if h.endswith(f".{self.base_domain}")}

        generic_words = {
            "python", "python3", "node", "nodejs", "docker", "system", "server",
            "uvicorn", "gunicorn", "app", "flask", "vite", "unknown", "unbekannt",
            "process", "service", "daemon", "client", "worker", "web", "http",
            "simplehttp", "simple"
        }
        raw_text = f"{process_name} {title}".lower()
        tokens = re.findall(r"[a-z0-9]+", raw_text)

        candidates = []
        for token in tokens:
            if token in generic_words or token.isdigit():
                continue
            if 2 <= len(token) <= 4:
                candidates.append(token)
            elif len(token) > 4:
                candidates.append(token[:4])
                candidates.append(token[:3])

        for cand in candidates:
            if re.match(r"^[a-z0-9]{2,4}$", cand) and cand not in existing_subdomains:
                return cand

        port_str = str(port)
        fallback_candidates = []
        if len(port_str) >= 3:
            fallback_candidates.append(f"s{port_str[-3:]}")
        if len(port_str) >= 2:
            fallback_candidates.append(f"s{port_str[-2:]}")
        fallback_candidates.append(f"p{port_str[-3:]}" if len(port_str) >= 3 else f"p{port_str}")

        for fc in fallback_candidates:
            if re.match(r"^[a-z0-9]{2,4}$", fc) and fc not in existing_subdomains:
                return fc

        for i in range(1, 100):
            fc = f"s{i:02d}"
            if fc not in existing_subdomains:
                return fc

        return f"s{port % 1000:03d}"[:4]

    def _route_dns(self, subdomain: str) -> bool:
        """Führt cloudflared tunnel route dns -f pimmel-tunnel <subdomain>.pimmel.site aus."""
        hostname = f"{subdomain}.{self.base_domain}"
        cmd = [self.binary_path, "tunnel", "route", "dns", "-f", self.tunnel_name, hostname]
        try:
            print(f"[CLOUDFLARED] Registriere DNS CNAME: {hostname}...", flush=True)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                print(f"[CLOUDFLARED] DNS CNAME erfolgreich registriert: {hostname}", flush=True)
                return True
            else:
                print(f"[CLOUDFLARED] DNS Route Fehler ({res.returncode}): {res.stderr.strip()}", file=sys.stderr)
                return False
        except Exception as e:
            print(f"[CLOUDFLARED] DNS Route Ausnahme für {hostname}: {e}", file=sys.stderr)
            return False

    def _restart_service_debounced(self):
        """Startet cloudflared.service mit Cooldown-Debounce (3s) neu."""
        with self._restart_lock:
            now = time.time()
            elapsed = now - self._last_restart_ts
            if elapsed < 3.0:
                time.sleep(3.0 - elapsed)
            try:
                print("[CLOUDFLARED] Starte systemctl --user restart cloudflared...", flush=True)
                subprocess.run(["systemctl", "--user", "restart", "cloudflared"], check=True, timeout=10)
                self._last_restart_ts = time.time()
                print("[CLOUDFLARED] cloudflared.service erfolgreich neugestartet.", flush=True)
            except Exception as e:
                print(f"[WARN] Fehler beim Neustarten von cloudflared.service: {e}", file=sys.stderr)

    def ensure_tunnel(self, port: int, process_name: str = "", title: str = "") -> str | None:
        """Prüft config.yml, generiert Subdomain falls unbekannt, erstellt DNS-Eintrag,
        fügt Ingress-Regel ein, validiert & startet cloudflared.service neu.
        """
        if port in (22, 111, 5432) or port >= 32768:
            return None

        with self._lock:
            existing_url = self.get_url(port)
            if existing_url:
                return existing_url

            subdomain = self.generate_subdomain(port, process_name=process_name, title=title)
            hostname = f"{subdomain}.{self.base_domain}"
            if port in DIRECT_AUTH_PORTS:
                target_service = f"http://localhost:{port}"
            else:
                target_service = "http://localhost:5050"
                if auth_proxy:
                    auth_proxy.register_subdomain(
                        subdomain=subdomain,
                        target_port=port,
                        service_key=subdomain,
                        name=title or process_name or subdomain.upper(),
                    )

            dns_ok = self._route_dns(subdomain)
            if not dns_ok:
                print(f"[WARN] DNS-Provisionierung für {hostname} fehlgeschlagen, fahre fort.", file=sys.stderr)

            bak_path = f"{self.config_path}.bak"
            try:
                if os.path.exists(self.config_path):
                    shutil.copyfile(self.config_path, bak_path)

                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                ingress = data.get("ingress", [])

                updated = False
                for r in ingress:
                    if r.get("hostname") == hostname:
                        r["service"] = target_service
                        updated = True
                        break

                if not updated:
                    new_rule = {"hostname": hostname, "service": target_service}
                    catch_all_idx = -1
                    for idx, r in enumerate(ingress):
                        if r.get("service") == "http_status:404" or "hostname" not in r:
                            catch_all_idx = idx
                            break
                    if catch_all_idx >= 0:
                        ingress.insert(catch_all_idx, new_rule)
                    else:
                        ingress.append(new_rule)

                data["ingress"] = ingress

                with open(self.config_path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, sort_keys=False)

                val_res = subprocess.run(
                    [self.binary_path, "tunnel", "--config", self.config_path, "ingress", "validate"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if val_res.returncode != 0:
                    print(f"[CLOUDFLARED] Validierungsfehler! Rollback auf {bak_path}: {val_res.stderr}", file=sys.stderr)
                    if os.path.exists(bak_path):
                        shutil.copyfile(bak_path, self.config_path)
                    return None

                print(f"[CLOUDFLARED] Ingress-Regel validiert für {hostname} -> {target_service}", flush=True)

            except Exception as e:
                print(f"[CLOUDFLARED] Fehler beim Aktualisieren von {self.config_path}: {e}", file=sys.stderr)
                if os.path.exists(bak_path):
                    shutil.copyfile(bak_path, self.config_path)
                return None

            self._restart_service_debounced()
            full_url = f"https://{hostname}"
            self._url_cache[port] = full_url
            return full_url

    def get_url(self, port: int) -> str | None:
        """Gibt die Cloudflare URL für einen Port zurück."""
        with self._lock:
            if port in self._url_cache:
                return self._url_cache[port]

            if port in STATIC_PORT_SUBDOMAINS:
                u = f"https://{STATIC_PORT_SUBDOMAINS[port]}.{self.base_domain}"
                self._url_cache[port] = u
                return u

            if os.path.exists(self.config_path) and yaml:
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    for r in data.get("ingress", []):
                        h = r.get("hostname", "")
                        s = r.get("service", "")
                        if h and s:
                            m = re.search(r":(\d+)$", s)
                            if m and int(m.group(1)) == port:
                                u = f"https://{h}"
                                self._url_cache[port] = u
                                return u
                except Exception:
                    pass
            return None

    def get_all_urls(self) -> dict:
        """Gibt {port: url} aller konfigurierten Services zurück."""
        with self._lock:
            self._load_config_cache()
            return dict(self._url_cache)

    def stop_tunnel(self, port: int):
        """Optional: Bereinigung bei Dienstbeendigung."""
        with self._lock:
            if port in STATIC_PORT_SUBDOMAINS:
                return
            self._url_cache.pop(port, None)

    def stop_all(self):
        """Wird bei Exit aufgerufen."""
        pass


CloudflaredTunnelManager = CloudflaredNamedTunnelManager
cf_tunnel_manager = CloudflaredNamedTunnelManager()
atexit.register(cf_tunnel_manager.stop_all)

if auth_proxy:
    auth_proxy.start_background()
    atexit.register(auth_proxy.stop)


# ---------------------------------------------------------------------------
# 5-Minuten Automatischer Webserver-Erkennungs-Dienst
# ---------------------------------------------------------------------------
class WebserverDiscoveryScanner:
    """Hintergrund-Dienst zur periodischen Erkennung aller laufenden Webserver.
    Sucht alle 5 Minuten (300 Sekunden) nach allen lauschenden Prozessen und ermittelt
    für jeden Dienst:
    - Port, PID, Prozessname, Befehlszeile
    - Lokale LAN-Adresse (z.B. http://192.168.31.210:PORT) - NIEMALS 127.0.0.1
    - Tailscale-Adresse (z.B. http://pimmel.tail3a782b.ts.net:PORT)
    - Cloudflared-Quick-Tunnel-Adresse (automatisch angelegt falls fehlend)
    """

    def __init__(self, interval_seconds=300):
        self.interval = interval_seconds
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self.last_scan_ts = 0
        self.last_scan_time = "Initialisierung..."
        self.next_scan_ts = 0
        self.scan_count = 0
        self.is_scanning = False
        self.discovered_servers = []

    def get_cloudflared_map(self):
        if "cf_tunnel_manager" in globals() and cf_tunnel_manager:
            return cf_tunnel_manager.get_all_urls()
        return {}

    def scan(self):
        with self._lock:
            self.is_scanning = True

        lan_ip = get_lan_ip()
        ts_info = get_tailscale_info()
        ts_host = ts_info["domain"] or ts_info["ip"] or lan_ip
        cf_map = self.get_cloudflared_map()

        ports_dict = {}
        if psutil:
            try:
                for c in psutil.net_connections(kind="tcp"):
                    if c.status == "LISTEN":
                        p = c.laddr.port
                        if p not in ports_dict:
                            ports_dict[p] = {"ip": c.laddr.ip, "pid": c.pid}
            except Exception as e:
                print(f"[WARN] Socket-Scan Fehler: {e}", file=sys.stderr)

        discovered = []
        skip_ports = {22, 111, 5432}

        for port, info in sorted(ports_dict.items()):
            if port in skip_ports:
                continue

            pid = info["pid"]
            pname = "Unbekannt"
            cmd = "N/A"
            if pid:
                try:
                    proc = psutil.Process(pid)
                    pname = proc.name()
                    cmd = " ".join(proc.cmdline())
                except Exception:
                    pass

            if "cloudflared" in pname and port > 20000:
                continue

            is_http = False
            http_status = None
            server_hdr = None
            latency_ms = None
            t0 = time.time()

            probe_hosts = ["127.0.0.1", "localhost"]
            if info["ip"] in ["::1", "::"]:
                probe_hosts = ["[::1]", "127.0.0.1"]

            for phost in probe_hosts:
                try:
                    req = urllib.request.Request(f"http://{phost}:{port}/", headers={"User-Agent": "System-Discovery/1.0"})
                    with urllib.request.urlopen(req, timeout=0.25) as resp:
                        http_status = resp.status
                        is_http = True
                        server_hdr = resp.headers.get("Server")
                        latency_ms = int((time.time() - t0) * 1000)
                        break
                except urllib.error.HTTPError as e:
                    http_status = e.code
                    is_http = True
                    server_hdr = e.headers.get("Server")
                    latency_ms = int((time.time() - t0) * 1000)
                    break
                except Exception:
                    pass

            web_procs = ["node", "python", "python3", "uvicorn", "gunicorn", "headroom", "caddy", "nginx", "apache2"]
            if not is_http and any(wp in pname.lower() for wp in web_procs) and port < 32768:
                is_http = True
                http_status = 200

            if not is_http:
                continue

            title = f"{pname.capitalize()} (Port {port})"
            icon = "🌐"
            for reg_key, reg_val in SERVICE_REGISTRY.items():
                if reg_val["port"] == port:
                    title = reg_val["title"]
                    icon = reg_val["icon"]
                    break

            if title.startswith(pname.capitalize()):
                if "xdcc" in cmd.lower() or port == 3000:
                    title = "xdcc-load-cast"
                    icon = "🚀"
                elif "agydashboard" in cmd.lower() or port == 5000:
                    title = "agydashboard (System)"
                    icon = "📟"
                elif "telemetry" in cmd.lower() or port == 8000:
                    title = "TelemetryVault"
                    icon = "🏎️"
                elif "homeassistant" in cmd.lower() or port == 8123:
                    title = "Home Assistant"
                    icon = "🏠"
                elif "matter" in cmd.lower() or port == 5580:
                    title = "Matter Server"
                    icon = "🔌"
                elif "headroom" in cmd.lower() or port == 8787:
                    title = "Headroom AI Compression"
                    icon = "🧠"
                elif "9router" in cmd.lower() or port == 20128:
                    title = "9Router AI Router"
                    icon = "⚙️"
                elif "vite" in cmd.lower() or port == 5173:
                    title = "Vite Dev Client"
                    icon = "⚡"
                elif port == 3001:
                    title = "Node Server (3001)"
                    icon = "🟢"

            # GRUNDSATZ: NIEMALS 127.0.0.1 anzeigen - immer netzwerkfähige Adressen
            lan_url = f"http://{lan_ip}:{port}"
            ts_url = f"http://{ts_host}:{port}"
            cf_url = cf_map.get(port)

            # Automatisch Cloudflared Named Tunnel anlegen, falls noch keiner existiert
            if not cf_url and port < 32768:
                cf_url = cf_tunnel_manager.ensure_tunnel(port, process_name=pname, title=title)
                if not cf_url:
                    cf_url = cf_tunnel_manager.get_url(port)

            for reg_key, reg_val in SERVICE_REGISTRY.items():
                if reg_val["port"] == port:
                    reg_val["lan_url"] = lan_url
                    if ts_info.get("active"):
                        reg_val["tailscale_url"] = f"http://{ts_host}:{port}"
                    if cf_url:
                        reg_val["cf_url"] = cf_url

            discovered.append({
                "port": port,
                "title": title,
                "icon": icon,
                "pid": pid,
                "process_name": pname,
                "cmdline": cmd[:90] + ("..." if len(cmd) > 90 else ""),
                "bind_addr": info["ip"],
                "is_http": is_http,
                "http_status": http_status,
                "server_header": server_hdr or "N/A",
                "latency_ms": latency_ms or 1,
                "lan_url": lan_url,
                "tailscale_url": ts_url,
                "cloudflared_url": cf_url,
                "status": "online",
                "last_seen": datetime.datetime.now().strftime("%H:%M:%S"),
            })

        # Kurz abwarten falls neue Tunnel gerade gestartet wurden, um URLs direkt zu erfassen
        pending = [d for d in discovered if not d.get("cloudflared_url") and d.get("port", 99999) < 32768]
        if pending:
            for d in pending:
                u = cf_tunnel_manager.get_url(d["port"])
                if u:
                    d["cloudflared_url"] = u
                    for reg_key, reg_val in SERVICE_REGISTRY.items():
                        if reg_val["port"] == d["port"]:
                            reg_val["cf_url"] = u

        now_ts = time.time()
        with self._lock:
            self.discovered_servers = discovered
            self.last_scan_ts = now_ts
            self.last_scan_time = datetime.datetime.now().strftime("%H:%M:%S")
            self.next_scan_ts = now_ts + self.interval
            self.scan_count += 1
            self.is_scanning = False

        print(f"[DISCOVERY] Scan #{self.scan_count} abgeschlossen: {len(discovered)} Webserver aktiv.", flush=True)
        return discovered

    def _worker(self):
        while self._running:
            slept = 0
            while self._running and slept < self.interval:
                time.sleep(1)
                slept += 1
            if self._running:
                try:
                    self.scan()
                except Exception as e:
                    print(f"[WARN] Fehler im WebserverScanner: {e}", file=sys.stderr)

    def start(self):
        if not self._running:
            self._running = True
            try:
                self.scan()
            except Exception as e:
                print(f"[WARN] Initialer Webserver-Scan fehlgeschlagen: {e}", file=sys.stderr)
            self.next_scan_ts = time.time() + self.interval
            self._thread = threading.Thread(target=self._worker, name="WebserverScannerWorker", daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False

    def get_results(self):
        with self._lock:
            cf_map = cf_tunnel_manager.get_all_urls()
            discovered = []
            for srv in self.discovered_servers:
                srv_copy = dict(srv)
                p = srv_copy.get("port")
                if p:
                    # Dynamisch Cloudflared-URL aktualisieren sobald verfügbar
                    if not srv_copy.get("cloudflared_url") and p in cf_map:
                        srv_copy["cloudflared_url"] = cf_map[p]
                        srv["cloudflared_url"] = cf_map[p]
                        for reg_key, reg_val in SERVICE_REGISTRY.items():
                            if reg_val["port"] == p:
                                reg_val["cf_url"] = cf_map[p]
                discovered.append(srv_copy)

            remaining = max(0, int(self.next_scan_ts - time.time())) if self.next_scan_ts else self.interval
            return {
                "discovered": discovered,
                "last_scan_time": self.last_scan_time,
                "next_scan_seconds": remaining,
                "scan_count": self.scan_count,
                "is_scanning": self.is_scanning,
            }


# Globaler Discovery Scanner
webserver_scanner = WebserverDiscoveryScanner(interval_seconds=300)
webserver_scanner.start()


# ---------------------------------------------------------------------------
# Hermes Agent SQLite Aggregation
# ---------------------------------------------------------------------------
class HermesManager:
    """Sammelt und aggregiert Modellnutzung, Token & Kosten aus Hermes SQLite-Datenbanken.
    Durchsucht ~/.hermes/state.db und ~/.hermes/profiles/*/state.db.
    """

    def __init__(self, cache_ttl=5):
        self.cache_ttl = cache_ttl
        self._cached_data = None
        self._last_fetch = 0
        self._lock = threading.Lock()

    def _read_db(self):
        db_paths = glob.glob("/home/cb/.hermes/state.db") + glob.glob("/home/cb/.hermes/profiles/*/state.db")
        aggregated = {}
        total_sessions = 0
        total_input = 0
        total_output = 0
        total_cost = 0.0
        found_dbs = []

        for p in db_paths:
            if not os.path.exists(p):
                continue
            db_name = os.path.basename(os.path.dirname(p)) if "profiles" in p else "default"
            if db_name not in found_dbs:
                found_dbs.append(db_name)
            try:
                conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
                c = conn.cursor()
                c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='sessions';")
                has_tbl = c.fetchone()[0]
                if not has_tbl:
                    conn.close()
                    continue
                c.execute(
                    "SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(COALESCE(estimated_cost_usd, 0)) "
                    "FROM sessions WHERE model IS NOT NULL GROUP BY model;"
                )
                for row in c.fetchall():
                    m, cnt, inp, outp, cost = row
                    cnt = int(cnt or 0)
                    inp = int(inp or 0)
                    outp = int(outp or 0)
                    cost = float(cost or 0.0)

                    if m not in aggregated:
                        aggregated[m] = {
                            "model": m,
                            "sessions": 0,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                            "cost_usd": 0.0,
                        }
                    aggregated[m]["sessions"] += cnt
                    aggregated[m]["input_tokens"] += inp
                    aggregated[m]["output_tokens"] += outp
                    aggregated[m]["total_tokens"] += (inp + outp)
                    aggregated[m]["cost_usd"] += cost

                    total_sessions += cnt
                    total_input += inp
                    total_output += outp
                    total_cost += cost
                conn.close()
            except Exception as e:
                print(f"[WARN] Hermes DB Fehler ({p}): {e}", file=sys.stderr)

        models_list = sorted(aggregated.values(), key=lambda x: x["total_tokens"], reverse=True)
        all_tokens = total_input + total_output
        for item in models_list:
            item["input_formatted"] = format_tokens(item["input_tokens"])
            item["output_formatted"] = format_tokens(item["output_tokens"])
            item["total_formatted"] = format_tokens(item["total_tokens"])
            item["cost_formatted"] = format_usd(item["cost_usd"])
            item["percent_tokens"] = round((item["total_tokens"] / all_tokens) * 100, 1) if all_tokens > 0 else 0.0

        return {
            "models": models_list,
            "total_sessions": total_sessions,
            "total_tokens": all_tokens,
            "total_tokens_formatted": format_tokens(all_tokens),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost_usd": round(total_cost, 4),
            "total_cost_formatted": format_usd(total_cost),
            "database_count": len(found_dbs),
            "profiles": found_dbs,
        }

    def get_stats(self):
        now = time.time()
        with self._lock:
            if self._cached_data and (now - self._last_fetch < self.cache_ttl):
                return self._cached_data
            data = self._read_db()
            self._cached_data = data
            self._last_fetch = now
            return data


hermes_manager = HermesManager(cache_ttl=5)


# ---------------------------------------------------------------------------
# OpenRouter Budget & Spendings API
# ---------------------------------------------------------------------------
class OpenRouterManager:
    def __init__(self, cache_ttl=60):
        self.cache_ttl = cache_ttl
        self._cached_data = None
        self._last_fetch_ts = 0
        self._lock = threading.Lock()
        self._fetching = False

    def get_api_key(self):
        env_path = "/home/cb/.hermes/.env"
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("OPENROUTER_API_KEY="):
                            val = line.split("=", 1)[1].strip()
                            return val.strip("\"'")
            except Exception:
                pass
        return os.environ.get("OPENROUTER_API_KEY")

    def _fetch_live(self):
        key = self.get_api_key()
        if not key:
            return {
                "available": False,
                "error": "Kein OPENROUTER_API_KEY in ~/.hermes/.env",
                "label": "Nicht konfiguriert",
                "limit": None,
                "limit_formatted": "N/A",
                "usage": 0.0,
                "usage_formatted": "$0.00",
                "remaining": 0.0,
                "remaining_formatted": "N/A",
                "percent_used": 0.0,
                "color": "success",
                "daily_usage": 0.0,
                "daily_formatted": "$0.00",
                "weekly_usage": 0.0,
                "weekly_formatted": "$0.00",
                "monthly_usage": 0.0,
                "monthly_formatted": "$0.00",
                "total_credits": 0.0,
                "total_credits_formatted": "$0.00",
                "total_usage": 0.0,
                "total_usage_formatted": "$0.00",
                "credits_remaining": 0.0,
                "credits_remaining_formatted": "$0.00",
            }

        headers = {
            "Authorization": f"Bearer {key}",
            "User-Agent": "AgyDashboard/1.0",
            "Accept": "application/json",
        }
        try:
            req_key = urllib.request.Request("https://openrouter.ai/api/v1/auth/key", headers=headers)
            with urllib.request.urlopen(req_key, timeout=4) as resp:
                key_json = json.loads(resp.read().decode("utf-8"))
            kdata = key_json.get("data", {})

            req_credits = urllib.request.Request("https://openrouter.ai/api/v1/credits", headers=headers)
            with urllib.request.urlopen(req_credits, timeout=4) as resp:
                credits_json = json.loads(resp.read().decode("utf-8"))
            cdata = credits_json.get("data", {})

            limit = kdata.get("limit")
            usage = float(kdata.get("usage", 0.0) or 0.0)
            rem = kdata.get("limit_remaining")
            if rem is None and limit is not None:
                rem = max(0.0, float(limit) - usage)
            elif rem is not None:
                rem = float(rem)

            tot_credits = float(cdata.get("total_credits", 0.0) or 0.0)
            tot_usage = float(cdata.get("total_usage", 0.0) or 0.0)
            cred_rem = max(0.0, tot_credits - tot_usage)

            pct = round(min(100.0, (usage / float(limit)) * 100), 1) if (limit and float(limit) > 0) else 0.0
            color = "danger" if pct >= 85 else ("warning" if pct >= 65 else "success")

            return {
                "available": True,
                "label": kdata.get("label", "OpenRouter Key"),
                "limit": limit,
                "limit_formatted": format_usd(limit) if limit is not None else "Unbegrenzt",
                "usage": usage,
                "usage_formatted": format_usd(usage),
                "remaining": rem,
                "remaining_formatted": format_usd(rem) if rem is not None else "N/A",
                "percent_used": pct,
                "color": color,
                "daily_usage": float(kdata.get("usage_daily", 0.0) or 0.0),
                "daily_formatted": format_usd(kdata.get("usage_daily", 0.0)),
                "weekly_usage": float(kdata.get("usage_weekly", 0.0) or 0.0),
                "weekly_formatted": format_usd(kdata.get("usage_weekly", 0.0)),
                "monthly_usage": float(kdata.get("usage_monthly", 0.0) or 0.0),
                "monthly_formatted": format_usd(kdata.get("usage_monthly", 0.0)),
                "total_credits": tot_credits,
                "total_credits_formatted": format_usd(tot_credits),
                "total_usage": tot_usage,
                "total_usage_formatted": format_usd(tot_usage),
                "credits_remaining": cred_rem,
                "credits_remaining_formatted": format_usd(cred_rem),
                "last_synced": datetime.datetime.now().strftime("%H:%M:%S"),
            }
        except Exception as e:
            if self._cached_data and self._cached_data.get("available"):
                return self._cached_data
            return {
                "available": False,
                "error": str(e),
                "label": "OpenRouter Key (Offline)",
                "limit": None,
                "limit_formatted": "N/A",
                "usage": 0.0,
                "usage_formatted": "$0.00",
                "remaining": 0.0,
                "remaining_formatted": "N/A",
                "percent_used": 0.0,
                "color": "warning",
                "daily_usage": 0.0,
                "daily_formatted": "$0.00",
                "weekly_usage": 0.0,
                "weekly_formatted": "$0.00",
                "monthly_usage": 0.0,
                "monthly_formatted": "$0.00",
                "total_credits": 0.0,
                "total_credits_formatted": "$0.00",
                "total_usage": 0.0,
                "total_usage_formatted": "$0.00",
                "credits_remaining": 0.0,
                "credits_remaining_formatted": "$0.00",
            }

    def _async_fetch(self):
        try:
            data = self._fetch_live()
            with self._lock:
                if data.get("available") or not self._cached_data:
                    self._cached_data = data
                self._last_fetch_ts = time.time()
        finally:
            self._fetching = False

    def get_budget(self):
        now = time.time()
        with self._lock:
            if self._cached_data is None:
                self._cached_data = self._fetch_live()
                self._last_fetch_ts = now
                return self._cached_data
            if (now - self._last_fetch_ts > self.cache_ttl) and not self._fetching:
                self._fetching = True
                threading.Thread(target=self._async_fetch, name="OpenRouterFetcher", daemon=True).start()
            return self._cached_data


openrouter_manager = OpenRouterManager(cache_ttl=60)


# ---------------------------------------------------------------------------
# Antigravity Status & Quota
# ---------------------------------------------------------------------------
_antigravity_cache = None
_antigravity_cache_ts = 0
_antigravity_lock = threading.Lock()


def get_antigravity_status(cache_ttl=20.0):
    global _antigravity_cache, _antigravity_cache_ts
    now = time.time()
    with _antigravity_lock:
        if _antigravity_cache is not None and (now - _antigravity_cache_ts < cache_ttl):
            return _antigravity_cache

    oauth_path = "/home/cb/.gemini/antigravity-cli/antigravity-oauth-token"
    token_present = False
    auth_method = "consumer"

    if os.path.exists(oauth_path):
        try:
            with open(oauth_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                token_present = bool(data.get("token"))
                auth_method = data.get("auth_method", "consumer")
        except Exception:
            pass

    session_ids = set()
    mtimes = []
    for p in glob.glob("/home/cb/.gemini/antigravity-cli/conversations/*.db"):
        fname = os.path.basename(p)
        if not fname.endswith("-shm") and not fname.endswith("-wal"):
            session_ids.add(fname[:-3])
            try:
                mtimes.append(os.path.getmtime(p))
            except Exception:
                pass

    for p in glob.glob("/home/cb/.gemini/antigravity-cli/brain/*"):
        if os.path.isdir(p) and not os.path.basename(p).startswith("."):
            session_ids.add(os.path.basename(p))
            try:
                mtimes.append(os.path.getmtime(p))
            except Exception:
                pass

    latest_str = "Keine Aktivität"
    if mtimes:
        latest_dt = datetime.datetime.fromtimestamp(max(mtimes))
        now_dt = datetime.datetime.now()
        diff = now_dt - latest_dt
        if diff.total_seconds() < 3600:
            mins = int(diff.total_seconds() // 60)
            latest_str = f"vor {max(1, mins)} Min."
        elif diff.total_seconds() < 86400 and latest_dt.date() == now_dt.date():
            t_str = latest_dt.strftime("%H:%M")
            latest_str = f"Heute, {t_str} Uhr"
        else:
            latest_str = latest_dt.strftime("%d.%m.%Y %H:%M")

    badge = "Aktiv (Google Consumer / Free Quota)" if token_present else "Inaktiv / Nicht angemeldet"
    res = {
        "status": "active" if token_present else "inactive",
        "badge": badge,
        "auth_method": auth_method,
        "account_label": "Google Consumer / Free Quota" if auth_method == "consumer" else auth_method.capitalize(),
        "sessions_count": len(session_ids),
        "latest_activity": latest_str,
        "quota": "Unbegrenzte Chat-/Agenten-Quota (Gemini Flash & Pro)",
        "token_valid": token_present,
    }
    with _antigravity_lock:
        _antigravity_cache = res
        _antigravity_cache_ts = now
    return res


# ---------------------------------------------------------------------------
# 9Router SQLite Telemetrie & Verbrauchs-Statistiken
# ---------------------------------------------------------------------------
_9router_stats_cache = None
_9router_stats_cache_ts = 0
_9router_stats_lock = threading.Lock()


def get_9router_stats(cache_ttl=10.0):
    """Liest Nutzungs-, Telemetrie- und Verbindungsdaten aus ~/.9router/db/data.sqlite mit 10s Cache."""
    global _9router_stats_cache, _9router_stats_cache_ts
    now = time.time()
    with _9router_stats_lock:
        if _9router_stats_cache is not None and (now - _9router_stats_cache_ts < cache_ttl):
            return _9router_stats_cache

    db_path = os.path.expanduser("~/.9router/db/data.sqlite")
    default_res = {
        "status": "offline",
        "error": "Datenbank nicht gefunden",
        "totals": {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "cost": 0.0,
            "cost_formatted": "$0.00",
            "tokens_formatted": "0",
            "prompt_formatted": "0",
            "completion_formatted": "0",
            "cached_formatted": "0",
            "saved_tokens": 0,
            "saved_tokens_formatted": "0",
            "cache_hit_rate": 0.0,
            "cache_hit_rate_formatted": "0.0%",
            "saved_cost": 0.0,
            "saved_cost_formatted": "$0.00",
            "saved_cost_pct": 0.0,
            "saved_cost_pct_formatted": "0.0%",
            "uncached_cost": 0.0,
            "uncached_cost_formatted": "$0.00",
        },
        "by_provider": {},
        "by_model": {},
        "daily_timeline": [],
        "recent_history": [],
        "connections": [],
    }
    if not os.path.exists(db_path):
        return default_res

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            c = conn.cursor()

            # 1. usageDaily: Alle Tage aggregieren
            c.execute("SELECT dateKey, data FROM usageDaily ORDER BY dateKey ASC")
            daily_rows = c.fetchall()

            total_requests = 0
            total_prompt_tokens = 0
            total_completion_tokens = 0
            total_cached_tokens = 0
            total_cost = 0.0

            aggregated_by_provider = {}
            aggregated_by_model = {}
            daily_timeline = []

            for date_key, data_raw in daily_rows:
                try:
                    day_data = json.loads(data_raw)
                except Exception:
                    continue

                reqs = day_data.get("requests", 0)
                p_tokens = day_data.get("promptTokens", 0)
                c_tokens = day_data.get("completionTokens", 0)
                cached = day_data.get("cachedTokens", 0)
                cost = day_data.get("cost", 0.0)

                total_requests += reqs
                total_prompt_tokens += p_tokens
                total_completion_tokens += c_tokens
                total_cached_tokens += cached
                total_cost += cost

                daily_timeline.append({
                    "date": date_key,
                    "requests": reqs,
                    "prompt_tokens": p_tokens,
                    "completion_tokens": c_tokens,
                    "cached_tokens": cached,
                    "total_tokens": p_tokens + c_tokens,
                    "cost": round(cost, 6),
                    "cost_formatted": f"${cost:.4f}",
                })

                # byProvider
                for prov, p_info in day_data.get("byProvider", {}).items():
                    if prov not in aggregated_by_provider:
                        aggregated_by_provider[prov] = {
                            "provider": prov,
                            "requests": 0,
                            "promptTokens": 0,
                            "completionTokens": 0,
                            "cachedTokens": 0,
                            "cost": 0.0,
                        }
                    aggregated_by_provider[prov]["requests"] += p_info.get("requests", 0)
                    aggregated_by_provider[prov]["promptTokens"] += p_info.get("promptTokens", 0)
                    aggregated_by_provider[prov]["completionTokens"] += p_info.get("completionTokens", 0)
                    aggregated_by_provider[prov]["cachedTokens"] += p_info.get("cachedTokens", 0)
                    aggregated_by_provider[prov]["cost"] += p_info.get("cost", 0.0)

                # byModel
                for mod_key, m_info in day_data.get("byModel", {}).items():
                    raw_model = m_info.get("rawModel") or mod_key.split("|")[0]
                    prov = m_info.get("provider") or (mod_key.split("|")[1] if "|" in mod_key else "unknown")
                    if raw_model not in aggregated_by_model:
                        aggregated_by_model[raw_model] = {
                            "model": raw_model,
                            "provider": prov,
                            "requests": 0,
                            "promptTokens": 0,
                            "completionTokens": 0,
                            "cachedTokens": 0,
                            "cost": 0.0,
                        }
                    aggregated_by_model[raw_model]["requests"] += m_info.get("requests", 0)
                    aggregated_by_model[raw_model]["promptTokens"] += m_info.get("promptTokens", 0)
                    aggregated_by_model[raw_model]["completionTokens"] += m_info.get("completionTokens", 0)
                    aggregated_by_model[raw_model]["cachedTokens"] += m_info.get("cachedTokens", 0)
                    aggregated_by_model[raw_model]["cost"] += m_info.get("cost", 0.0)

            # 2. usageHistory: Letzte 15 Requests
            c.execute("""
                SELECT id, timestamp, provider, model, promptTokens, completionTokens, cost, status, tokens, meta
                FROM usageHistory
                ORDER BY id DESC
                LIMIT 15
            """)
            history_rows = c.fetchall()
            recent_requests = []
            for row in history_rows:
                req_id, ts, prov, model, pt, ct, cost, status, tokens_raw, meta_raw = row
                cached_tok = 0
                total_tok = (pt or 0) + (ct or 0)
                if tokens_raw:
                    try:
                        tok_obj = json.loads(tokens_raw)
                        cached_tok = tok_obj.get("cached_tokens", 0)
                        if "total_tokens" in tok_obj:
                            total_tok = tok_obj["total_tokens"]
                    except Exception:
                        pass

                time_display = ts
                if ts and "T" in ts:
                    try:
                        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
                        time_display = dt.strftime("%d.%m %H:%M:%S")
                    except Exception:
                        time_display = ts.split("T")[1][:8] if len(ts) > 19 else ts

                recent_requests.append({
                    "id": req_id,
                    "timestamp": ts,
                    "time_display": time_display,
                    "provider": prov or "unknown",
                    "model": model or "unknown",
                    "prompt_tokens": pt or 0,
                    "completion_tokens": ct or 0,
                    "cached_tokens": cached_tok,
                    "total_tokens": total_tok,
                    "cost": round(cost or 0.0, 6),
                    "cost_formatted": f"${(cost or 0.0):.4f}",
                    "status": status or "ok"
                })

            # 3. providerConnections
            c.execute("""
                SELECT id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt
                FROM providerConnections
                ORDER BY priority ASC, name ASC
            """)
            conn_rows = c.fetchall()
            connections = []
            for row in conn_rows:
                conn_id, prov, auth_type, name, email, priority, is_active, data_raw, created_at, updated_at = row
                connections.append({
                    "id": conn_id,
                    "provider": prov,
                    "auth_type": auth_type,
                    "name": name or prov,
                    "email": email,
                    "priority": priority,
                    "is_active": bool(is_active),
                    "created_at": created_at,
                    "updated_at": updated_at
                })

            total_tokens = total_prompt_tokens + total_completion_tokens

            # Ersparnis durch Context/Prompt Caching ermitteln mit direkter SQL-Aggregation
            uncached_cost = 0.0
            try:
                c.execute("""
                    SELECT 
                        SUM(CASE WHEN LOWER(COALESCE(model, '')) LIKE '%high%' 
                                 THEN (COALESCE(promptTokens, 0) * 0.0000025 + COALESCE(completionTokens, 0) * 0.000010)
                                 ELSE (COALESCE(promptTokens, 0) * 0.0000005 + COALESCE(completionTokens, 0) * 0.000003) END)
                    FROM usageHistory;
                """)
                row = c.fetchone()
                if row and row[0] is not None:
                    uncached_cost = float(row[0])
            except Exception:
                pass

            if uncached_cost <= 0.0 and total_cached_tokens > 0:
                uncached_cost = total_cost + (total_cached_tokens * 0.00000045)

            estimated_saved_cost = max(0.0, uncached_cost - total_cost)
            cache_hit_rate = round((total_cached_tokens / total_prompt_tokens * 100), 1) if total_prompt_tokens > 0 else 0.0
            saved_cost_pct = round((estimated_saved_cost / uncached_cost * 100), 1) if uncached_cost > 0 else 0.0

            def fmt_num(n):
                if n >= 1_000_000:
                    return f"{n/1_000_000:.2f}M"
                elif n >= 1_000:
                    return f"{n/1_000:.1f}k"
                return str(n)

            res = {
                "status": "online",
                "totals": {
                    "requests": total_requests,
                    "prompt_tokens": total_prompt_tokens,
                    "completion_tokens": total_completion_tokens,
                    "cached_tokens": total_cached_tokens,
                    "saved_tokens": total_cached_tokens,
                    "total_tokens": total_tokens,
                    "cost": round(total_cost, 6),
                    "cost_formatted": f"${total_cost:.4f}",
                    "tokens_formatted": fmt_num(total_tokens),
                    "prompt_formatted": fmt_num(total_prompt_tokens),
                    "completion_formatted": fmt_num(total_completion_tokens),
                    "cached_formatted": fmt_num(total_cached_tokens),
                    "saved_tokens_formatted": fmt_num(total_cached_tokens),
                    "cache_hit_rate": cache_hit_rate,
                    "cache_hit_rate_formatted": f"{cache_hit_rate:.1f}%",
                    "saved_cost": round(estimated_saved_cost, 4),
                    "saved_cost_formatted": f"${estimated_saved_cost:.4f}",
                    "saved_cost_pct": saved_cost_pct,
                    "saved_cost_pct_formatted": f"{saved_cost_pct:.1f}%",
                    "uncached_cost": round(uncached_cost, 4),
                    "uncached_cost_formatted": f"${uncached_cost:.4f}",
                },
                "by_provider": aggregated_by_provider,
                "by_model": aggregated_by_model,
                "daily_timeline": daily_timeline,
                "recent_history": recent_requests,
                "connections": connections
            }

            with _9router_stats_lock:
                _9router_stats_cache = res
                _9router_stats_cache_ts = time.time()
            return res
        finally:
            conn.close()

    except Exception as e:
        print(f"[WARN] 9Router Stats Fehler: {e}", file=sys.stderr)
        err_res = dict(default_res)
        err_res["status"] = "error"
        err_res["error"] = str(e)
        return err_res


# ---------------------------------------------------------------------------
# Gesamt-System-Stats Sammler
# ---------------------------------------------------------------------------
def get_system_stats(include_history=False):
    stats = {
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # CPU Metriken
    if psutil:
        try:
            stats["cpu"] = {
                "percent": round(psutil.cpu_percent(interval=None), 1),
                "cores": psutil.cpu_count(logical=True),
                "cores_physical": psutil.cpu_count(logical=False),
            }
        except Exception:
            stats["cpu"] = {"percent": 0.0, "cores": os.cpu_count() or 1}
    else:
        try:
            load = os.getloadavg()[0]
            cores = os.cpu_count() or 1
            stats["cpu"] = {
                "percent": round(min(100.0, (load / cores) * 100), 1),
                "cores": cores,
            }
        except Exception:
            stats["cpu"] = {"percent": 0.0, "cores": os.cpu_count() or 1}

    # RAM & Disk
    stats["ram"] = get_ram_metrics()
    stats["disk"] = get_disk_metrics()

    # CPU Temperatur
    temp_val, temp_str = get_temperature()
    stats["temperature"] = {
        "value": temp_val,
        "display": temp_str,
    }

    # Uptime
    if psutil:
        try:
            boot = psutil.boot_time()
            uptime_sec = time.time() - boot
            boot_dt = datetime.datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M:%S")
            stats["uptime"] = {
                "seconds": int(uptime_sec),
                "display": format_uptime(uptime_sec),
                "boot_time": boot_dt,
            }
        except Exception:
            stats["uptime"] = {"seconds": 0, "display": "N/A", "boot_time": "N/A"}
    else:
        stats["uptime"] = {"seconds": 0, "display": "N/A", "boot_time": "N/A"}

    # Throttling
    is_throttled, throttled_raw = get_throttled_status()
    stats["throttled"] = {
        "active": is_throttled,
        "raw": throttled_raw,
    }

    # Cloudflare Quick-Tunnel URLs
    stats["cloudflared"] = get_cloudflared_urls()

    # Web-Services Status
    services_status = {}
    for svc_key, svc_info in SERVICE_REGISTRY.items():
        is_online = check_service_status(port=svc_info["port"])
        services_status[svc_key] = {
            "name": svc_info["name"],
            "title": svc_info["title"],
            "icon": svc_info["icon"],
            "port": svc_info["port"],
            "description": svc_info["description"],
            "lan_url": svc_info.get("lan_url"),
            "tailscale_url": svc_info.get("tailscale_url"),
            "cf_url": svc_info.get("cf_url"),
            "online": is_online,
        }
    stats["services"] = services_status

    # KI-Agenten: Budgets & Spendings
    stats["openrouter"] = openrouter_manager.get_budget()
    stats["antigravity"] = get_antigravity_status()

    # Hermes Agent
    stats["hermes"] = hermes_manager.get_stats()

    # 9Router SQLite Telemetrie & Statistiken (~/.9router/db/data.sqlite)
    stats["nine_router"] = get_9router_stats()

    # Discovered Webservers (5-min Background Scanner)
    disc_data = webserver_scanner.get_results()
    stats["discovered_servers"] = disc_data["discovered"]
    stats["scanner_meta"] = {
        "last_scan_time": disc_data["last_scan_time"],
        "next_scan_seconds": disc_data["next_scan_seconds"],
        "scan_count": disc_data["scan_count"],
        "is_scanning": disc_data["is_scanning"],
        "interval_seconds": webserver_scanner.interval,
    }

    stats["lan_ip"] = get_lan_ip()
    stats["history_samples"] = history_store.get_samples(range_seconds=3600) if (include_history and "history_store" in globals()) else []
    stats["stardate"] = calculate_stardate()
    stats["alerts"] = alert_monitor.get_status() if "alert_monitor" in globals() else {"active": False, "reasons": []}

    # Solar Balkonsolar Vitals (Akkustand & Hausbedarf für Top-Right Badges)
    if ha_service:
        stats["solar"] = ha_service.get_solar_summary()
    else:
        stats["solar"] = {"battery_soc_str": "--%", "house_power_str": "-- W", "available": False}

    return stats


# ---------------------------------------------------------------------------
# In-Memory 24h Metrics History Store
# ---------------------------------------------------------------------------
RANGE_MAP = {
    "10m": 600,
    "10min": 600,
    "30m": 1800,
    "30min": 1800,
    "1h": 3600,
    "12h": 43200,
    "24h": 86400,
}


def parse_range_param(val):
    if not val:
        return "1h", 3600
    val_clean = str(val).strip().lower()
    if val_clean in RANGE_MAP:
        return val_clean, RANGE_MAP[val_clean]
    m = re.match(r"^(\d+)\s*(m|min|h|d)?$", val_clean)
    if m:
        num = int(m.group(1))
        unit = m.group(2) or "m"
        if unit in ("m", "min"):
            sec = num * 60
        elif unit == "h":
            sec = num * 3600
        elif unit == "d":
            sec = num * 86400
        else:
            sec = num
        return val_clean, max(60, min(86400, sec))
    return "1h", 3600


class MetricsHistory:
    def __init__(self, retention_seconds=86400, sample_interval=10):
        self.retention_seconds = retention_seconds
        self.sample_interval = sample_interval
        max_samples = (retention_seconds // sample_interval) + 120
        self._samples = deque(maxlen=max_samples)
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def record_current(self):
        now_ts = int(time.time())
        cpu_val = 0.0
        if psutil:
            try:
                cpu_val = round(psutil.cpu_percent(interval=None), 1)
            except Exception:
                pass
        else:
            try:
                load = os.getloadavg()[0]
                cores = os.cpu_count() or 1
                cpu_val = round(min(100.0, (load / cores) * 100), 1)
            except Exception:
                pass

        temp_c, _ = get_temperature()
        is_throttled, throttled_raw = get_throttled_status()
        ram_pct = get_ram_metrics().get("percent", 0.0)
        disk_pct = get_disk_metrics().get("percent", 0.0)

        sample = {
            "ts": now_ts,
            "time": datetime.datetime.fromtimestamp(now_ts).strftime("%H:%M:%S"),
            "temp": temp_c,
            "cpu": cpu_val,
            "throttled": bool(is_throttled),
            "ram": ram_pct,
            "disk": disk_pct,
        }

        if "alert_monitor" in globals():
            try:
                alert_monitor.evaluate(cpu_val, ram_pct, disk_pct, temp_c)
            except Exception:
                pass

        with self._lock:
            self._samples.append(sample)
            cutoff = now_ts - self.retention_seconds
            while self._samples and self._samples[0]["ts"] < cutoff:
                self._samples.popleft()

    def get_samples(self, range_seconds=3600):
        cutoff = int(time.time()) - range_seconds
        with self._lock:
            return [s for s in self._samples if s["ts"] >= cutoff]

    def _worker(self):
        while self._running:
            try:
                self.record_current()
            except Exception as e:
                print(f"[WARN] Fehler im History-Worker: {e}", file=sys.stderr)
            time.sleep(self.sample_interval)

    def start(self):
        if not self._running:
            self._running = True
            try:
                self.record_current()
            except Exception:
                pass
            self._thread = threading.Thread(target=self._worker, name="MetricsHistoryWorker", daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False


history_store = MetricsHistory(retention_seconds=86400, sample_interval=10)
history_store.start()


# ---------------------------------------------------------------------------
# KI-Agenten & 9Router Proxy Integration
# ---------------------------------------------------------------------------
def get_9router_api_key():
    """Ermittelt den API-Key für den lokalen 9Router Proxy (Port 20128)."""
    env_key = os.environ.get("ROUTER_API_KEY") or os.environ.get("NINEROUTER_API_KEY") or os.environ.get("HERMES_API_KEY")
    if env_key:
        return env_key
    db_path = os.path.expanduser("~/.9router/db/data.sqlite")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
            c = conn.cursor()
            c.execute("SELECT key FROM apiKeys ORDER BY rowid ASC LIMIT 1;")
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
    return "sk-a83b72936d0528ea-bhpytf-49c7be05"


# ---------------------------------------------------------------------------
# Schwellwert- & Red-Alert-Überwachung (AlertMonitor)
# ---------------------------------------------------------------------------
DEFAULT_ALERT_CONFIG = {
    "cpu_threshold": 90.0,
    "ram_threshold": 90.0,
    "disk_threshold": 90.0,
    "temp_threshold": 80.0,
    "duration_seconds": 60,
    "gateway_check_interval_seconds": 600,
    "gateway_url": "http://127.0.0.1:20128",
    "antigravity_ide_url": "https://antigravity.google.com/r/f9e18040-ab9a-4f0c-9d4a-2b4f5281af22-v2?p=c%2F3a633d37-def3-419b-ab1e-8498973ae694%3Fsection%3Dc15db4e2-c36e-442e-850b-0d28e944e2c6",
}


class AlertMonitor:
    def __init__(self, config_path=None):
        self.config_path = config_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        self.config = self._load_config()
        self.lock = threading.Lock()

        # High state timestamps
        self.cpu_high_since = None
        self.ram_high_since = None
        self.disk_high_since = None
        self.temp_high_since = None

        # Current metrics snapshot
        self.last_cpu = 0.0
        self.last_ram = 0.0
        self.last_disk = 0.0
        self.last_temp = 0.0

        # Gateway state
        self.gateway_last_check = 0
        self.gateway_last_check_str = "Noch nicht geprüft"
        self.gateway_status = "ok"
        self.gateway_error = ""

        # Overall alert state
        self.is_red_alert = False
        self.active_reasons = []

        self._running = False
        self._thread = None

    def _load_config(self):
        cfg = dict(DEFAULT_ALERT_CONFIG)
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        cfg.update(data)
            except Exception as e:
                print(f"[WARN] AlertMonitor: Fehler beim Laden von config.json: {e}", file=sys.stderr)
        else:
            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
            except Exception:
                pass
        return cfg

    def save_config(self, updates):
        with self.lock:
            for k, v in updates.items():
                if k in self.config:
                    if k in ("cpu_threshold", "ram_threshold", "disk_threshold", "temp_threshold"):
                        try:
                            self.config[k] = float(v)
                        except (ValueError, TypeError):
                            pass
                    elif k in ("duration_seconds", "gateway_check_interval_seconds"):
                        try:
                            self.config[k] = int(v)
                        except (ValueError, TypeError):
                            pass
                    elif k in ("gateway_url", "antigravity_ide_url"):
                        self.config[k] = str(v).strip()
            try:
                disk_data = {}
                if os.path.exists(self.config_path):
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        disk_data = json.load(f)
                        if not isinstance(disk_data, dict):
                            disk_data = {}
                disk_data.update(self.config)
                self.config = disk_data
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, indent=2)
            except Exception as e:
                print(f"[WARN] AlertMonitor: Fehler beim Speichern von config.json: {e}", file=sys.stderr)
        return self.get_status()

    def check_gateway(self):
        url = self.config.get("gateway_url", "http://127.0.0.1:20128").rstrip("/")
        success = False
        err_msg = ""
        now_ts = int(time.time())

        # Test both /v1/models and base url
        check_endpoints = [f"{url}/v1/models", url]
        last_err = ""
        for ep in check_endpoints:
            try:
                headers = {}
                api_key = get_9router_api_key()
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                req = urllib.request.Request(ep, headers=headers)
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
                with opener.open(req, timeout=5) as resp:
                    code = resp.getcode()
                    if code in (200, 301, 302, 307, 308):
                        success = True
                        break
            except urllib.error.HTTPError as he:
                if he.code in (200, 301, 302, 307, 308, 401, 403):
                    success = True
                    break
                last_err = f"HTTP {he.code}"
            except Exception as e:
                last_err = str(e)

        if not success:
            err_msg = last_err or "Keine Antwort vom 9Router Gateway"

        with self.lock:
            self.gateway_last_check = now_ts
            self.gateway_last_check_str = datetime.datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")
            if success:
                self.gateway_status = "ok"
                self.gateway_error = ""
            else:
                self.gateway_status = "error"
                self.gateway_error = err_msg

        return {"status": self.gateway_status, "error": self.gateway_error, "time": self.gateway_last_check_str}

    def evaluate(self, cpu_val, ram_pct, disk_pct, temp_c):
        now_ts = int(time.time())
        with self.lock:
            self.last_cpu = cpu_val
            self.last_ram = ram_pct
            self.last_disk = disk_pct
            self.last_temp = temp_c or 0.0

            cpu_th = float(self.config.get("cpu_threshold", 90.0))
            ram_th = float(self.config.get("ram_threshold", 90.0))
            disk_th = float(self.config.get("disk_threshold", 90.0))
            temp_th = float(self.config.get("temp_threshold", 80.0))
            dur_sec = int(self.config.get("duration_seconds", 60))
            gw_int = int(self.config.get("gateway_check_interval_seconds", 600))

            # Gateway check due?
            if (now_ts - self.gateway_last_check) >= gw_int or self.gateway_last_check == 0:
                threading.Thread(target=self.check_gateway, daemon=True).start()

            # Check CPU
            if cpu_val >= cpu_th:
                if self.cpu_high_since is None:
                    self.cpu_high_since = now_ts
            else:
                self.cpu_high_since = None

            # Check RAM
            if ram_pct >= ram_th:
                if self.ram_high_since is None:
                    self.ram_high_since = now_ts
            else:
                self.ram_high_since = None

            # Check Disk
            if disk_pct >= disk_th:
                if self.disk_high_since is None:
                    self.disk_high_since = now_ts
            else:
                self.disk_high_since = None

            # Check Temp
            if (temp_c or 0.0) >= temp_th:
                if self.temp_high_since is None:
                    self.temp_high_since = now_ts
            else:
                self.temp_high_since = None

            reasons = []
            if self.cpu_high_since and (now_ts - self.cpu_high_since) >= dur_sec:
                reasons.append(f"CPU Auslastung ({cpu_val:.1f}%) > {cpu_th:.0f}% seit {now_ts - self.cpu_high_since}s")
            if self.ram_high_since and (now_ts - self.ram_high_since) >= dur_sec:
                reasons.append(f"RAM Belegung ({ram_pct:.1f}%) > {ram_th:.0f}% seit {now_ts - self.ram_high_since}s")
            if self.disk_high_since and (now_ts - self.disk_high_since) >= dur_sec:
                reasons.append(f"Festplatte ({disk_pct:.1f}%) > {disk_th:.0f}% seit {now_ts - self.disk_high_since}s")
            if self.temp_high_since and (now_ts - self.temp_high_since) >= dur_sec:
                reasons.append(f"Temperatur ({temp_c:.1f}°C) > {temp_th:.0f}°C seit {now_ts - self.temp_high_since}s")

            if self.gateway_status == "error":
                reasons.append(f"9Router Gateway nicht funktionsfähig: {self.gateway_error or 'Offline'}")

            self.is_red_alert = len(reasons) > 0
            self.active_reasons = reasons

    def get_status(self):
        now_ts = int(time.time())
        with self.lock:
            return {
                "active": self.is_red_alert,
                "reasons": list(self.active_reasons),
                "thresholds": dict(self.config),
                "gateway": {
                    "status": self.gateway_status,
                    "error": self.gateway_error,
                    "last_check": self.gateway_last_check_str,
                    "url": self.config.get("gateway_url", "http://127.0.0.1:20128"),
                },
                "current_values": {
                    "cpu": self.last_cpu,
                    "ram": self.last_ram,
                    "disk": self.last_disk,
                    "temp": self.last_temp,
                },
                "high_durations": {
                    "cpu_seconds": int(now_ts - self.cpu_high_since) if self.cpu_high_since else 0,
                    "ram_seconds": int(now_ts - self.ram_high_since) if self.ram_high_since else 0,
                    "disk_seconds": int(now_ts - self.disk_high_since) if self.disk_high_since else 0,
                    "temp_seconds": int(now_ts - self.temp_high_since) if self.temp_high_since else 0,
                },
                "stardate": calculate_stardate(),
            }

    def _worker(self):
        while self._running:
            try:
                cpu_val = 0.0
                if psutil:
                    try:
                        cpu_val = round(psutil.cpu_percent(interval=None), 1)
                    except Exception:
                        pass
                temp_c, _ = get_temperature()
                ram_pct = get_ram_metrics().get("percent", 0.0)
                disk_pct = get_disk_metrics().get("percent", 0.0)
                self.evaluate(cpu_val, ram_pct, disk_pct, temp_c)
            except Exception as e:
                print(f"[WARN] AlertMonitor worker error: {e}", file=sys.stderr)
            time.sleep(10)

    def start(self):
        if not self._running:
            self._running = True
            threading.Thread(target=self.check_gateway, daemon=True).start()
            self._thread = threading.Thread(target=self._worker, name="AlertMonitorWorker", daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False


alert_monitor = AlertMonitor()
alert_monitor.start()

if espn_client:
    try:
        espn_client.start_poller()
    except Exception as _espn_poller_err:
        print(f"[WARN] EspnScorePoller konnte nicht gestartet werden: {_espn_poller_err}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Hermes Agent Helper Functions
# ---------------------------------------------------------------------------
def get_hermes_profiles():
    """Liest alle verfügbaren Hermes Profile auf dem System aus (~/.hermes)."""
    base = os.path.expanduser("~/.hermes")
    if not os.path.isdir(base):
        return []

    try:
        ps_out = subprocess.check_output(["ps", "aux"]).decode("utf-8", errors="ignore")
    except Exception:
        ps_out = ""

    profiles = []

    # 1. Default-Profil in ~/.hermes
    cfg_file = os.path.join(base, "config.yaml")
    model = "ag/gemini-3-flash"
    provider = "custom (9Router)"
    personality = "Standard System-Agent"
    if os.path.exists(cfg_file):
        try:
            if yaml:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    c = yaml.safe_load(f) or {}
                    m = c.get("model", {})
                    if isinstance(m, dict):
                        model = m.get("default", model)
                        provider = m.get("provider", provider)
                    elif isinstance(m, str):
                        model = m
                    if c.get("personality"):
                        personality = c.get("personality")
        except Exception:
            pass

    def_gw = ("hermes_cli.main gateway run" in ps_out) or os.path.exists(os.path.join(base, "gateway.sock"))
    profiles.append({
        "name": "default",
        "display_name": "Default Agent",
        "is_default": True,
        "model": model,
        "provider": provider,
        "personality": personality,
        "gateway_running": def_gw,
        "path": base,
        "description": "Zentraler Hermes System-Agent // Star Trek ODN Core"
    })

    # 2. Named Profiles in ~/.hermes/profiles/*
    p_dir = os.path.join(base, "profiles")
    if os.path.isdir(p_dir):
        for name in sorted(os.listdir(p_dir)):
            p_path = os.path.join(p_dir, name)
            if not os.path.isdir(p_path) or name.startswith("."):
                continue
            cfg_p = os.path.join(p_path, "config.yaml")
            p_model = "ag/gemini-3-flash"
            p_provider = "custom"
            p_desc = ""
            p_personality = ""
            if os.path.exists(cfg_p):
                try:
                    if yaml:
                        with open(cfg_p, "r", encoding="utf-8") as f:
                            c = yaml.safe_load(f) or {}
                            m = c.get("model", {})
                            if isinstance(m, dict):
                                p_model = m.get("default", p_model)
                                p_provider = m.get("provider", p_provider)
                            elif isinstance(m, str):
                                p_model = m
                            p_personality = c.get("personality", "")
                            p_desc = c.get("description", "") or p_personality
                except Exception:
                    pass
            soul_p = os.path.join(p_path, "SOUL.md")
            if os.path.exists(soul_p) and not p_desc:
                try:
                    with open(soul_p, "r", encoding="utf-8") as f:
                        for line in f:
                            line_s = line.strip()
                            if line_s and not line_s.startswith("#"):
                                p_desc = line_s[:100]
                                break
                except Exception:
                    pass
            gw_running = (f"--profile {name}" in ps_out)
            profiles.append({
                "name": name,
                "display_name": name.capitalize(),
                "is_default": False,
                "model": p_model,
                "provider": p_provider,
                "personality": p_personality or name,
                "gateway_running": gw_running,
                "path": p_path,
                "description": p_desc or f"Profil {name}"
            })
    return profiles


def create_hermes_profile(name, description="", clone_from="default", model=""):
    """Erstellt einen neuen Hermes Agenten über hermes profile create."""
    clean_name = re.sub(r"[^a-z0-9_-]", "", name.lower().strip())
    if not clean_name:
        return {"success": False, "error": "Ungültiger Agenten-Name. Nur Kleinbuchstaben, Ziffern und Bindestriche erlaubt."}
    if clean_name == "default":
        return {"success": False, "error": "Der Name 'default' ist für das Hauptprofil reserviert."}

    hermes_bin = "/home/cb/.local/bin/hermes"
    if not os.path.exists(hermes_bin):
        hermes_bin = "hermes"

    cmd = [hermes_bin, "profile", "create", clean_name, "--clone"]
    if clone_from and clone_from != "default":
        cmd = [hermes_bin, "profile", "create", clean_name, "--clone-from", clone_from]
    if description:
        cmd.extend(["--description", description.strip()])

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45)
        out_combined = f"{res.stdout} {res.stderr}".lower()
        if res.returncode != 0 and "already exists" not in out_combined:
            return {"success": False, "error": f"Fehler beim Erstellen: {res.stderr or res.stdout}"}
    except Exception as e:
        return {"success": False, "error": f"Ausführungsfehler: {str(e)}"}

    # Optionales Modell in der neu angelegten config.yaml hinterlegen
    profile_cfg = os.path.expanduser(f"~/.hermes/profiles/{clean_name}/config.yaml")
    if model and os.path.exists(profile_cfg) and yaml:
        try:
            with open(profile_cfg, "r", encoding="utf-8") as f:
                c = yaml.safe_load(f) or {}
            if "model" not in c or not isinstance(c["model"], dict):
                c["model"] = {}
            c["model"]["default"] = model.strip()
            with open(profile_cfg, "w", encoding="utf-8") as f:
                yaml.safe_dump(c, f)
        except Exception:
            pass

    return {
        "success": True,
        "name": clean_name,
        "message": f"Hermes Agent '{clean_name}' erfolgreich initialisiert.",
        "profiles": get_hermes_profiles()
    }


def hermes_chat_prompt(profile, message):
    """Sendet einen Prompt an einen ausgewählten Hermes Agenten via One-Shot CLI."""
    if not message or not message.strip():
        return {"success": False, "error": "Leere Nachricht übermittelt."}

    python_bin = "/home/cb/.hermes/hermes-agent/venv/bin/python"
    if not os.path.exists(python_bin):
        python_bin = sys.executable

    cmd = [python_bin, "-m", "hermes_cli.main"]
    if profile and profile != "default":
        cmd.extend(["--profile", profile])
    cmd.extend(["-z", message.strip()])

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90)
        output = (res.stdout or "").strip()
        if not output and res.stderr:
            output = f"[Hermes Fehler]: {res.stderr.strip()}"
        return {
            "success": res.returncode == 0,
            "reply": output or "Keine Ausgabe vom Hermes-Agenten erhalten.",
            "profile": profile or "default"
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Zeitüberschreitung (90s Timeout) beim Kommunizieren mit Hermes.", "reply": ""}
    except Exception as e:
        return {"success": False, "error": f"Verbindungsfehler: {str(e)}", "reply": ""}


def fetch_chat_models():
    """Fragt verfügbare Modelle vom lokalen Proxy (Port 20128) ab mit Fallback."""
    fallback_models = ['ag/gemini-3.8-flash-high', 'qwen2.5:7b']
    api_key = get_9router_api_key()
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = urllib.request.Request("http://127.0.0.1:20128/v1/models", headers=headers)
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["id"] for m in data.get("data", []) if "id" in m]
            if models:
                return {
                    "models": models,
                    "data": models,
                    "status": "online"
                }
    except Exception as e:
        print(f"[WARN] 9Router Models Abfrage fehlgeschlagen ({e}), verwende Fallback.", file=sys.stderr)
    return {
        "models": fallback_models,
        "data": fallback_models,
        "status": "offline"
    }


def parse_sse_completion(text):
    """Parst SSE-Stream Datenzeilen und setzt den finalen Assistant-Text zusammen."""
    content_parts = []
    model_name = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            payload = line[6:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
                if "model" in chunk and not model_name:
                    model_name = chunk["model"]
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    if "content" in delta and delta["content"]:
                        content_parts.append(delta["content"])
            except Exception:
                pass
    full_content = "".join(content_parts)
    return {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": full_content}, "finish_reason": "stop"}],
        "model": model_name
    }


def forward_chat_completion(model, messages, auth_header=None):
    """Leitet Chat Completion Requests an den lokalen Proxy weiter."""
    if not auth_header:
        key = get_9router_api_key()
        if key:
            auth_header = f"Bearer {key}"

    headers = {
        "Content-Type": "application/json"
    }
    if auth_header:
        headers["Authorization"] = auth_header

    payload = {
        "model": model,
        "messages": messages,
        "stream": False
    }

    req = urllib.request.Request(
        "http://127.0.0.1:20128/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            resp_bytes = resp.read()
            resp_text = resp_bytes.decode("utf-8", errors="replace")
            try:
                res_json = json.loads(resp_text)
            except Exception:
                res_json = parse_sse_completion(resp_text)

            choices = res_json.get("choices", [])
            assistant_msg = choices[0].get("message") if choices else None
            if not assistant_msg:
                delta = choices[0].get("delta", {}) if choices else {}
                assistant_msg = {"role": "assistant", "content": delta.get("content", "")}

            return {
                "message": assistant_msg,
                "content": assistant_msg.get("content", ""),
                "role": assistant_msg.get("role", "assistant"),
                "model": res_json.get("model", model),
                "choices": choices,
                "usage": res_json.get("usage", {}),
                "raw": res_json
            }, 200

    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_body)
        except Exception:
            err_json = {"error": err_body}
        return {
            "error": f"Upstream proxy HTTP error: {e.code}",
            "message": {"role": "assistant", "content": f"[LCARS FEHLER] Der KI-Proxy meldet Fehler {e.code}."},
            "details": err_json
        }, e.code

    except Exception as e:
        return {
            "error": "Upstream proxy offline or unreachable",
            "message": {"role": "assistant", "content": f"[LCARS FEHLER] Verbindung zum KI-Proxy fehlgeschlagen: {str(e)}"},
            "details": str(e)
        }, 502


# ---------------------------------------------------------------------------
# Star Trek LCARS Fullscreen Dashboard UI (HTML, CSS & JavaScript)
# Vorlage: https://www.thelcars.com/
# ---------------------------------------------------------------------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="de" data-theme="classic">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="format-detection" content="telephone=no, date=no">
  <title>SYSTEM DASHBOARD // {{ stats.hostname }}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Antonio:wght@400;600;700&family=Bebas+Neue&family=Share+Tech+Mono&display=swap" rel="stylesheet">
  <script src="/static/chart.umd.min.js"></script>
  <script>
    if (typeof Chart === 'undefined') {
      document.write('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"><' + '/script>');
    }
  </script>
  <style>
    /* ==========================================================================
       LCARS THEME PALETTES & CSS VARIABLES
       ========================================================================== */
    :root {
      --bg: #000000;
      --font-family: 'Antonio', 'Arial Narrow', sans-serif;
      --mono-family: 'Share Tech Mono', monospace;
      --lfw: 200px;
      --elbow-radius: 0 0 0 110px;
      --elbow-radius-bottom: 110px 0 0 0;
      --corner-cutout: 0 0 0 45px;
      --corner-cutout-bottom: 45px 0 0 0;
      --bar-height: 24px;
      --pill-height: 54px;

      /* Classic TNG Theme (Default) */
      --c-primary: #eb943a;       /* Okuda Orange */
      --c-secondary: #baa4e5;     /* African Violet / Lilac */
      --c-blue: #8899ff;          /* Bluey */
      --c-almond: #d29b7f;        /* Almond */
      --c-butterscotch: #ea9c72;  /* Butterscotch */
      --c-red: #cf4f4f;           /* Mars Red / Danger */
      --c-gold: #edb378;          /* Barley Gold */
      --c-text: #ffffff;
      --c-card-bg: rgba(18, 18, 26, 0.92);
      --c-card-border: rgba(235, 148, 58, 0.35);

      /* UI element assignments */
      --elbow-top: var(--c-blue);
      --elbow-bottom: var(--c-almond);
      --banner-color: var(--c-primary);
      --data-cascade-color: var(--c-primary);
      --nav-active: #ffffff;
    }

    /* Nemesis Blue Tactical Theme */
    [data-theme="nemesis"] {
      --c-primary: #6699ff;
      --c-secondary: #88bbff;
      --c-blue: #0048f0;
      --c-almond: #ff8833;
      --c-butterscotch: #2266ff;
      --c-red: #da2537;
      --c-gold: #ebf0ff;
      --elbow-top: #6699ff;
      --elbow-bottom: #0048f0;
      --banner-color: #88bbff;
      --data-cascade-color: #6699ff;
      --c-card-border: rgba(102, 153, 255, 0.4);
    }

    /* Lower Decks Warm Theme */
    [data-theme="lowerdecks"] {
      --c-primary: #ffaa44;
      --c-secondary: #ff7700;
      --c-blue: #ffcc99;
      --c-almond: #ffeecc;
      --c-butterscotch: #ff9911;
      --c-red: #ff4400;
      --c-gold: #faad44;
      --elbow-top: #ffaa44;
      --elbow-bottom: #ff7700;
      --banner-color: #ffaa44;
      --data-cascade-color: #ffaa44;
      --c-card-border: rgba(255, 170, 68, 0.4);
    }

    /* Lower Decks PADD Theme */
    [data-theme="lowerdecks-padd"] {
      --c-primary: #5588ee;
      --c-secondary: #66ccff;
      --c-blue: #7799dd;
      --c-almond: #88eeff;
      --c-butterscotch: #455580;
      --c-red: #ff3500;
      --c-gold: #f3f3fc;
      --elbow-top: #5588ee;
      --elbow-bottom: #344470;
      --banner-color: #66ccff;
      --data-cascade-color: #5588ee;
      --c-card-border: rgba(85, 136, 238, 0.4);
    }

    /* Picard 25th Century Theme */
    [data-theme="picard"] {
      --c-primary: #37a6d1;
      --c-secondary: #41c4f7;
      --c-blue: #2a7193;
      --c-almond: #9ea5ba;
      --c-butterscotch: #ff6753;
      --c-red: #e7442a;
      --c-gold: #f3f4f7;
      --elbow-top: #37a6d1;
      --elbow-bottom: #1c3c55;
      --banner-color: #41c4f7;
      --data-cascade-color: #37a6d1;
      --c-card-border: rgba(55, 166, 209, 0.4);
    }

    /* Voyager Theme */
    [data-theme="voyager"] {
      --c-primary: #55a7ff;
      --c-secondary: #fb9004;
      --c-blue: #2288ff;
      --c-almond: #ffe1ca;
      --c-butterscotch: #ffbb33;
      --c-red: #ff3300;
      --c-gold: #94b300;
      --elbow-top: #828cad;
      --elbow-bottom: #74788b;
      --banner-color: #55a7ff;
      --data-cascade-color: #55a7ff;
      --c-card-border: rgba(85, 167, 255, 0.4);
    }

    /* Red Alert Combat Theme (Automatisch bei Alarm) */
    [data-theme="redalert"] {
      --c-primary: #cf3030;
      --c-secondary: #ff4444;
      --c-blue: #ffaa00;
      --c-almond: #881111;
      --c-butterscotch: #ff2222;
      --c-red: #ff0000;
      --c-gold: #ffcc00;
      --elbow-top: #cf3030;
      --elbow-bottom: #881111;
      --banner-color: #ff4444;
      --data-cascade-color: #ff3333;
      --c-card-border: rgba(207, 48, 48, 0.65);
    }

    /* ==========================================================================
       GLOBAL LAYOUT (NO HORIZONTAL OVERFLOW)
       ========================================================================== */
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-tap-highlight-color: transparent;
    }
    html, body {
      width: 100%;
      max-width: 100vw;
      min-height: 100vh;
      background-color: var(--bg);
      color: var(--c-text);
      font-family: var(--font-family);
      font-size: 1.15rem;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      overflow-x: hidden;
    }
    body {
      display: flex;
      flex-direction: column;
      padding: 0.35rem 0.6rem;
    }

    .wrap-standard {
      width: 100%;
      max-width: 100%;
      min-height: 98vh;
      display: flex;
      flex-direction: column;
      position: relative;
      overflow-x: hidden;
    }
    .wrap {
      display: flex;
      width: 100%;
      max-width: 100%;
      position: relative;
      box-sizing: border-box;
      overflow-x: hidden;
    }

    /* ==========================================================================
       UPPER FRAME & ELBOW
       ========================================================================== */
    .left-frame-top {
      width: var(--lfw);
      flex-shrink: 0;
      display: flex;
      flex-direction: column;
      background-color: var(--elbow-top);
      border-radius: var(--elbow-radius);
      padding: 0.5rem;
      text-align: right;
      color: #000;
      font-weight: 700;
      justify-content: space-between;
      min-height: 120px;
    }
    .left-frame-top button {
      background: transparent;
      border: none;
      color: #000;
      font-family: var(--font-family);
      font-size: 1.35rem;
      font-weight: 700;
      text-transform: uppercase;
      text-align: right;
      cursor: pointer;
      line-height: 1.1;
      padding: 0;
    }

    /* Red Alert Alarm Banner */
    .red-alert-banner {
      background: linear-gradient(90deg, #cf3030 0%, #ff0000 50%, #cf3030 100%);
      color: #fff;
      padding: 0.55rem 1rem;
      border-radius: 6px;
      margin-bottom: 0.85rem;
      box-shadow: 0 0 20px rgba(255, 0, 0, 0.65);
      animation: alert-pulse 1.2s infinite alternate;
      border: 2px solid #ff4444;
      font-family: var(--font-family);
      font-weight: 800;
      letter-spacing: 1px;
    }
    @keyframes alert-pulse {
      0% { opacity: 0.85; filter: brightness(1); }
      100% { opacity: 1; filter: brightness(1.35); }
    }
    .red-alert-content {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .red-alert-icon {
      font-size: 1.4rem;
      animation: icon-bounce 0.6s infinite alternate;
    }
    @keyframes icon-bounce {
      from { transform: scale(1); }
      to { transform: scale(1.25); }
    }
    .red-alert-title {
      font-size: 1.1rem;
      text-transform: uppercase;
      color: #fff;
    }
    .red-alert-reasons {
      font-family: var(--mono-family);
      font-size: 0.92rem;
      background: rgba(0, 0, 0, 0.4);
      padding: 0.2rem 0.6rem;
      border-radius: 4px;
      color: #ffea00;
    }

    /* LCARS Input styling */
    .lcars-input {
      width: 100%;
      background: #000;
      color: #fff;
      border: 1px solid var(--c-primary);
      border-radius: 4px;
      padding: 0.45rem 0.65rem;
      font-family: var(--mono-family);
      font-size: 0.92rem;
      box-sizing: border-box;
      outline: none;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }
    .lcars-input:focus {
      border-color: #fff;
      box-shadow: 0 0 8px var(--c-primary);
    }

    .right-frame-top {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      position: relative;
      overflow: hidden;
    }
    .right-frame-top::before {
      content: '';
      display: block;
      width: 45px;
      height: 45px;
      background-color: var(--elbow-top);
      position: absolute;
      left: 0;
      bottom: var(--bar-height);
      z-index: 1;
    }
    .right-frame-top::after {
      content: '';
      display: block;
      width: 45px;
      height: 45px;
      background-color: #000;
      border-radius: var(--corner-cutout);
      position: absolute;
      left: 0;
      bottom: var(--bar-height);
      z-index: 2;
    }

    .banner-container {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      padding-bottom: 0.35rem;
      padding-left: 55px;
      flex-wrap: wrap;
      gap: 0.4rem;
      max-width: 100%;
    }
    .banner-title {
      font-size: clamp(1.2rem, 2.4vw, 2.2rem);
      font-weight: 700;
      color: var(--banner-color);
      line-height: 1;
      letter-spacing: 0.08em;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .banner-stardate {
      font-family: var(--mono-family);
      font-size: 1.1rem;
      color: var(--c-gold);
      display: flex;
      align-items: center;
      gap: 0.4rem;
      white-space: nowrap;
    }

    .data-cascade-bar {
      display: flex;
      justify-content: flex-end;
      align-items: center;
      gap: 0.5rem;
      padding-bottom: 0.35rem;
      overflow: hidden;
    }
    .top-vitals-cascade {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      user-select: none;
    }
    .top-vital-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.32rem;
      padding: 0.2rem 0.55rem;
      background: rgba(0, 0, 0, 0.45);
      border: 1px solid rgba(255, 255, 255, 0.2);
      border-radius: 12px;
      font-weight: 700;
      color: var(--data-cascade-color);
      cursor: pointer;
      white-space: nowrap;
      transition: background-color 0.2s, border-color 0.2s, transform 0.15s;
    }
    .top-vital-badge:hover {
      background: rgba(255, 255, 255, 0.12);
      transform: translateY(-1px);
    }
    .vital-badge-icon {
      font-size: 0.76rem;
      line-height: 1;
    }
    .vital-badge-label {
      font-family: var(--font-family);
      font-size: 0.72rem;
      letter-spacing: 0.5px;
      opacity: 0.85;
    }
    .vital-badge-num {
      font-family: var(--mono-family);
      font-weight: 800;
      font-size: 0.82rem;
      letter-spacing: 0.3px;
    }

    /* Pulsieren wie die bisherigen Fake-Zahlen (authentischer LCARS Telemetrie-Blink) */
    .dc-pulse-1 { animation: vital-dc-blink 2.2s infinite; }
    .dc-pulse-2 { animation: vital-dc-blink 3.1s infinite 0.5s; }
    .dc-pulse-3 { animation: vital-dc-blink 2.6s infinite 1.0s; }
    .dc-pulse-4 { animation: vital-dc-blink 2.0s infinite 1.5s; }
    .dc-pulse-5 { animation: vital-dc-blink 2.8s infinite 0.7s; }
    .dc-pulse-6 { animation: vital-dc-blink 2.4s infinite 1.2s; }

    @keyframes vital-dc-blink {
      0%, 100% {
        opacity: 0.95;
        color: var(--c-primary);
        border-color: rgba(255, 170, 0, 0.45);
      }
      50% {
        opacity: 0.45;
        color: var(--c-secondary);
        border-color: rgba(255, 170, 0, 0.12);
      }
      75% {
        opacity: 1;
        color: #ffffff;
        border-color: rgba(255, 255, 255, 0.5);
      }
    }

    /* Roter Alarm Modus für Badges */
    .top-vital-badge.vital-alert {
      background: rgba(207, 48, 48, 0.45) !important;
      border-color: #ff3333 !important;
      color: #ffffff !important;
      animation: vital-alarm-flash 0.7s infinite alternate !important;
    }
    @keyframes vital-alarm-flash {
      from {
        opacity: 0.8;
        border-color: #cf3030;
        box-shadow: 0 0 5px rgba(255, 0, 0, 0.5);
      }
      to {
        opacity: 1;
        border-color: #ffffff;
        box-shadow: 0 0 14px rgba(255, 0, 0, 0.9);
      }
    }

    /* Segmentierte Balken */
    .bar-panel {
      display: flex;
      height: var(--bar-height);
      gap: 4px;
      width: 100%;
      position: relative;
      z-index: 3;
    }
    .bar-1 { width: 38%; background-color: var(--elbow-top); }
    .bar-2 { width: 5%; background-color: var(--c-primary); }
    .bar-3 { width: 18%; background-color: var(--c-secondary); }
    .bar-4 { flex: 1; background-color: var(--c-butterscotch); }
    .bar-5 { width: 6%; background-color: var(--c-red); }

    /* ==========================================================================
       MIDDLE FRAME: LEFT CONTROL PILLAR & MAIN
       ========================================================================== */
    .gap-wrap {
      margin-top: 4px;
      flex: 1;
      display: flex;
      min-width: 0;
    }
    .left-frame {
      width: var(--lfw);
      flex-shrink: 0;
      background-color: var(--elbow-bottom);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: relative;
      padding-top: 4px;
    }

    /* 4 Haupt-Steuerungsbuttons (OHNE Nummern / Slashes) */
    .nav-pillar {
      display: flex;
      flex-direction: column;
      gap: 4px;
      width: 100%;
    }
    .lcars-pill-btn {
      display: flex;
      justify-content: flex-end;
      align-items: center;
      width: 100%;
      height: var(--pill-height);
      padding: 0.5rem 0.8rem;
      border: none;
      outline: none;
      background-color: var(--c-primary);
      color: #000;
      font-family: var(--font-family);
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      text-align: right;
      white-space: nowrap;
      cursor: pointer;
      position: relative;
      transition: filter 0.15s ease, background-color 0.15s ease, color 0.15s ease;
      user-select: none;
    }
    .lcars-pill-btn:hover {
      filter: brightness(1.2);
    }
    .lcars-pill-btn:active {
      filter: brightness(0.85);
    }
    .lcars-pill-btn.active {
      background-color: #ffffff !important;
      color: #000000 !important;
      box-shadow: inset 0 0 12px rgba(235, 148, 58, 0.4);
    }
    .lcars-pill-btn.active::before {
      content: '▶ ';
      color: var(--c-red);
      font-size: 0.9rem;
      margin-right: 0.4rem;
    }
    .pill-sys   { background-color: var(--c-primary); }
    .pill-srv   { background-color: var(--c-blue); }
    .pill-ai    { background-color: var(--c-secondary); }
    .pill-info  { background-color: var(--c-butterscotch); }
    .pill-cfg   { background-color: var(--c-gold); }
    .pill-fantasy { background-color: var(--c-almond); }
    .pill-solar { background-color: var(--c-gold); color: #000; }
    .pill-ha { background-color: var(--c-secondary); }
    .pill-cycle { background-color: var(--c-secondary); color: #000; }
    .pill-pulsecast { background-color: var(--c-butterscotch); color: #000; }
    .pill-gemini-live { background-color: var(--c-secondary); color: #000; font-weight: 700; }
    .pill-pimmel { background-color: #8899ff; color: #000; font-weight: 700; }
    .pill-auth  { background-color: var(--c-almond); color: #000; }

    /* Gemini 3.8 Live & Audio Visualizer Styling */
    .lcars-meter-seg { flex: 1; border-radius: 2px; background: rgba(255,255,255,0.06); transition: background 0.06s ease; }
    .lcars-meter-seg.active-low { background: #44dd88; box-shadow: 0 0 6px rgba(68,221,136,0.6); }
    .lcars-meter-seg.active-mid { background: var(--c-gold); box-shadow: 0 0 6px rgba(237,179,120,0.6); }
    .lcars-meter-seg.active-high { background: var(--c-red); box-shadow: 0 0 8px rgba(207,79,79,0.8); }
    @keyframes lcarsPulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.3; transform: scale(0.85); } }

    /* PulseCast Media & Downloads Styling */
    .pulsecast-catalog-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(185px, 1fr));
      gap: 1rem;
    }
    .pulsecast-card {
      background: rgba(0, 0, 0, 0.55);
      border: 1px solid rgba(235, 148, 58, 0.35);
      border-radius: 6px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
    }
    .pulsecast-card:hover {
      transform: translateY(-2px);
      border-color: var(--c-primary);
      box-shadow: 0 4px 16px rgba(235, 148, 58, 0.2);
    }
    .pulsecast-card-poster {
      width: 100%;
      aspect-ratio: 2 / 3;
      object-fit: cover;
      background: #111;
      display: block;
    }
    .pulsecast-card-body {
      padding: 0.75rem;
      display: flex;
      flex-direction: column;
      flex: 1;
      justify-content: space-between;
    }
    .pulsecast-progress-container {
      background: rgba(255, 255, 255, 0.08);
      border-radius: 4px;
      height: 14px;
      position: relative;
      overflow: hidden;
      margin: 0.4rem 0;
      border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .pulsecast-progress-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--c-primary), var(--c-gold));
      border-radius: 3px;
      transition: width 0.3s ease;
    }
    .lcars-tag-btn {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.2);
      border-radius: 12px;
      color: #ddd;
      font-family: var(--mono-family);
      font-size: 0.78rem;
      padding: 0.2rem 0.6rem;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .lcars-tag-btn:hover {
      background: rgba(255, 255, 255, 0.2);
      color: #fff;
      border-color: var(--c-primary);
    }


    /* Command Code Keypad & Rights Management */
    .keypad-btn {
      background: rgba(235, 148, 58, 0.15);
      border: 1px solid var(--c-primary);
      border-radius: 4px;
      color: #ffffff;
      font-family: var(--mono-family);
      font-size: 1.25rem;
      font-weight: 700;
      padding: 0.6rem 0.4rem;
      cursor: pointer;
      transition: all 0.15s ease;
      text-align: center;
      user-select: none;
    }
    .keypad-btn:hover {
      background: var(--c-primary);
      color: #000000;
    }
    .keypad-btn:active {
      transform: scale(0.95);
    }
    .keypad-btn.keypad-special {
      font-size: 0.85rem;
      background: rgba(255, 255, 255, 0.1);
      border-color: #888888;
    }
    .keypad-btn.keypad-special:hover {
      background: #ffffff;
      color: #000000;
    }
    .perm-checkbox-card {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 6px;
      padding: 0.75rem;
      cursor: pointer;
      transition: border-color 0.2s, background 0.2s;
    }
    .perm-checkbox-card:hover {
      background: rgba(255, 255, 255, 0.05);
      border-color: var(--c-primary);
    }
    .perm-checkbox-card input[type="checkbox"] {
      width: 1.3rem;
      height: 1.3rem;
      accent-color: var(--c-primary);
      cursor: pointer;
    }
    .perm-card-info {
      display: flex;
      flex-direction: column;
    }
    .perm-name {
      font-family: var(--font-family);
      font-weight: 700;
      font-size: 1.05rem;
      letter-spacing: 0.05em;
      color: var(--c-text);
    }
    .perm-desc {
      font-size: 0.75rem;
      color: #888888;
    }

    /* Cycle Tracker UI */
    .cycle-tape-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(28px, 1fr));
      gap: 4px;
      margin-bottom: 0.5rem;
    }
    .cycle-tape-cell {
      height: 38px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      border-radius: 4px;
      font-family: var(--mono-family);
      font-size: 0.75rem;
      cursor: pointer;
      user-select: none;
      transition: all 0.15s ease;
      color: #000;
      font-weight: 700;
      border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .cycle-tape-cell:hover {
      filter: brightness(1.3);
      transform: translateY(-2px);
    }
    .cycle-tape-cell.current-day {
      border: 2px solid #ffffff !important;
      box-shadow: 0 0 12px rgba(255, 255, 255, 0.8), inset 0 0 6px rgba(255, 255, 255, 0.5);
      animation: pulseHighlight 1.5s infinite alternate;
      color: #000000 !important;
      font-size: 0.85rem !important;
    }
    @keyframes pulseHighlight {
      0% { box-shadow: 0 0 8px rgba(255, 255, 255, 0.7); filter: brightness(1.1); }
      100% { box-shadow: 0 0 18px rgba(255, 255, 255, 1.0); filter: brightness(1.4); }
    }

    /* Solar LCARS UI */
    .solar-flow-card {
      background: rgba(0, 0, 0, 0.45);
      border: 1px solid var(--c-card-border);
      border-left: 6px solid var(--c-gold);
      border-radius: 8px;
      padding: 1.1rem;
      margin-bottom: 1.25rem;
    }
    .solar-flow-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 0.9rem;
      align-items: stretch;
      margin-top: 0.8rem;
    }
    .flow-node {
      background: rgba(15, 15, 20, 0.75);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 6px;
      padding: 0.9rem;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: relative;
      transition: all 0.2s ease;
    }
    .flow-node:hover {
      transform: translateY(-2px);
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
    }
    .flow-node.node-solar { border-top: 3px solid var(--c-gold); }
    .flow-node.node-bat { border-top: 3px solid var(--c-secondary); }
    .flow-node.node-house { border-top: 3px solid var(--c-primary); }
    .flow-node.node-grid { border-top: 3px solid var(--c-blue); }
    .flow-node-title {
      font-size: 0.8rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 0.5rem;
    }
    .flow-node-val {
      font-family: var(--mono-family);
      font-size: 1.6rem;
      font-weight: 800;
      margin-bottom: 0.25rem;
    }
    .flow-node-sub {
      font-size: 0.76rem;
      color: #aaa;
      font-family: var(--mono-family);
    }
    .solar-detail-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.38rem 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      font-size: 0.82rem;
    }
    .solar-detail-label {
      color: #999;
      text-transform: uppercase;
      font-size: 0.76rem;
      letter-spacing: 0.04em;
    }
    .solar-detail-val {
      font-family: var(--mono-family);
      font-weight: 700;
      color: #eee;
    }

    /* Home Assistant LCARS UI */
    .ha-room-card {
      background: var(--c-card-bg);
      border: 1px solid var(--c-card-border);
      border-left: 6px solid var(--c-secondary);
      border-radius: 8px;
      padding: 1.1rem;
      transition: all 0.2s ease;
    }
    .ha-room-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.9rem;
      padding-bottom: 0.5rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .ha-room-title {
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--c-secondary);
      letter-spacing: 0.05em;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      text-transform: uppercase;
    }
    .ha-entities-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
      gap: 0.85rem;
    }
    .ha-entity-tile {
      background: rgba(0, 0, 0, 0.55);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 6px;
      padding: 0.75rem;
      cursor: pointer;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 92px;
      transition: all 0.15s ease;
      position: relative;
    }
    .ha-entity-tile:hover {
      border-color: var(--c-primary);
      background: rgba(235, 148, 58, 0.08);
      transform: translateY(-2px);
    }
    .ha-entity-tile.entity-active {
      border-color: var(--c-primary);
      box-shadow: inset 0 0 10px rgba(235, 148, 58, 0.25);
    }
    .ha-tile-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 0.4rem;
    }
    .ha-tile-name {
      font-size: 0.95rem;
      font-weight: 700;
      color: #fff;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 155px;
    }
    .ha-tile-id {
      font-size: 0.72rem;
      color: #888;
      font-family: var(--mono-family);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 155px;
    }
    .ha-tile-bottom {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 0.5rem;
      gap: 0.4rem;
    }
    .ha-tile-state-badge {
      font-size: 0.75rem;
      font-family: var(--mono-family);
      font-weight: 700;
      padding: 0.15rem 0.5rem;
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.1);
      color: #bbb;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .ha-tile-state-badge.state-on {
      background: rgba(68, 221, 136, 0.2);
      color: #44dd88;
      border: 1px solid rgba(68, 221, 136, 0.4);
    }
    .ha-tile-state-badge.state-active {
      background: rgba(235, 148, 58, 0.2);
      color: var(--c-primary);
      border: 1px solid rgba(235, 148, 58, 0.4);
    }
    .ha-tile-toggle-btn {
      background: rgba(255, 255, 255, 0.1);
      border: 1px solid rgba(255, 255, 255, 0.25);
      color: #fff;
      border-radius: 4px;
      padding: 0.25rem 0.6rem;
      font-size: 0.75rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.15s ease;
      font-family: var(--font-family);
      letter-spacing: 0.04em;
    }
    .ha-tile-toggle-btn:hover {
      background: var(--c-primary);
      color: #000;
      border-color: var(--c-primary);
    }
    .ha-tile-toggle-btn.btn-active {
      background: #44dd88;
      color: #000;
      border-color: #44dd88;
    }

    /* Modal Overlay & Card */
    .ha-modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      background: rgba(0, 0, 0, 0.8);
      backdrop-filter: blur(5px);
      z-index: 99999;
      display: flex;
      justify-content: center;
      align-items: center;
      padding: 1rem;
    }
    .ha-modal-content {
      background: #0f1118;
      border: 2px solid var(--c-secondary);
      border-radius: 12px;
      width: 100%;
      max-width: 520px;
      box-shadow: 0 0 35px rgba(0, 0, 0, 0.95), 0 0 15px rgba(186, 164, 229, 0.3);
      overflow: hidden;
      animation: haModalIn 0.18s ease-out;
    }
    @keyframes haModalIn {
      from { transform: scale(0.95); opacity: 0; }
      to { transform: scale(1); opacity: 1; }
    }
    .ha-modal-header {
      background: var(--c-secondary);
      padding: 0.75rem 1rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .ha-modal-close-btn {
      background: #000;
      color: var(--c-secondary);
      border: 1px solid #000;
      padding: 0.3rem 0.7rem;
      border-radius: 4px;
      font-weight: 700;
      font-size: 0.82rem;
      cursor: pointer;
      font-family: var(--font-family);
      letter-spacing: 0.05em;
    }
    .ha-modal-close-btn:hover {
      background: #fff;
      color: #000;
    }
    .ha-modal-body {
      padding: 1.25rem;
      max-height: 75vh;
      overflow-y: auto;
    }
    .ha-ctrl-row {
      margin-bottom: 1.2rem;
    }
    .ha-ctrl-label {
      font-size: 0.85rem;
      font-weight: 700;
      color: var(--c-gold);
      margin-bottom: 0.4rem;
      display: flex;
      justify-content: space-between;
    }
    .ha-slider {
      width: 100%;
      height: 10px;
      border-radius: 5px;
      background: #222634;
      outline: none;
      -webkit-appearance: none;
      cursor: pointer;
    }
    .ha-slider::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 22px;
      height: 22px;
      border-radius: 50%;
      background: var(--c-primary);
      cursor: pointer;
      box-shadow: 0 0 8px var(--c-primary);
    }
    .ha-color-circle {
      width: 32px;
      height: 32px;
      border-radius: 50%;
      border: 2px solid rgba(255, 255, 255, 0.3);
      cursor: pointer;
      transition: transform 0.15s ease;
    }
    .ha-color-circle:hover {
      transform: scale(1.18);
      border-color: #fff;
    }

    @keyframes pulseScoreGain {
      0% {
        background-color: rgba(68, 221, 136, 0.28);
        box-shadow: inset 0 0 14px rgba(68, 221, 136, 0.5);
      }
      50% {
        background-color: rgba(68, 221, 136, 0.08);
        box-shadow: inset 0 0 4px rgba(68, 221, 136, 0.2);
      }
      100% {
        background-color: rgba(68, 221, 136, 0.28);
        box-shadow: inset 0 0 14px rgba(68, 221, 136, 0.5);
      }
    }
    .player-scored-highlight {
      animation: pulseScoreGain 2.4s infinite ease-in-out !important;
      border-left: 4px solid #44dd88 !important;
    }

    .left-frame-lower {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin-top: auto;
    }
    .left-action-btn {
      display: flex;
      justify-content: flex-end;
      align-items: center;
      gap: 0.4rem;
      min-height: 40px;
      padding: 0.4rem 0.8rem;
      background-color: #000;
      border: 2px solid var(--elbow-bottom);
      color: var(--elbow-bottom);
      font-family: var(--font-family);
      font-size: 0.95rem;
      font-weight: 700;
      cursor: pointer;
      text-transform: uppercase;
      transition: all 0.15s ease;
      word-break: break-all;
    }
    .left-action-btn:hover {
      background-color: var(--elbow-bottom);
      color: #000;
    }
    .left-elbow-bottom {
      height: 75px;
      background-color: var(--elbow-bottom);
      border-radius: var(--elbow-radius-bottom);
      display: flex;
      align-items: flex-end;
      justify-content: flex-end;
      padding: 0.5rem;
      color: #000;
      font-weight: 700;
      font-size: 0.95rem;
    }

    /* ==========================================================================
       MAIN CONTENT CONTAINER (RESPONSIVE, KEIN HORIZONTALER SCROLL)
       ========================================================================== */
    .right-frame {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      position: relative;
      padding-left: 0;
      background: transparent;
      overflow-x: hidden;
    }
    .right-frame::after {
      content: '';
      display: block;
      width: 45px;
      height: 45px;
      background-color: var(--elbow-bottom);
      position: absolute;
      left: 0;
      bottom: var(--bar-height);
      z-index: 1;
    }
    .right-frame-inner-corner {
      width: 45px;
      height: 45px;
      background-color: #000;
      border-radius: var(--corner-cutout-bottom);
      position: absolute;
      left: 0;
      bottom: var(--bar-height);
      z-index: 2;
    }

    main {
      flex: 1;
      width: 100%;
      min-width: 0;
      padding: 0.8rem 1rem 2rem clamp(0.8rem, 2vw, 2rem);
      overflow-y: auto;
      overflow-x: hidden;
      max-height: calc(100vh - 170px);
      box-sizing: border-box;
    }

    .lcars-section {
      display: none;
      animation: lcars-fade 0.15s ease-in;
      width: 100%;
      min-width: 0;
      box-sizing: border-box;
    }
    .lcars-section.active-section {
      display: block;
    }
    @keyframes lcars-fade {
      from { opacity: 0; transform: translateY(3px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .lcars-header-bar {
      display: flex;
      align-items: center;
      gap: 0.8rem;
      margin-bottom: 1.1rem;
      border-bottom: 2px solid var(--c-primary);
      padding-bottom: 0.35rem;
      flex-wrap: wrap;
    }
    .lcars-header-bar h2 {
      font-size: 1.45rem;
      color: var(--c-primary);
      font-weight: 700;
      letter-spacing: 0.08em;
    }
    .lcars-pill-tag {
      background-color: var(--c-secondary);
      color: #000;
      padding: 0.15rem 0.6rem;
      border-radius: 100vmax;
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.05em;
    }

    /* Grids & Cards (Fully Responsive) */
    .readout-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 0.85rem;
      margin-bottom: 1.25rem;
      width: 100%;
      min-width: 0;
      box-sizing: border-box;
    }
    .lcars-card {
      background-color: var(--c-card-bg);
      border-left: 6px solid var(--c-primary);
      border-top: 1px solid var(--c-card-border);
      border-right: 1px solid var(--c-card-border);
      border-bottom: 1px solid var(--c-card-border);
      border-radius: 0 12px 12px 0;
      padding: 1rem;
      position: relative;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-width: 0;
      overflow: hidden;
      box-sizing: border-box;
    }
    .lcars-card.card-violet { border-left-color: var(--c-secondary); }
    .lcars-card.card-blue { border-left-color: var(--c-blue); }
    .lcars-card.card-almond { border-left-color: var(--c-almond); }
    .lcars-card.card-red { border-left-color: var(--c-red); }

    .card-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.5rem;
    }
    .card-head-title {
      font-size: 0.95rem;
      color: var(--c-secondary);
      font-weight: 700;
      letter-spacing: 0.06em;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .card-head-icon {
      font-size: 1.25rem;
      flex-shrink: 0;
    }
    .card-metric {
      font-size: 2.2rem;
      font-weight: 700;
      color: #fff;
      line-height: 1;
      margin-bottom: 0.35rem;
    }
    .card-metric-sub {
      font-size: 0.86rem;
      color: var(--c-gold);
      margin-bottom: 0.65rem;
      font-family: var(--mono-family);
      word-break: break-word;
    }

    .lcars-bar-track {
      width: 100%;
      height: 12px;
      background-color: #11141f;
      border-radius: 100vmax;
      overflow: hidden;
      border: 1px solid rgba(255, 255, 255, 0.15);
      position: relative;
    }
    .lcars-bar-fill {
      height: 100%;
      background-color: var(--c-primary);
      border-radius: 100vmax;
      transition: width 0.4s ease;
    }

    .badge-status {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.15rem 0.55rem;
      border-radius: 100vmax;
      font-size: 0.78rem;
      font-weight: 700;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .badge-online { background-color: #059669; color: #fff; }
    .badge-offline { background-color: #dc2626; color: #fff; }
    .badge-warn { background-color: #d97706; color: #fff; }

    /* URL Action Buttons in Cards */
    .card-action-links {
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
      margin-top: 0.6rem;
      width: 100%;
      min-width: 0;
    }
    .url-chip-btn {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.4rem 0.65rem;
      background: rgba(0, 0, 0, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.2);
      border-radius: 6px;
      color: #fff;
      text-decoration: none;
      font-family: var(--mono-family);
      font-size: 0.84rem;
      word-break: break-all;
      overflow-wrap: anywhere;
      transition: all 0.15s ease;
    }
    .url-chip-btn:hover {
      border-color: var(--c-primary);
      background: rgba(235, 148, 58, 0.2);
      color: #fff;
    }
    .url-chip-btn.chip-ts {
      border-color: rgba(186, 164, 229, 0.3);
      color: var(--c-secondary);
    }
    .url-chip-btn.chip-ts:hover {
      background: rgba(186, 164, 229, 0.2);
      color: #fff;
    }
    .url-chip-btn.chip-cf {
      border-color: rgba(235, 148, 58, 0.4);
      color: var(--c-primary);
    }
    .url-chip-btn.chip-cf:hover {
      background: rgba(235, 148, 58, 0.25);
      color: #fff;
    }

    .cmd-text-box {
      font-family: var(--mono-family);
      font-size: 0.76rem;
      color: rgba(255, 255, 255, 0.65);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin: 0.2rem 0 0.4rem 0;
      max-width: 100%;
    }

    .card-service-footer {
      display: flex;
      justify-content: flex-end;
      align-items: center;
      margin-top: 0.65rem;
      padding-top: 0.45rem;
      border-top: 1px solid rgba(255, 255, 255, 0.08);
    }
    .system-kernel-badge {
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      color: var(--c-gold);
      padding: 0.2rem 0.55rem;
      border: 1px solid rgba(255, 170, 68, 0.3);
      border-radius: 6px;
      background: rgba(255, 170, 68, 0.08);
    }
    .btn-stop-service {
      background-color: rgba(207, 48, 48, 0.15);
      color: var(--c-red);
      border: 1px solid var(--c-red);
      font-family: inherit;
      font-size: 0.74rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      padding: 0.25rem 0.65rem;
      border-radius: 10px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      transition: all 0.15s ease;
    }
    .btn-stop-service:hover {
      background-color: var(--c-red);
      color: #000;
      transform: scale(1.02);
    }

    /* Scanner Header Bar */
    .scanner-dashboard-box {
      background: rgba(20, 20, 30, 0.85);
      border: 2px solid var(--c-primary);
      border-radius: 12px;
      padding: 1rem 1.25rem;
      margin-bottom: 1.25rem;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 0.8rem;
      width: 100%;
      min-width: 0;
      box-sizing: border-box;
    }
    .scanner-telemetry-col {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
      min-width: 140px;
    }
    .scanner-telemetry-col strong {
      color: var(--c-primary);
      font-size: 1.15rem;
    }
    .scanner-telemetry-col span {
      font-family: var(--mono-family);
      font-size: 0.92rem;
      color: #e2e8f0;
    }
    .btn-scan-trigger {
      background-color: var(--c-primary);
      color: #000;
      font-family: var(--font-family);
      font-size: 1.15rem;
      font-weight: 700;
      padding: 0.6rem 1.4rem;
      border-radius: 100vmax;
      border: none;
      cursor: pointer;
      text-transform: uppercase;
      display: flex;
      align-items: center;
      gap: 0.4rem;
      transition: filter 0.15s ease, transform 0.1s ease;
    }
    .btn-scan-trigger:hover { filter: brightness(1.25); }
    /* ==========================================================================
       LCARS AI AGENT CHAT TERMINAL
       ========================================================================== */
    .lcars-chat-card {
      background: var(--c-card-bg);
      border: 2px solid var(--c-card-border);
      border-left: 8px solid var(--c-primary);
      border-radius: 12px;
      padding: 1.1rem;
      margin-top: 1.25rem;
      width: 100%;
      min-width: 0;
      box-sizing: border-box;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
    }
    .lcars-select-wrap {
      position: relative;
      display: inline-block;
      max-width: 100%;
    }
    .lcars-chat-select {
      background: #000;
      border: 2px solid var(--c-primary);
      color: #fff;
      font-family: var(--mono-family);
      font-size: 0.85rem;
      font-weight: 700;
      padding: 0.35rem 0.8rem;
      border-radius: 100vmax;
      outline: none;
      cursor: pointer;
      max-width: 260px;
      text-overflow: ellipsis;
      white-space: nowrap;
      overflow: hidden;
      transition: all 0.2s ease;
    }
    .lcars-chat-select:focus {
      box-shadow: 0 0 10px var(--c-primary);
    }
    .lcars-chat-select option {
      background: #11141f;
      color: #fff;
    }
    .lcars-chat-log {
      height: 540px;
      min-height: 420px;
      max-height: calc(100vh - 340px);
      overflow-y: auto;
      overflow-x: hidden;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      padding: 0.9rem;
      background: rgba(10, 12, 18, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 8px;
      margin: 0.75rem 0;
      scroll-behavior: smooth;
    }
    .lcars-chat-log::-webkit-scrollbar {
      width: 6px;
    }
    .lcars-chat-log::-webkit-scrollbar-track {
      background: rgba(0, 0, 0, 0.4);
    }
    .lcars-chat-log::-webkit-scrollbar-thumb {
      background: var(--c-primary);
      border-radius: 3px;
    }
    .lcars-msg {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      padding: 0.65rem 0.85rem;
      border-radius: 6px;
      max-width: 88%;
      word-break: break-word;
      overflow-wrap: anywhere;
      box-sizing: border-box;
      animation: lcarsMsgFadeIn 0.2s ease-out;
    }
    @keyframes lcarsMsgFadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .lcars-msg-user {
      align-self: flex-end;
      border-right: 4px solid var(--c-blue);
      border-left: none;
      background: rgba(136, 153, 255, 0.1);
    }
    .lcars-msg-user .lcars-msg-sender {
      color: var(--c-blue);
    }
    .lcars-msg-agent {
      align-self: flex-start;
      border-left: 4px solid var(--c-primary);
      background: rgba(235, 148, 58, 0.08);
    }
    .lcars-msg-agent .lcars-msg-sender {
      color: var(--c-primary);
    }
    .lcars-msg-error {
      align-self: flex-start;
      border-left: 4px solid var(--c-red);
      background: rgba(207, 79, 79, 0.12);
    }
    .lcars-msg-error .lcars-msg-sender {
      color: var(--c-red);
    }
    .lcars-msg-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.5rem;
      font-family: var(--font-family);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.07em;
      text-transform: uppercase;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      padding-bottom: 0.25rem;
    }
    .lcars-msg-time {
      color: rgba(255, 255, 255, 0.5);
      font-family: var(--mono-family);
      font-size: 0.74rem;
    }
    .lcars-msg-body {
      font-family: var(--mono-family);
      font-size: 0.9rem;
      line-height: 1.5;
      color: #f1f5f9;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    .lcars-msg-body pre {
      background: rgba(0, 0, 0, 0.85);
      border: 1px solid var(--c-primary);
      border-radius: 4px;
      padding: 0.5rem 0.75rem;
      margin: 0.5rem 0;
      overflow-x: auto;
      font-family: var(--mono-family);
      font-size: 0.82rem;
      color: #93c5fd;
      max-width: 100%;
      white-space: pre;
    }
    .lcars-msg-body code {
      font-family: var(--mono-family);
    }
    .lcars-inline-code {
      background: rgba(255, 255, 255, 0.12);
      color: var(--c-gold);
      padding: 0.1rem 0.35rem;
      border-radius: 3px;
      font-size: 0.85em;
    }
    .lcars-chat-loading {
      display: flex;
      align-items: center;
      gap: 0.65rem;
      padding: 0.55rem 0.85rem;
      background: rgba(235, 148, 58, 0.1);
      border: 1px dashed var(--c-primary);
      border-radius: 6px;
      font-family: var(--mono-family);
      font-size: 0.84rem;
      color: var(--c-primary);
      animation: lcarsBlinkAnim 1.2s infinite ease-in-out;
    }
    @keyframes lcarsBlinkAnim {
      0%, 100% { opacity: 1; border-color: var(--c-primary); }
      50% { opacity: 0.35; border-color: transparent; }
    }
    .lcars-loading-bars {
      display: inline-flex;
      gap: 3px;
      height: 14px;
      align-items: center;
    }
    .lcars-loading-bar {
      width: 4px;
      height: 6px;
      background-color: var(--c-primary);
      animation: lcarsBarScale 0.7s infinite alternate ease-in-out;
    }
    .lcars-loading-bar:nth-child(2) { animation-delay: 0.2s; background-color: var(--c-secondary); }
    .lcars-loading-bar:nth-child(3) { animation-delay: 0.4s; background-color: var(--c-gold); }
    @keyframes lcarsBarScale {
      0% { height: 4px; }
      100% { height: 14px; }
    }
    .lcars-chat-input-row {
      display: flex;
      gap: 0.5rem;
      width: 100%;
      min-width: 0;
      align-items: stretch;
    }
    .lcars-chat-input {
      width: 100%;
      min-width: 0;
      background: #080a10;
      border: 2px solid var(--c-card-border);
      color: #fff;
      font-family: var(--mono-family);
      font-size: 0.92rem;
      padding: 0.65rem 0.9rem;
      border-radius: 8px;
      outline: none;
      box-sizing: border-box;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }
    .lcars-chat-input:focus {
      border-color: var(--c-primary);
      box-shadow: 0 0 10px rgba(235, 148, 58, 0.35);
    }
    .lcars-chat-btn-send {
      background-color: var(--c-primary);
      color: #000;
      font-family: var(--font-family);
      font-size: 1.05rem;
      font-weight: 700;
      text-transform: uppercase;
      padding: 0.65rem 1.3rem;
      border-radius: 100vmax;
      border: none;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      white-space: nowrap;
      flex-shrink: 0;
      transition: all 0.15s ease;
    }
    .lcars-chat-btn-send:hover {
      filter: brightness(1.25);
      transform: scale(1.02);
    }
    .lcars-chat-btn-send:active {
      transform: scale(0.98);
    }
    .lcars-chat-btn-send:disabled {
      opacity: 0.4;
      cursor: not-allowed;
      transform: none;
      filter: none;
    }

    /* LCARS Voice Comm-Link & Microphone Button */
    .lcars-chat-btn-mic {
      background-color: var(--c-blue);
      color: #000;
      font-family: var(--font-family);
      font-size: 1.05rem;
      font-weight: 700;
      text-transform: uppercase;
      padding: 0.65rem 1.1rem;
      border-radius: 100vmax;
      border: none;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      white-space: nowrap;
      flex-shrink: 0;
      transition: all 0.15s ease;
    }
    .lcars-chat-btn-mic:hover {
      filter: brightness(1.25);
      transform: scale(1.02);
    }
    .lcars-chat-btn-mic:active {
      transform: scale(0.98);
    }
    .lcars-chat-btn-mic.listening {
      background-color: #ff3355 !important;
      color: #fff !important;
      animation: pulse-mic 0.7s infinite alternate ease-in-out;
    }
    .lcars-chat-btn-mic.speaking {
      background-color: #33dd88 !important;
      color: #000 !important;
      animation: pulse-mic 0.9s infinite alternate ease-in-out;
    }
    .lcars-chat-btn-mic.computing {
      background-color: var(--c-gold) !important;
      color: #000 !important;
      animation: pulse-mic 0.8s infinite alternate ease-in-out;
    }

    @keyframes pulse-mic {
      0% { box-shadow: 0 0 5px rgba(255, 51, 85, 0.4); }
      100% { box-shadow: 0 0 20px rgba(255, 51, 85, 0.9); transform: scale(1.04); }
    }

    /* Banner Comm-Link Badge */
    .banner-comm-link {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      background: rgba(136, 153, 255, 0.12);
      border: 1.5px solid var(--c-blue);
      border-radius: 100vmax;
      padding: 0.25rem 0.85rem;
      cursor: pointer;
      user-select: none;
      font-family: var(--mono-family);
      font-size: 0.88rem;
      font-weight: 700;
      color: var(--c-blue);
      transition: all 0.18s ease;
      white-space: nowrap;
    }
    .banner-comm-link:hover {
      background: var(--c-blue);
      color: #000;
      box-shadow: 0 0 14px rgba(136, 153, 255, 0.5);
    }
    .banner-comm-link.listening {
      border-color: #ff3355;
      background: rgba(255, 51, 85, 0.22);
      color: #ff5577;
      animation: pulse-comm 0.7s infinite alternate ease-in-out;
    }
    .banner-comm-link.speaking {
      border-color: #33dd88;
      background: rgba(51, 221, 136, 0.22);
      color: #33dd88;
      animation: pulse-comm 0.9s infinite alternate ease-in-out;
    }
    .banner-comm-link.computing {
      border-color: var(--c-gold);
      background: rgba(235, 148, 58, 0.22);
      color: var(--c-gold);
      animation: pulse-comm 0.8s infinite alternate ease-in-out;
    }
    .banner-comm-link.passive-listen {
      border-color: var(--c-gold);
      color: var(--c-gold);
    }

    @keyframes pulse-comm {
      0% { box-shadow: 0 0 4px rgba(255, 51, 85, 0.3); }
      100% { box-shadow: 0 0 14px rgba(255, 51, 85, 0.8); }
    }

    .comm-wave-bars {
      display: inline-flex;
      align-items: center;
      gap: 2px;
      height: 14px;
    }
    .comm-wave-bars span {
      display: block;
      width: 3px;
      height: 4px;
      background: currentColor;
      border-radius: 1px;
      transition: height 0.1s ease;
    }
    .banner-comm-link.listening .comm-wave-bars span:nth-child(1),
    .banner-comm-link.speaking .comm-wave-bars span:nth-child(1) {
      animation: wave-bar 0.6s infinite ease-in-out alternate;
    }
    .banner-comm-link.listening .comm-wave-bars span:nth-child(2),
    .banner-comm-link.speaking .comm-wave-bars span:nth-child(2) {
      animation: wave-bar 0.4s infinite ease-in-out alternate 0.15s;
    }
    .banner-comm-link.listening .comm-wave-bars span:nth-child(3),
    .banner-comm-link.speaking .comm-wave-bars span:nth-child(3) {
      animation: wave-bar 0.5s infinite ease-in-out alternate 0.3s;
    }

    @keyframes wave-bar {
      0% { height: 3px; }
      100% { height: 14px; }
    }

    /* LCARS Voice Visualizer HUD */
    .lcars-voice-hud {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      padding: 0.5rem 0.85rem;
      background: rgba(0, 0, 0, 0.75);
      border: 1.5px solid var(--c-blue);
      border-radius: 8px;
      margin-top: 0.6rem;
      font-family: var(--mono-family);
      font-size: 0.82rem;
      box-shadow: 0 0 12px rgba(136, 153, 255, 0.2);
    }
    .lcars-voice-bars {
      display: flex;
      align-items: flex-end;
      gap: 3px;
      height: 20px;
    }
    .lcars-voice-bar-col {
      width: 4px;
      height: 4px;
      background-color: var(--c-gold);
      border-radius: 1px;
      transition: height 0.08s ease, background-color 0.15s ease;
    }
    .lcars-chat-quick-actions {
      display: flex;
      gap: 0.4rem;
      flex-wrap: wrap;
      align-items: center;
    }
    .lcars-quick-btn {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.15);
      color: var(--c-gold);
      font-family: var(--mono-family);
      font-size: 0.74rem;
      padding: 0.25rem 0.55rem;
      border-radius: 4px;
      cursor: pointer;
      transition: all 0.15s ease;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .lcars-quick-btn:hover {
      border-color: var(--c-primary);
      color: #fff;
      background: rgba(235, 148, 58, 0.18);
    }
    .lcars-status-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background-color: #10b981;
      display: inline-block;
    }
    @media (max-width: 650px) {
      .lcars-msg { max-width: 96%; }
      .lcars-chat-input-row { flex-direction: column; }
      .lcars-chat-btn-send { width: 100%; justify-content: center; }
      .lcars-chat-select { max-width: 180px; }
    }

    /* ==========================================================================
       KI-AGENTEN SUBGRUPPEN (9ROUTER, HERMES, ANTIGRAVITY IDE)
       ========================================================================== */
    .lcars-subnav-bar {
      display: flex;
      gap: 0.65rem;
      margin: 0.6rem 0 1.1rem 0;
      flex-wrap: wrap;
      border-bottom: 2px solid rgba(255, 255, 255, 0.12);
      padding-bottom: 0.8rem;
    }
    .lcars-subnav-pill {
      background: rgba(0, 0, 0, 0.6);
      border: 2px solid var(--c-primary);
      color: var(--c-primary);
      font-family: var(--font-family);
      font-size: 0.95rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 0.5rem 1.25rem;
      border-radius: 24px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      transition: all 0.2s ease;
      user-select: none;
    }
    .lcars-subnav-pill:hover {
      background: rgba(255, 255, 255, 0.12);
      transform: translateY(-1px);
    }
    .lcars-subnav-pill.active {
      background: var(--c-primary);
      color: #000;
      box-shadow: 0 0 14px var(--c-primary);
    }
    .agent-subview {
      display: none;
    }
    .agent-subview.active-subview {
      display: block;
      animation: subviewFadeIn 0.25s ease-out;
    }
    @keyframes subviewFadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* Hermes Agent Cards & Interface */
    .hermes-profiles-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1rem;
      margin-top: 0.75rem;
      margin-bottom: 1.2rem;
    }
    .hermes-card {
      background: rgba(12, 15, 24, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-left: 6px solid var(--c-secondary);
      border-radius: 8px;
      padding: 1rem 1.1rem;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 0.8rem;
      box-shadow: 0 4px 15px rgba(0, 0, 0, 0.4);
      transition: all 0.2s ease;
    }
    .hermes-card:hover {
      border-color: var(--c-secondary);
      box-shadow: 0 0 14px rgba(186, 164, 229, 0.3);
      transform: translateY(-2px);
    }
    .hermes-card.active-card {
      border-left-color: var(--c-primary);
      border-color: var(--c-primary);
      box-shadow: 0 0 15px rgba(235, 148, 58, 0.35);
    }
    .hermes-card-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.5rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      padding-bottom: 0.5rem;
    }
    .hermes-card-name {
      font-size: 1.2rem;
      font-weight: 700;
      color: var(--c-gold);
      letter-spacing: 0.06em;
      display: flex;
      align-items: center;
      gap: 0.45rem;
    }
    .hermes-card-meta {
      font-family: var(--mono-family);
      font-size: 0.8rem;
      color: #cbd5e1;
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }
    .hermes-meta-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.15rem 0;
    }
    .hermes-meta-lbl {
      color: #94a3b8;
      font-family: var(--font-family);
      font-size: 0.72rem;
      letter-spacing: 0.05em;
    }
    .hermes-meta-val {
      font-weight: 700;
      color: #fff;
    }

    /* Antigravity IDE Hero & Param Readouts */
    .ide-hero-card {
      background: linear-gradient(135deg, rgba(16, 24, 42, 0.9) 0%, rgba(10, 14, 26, 0.95) 100%);
      border: 2px solid var(--c-primary);
      border-radius: 10px;
      padding: 1.5rem;
      margin-top: 0.5rem;
      margin-bottom: 1.2rem;
      box-shadow: 0 6px 24px rgba(0, 0, 0, 0.6);
    }
    .lcars-btn-ide-launch {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.65rem;
      background: var(--c-primary);
      color: #000;
      font-family: var(--font-family);
      font-size: 1.18rem;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      text-decoration: none;
      padding: 0.85rem 1.8rem;
      border-radius: 30px;
      border: none;
      cursor: pointer;
      box-shadow: 0 0 16px var(--c-primary);
      transition: all 0.25s ease;
      user-select: none;
    }
    .lcars-btn-ide-launch:hover {
      background: #ffffff;
      color: #000;
      box-shadow: 0 0 25px var(--c-primary);
      transform: scale(1.02);
    }
    .ide-param-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 0.8rem;
      margin: 1.2rem 0;
    }
    .ide-param-item {
      background: rgba(0, 0, 0, 0.45);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-left: 4px solid var(--c-gold);
      border-radius: 4px;
      padding: 0.6rem 0.8rem;
    }
    .ide-param-lbl {
      font-family: var(--font-family);
      font-size: 0.72rem;
      color: var(--c-gold);
      letter-spacing: 0.06em;
      font-weight: 700;
      text-transform: uppercase;
    }
    .ide-param-val {
      font-family: var(--mono-family);
      font-size: 0.84rem;
      color: #fff;
      margin-top: 0.25rem;
      word-break: break-all;
    }

    /* Theme Buttons in Config */
    .theme-selector-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 0.85rem;
      margin-bottom: 1.5rem;
      width: 100%;
      min-width: 0;
      box-sizing: border-box;
    }
    .theme-btn {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 0.5rem;
      padding: 0.9rem;
      background-color: #12141e;
      border: 2px solid rgba(255, 255, 255, 0.15);
      border-radius: 10px;
      color: #fff;
      cursor: pointer;
      font-family: var(--font-family);
      text-transform: uppercase;
      transition: all 0.15s ease;
      width: 100%;
      min-width: 0;
    }
    .theme-btn:hover {
      border-color: var(--c-primary);
      filter: brightness(1.2);
    }
    .theme-btn.active-theme {
      border-color: #fff;
      box-shadow: 0 0 14px rgba(255, 255, 255, 0.3);
      background-color: #1a1e2e;
    }
    .theme-swatches {
      display: flex;
      gap: 4px;
      width: 100%;
      height: 16px;
    }
    .theme-swatch {
      flex: 1;
      height: 100%;
      border-radius: 2px;
    }

    /* ==========================================================================
       BOTTOM FRAME & ELBOW
       ========================================================================== */
    .bar-panel-bottom {
      display: flex;
      height: var(--bar-height);
      gap: 4px;
      width: 100%;
      position: relative;
      z-index: 3;
    }
    .bar-6 { width: 38%; background-color: var(--c-red); }
    .bar-7 { width: 6%; background-color: var(--c-butterscotch); }
    .bar-8 { width: 18%; background-color: var(--c-red); }
    .bar-9 { flex: 1; background-color: var(--c-secondary); }
    .bar-10 { width: 5%; background-color: var(--c-butterscotch); }

    footer {
      padding: 0.4rem 0.8rem;
      font-size: 0.78rem;
      color: rgba(255, 255, 255, 0.5);
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 0.5rem;
      font-family: var(--mono-family);
    }
    footer a { color: var(--c-primary); text-decoration: none; }

    /* ==========================================================================
       RESPONSIVE BREAKPOINTS (KEIN HORIZONTALER OVERFLOW)
       ========================================================================== */
    @media (max-width: 860px) {
      :root { --lfw: 150px; --pill-height: 48px; }
      .top-vital-badge { padding: 0.12rem 0.35rem; font-size: 0.72rem; }
      .banner-title { font-size: 1.15rem; }
      main { padding: 0.6rem; }
    }
    @media (max-width: 640px) {
      body { padding: 0; }
      .wrap-standard { min-height: auto; }
      .wrap { flex-direction: column; }
      .left-frame-top, .left-frame { width: 100%; border-radius: 0; }
      .right-frame-top::before, .right-frame-top::after,
      .right-frame::after, .right-frame-inner-corner { display: none; }
      .banner-container { padding-left: 0.6rem; }
      .data-cascade-bar { justify-content: flex-start; padding-left: 0.6rem; flex-wrap: wrap; }
      main { max-height: none; overflow: visible; }
    }
  </style>
</head>
<body>

<section class="wrap-standard">
  <!-- OBERER RAHMEN -->
  <div class="wrap">
    <div class="left-frame-top">
      <button onclick="playLcarsBeep(880, 1760); switchCategory('system')" title="Terminal 47 // Klicken für System-Details">TERMINAL 47<br><span style="font-size:0.8rem; opacity:0.85;">AGY-PI</span></button>
      <div style="font-size: 0.8rem; font-family: var(--mono-family);">ONLINE</div>
    </div>
    <div class="right-frame-top">
      <div class="banner-container">
        <div class="banner-title" id="bannerSectionTitle">SYSTEM & SENSOR VERLAUF</div>
        <div style="display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
          <div class="banner-comm-link" id="topCommBadge" onclick="toggleVoiceListening()" title="LCARS Voice Comm-Link // Klicken zum Sprechen oder 'Computer' rufen">
            <span class="comm-mic-icon" id="topCommIcon">🎙️</span>
            <span class="comm-text" id="topCommText">COMM: BEREIT</span>
            <span class="comm-wave-bars"><span></span><span></span><span></span></span>
          </div>
          <div class="banner-stardate">
            <span>STARDATE:</span>
            <span id="stardateValue" style="font-weight:700;">{{ stats.stardate or '--------.-' }}</span>
          </div>
        </div>
      </div>
      <div class="data-cascade-bar">
        <!-- PULSIERENDE TELEMETRIE STATUS-BADGES UNTER STARDATE -->
        <div class="top-vitals-cascade" id="topVitalsCascade">
          <div class="top-vital-badge dc-pulse-1" id="topVitalCpu" onclick="playLcarsBeep(880, 1760); switchCategory('system')" title="CPU Auslastung // Klicken für Details">
            <span class="vital-badge-icon">⚡</span>
            <span class="vital-badge-label">CPU</span>
            <span class="vital-badge-num" id="topCpuVal">{{ stats.cpu.percent or 0 }}%</span>
          </div>
          <div class="top-vital-badge dc-pulse-2" id="topVitalRam" onclick="playLcarsBeep(880, 1760); switchCategory('system')" title="Arbeitsspeicher (RAM) // Klicken für Details">
            <span class="vital-badge-icon">💾</span>
            <span class="vital-badge-label">RAM</span>
            <span class="vital-badge-num" id="topRamVal">{{ stats.ram.percent or 0 }}%</span>
          </div>
          <div class="top-vital-badge dc-pulse-3" id="topVitalDisk" onclick="playLcarsBeep(880, 1760); switchCategory('system')" title="Festplatte (Root /) // Klicken für Details">
            <span class="vital-badge-icon">💽</span>
            <span class="vital-badge-label">DSK</span>
            <span class="vital-badge-num" id="topDiskVal">{{ stats.disk.percent or 0 }}%</span>
          </div>
          <div class="top-vital-badge dc-pulse-4" id="topVitalTemp" onclick="playLcarsBeep(880, 1760); switchCategory('system')" title="SoC Temperatur // Klicken für Details">
            <span class="vital-badge-icon">🌡️</span>
            <span class="vital-badge-label">TMP</span>
            <span class="vital-badge-num" id="topTempVal">{{ (stats.temperature.value|round|int) if stats.temperature.value else '--' }}°</span>
          </div>
          <div class="top-vital-badge dc-pulse-5" id="topVitalBat" onclick="playLcarsBeep(880, 1760); switchCategory('solar')" title="Balkonsolar Akku // Klicken für Solar-Details">
            <span class="vital-badge-icon">🔋</span>
            <span class="vital-badge-label">AKKU</span>
            <span class="vital-badge-num" id="topBatVal">{{ stats.solar.battery_soc_str if stats.solar and stats.solar.battery_soc_str else '--%' }}</span>
          </div>
          <div class="top-vital-badge dc-pulse-6" id="topVitalHouse" onclick="playLcarsBeep(880, 1760); switchCategory('solar')" title="Aktueller Stromverbrauch Haus // Klicken für Solar-Details">
            <span class="vital-badge-icon">⚡</span>
            <span class="vital-badge-label">HAUS</span>
            <span class="vital-badge-num" id="topHouseVal">{{ stats.solar.house_power_str if stats.solar and stats.solar.house_power_str else '-- W' }}</span>
          </div>
        </div>
      </div>
      <div class="bar-panel">
        <div class="bar-1"></div>
        <div class="bar-2"></div>
        <div class="bar-3"></div>
        <div class="bar-4"></div>
        <div class="bar-5"></div>
      </div>
    </div>
  </div>

  <!-- MITTLERER RAHMEN: 5 KATEGORIEN OHNE ZAHLEN -->
  <div class="wrap gap-wrap">
    <!-- Linke Steuerungssäule -->
    <div class="left-frame">
      <nav class="nav-pillar">
        <button class="lcars-pill-btn pill-sys active" onclick="switchCategory('system')" id="btn-cat-system">
          SYSTEM
        </button>
        <button class="lcars-pill-btn pill-srv" onclick="switchCategory('services')" id="btn-cat-services">
          SERVICES
        </button>
        <button class="lcars-pill-btn pill-ai" onclick="switchCategory('agents')" id="btn-cat-agents">
          KI-AGENTEN
        </button>
        <button class="lcars-pill-btn pill-info" onclick="switchCategory('ai-info')" id="btn-cat-ai-info">
          KI-INFO
        </button>
        <button class="lcars-pill-btn pill-cfg" onclick="switchCategory('config')" id="btn-cat-config">
          CONFIG
        </button>
        <button class="lcars-pill-btn pill-fantasy" onclick="switchCategory('fantasy')" id="btn-cat-fantasy">
          FANTASY
        </button>
        <button class="lcars-pill-btn pill-solar" onclick="switchCategory('solar')" id="btn-cat-solar">
          SOLAR
        </button>
        <button class="lcars-pill-btn pill-ha" onclick="switchCategory('homeassistant')" id="btn-cat-homeassistant" style="display: none;">
          ASSISTANT
        </button>
        <button class="lcars-pill-btn pill-cycle" onclick="switchCategory('cycle')" id="btn-cat-cycle" style="display: none;">
          ZYKLUS
        </button>
        <button class="lcars-pill-btn pill-pulsecast" onclick="switchCategory('pulsecast')" id="btn-cat-pulsecast" style="display: none;">
          PULSECAST
        </button>
        <button class="lcars-pill-btn pill-gemini-live" onclick="switchCategory('gemini_live')" id="btn-cat-gemini_live" style="display: none;">
          SUBRAUM COMM
        </button>
        <button class="lcars-pill-btn pill-pimmel" onclick="switchCategory('pimmel')" id="btn-cat-pimmel">
          PIMMEL
        </button>
        <button class="lcars-pill-btn pill-auth" onclick="toggleAuthModal()" id="btn-auth-toggle">
          <span id="authBtnIcon">🔒</span> <span id="authBtnLabel">CODE</span>
        </button>
      </nav>

      <div class="left-frame-lower">
        <button class="left-action-btn" onclick="toggleFullscreen()">
          <span id="fsIcon">⛶</span> <span id="fsLabel">VOLLBILD</span>
        </button>
        <button class="left-action-btn" onclick="toggleAudio()" id="audioBtn">
          <span>🔊</span> <span id="audioLabel">AUDIO AN</span>
        </button>
        <button class="left-action-btn" onclick="fetchLiveStats(true)">
          <span>⟳</span> <span>REFRESH</span>
        </button>
        <div class="left-elbow-bottom">
          <span>PI-4B // 8G</span>
        </div>
      </div>
    </div>

    <!-- Haupt-Inhaltsbereich -->
    <div class="right-frame">
      <div class="right-frame-inner-corner"></div>
      <main>

        <!-- RED ALERT ALARM BANNER (WENN STATUS KRITISCH ODER GATEWAY OFFLINE) -->
        <div id="redAlertBanner" class="red-alert-banner" style="display: none;">
          <div class="red-alert-content">
            <span class="red-alert-icon">🚨</span>
            <span class="red-alert-title">RED ALERT // KRITISCHER STATUS</span>
            <span class="red-alert-reasons" id="redAlertReasons">ODN SYSTEM LIMIT ÜBERSCHRITTEN</span>
          </div>
        </div>

        <!-- KATEGORIE 1: SYSTEM & 24H SENSOR VERLAUF (KOMBINIERT) -->
        <section class="lcars-section active-section" id="section-system">
          <div class="lcars-header-bar">
            <h2>SYSTEM STATUS & 24H VERLAUF</h2>
            <span class="lcars-pill-tag">LIVE ODN METRIKEN</span>
          </div>

          <!-- 24H SENSOR VERLAUFS-CHART (ÜBER DEN LIVE-WERTEN) -->
          <div class="lcars-card" style="margin-bottom: 1.25rem; padding: 1.1rem; width: 100%; min-width: 0;">
            <div class="card-head">
              <span class="card-head-title" style="color:var(--c-primary); font-size:1.1rem;">24H SENSOR HISTORIE & SENSOR-KURVEN</span>
              <span class="card-head-icon">📈</span>
            </div>

            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom: 1rem;">
              <!-- Range Selector Pills -->
              <div style="display:flex; gap:0.35rem; flex-wrap:wrap;">
                <button class="left-action-btn range-btn" onclick="setChartRange('10m')">10 MIN</button>
                <button class="left-action-btn range-btn" onclick="setChartRange('30m')">30 MIN</button>
                <button class="left-action-btn range-btn active-range" onclick="setChartRange('1h')">1 STUNDE</button>
                <button class="left-action-btn range-btn" onclick="setChartRange('12h')">12 STUNDEN</button>
                <button class="left-action-btn range-btn" onclick="setChartRange('24h')">24 STUNDEN</button>
              </div>

              <!-- Dataset Toggles -->
              <div style="display:flex; gap:0.35rem; flex-wrap:wrap;">
                <button class="left-action-btn" onclick="toggleDataset(0)" id="dsBtn0" style="border-color:var(--c-primary); color:var(--c-primary);">TEMPERATUR</button>
                <button class="left-action-btn" onclick="toggleDataset(1)" id="dsBtn1" style="border-color:var(--c-blue); color:var(--c-blue);">CPU %</button>
                <button class="left-action-btn" onclick="toggleDataset(2)" id="dsBtn2" style="border-color:var(--c-red); color:var(--c-red);">THROTTLING</button>
                <button class="left-action-btn" onclick="toggleDataset(3)" id="dsBtn3" style="border-color:var(--c-secondary); color:var(--c-secondary);">RAM %</button>
              </div>
            </div>

            <!-- Canvas Container mit fester Höhe & responsiver Breite -->
            <div style="position: relative; width: 100%; height: 320px; min-width: 0; overflow: hidden;">
              <canvas id="historyChart"></canvas>
            </div>
          </div>

          <!-- Telemetrie Readout Cards (Live-Werte) -->
          <div class="readout-grid">
            <!-- CPU -->
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">CPU AUSLASTUNG</span>
                <span class="card-head-icon">⚡</span>
              </div>
              <div class="card-metric" id="sysCpuVal">--%</div>
              <div class="card-metric-sub" id="sysCpuCores">Kerne: {{ stats.cpu.cores or 1 }}</div>
              <div class="lcars-bar-track">
                <div class="lcars-bar-fill" id="sysCpuBar" style="width: 0%;"></div>
              </div>
            </div>

            <!-- RAM -->
            <div class="lcars-card card-violet">
              <div class="card-head">
                <span class="card-head-title">ARBEITSSPEICHER (RAM)</span>
                <span class="card-head-icon">💾</span>
              </div>
              <div class="card-metric" id="sysRamVal">--%</div>
              <div class="card-metric-sub" id="sysRamSub">-- GB / -- GB</div>
              <div class="lcars-bar-track">
                <div class="lcars-bar-fill" id="sysRamBar" style="width: 0%; background-color: var(--c-secondary);"></div>
              </div>
            </div>

            <!-- TEMPERATURE -->
            <div class="lcars-card card-red">
              <div class="card-head">
                <span class="card-head-title">SOC TEMPERATUR</span>
                <span class="card-head-icon">🌡️</span>
              </div>
              <div class="card-metric" id="sysTempVal">-- °C</div>
              <div class="card-metric-sub" id="sysThrottledSub">Status: Normal (0x0)</div>
              <div class="lcars-bar-track">
                <div class="lcars-bar-fill" id="sysTempBar" style="width: 0%; background-color: var(--c-red);"></div>
              </div>
            </div>

            <!-- ROOT DISK -->
            <div class="lcars-card card-blue">
              <div class="card-head">
                <span class="card-head-title">FESTPLATTE (ROOT /)</span>
                <span class="card-head-icon">💽</span>
              </div>
              <div class="card-metric" id="sysDiskVal">--%</div>
              <div class="card-metric-sub" id="sysDiskSub">-- GB verwendet</div>
              <div class="lcars-bar-track">
                <div class="lcars-bar-fill" id="sysDiskBar" style="width: 0%; background-color: var(--c-blue);"></div>
              </div>
            </div>

            <!-- UPTIME -->
            <div class="lcars-card card-almond">
              <div class="card-head">
                <span class="card-head-title">SYSTEM UPTIME</span>
                <span class="card-head-icon">⏱️</span>
              </div>
              <div class="card-metric" id="sysUptimeVal" style="font-size: 1.8rem;">--</div>
              <div class="card-metric-sub" id="sysBootTimeSub">Boot: --</div>
              <div class="badge-status badge-online">ONLINE</div>
            </div>

            <!-- HARDWARE SPEC -->
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">SPEZIFIKATION</span>
                <span class="card-head-icon">🎛️</span>
              </div>
              <div style="font-size: 1.15rem; color:#fff; margin-bottom: 0.25rem;">{{ stats.hostname }}</div>
              <div class="card-metric-sub">{{ stats.platform }}</div>
              <div class="card-metric-sub">LAN IP: <span id="sysLanIp">{{ stats.lan_ip or '192.168.31.210' }}</span></div>
            </div>
          </div>
        </section>

        <!-- KATEGORIE 2: SERVICES & SCANNER (KOMBINIERT) -->
        <section class="lcars-section" id="section-services">
          <div class="lcars-header-bar">
            <h2>SERVICES & PROZESS-SCANNER</h2>
            <span class="lcars-pill-tag">5-MIN SCANNER AKTIV</span>
          </div>

          <!-- Scanner Telemetrie Status Box -->
          <div class="scanner-dashboard-box">
            <div class="scanner-telemetry-col">
              <strong>STATUS: AUTOMATISCHER SCAN AKTIV</strong>
              <span>Intervall: Alle 5 Minuten (300 Sekunden)</span>
            </div>
            <div class="scanner-telemetry-col">
              <span>Letzter Scan: <strong id="scanLastTime" style="color:#fff;">{{ stats.scanner_meta.last_scan_time }}</strong></span>
              <span>Nächster Scan in: <strong id="scanCountdown" style="color:var(--c-gold);">--:--</strong></span>
            </div>
            <div class="scanner-telemetry-col">
              <span>Aktive Webserver: <strong id="scanFoundCount" style="color:#fff;">{{ stats.discovered_servers|length }}</strong></span>
            </div>
            <div>
              <button class="btn-scan-trigger" onclick="triggerWebserverScan()" id="btnScanTrigger">
                <span>⚡</span> <span id="btnScanLabel">JETZT SCANNEN</span>
              </button>
            </div>
          </div>

          <!-- Responsive Diagnostic Cards aller erkannten Webserver & Services -->
          <div class="readout-grid" id="servicesGrid">
            {% for s in stats.discovered_servers %}
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">{{ s.title }}</span>
                <span class="card-head-icon">{{ s.icon or '🌐' }}</span>
              </div>
              <div>
                <span class="badge-status badge-online">
                  PORT {{ s.port }} // {{ s.http_status or 200 }} OK
                </span>
              </div>
              <div class="cmd-text-box" title="{{ s.cmdline }}">
                PID {{ s.pid }} [{{ s.process_name }}] // {{ s.cmdline }}
              </div>

              <!-- Links ohne horizontalen Überlauf -->
              <div class="card-action-links">
                {% if s.lan_url %}
                <a href="{{ s.lan_url }}" target="_blank" class="url-chip-btn">
                  <span>🏠</span> <span>LAN: {{ s.lan_url }}</span>
                </a>
                {% endif %}
                {% if s.tailscale_url %}
                <a href="{{ s.tailscale_url }}" target="_blank" class="url-chip-btn chip-ts">
                  <span>🌐</span> <span>TS: {{ s.tailscale_url }}</span>
                </a>
                {% endif %}
                {% if s.cloudflared_url %}
                <a href="{{ s.cloudflared_url }}" target="_blank" class="url-chip-btn chip-cf">
                  <span>☁️</span> <span>CF: {{ s.cloudflared_url }}</span>
                </a>
                {% else %}
                <span style="font-size:0.75rem; color:var(--c-gold); padding-left:0.2rem;">☁️ Tunnel wird aufgebaut...</span>
                {% endif %}
              </div>

              <!-- Service Beenden Action -->
              <div class="card-service-footer">
                {% if s.port == 5000 %}
                <span class="system-kernel-badge">🔒 SYSTEM KERNEL</span>
                {% else %}
                <button class="btn-stop-service" onclick="stopService({{ s.pid or 0 }}, {{ s.port }}, '{{ s.title|escape }}')">
                  <span>🛑</span> <span>PROZESS BEENDEN</span>
                </button>
                {% endif %}
              </div>
            </div>
            {% endfor %}
          </div>
        </section>

        <!-- KATEGORIE 3: KI-AGENTEN (3 UNTERGRUPPEN: 9ROUTER, HERMES, ANTIGRAVITY IDE) -->
        <section class="lcars-section" id="section-agents">
          <div class="lcars-header-bar">
            <h2>LCARS SUBRAUM COMM-LINK // KI-AGENTEN MATRIX</h2>
            <span class="lcars-pill-tag">ODN MULTI-AGENT TRANSCEIVER</span>
          </div>

          <!-- SUBGRUPPEN NAVIGATION -->
          <div class="lcars-subnav-bar">
            <button type="button" class="lcars-subnav-pill active" id="subtab-btn-9router" onclick="switchAgentSubgroup('9router')">
              <span>⚡</span> 1. 9ROUTER COMM-LINK
            </button>
            <button type="button" class="lcars-subnav-pill" id="subtab-btn-hermes" onclick="switchAgentSubgroup('hermes')">
              <span>🤖</span> 2. HERMES SYSTEM-AGENTEN
            </button>
            <button type="button" class="lcars-subnav-pill" id="subtab-btn-ide" onclick="switchAgentSubgroup('ide')">
              <span>🚀</span> 3. ANTIGRAVITY IDE
            </button>
          </div>

          <!-- UNTERGRUPPE 1: 9ROUTER COMM-LINK (AKTUELLES CHAT-TERMINAL) -->
          <div class="agent-subview active-subview" id="agent-subview-9router">
            <div class="lcars-card lcars-chat-card" style="margin-top: 0.2rem;">
              <!-- Header-Leiste des Chat Terminals -->
              <div class="card-head" style="flex-wrap: wrap; gap: 0.6rem; border-bottom: 2px solid var(--c-primary); padding-bottom: 0.6rem; margin-bottom: 0.8rem;">
                <div style="display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;">
                  <span class="card-head-title" style="color: var(--c-primary); font-size: 1.1rem; letter-spacing: 0.08em;">
                    9ROUTER SUBRAUM-COMM
                  </span>
                  <span class="badge-status badge-online" id="chatProxyStatusBadge">
                    <span class="lcars-status-dot"></span>
                    <span id="chatProxyStatusText">PROXY BEREIT</span>
                  </span>
                </div>

                <!-- Modell-Auswahl & LCARS Steuerung -->
                <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                  <label for="chatModelSelect" style="font-family: var(--font-family); font-size: 0.82rem; font-weight: 700; color: var(--c-secondary); text-transform: uppercase; letter-spacing: 0.05em;">
                    MODELL:
                  </label>
                  <div class="lcars-select-wrap">
                    <select id="chatModelSelect" class="lcars-chat-select" onchange="onChatModelChange()">
                      <option value="ag/gemini-3-flash">ag/gemini-3-flash</option>
                      <option value="openrouter/openrouter/free">openrouter/openrouter/free</option>
                    </select>
                  </div>
                  <button type="button" class="left-action-btn" onclick="loadChatModels(true)" title="Modell-Liste neu laden" style="padding: 0.3rem 0.6rem; font-size: 0.8rem;">
                    <span>⟳</span>
                  </button>
                  <button type="button" class="left-action-btn" onclick="clearChatHistory()" title="Dialog zurücksetzen" style="padding: 0.3rem 0.7rem; font-size: 0.8rem; border-color: var(--c-red); color: var(--c-red);">
                    <span>🗑️ RESET</span>
                  </button>
                </div>
              </div>

              <!-- Chat Verlauf / ODN Log Screen -->
              <div id="lcarsChatLog" class="lcars-chat-log" role="log" aria-live="polite">
                <div class="lcars-msg lcars-msg-agent">
                  <div class="lcars-msg-header">
                    <span class="lcars-msg-sender">▶ 9ROUTER // SUBRAUM-COMM</span>
                    <span class="lcars-msg-time" id="chatInitialTime">--:--:--</span>
                  </div>
                  <div class="lcars-msg-body">LCARS Subraum-Transceiver initialisiert. Kanal zum lokalen 9Router Proxy (Port 20128) etabliert. Wählen Sie ein Modell und geben Sie eine Anweisung ein.</div>
                </div>
              </div>

              <!-- LCARS Ladeanzeige -->
              <div id="lcarsChatLoading" class="lcars-chat-loading" style="display: none;">
                <div class="lcars-loading-bars">
                  <div class="lcars-loading-bar"></div>
                  <div class="lcars-loading-bar"></div>
                  <div class="lcars-loading-bar"></div>
                </div>
                <span id="lcarsChatLoadingText">KOGNITIVER PROZESSOR AKTIV // VERARBEITE SUBRAUM-TRANSMISSION...</span>
              </div>

              <!-- LCARS Voice Visualizer & Transkription HUD -->
              <div id="lcarsVoiceHud" class="lcars-voice-hud" style="display: none;">
                <div style="display: flex; align-items: center; gap: 0.5rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                  <span id="lcarsVoiceHudStatus" style="color: var(--c-blue); font-weight: 700; font-family: var(--font-family); font-size: 0.9rem; letter-spacing: 0.05em;">● ODN AUDIO-LINK</span>
                  <span id="lcarsVoiceHudTranscript" style="color: var(--c-gold); font-style: italic;"></span>
                </div>
                <div style="display: flex; align-items: center; gap: 0.75rem; flex-shrink: 0;">
                  <div class="lcars-voice-bars" id="lcarsVoiceBars">
                    <div class="lcars-voice-bar-col"></div>
                    <div class="lcars-voice-bar-col"></div>
                    <div class="lcars-voice-bar-col"></div>
                    <div class="lcars-voice-bar-col"></div>
                    <div class="lcars-voice-bar-col"></div>
                    <div class="lcars-voice-bar-col"></div>
                    <div class="lcars-voice-bar-col"></div>
                    <div class="lcars-voice-bar-col"></div>
                    <div class="lcars-voice-bar-col"></div>
                    <div class="lcars-voice-bar-col"></div>
                  </div>
                  <button type="button" class="lcars-quick-btn" onclick="stopVoiceComm(true)" style="padding: 0.2rem 0.6rem; font-size: 0.72rem; border-color: #ff3355; color: #ff5577;">ABBRUCH [ESC]</button>
                </div>
              </div>

              <!-- Eingabebereich mit LCARS Send-Button & Comm-Link Mic -->
              <form id="lcarsChatForm" onsubmit="handleChatSubmit(event)" style="margin-top: 0.75rem; width: 100%; min-width: 0;">
                <div class="lcars-chat-input-row">
                  <input
                    type="text"
                    id="lcarsChatInput"
                    class="lcars-chat-input"
                    placeholder="BEFEHL AN 9ROUTER AGENTEN EINGEBEN ODER SPRECHEN..."
                    autocomplete="off"
                    required
                  />
                  <button type="button" id="lcarsChatMicBtn" class="lcars-chat-btn-mic" onclick="toggleVoiceListening('9router')" title="LCARS Spracheingabe (Mikrofon)">
                    <span id="lcarsChatMicIcon">🎙️</span> <span id="lcarsChatMicLabel">COMM</span>
                  </button>
                  <button type="submit" id="lcarsChatSendBtn" class="lcars-chat-btn-send">
                    <span id="lcarsChatSendLabel">TRANSMIT</span> <span>↵</span>
                  </button>
                </div>
              </form>

              <!-- Schnellbefehle & Statusleiste -->
              <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.65rem;">
                <div class="lcars-chat-quick-actions">
                  <span style="font-size: 0.75rem; color: rgba(255,255,255,0.5); align-self: center; font-family: var(--mono-family);">SCHNELL-BEFEHLE:</span>
                  <button type="button" class="lcars-quick-btn" onclick="sendQuickPrompt('Statusbericht aller Subsysteme anfordern.')">STATUSBERICHT</button>
                  <button type="button" class="lcars-quick-btn" onclick="sendQuickPrompt('Welche Webdienste laufen aktuell auf dem Server?')">DIENSTE ANALYSIEREN</button>
                  <button type="button" class="lcars-quick-btn" onclick="sendQuickPrompt('Wer bist du und welche Aufgaben kannst du übernehmen?')">IDENTIFIKATION</button>
                </div>
                <div id="chatMetaStatus" style="font-family: var(--mono-family); font-size: 0.76rem; color: var(--c-gold);">
                  BEREIT // PROXY: 127.0.0.1:20128
                </div>
              </div>
            </div>
          </div>

          <!-- UNTERGRUPPE 2: HERMES SYSTEM-AGENTEN -->
          <div class="agent-subview" id="agent-subview-hermes">
            <!-- Kopfzeile mit Steuerknöpfen -->
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.75rem; margin-top: 0.2rem; margin-bottom: 0.85rem;">
              <div>
                <span style="font-family: var(--font-family); font-size: 1.15rem; font-weight: 700; color: var(--c-secondary); letter-spacing: 0.06em;">
                  HERMES AUTONOMOUS RUNTIME // SYSTEM-AGENTEN
                </span>
                <div style="font-family: var(--mono-family); font-size: 0.78rem; color: #888; margin-top: 2px;">
                  Installiert unter ~/.hermes // Autonomes Multi-Agent Framework auf dem Raspberry Pi
                </div>
              </div>
              <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
                <button type="button" class="left-action-btn" onclick="loadHermesProfiles(true)" style="padding: 0.4rem 0.8rem; font-size: 0.82rem;">
                  <span>⟳</span> <span>PROFILES NEU LADEN</span>
                </button>
                <button type="button" class="left-action-btn" onclick="toggleHermesCreateForm()" id="btnToggleHermesCreate" style="padding: 0.4rem 0.9rem; font-size: 0.82rem; border-color: var(--c-primary); color: var(--c-primary); font-weight: 700;">
                  <span>➕</span> <span>NEUEN AGENTEN ANLEGEN</span>
                </button>
              </div>
            </div>

            <!-- Aufklappbares Formular: Neuen Agenten anlegen -->
            <div id="hermesCreateAgentCard" class="lcars-card" style="display: none; margin-bottom: 1.25rem; border-left: 6px solid var(--c-primary); background: rgba(18, 14, 28, 0.95);">
              <div class="card-head" style="border-bottom: 1px solid var(--c-primary); padding-bottom: 0.5rem; margin-bottom: 0.8rem;">
                <span class="card-head-title" style="color: var(--c-primary);">NEUEN HERMES SYSTEM-AGENTEN INITIALISIEREN</span>
                <button type="button" class="left-action-btn" onclick="toggleHermesCreateForm(false)" style="padding: 0.2rem 0.5rem; font-size: 0.75rem;">✕ SCHLIESSEN</button>
              </div>
              <form id="hermesCreateForm" onsubmit="handleCreateHermesAgent(event)">
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.85rem; margin-bottom: 1rem;">
                  <div class="config-field">
                    <label for="newAgentName" style="font-size: 0.8rem; font-weight: 700; color: var(--c-gold); display: block; margin-bottom: 0.3rem;">
                      AGENTEN-NAME (KENNUNG):
                    </label>
                    <input type="text" id="newAgentName" class="lcars-input" placeholder="z. B. scotty, data, spock" pattern="[a-z0-9_-]+" required>
                    <div style="font-size: 0.72rem; color: #888; margin-top: 0.2rem;">Nur Kleinbuchstaben, Ziffern und Bindestriche</div>
                  </div>
                  <div class="config-field">
                    <label for="newAgentCloneFrom" style="font-size: 0.8rem; font-weight: 700; color: var(--c-secondary); display: block; margin-bottom: 0.3rem;">
                      BASIS-VORLAGE (CLONE):
                    </label>
                    <select id="newAgentCloneFrom" class="lcars-input" style="cursor: pointer;">
                      <option value="default">default (Standard ODN)</option>
                      <option value="jennifer">jennifer</option>
                    </select>
                    <div style="font-size: 0.72rem; color: #888; margin-top: 0.2rem;">Übernimmt API-Keys & Grundkonfiguration</div>
                  </div>
                  <div class="config-field">
                    <label for="newAgentModel" style="font-size: 0.8rem; font-weight: 700; color: var(--c-primary); display: block; margin-bottom: 0.3rem;">
                      STANDARD-MODELL:
                    </label>
                    <input type="text" id="newAgentModel" class="lcars-input" value="ag/gemini-3-flash" placeholder="z. B. ag/gemini-3-flash">
                    <div style="font-size: 0.72rem; color: #888; margin-top: 0.2rem;">Modell für diesen Agenten</div>
                  </div>
                  <div class="config-field" style="grid-column: 1 / -1;">
                    <label for="newAgentDesc" style="font-size: 0.8rem; font-weight: 700; color: var(--c-gold); display: block; margin-bottom: 0.3rem;">
                      BESCHREIBUNG / ROLLE / FÄHIGKEITEN:
                    </label>
                    <input type="text" id="newAgentDesc" class="lcars-input" placeholder="z. B. Chefingenieur für ODN, Wartung & Shell-Automatisierung">
                  </div>
                </div>
                <div style="display: flex; justify-content: flex-end; gap: 0.6rem;">
                  <button type="button" class="left-action-btn" onclick="toggleHermesCreateForm(false)" style="padding: 0.45rem 1rem;">ABBRECHEN</button>
                  <button type="submit" class="left-action-btn" id="btnSubmitCreateAgent" style="padding: 0.45rem 1.4rem; font-weight: 700; border-color: var(--c-primary); color: var(--c-primary);">
                    <span>⚡ AGENT ANLEGEN</span>
                  </button>
                </div>
                <div id="hermesCreateStatus" style="margin-top: 0.5rem; font-family: var(--mono-family); font-size: 0.8rem; display: none;"></div>
              </form>
            </div>

            <!-- Raster aller Hermes Profile -->
            <div id="hermesProfilesGrid" class="hermes-profiles-grid">
              <!-- Dynamisch via JavaScript gefüllt -->
              <div style="color: #888; font-family: var(--mono-family); padding: 1rem;">Lade Hermes System-Agenten...</div>
            </div>

            <!-- Hermes Direkter Kommunikationskanal -->
            <div class="lcars-card lcars-chat-card" style="margin-top: 0.5rem; border-left-color: var(--c-secondary);">
              <div class="card-head" style="flex-wrap: wrap; gap: 0.6rem; border-bottom: 2px solid var(--c-secondary); padding-bottom: 0.6rem; margin-bottom: 0.8rem;">
                <div style="display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;">
                  <span class="card-head-title" style="color: var(--c-secondary); font-size: 1.1rem; letter-spacing: 0.08em;">
                    HERMES DIREKT-COMM // <span id="hermesActiveProfileHeader" style="color: var(--c-gold);">DEFAULT</span>
                  </span>
                  <span class="badge-status badge-online" id="hermesGatewayStatusBadge">
                    <span class="lcars-status-dot"></span>
                    <span>HERMES BEREIT</span>
                  </span>
                </div>

                <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                  <label for="hermesProfileSelect" style="font-family: var(--font-family); font-size: 0.82rem; font-weight: 700; color: var(--c-gold); text-transform: uppercase;">
                    ZIEL-AGENT:
                  </label>
                  <div class="lcars-select-wrap">
                    <select id="hermesProfileSelect" class="lcars-chat-select" onchange="onHermesProfileChange()" style="border-color: var(--c-secondary);">
                      <option value="default">default (Core)</option>
                      <option value="jennifer">jennifer</option>
                    </select>
                  </div>
                  <button type="button" class="left-action-btn" onclick="clearHermesChatHistory()" title="Dialog zurücksetzen" style="padding: 0.3rem 0.7rem; font-size: 0.8rem; border-color: var(--c-red); color: var(--c-red);">
                    <span>🗑️ RESET</span>
                  </button>
                </div>
              </div>

              <!-- Hermes Chat Log -->
              <div id="hermesChatLog" class="lcars-chat-log" role="log" aria-live="polite">
                <div class="lcars-msg lcars-msg-agent" style="border-left-color: var(--c-secondary); background: rgba(186, 164, 229, 0.08);">
                  <div class="lcars-msg-header">
                    <span class="lcars-msg-sender" style="color: var(--c-secondary);">▶ HERMES // SYSTEM COMM-LINK</span>
                    <span class="lcars-msg-time" id="hermesInitialTime">--:--:--</span>
                  </div>
                  <div class="lcars-msg-body">Direkter ODN-Kommunikationskanal zum lokalen Hermes Agenten etabliert. Anfragen werden direkt an den Agentenprozess auf dem System übermittelt.</div>
                </div>
              </div>

              <!-- Hermes Ladeanzeige -->
              <div id="hermesChatLoading" class="lcars-chat-loading" style="display: none;">
                <div class="lcars-loading-bars">
                  <div class="lcars-loading-bar" style="background-color: var(--c-secondary);"></div>
                  <div class="lcars-loading-bar" style="background-color: var(--c-primary);"></div>
                  <div class="lcars-loading-bar" style="background-color: var(--c-gold);"></div>
                </div>
                <span id="hermesChatLoadingText">HERMES AGENT VERARBEITET ANFRAGE...</span>
              </div>

              <!-- Hermes Eingabeformular -->
              <form id="hermesChatForm" onsubmit="handleHermesChatSubmit(event)" style="margin-top: 0.75rem; width: 100%; min-width: 0;">
                <div class="lcars-chat-input-row">
                  <input
                    type="text"
                    id="hermesChatInput"
                    class="lcars-chat-input"
                    placeholder="BEFEHL ODER FRAGE AN HERMES AGENTEN SENDEN..."
                    autocomplete="off"
                    required
                    style="border-color: var(--c-secondary);"
                  />
                  <button type="button" id="hermesChatMicBtn" class="lcars-chat-btn-mic" onclick="toggleVoiceListening('hermes')" title="Hermes Spracheingabe (Mikrofon)" style="background: var(--c-secondary);">
                    <span>🎙️</span> <span>COMM</span>
                  </button>
                  <button type="submit" id="hermesChatSendBtn" class="lcars-chat-btn-send" style="background: var(--c-secondary); border-color: var(--c-secondary); color: #000;">
                    <span id="hermesChatSendLabel">SENDEN</span> <span>↵</span>
                  </button>
                </div>
              </form>

              <!-- Schnellbefehle für Hermes -->
              <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.65rem;">
                <div class="lcars-chat-quick-actions">
                  <span style="font-size: 0.75rem; color: rgba(255,255,255,0.5); align-self: center; font-family: var(--mono-family);">HERMES SCHNELL-BEFEHLE:</span>
                  <button type="button" class="lcars-quick-btn" onclick="sendHermesQuickPrompt('Gib einen kurzen Statusbericht über deine aktuellen Aufgaben und Systemzustand.')">STATUSBERICHT</button>
                  <button type="button" class="lcars-quick-btn" onclick="sendHermesQuickPrompt('Welche Modelle und Fähigkeiten stehen dir zur Verfügung?')">FÄHIGKEITEN</button>
                  <button type="button" class="lcars-quick-btn" onclick="sendHermesQuickPrompt('Wer bist du und was ist deine Rolle?')">IDENTIFIKATION</button>
                </div>
                <div id="hermesMetaStatus" style="font-family: var(--mono-family); font-size: 0.76rem; color: var(--c-gold);">
                  HERMES CLI EXEC // LOCALHOST
                </div>
              </div>
            </div>
          </div>

          <!-- UNTERGRUPPE 3: ANTIGRAVITY IDE (ENTWICKLUNGSUMGEBUNG) -->
          <div class="agent-subview" id="agent-subview-ide">
            <div class="ide-hero-card">
              <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 1rem;">
                <div>
                  <h3 style="font-size: 1.5rem; color: var(--c-primary); letter-spacing: 0.06em; margin-bottom: 0.35rem;">
                    GOOGLE ANTIGRAVITY // WEBBASIERTE ENTWICKLUNGSUMGEBUNG
                  </h3>
                  <p style="color: var(--c-gold); font-size: 0.95rem; max-width: 800px; line-height: 1.4;">
                    Direkter Zugriff auf die Cloud-basierte Google Antigravity Agentic IDE für dieses Dashboard-Projekt. Hier arbeiten Sie mit dem KI-Coding-Agenten an diesem Dashboard.
                  </p>
                </div>
                <span class="badge-status badge-online" style="font-size: 0.85rem; padding: 0.35rem 0.8rem;">
                  ODN SUBRAUM-LINK AKTIV
                </span>
              </div>

              <!-- Großer Launch-Button -->
              <div style="text-align: center; margin: 1.5rem 0;">
                <a
                  id="ideLaunchBtn"
                  href="https://antigravity.google.com/r/f9e18040-ab9a-4f0c-9d4a-2b4f5281af22-v2?p=c%2F3a633d37-def3-419b-ab1e-8498973ae694%3Fsection%3Dc15db4e2-c36e-442e-850b-0d28e944e2c6"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="lcars-btn-ide-launch"
                  onclick="playLcarsBeep(1200, 2400)"
                >
                  <span>🚀</span> <span>ANTIGRAVITY IDE ÖFFNEN // ZUR ENTWICKLUNGSUMGEBUNG ↗</span>
                </a>
                <div style="font-family: var(--mono-family); font-size: 0.8rem; color: #94a3b8; margin-top: 0.4rem;">
                  Öffnet in neuem Browser-Tab // Sichere Google Antigravity Session
                </div>
              </div>

              <!-- Parameter Telemetrie Readouts -->
              <div class="ide-param-grid">
                <div class="ide-param-item">
                  <div class="ide-param-lbl">RUNNER / WORKSPACE ID</div>
                  <div class="ide-param-val" id="ideRunnerId">f9e18040-ab9a-4f0c-9d4a-2b4f5281af22-v2</div>
                </div>
                <div class="ide-param-item">
                  <div class="ide-param-lbl">KONVERSATION / SESSION</div>
                  <div class="ide-param-val" id="ideConvId">3a633d37-def3-419b-ab1e-8498973ae694</div>
                </div>
                <div class="ide-param-item">
                  <div class="ide-param-lbl">AKTIVER THREAD-ABSCHNITT</div>
                  <div class="ide-param-val" id="ideSectionId">c15db4e2-c36e-442e-850b-0d28e944e2c6</div>
                </div>
                <div class="ide-param-item">
                  <div class="ide-param-lbl">LOKALER PROJEKT-PFAD</div>
                  <div class="ide-param-val">/home/cb/Projects/agydashboard</div>
                </div>
              </div>

              <!-- Konfigurierbare / Dynamische URL Box -->
              <div style="background: rgba(0, 0, 0, 0.5); border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 6px; padding: 1.1rem; margin-top: 1.2rem;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.6rem;">
                  <span style="font-family: var(--font-family); font-size: 0.9rem; font-weight: 700; color: var(--c-primary); letter-spacing: 0.05em;">
                    DYNAMISCHE IDE-URL VERWALTUNG
                  </span>
                  <span style="font-size: 0.75rem; color: #888;">(Persistent in config.json gespeichert)</span>
                </div>
                <p style="font-size: 0.82rem; color: #cbd5e1; margin-bottom: 0.8rem; line-height: 1.4;">
                  Falls eine neue Entwicklungs-Session oder ein anderer Workspace verwendet wird, kann der Link hier angepasst werden. Klick auf „SPEICHERN“ aktualisiert den Button dauerhaft.
                </p>
                <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center;">
                  <input
                    type="url"
                    id="ideUrlInput"
                    class="lcars-input"
                    style="flex: 1; min-width: 280px;"
                    value="https://antigravity.google.com/r/f9e18040-ab9a-4f0c-9d4a-2b4f5281af22-v2?p=c%2F3a633d37-def3-419b-ab1e-8498973ae694%3Fsection%3Dc15db4e2-c36e-442e-850b-0d28e944e2c6"
                  />
                  <button type="button" class="left-action-btn" onclick="saveIdeUrl()" style="border-color: var(--c-primary); color: var(--c-primary); font-weight: 700; padding: 0.45rem 1rem;">
                    <span>💾</span> <span>SPEICHERN</span>
                  </button>
                  <button type="button" class="left-action-btn" onclick="copyIdeUrl()" style="padding: 0.45rem 0.8rem;">
                    <span>📋</span> <span>KOPIEREN</span>
                  </button>
                  <button type="button" class="left-action-btn" onclick="resetIdeUrl()" style="padding: 0.45rem 0.8rem; border-color: #888; color: #bbb;">
                    <span>↺</span> <span>STANDARD</span>
                  </button>
                </div>
                <div id="ideSaveStatus" style="font-family: var(--mono-family); font-size: 0.8rem; margin-top: 0.5rem; display: none;"></div>
              </div>
            </div>
          </div>
        </section>

        <!-- KATEGORIE 4: KI-INFO (9ROUTER TELEMETRIE, AGENTEN-STATISTIKEN & ARCHIV) -->
        <section class="lcars-section" id="section-ai-info">
          <div class="lcars-header-bar">
            <h2>KI-INFO // 9ROUTER & NEURAL-STATISTIKEN</h2>
            <span class="lcars-pill-tag">ODN TELEMETRIE // 9ROUTER PROXY</span>
          </div>

          <!-- 9ROUTER HAUPT-METRIKEN (DATA TILES) -->
          <div class="readout-grid">
            <!-- 9Router Total Requests -->
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">9ROUTER ANFRAGEN</span>
                <span class="card-head-icon">⚡</span>
              </div>
              <div class="card-metric" id="nrTotalRequests">{{ (stats.nine_router.totals.requests if stats.nine_router else 0) }}</div>
              <div class="card-metric-sub">ODN Subraum-Transceiver</div>
              <div class="card-metric-sub">Proxy Port 20128 // Status: {{ (stats.nine_router.status if stats.nine_router else 'offline')|upper }}</div>
              <div class="badge-status badge-online">9ROUTER AKTIV</div>
            </div>

            <!-- 9Router Total Tokens mit Aufschlüsselung -->
            <div class="lcars-card card-violet">
              <div class="card-head">
                <span class="card-head-title">9ROUTER TOKEN-VOLUMEN</span>
                <span class="card-head-icon">🔢</span>
              </div>
              <div class="card-metric" id="nrTotalTokens">{{ (stats.nine_router.totals.tokens_formatted if stats.nine_router else '0') }}</div>
              <div class="card-metric-sub" id="nrPromptComplTokens">Prompt: {{ (stats.nine_router.totals.prompt_formatted if stats.nine_router else '0') }} | Compl: {{ (stats.nine_router.totals.completion_formatted if stats.nine_router else '0') }}</div>
              <div class="card-metric-sub" id="nrCachedTokens" style="color:var(--c-blue);">Cached: {{ (stats.nine_router.totals.cached_formatted if stats.nine_router else '0') }}{% if stats.nine_router and stats.nine_router.totals.cache_hit_rate %} ({{ stats.nine_router.totals.cache_hit_rate }}% Quote){% endif %}</div>
              <div class="badge-status badge-online">PROMPT & CACHE TELEMETRIE</div>
            </div>

            <!-- 9Router Ersparnis durch Prompt Caching -->
            <div class="lcars-card card-blue">
              <div class="card-head">
                <span class="card-head-title">CACHE-ERSPARNIS</span>
                <span class="card-head-icon">🛡️</span>
              </div>
              <div class="card-metric" id="nrSavingsRate" style="color:var(--c-blue);">{{ (stats.nine_router.totals.cache_hit_rate_formatted if stats.nine_router else '0.0%') }}</div>
              <div class="card-metric-sub" id="nrSavedTokens">Tokens gespart: {{ (stats.nine_router.totals.saved_tokens_formatted if stats.nine_router else '0') }}</div>
              <div class="card-metric-sub" id="nrSavedCost" style="color:var(--c-accent);">Kosten gespart: ~{{ (stats.nine_router.totals.saved_cost_formatted if stats.nine_router else '$0.00') }}{% if stats.nine_router and stats.nine_router.totals.saved_cost_pct %} (-{{ stats.nine_router.totals.saved_cost_pct }}%){% endif %}</div>
              <div class="badge-status badge-online">PROMPT CACHE EFFIZIENZ</div>
            </div>

            <!-- 9Router Total Cost -->
            <div class="lcars-card card-almond">
              <div class="card-head">
                <span class="card-head-title">9ROUTER GESAMTKOSTEN</span>
                <span class="card-head-icon">💳</span>
              </div>
              <div class="card-metric" id="nrTotalCost">{{ (stats.nine_router.totals.cost_formatted if stats.nine_router else '$0.00') }}</div>
              <div class="card-metric-sub" id="nrUncachedCost">Ohne Cache: ~{{ (stats.nine_router.totals.uncached_cost_formatted if stats.nine_router else '$0.00') }}</div>
              <div class="card-metric-sub" id="nrCostSavingsSub">Ersparnis: ~{{ (stats.nine_router.totals.saved_cost_formatted if stats.nine_router else '$0.00') }}</div>
              <div class="badge-status badge-online">ROUTING SPENDINGS</div>
            </div>

            <!-- Active Provider Connections -->
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">AKTIVE PROVIDER</span>
                <span class="card-head-icon">🛰️</span>
              </div>
              <div class="card-metric" id="nrActiveConnCount">{{ (stats.nine_router.connections|length if stats.nine_router and stats.nine_router.connections else 0) }}</div>
              <div class="card-metric-sub">Verbundene Backends:</div>
              <div class="card-metric-sub" id="nrConnProvidersList">
                {% if stats.nine_router and stats.nine_router.connections %}
                  {% for c in stats.nine_router.connections %}{{ c.provider|capitalize }}{% if not loop.last %} • {% endif %}{% endfor %}
                {% else %}
                  Keine Provider
                {% endif %}
              </div>
              <div class="badge-status badge-online">UPSTREAM BEREIT</div>
            </div>
          </div>

          <!-- WEITERE KI-DIENSTE (OPENROUTER, ANTIGRAVITY, HERMES) -->
          <div class="readout-grid" style="margin-top: 1rem;">
            <!-- OpenRouter -->
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">OPENROUTER BUDGET</span>
                <span class="card-head-icon">🤖</span>
              </div>
              <div class="card-metric" id="orUsageVal">${{ "%.2f"|format(stats.openrouter.usage or 0.0) }}</div>
              <div class="card-metric-sub">Limit: {{ stats.openrouter.limit_formatted or 'Unbegrenzt' }}</div>
              <div class="card-metric-sub">Restguthaben: {{ stats.openrouter.credits_remaining_formatted or '$0.00' }}</div>
              <div class="badge-status badge-online">OPENROUTER SYNCED</div>
            </div>

            <!-- Antigravity -->
            <div class="lcars-card card-violet">
              <div class="card-head">
                <span class="card-head-title">ANTIGRAVITY CLI</span>
                <span class="card-head-icon">🌌</span>
              </div>
              <div class="card-metric" id="agySessionsVal">{{ stats.antigravity.sessions_count or 0 }}</div>
              <div class="card-metric-sub">Aktive Sessions</div>
              <div class="card-metric-sub">Aktivität: {{ stats.antigravity.latest_activity }}</div>
              <div class="badge-status badge-online">{{ stats.antigravity.account_label }}</div>
            </div>

            <!-- Hermes Overview -->
            <div class="lcars-card card-almond">
              <div class="card-head">
                <span class="card-head-title">HERMES GESAMT-TOKEN</span>
                <span class="card-head-icon">🧠</span>
              </div>
              <div class="card-metric" id="hermesTotalTokens">{{ stats.hermes.total_tokens_formatted or '0' }}</div>
              <div class="card-metric-sub">Sessions: {{ stats.hermes.total_sessions or 0 }} | Kosten: {{ stats.hermes.total_cost_formatted or '$0.00' }}</div>
              <div class="badge-status badge-online">{{ stats.hermes.database_count }} DATENBANKEN</div>
            </div>
          </div>

          <!-- PROVIDER-VERBINDUNGEN & MODELL-VERTEILUNG -->
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap:1rem; margin-top:1.25rem; width:100%; min-width:0;">
            <!-- Provider Connections Card -->
            <div class="lcars-card" style="width:100%; min-width:0;">
              <div class="card-head" style="margin-bottom:0.75rem;">
                <span class="card-head-title">PROVIDER-VERBINDUNGEN (9ROUTER)</span>
                <span class="card-head-icon">🔌</span>
              </div>
              <div id="nineRouterConnectionsList" style="display:flex; flex-direction:column; gap:0.6rem;">
                {% if stats.nine_router and stats.nine_router.connections %}
                  {% for conn in stats.nine_router.connections %}
                  <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.5); padding:0.6rem 0.8rem; border-left:4px solid var(--c-primary); border-radius:6px; flex-wrap:wrap; gap:0.5rem;">
                    <div>
                      <div style="font-weight:700; color:var(--c-primary); font-size:0.95rem;">{{ conn.provider|upper }} // {{ conn.name or conn.provider }}</div>
                      <div style="font-family:var(--mono-family); font-size:0.78rem; color:#888;">Auth: {{ conn.auth_type|upper }} | Prio: {{ conn.priority }} {% if conn.email %}| {{ conn.email }}{% endif %}</div>
                    </div>
                    <div>
                      <span class="badge-status {% if conn.is_active %}badge-online{% else %}badge-offline{% endif %}">
                        {% if conn.is_active %}AKTIV // VERBUNDEN{% else %}INAKTIV{% endif %}
                      </span>
                    </div>
                  </div>
                  {% endfor %}
                {% else %}
                  <div style="color:#888; font-size:0.85rem; padding:0.5rem;">Keine Provider-Verbindungen konfiguriert.</div>
                {% endif %}
              </div>
            </div>

            <!-- Model Distribution Doughnut Chart -->
            <div class="lcars-card" style="min-height:320px; display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; min-width:0;">
              <div class="card-head" style="align-self:flex-start; width:100%; margin-bottom:0.75rem;">
                <span class="card-head-title">9ROUTER MODELL-TOKEN VERTEILUNG</span>
                <span class="card-head-icon">📊</span>
              </div>
              <div style="position:relative; width:100%; max-width:320px; height:260px; min-width:0;">
                <canvas id="nineRouterModelChart"></canvas>
              </div>
            </div>
          </div>

          <!-- 9ROUTER VERLAUFS-CHART -->
          <div class="lcars-card" style="margin-top:1.25rem; padding:1.1rem; width:100%; min-width:0;">
            <div class="card-head" style="margin-bottom:0.75rem;">
              <span class="card-head-title" style="color:var(--c-primary); font-size:1.1rem;">9ROUTER TRANSMISSIONS-HISTORIE & TOKEN-FLOW</span>
              <span class="card-head-icon">📈</span>
            </div>
            <div style="position:relative; width:100%; height:260px; min-width:0;">
              <canvas id="nineRouterTimelineChart"></canvas>
            </div>
          </div>

          <!-- 9ROUTER LETZTE TRANSMISSIONEN (HISTORIE TABELLE) -->
          <div class="lcars-card" style="margin-top:1.25rem; width:100%; min-width:0; overflow-x:auto;">
            <div class="card-head" style="margin-bottom:0.75rem; justify-content:space-between; flex-wrap:wrap; gap:0.5rem;">
              <div style="display:flex; align-items:center; gap:0.5rem;">
                <span class="card-head-title">9ROUTER TRANSMISSIONS-LOG // LETZTE 15 REQUESTS</span>
                <span class="card-head-icon">📋</span>
              </div>
              <button type="button" class="left-action-btn" onclick="fetchNineRouterStats(true)" title="9Router Daten aktualisieren" style="padding:0.25rem 0.6rem; font-size:0.8rem;">
                <span>⟳ REFRESH</span>
              </button>
            </div>
            <div style="width:100%; overflow-x:auto;">
              <table style="width:100%; border-collapse:collapse; font-size:0.86rem; text-align:left;">
                <thead>
                  <tr style="border-bottom:2px solid var(--c-primary); color:var(--c-primary); font-family:var(--font-family); letter-spacing:0.06em;">
                    <th style="padding:0.5rem 0.4rem;">ID</th>
                    <th style="padding:0.5rem 0.4rem;">ZEITPUNKT</th>
                    <th style="padding:0.5rem 0.4rem;">PROVIDER</th>
                    <th style="padding:0.5rem 0.4rem;">MODELL</th>
                    <th style="padding:0.5rem 0.4rem;">PROMPT</th>
                    <th style="padding:0.5rem 0.4rem;">COMPL</th>
                    <th style="padding:0.5rem 0.4rem;">CACHED</th>
                    <th style="padding:0.5rem 0.4rem;">TOTAL</th>
                    <th style="padding:0.5rem 0.4rem;">KOSTEN</th>
                    <th style="padding:0.5rem 0.4rem;">STATUS</th>
                  </tr>
                </thead>
                <tbody id="nineRouterHistoryTableBody">
                  {% if stats.nine_router and stats.nine_router.recent_history %}
                    {% for r in stats.nine_router.recent_history %}
                    <tr style="border-bottom:1px solid rgba(255,255,255,0.08); font-family:var(--mono-family);">
                      <td style="padding:0.45rem 0.4rem; color:var(--c-gold);">#{{ r.id }}</td>
                      <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:#ccc;">{{ r.time_display }}</td>
                      <td style="padding:0.45rem 0.4rem;"><span style="color:var(--c-blue); font-weight:700;">{{ r.provider|upper }}</span></td>
                      <td style="padding:0.45rem 0.4rem; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="{{ r.model }}">{{ r.model }}</td>
                      <td style="padding:0.45rem 0.4rem; white-space:nowrap;">{{ r.prompt_tokens }}</td>
                      <td style="padding:0.45rem 0.4rem; white-space:nowrap;">{{ r.completion_tokens }}</td>
                      <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:var(--c-blue);">{{ r.cached_tokens }}</td>
                      <td style="padding:0.45rem 0.4rem; white-space:nowrap; font-weight:700; color:var(--c-primary);">{{ r.total_tokens }}</td>
                      <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:var(--c-gold);">{{ r.cost_formatted }}</td>
                      <td style="padding:0.45rem 0.4rem;">
                        <span class="badge-status {% if r.status == 'ok' %}badge-online{% else %}badge-offline{% endif %}" style="padding:0.15rem 0.4rem; font-size:0.75rem;">
                          {{ r.status|upper }}
                        </span>
                      </td>
                    </tr>
                    {% endfor %}
                  {% else %}
                    <tr>
                      <td colspan="10" style="padding:0.8rem; text-align:center; color:#888;">Keine Transaktionen aufgezeichnet.</td>
                    </tr>
                  {% endif %}
                </tbody>
              </table>
            </div>
          </div>

          <!-- Hermes Donut & Model Table -->
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap:1rem; margin-top:1.25rem; width:100%; min-width:0;">
            <!-- Donut Canvas Card -->
            <div class="lcars-card" style="min-height:320px; display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; min-width:0;">
              <div class="card-head-title" style="align-self:flex-start; margin-bottom:0.75rem;">HERMES MODELL TOKEN-VERTEILUNG</div>
              <div style="position:relative; width:100%; max-width:300px; height:260px; min-width:0;">
                <canvas id="hermesChart"></canvas>
              </div>
            </div>

            <!-- Model Table Card -->
            <div class="lcars-card" style="width:100%; min-width:0; overflow-x:auto;">
              <div class="card-head-title" style="margin-bottom:0.75rem;">HERMES MODELLE & KOSTEN</div>
              <div style="width:100%; overflow-x:auto;">
                <table style="width:100%; border-collapse:collapse; font-size:0.86rem; text-align:left;">
                  <thead>
                    <tr style="border-bottom:2px solid var(--c-primary); color:var(--c-primary);">
                      <th style="padding:0.5rem 0.4rem;">MODELL</th>
                      <th style="padding:0.5rem 0.4rem;">TOKENS</th>
                      <th style="padding:0.5rem 0.4rem;">SESS.</th>
                      <th style="padding:0.5rem 0.4rem;">KOSTEN</th>
                    </tr>
                  </thead>
                  <tbody id="hermesTableBody">
                    {% for m in stats.hermes.models %}
                    <tr style="border-bottom:1px solid rgba(255,255,255,0.08);">
                      <td style="padding:0.45rem 0.4rem; word-break:break-all;"><strong>{{ m.model }}</strong></td>
                      <td style="padding:0.45rem 0.4rem; white-space:nowrap;">{{ m.total_formatted }}</td>
                      <td style="padding:0.45rem 0.4rem;">{{ m.sessions }}</td>
                      <td style="padding:0.45rem 0.4rem; white-space:nowrap;">{{ m.cost_formatted }}</td>
                    </tr>
                    {% endfor %}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </section>

        <!-- KATEGORIE 5: CONFIG & FARBMODI -->
        <section class="lcars-section" id="section-config">
          <div class="lcars-header-bar">
            <h2>SYSTEM CONFIG & FARBMODI</h2>
            <span class="lcars-pill-tag">TERMINAL OPTIONEN</span>
          </div>

          <p style="margin-bottom:1.1rem; color:var(--c-gold);">
            WÄHLEN SIE DEN FARBMODUS FÜR DAS GESAMTE DASHBOARD AUS. EINSTELLUNGEN WERDEN AUTOMATISCH GESPEICHERT.
          </p>

          <div class="theme-selector-grid">
            <!-- Classic TNG -->
            <button class="theme-btn active-theme" onclick="setLcarsTheme('classic')" id="theme-btn-classic">
              <span style="font-weight:700; font-size:1.15rem;">CLASSIC 24TH C.</span>
              <span style="font-size:0.8rem; color:#aaa;">TNG / DS9 / OKUDA ORIGINAL</span>
              <div class="theme-swatches">
                <div class="theme-swatch" style="background-color:#eb943a;"></div>
                <div class="theme-swatch" style="background-color:#baa4e5;"></div>
                <div class="theme-swatch" style="background-color:#8899ff;"></div>
                <div class="theme-swatch" style="background-color:#d29b7f;"></div>
                <div class="theme-swatch" style="background-color:#cf4f4f;"></div>
              </div>
            </button>

            <!-- Nemesis Blue -->
            <button class="theme-btn" onclick="setLcarsTheme('nemesis')" id="theme-btn-nemesis">
              <span style="font-weight:700; font-size:1.15rem;">NEMESIS BLUE</span>
              <span style="font-size:0.8rem; color:#aaa;">FIRST CONTACT / TACTICAL</span>
              <div class="theme-swatches">
                <div class="theme-swatch" style="background-color:#6699ff;"></div>
                <div class="theme-swatch" style="background-color:#88bbff;"></div>
                <div class="theme-swatch" style="background-color:#0048f0;"></div>
                <div class="theme-swatch" style="background-color:#ff8833;"></div>
                <div class="theme-swatch" style="background-color:#da2537;"></div>
              </div>
            </button>

            <!-- Lower Decks -->
            <button class="theme-btn" onclick="setLcarsTheme('lowerdecks')" id="theme-btn-lowerdecks">
              <span style="font-weight:700; font-size:1.15rem;">LOWER DECKS</span>
              <span style="font-size:0.8rem; color:#aaa;">CALIFORNIA CLASS GOLD</span>
              <div class="theme-swatches">
                <div class="theme-swatch" style="background-color:#ffaa44;"></div>
                <div class="theme-swatch" style="background-color:#ff7700;"></div>
                <div class="theme-swatch" style="background-color:#ffcc99;"></div>
                <div class="theme-swatch" style="background-color:#ffeecc;"></div>
                <div class="theme-swatch" style="background-color:#ff4400;"></div>
              </div>
            </button>

            <!-- Lower Decks PADD -->
            <button class="theme-btn" onclick="setLcarsTheme('lowerdecks-padd')" id="theme-btn-lowerdecks-padd">
              <span style="font-weight:700; font-size:1.15rem;">LOWER DECKS PADD</span>
              <span style="font-size:0.8rem; color:#aaa;">USS CERRITOS PADD INTERFACE</span>
              <div class="theme-swatches">
                <div class="theme-swatch" style="background-color:#5588ee;"></div>
                <div class="theme-swatch" style="background-color:#66ccff;"></div>
                <div class="theme-swatch" style="background-color:#7799dd;"></div>
                <div class="theme-swatch" style="background-color:#88eeff;"></div>
                <div class="theme-swatch" style="background-color:#ff3500;"></div>
              </div>
            </button>

            <!-- Picard 25th Century -->
            <button class="theme-btn" onclick="setLcarsTheme('picard')" id="theme-btn-picard">
              <span style="font-weight:700; font-size:1.15rem;">PICARD 25TH C.</span>
              <span style="font-size:0.8rem; color:#aaa;">TITAN-A / LA SIRENA MODERN</span>
              <div class="theme-swatches">
                <div class="theme-swatch" style="background-color:#37a6d1;"></div>
                <div class="theme-swatch" style="background-color:#41c4f7;"></div>
                <div class="theme-swatch" style="background-color:#2a7193;"></div>
                <div class="theme-swatch" style="background-color:#9ea5ba;"></div>
                <div class="theme-swatch" style="background-color:#e7442a;"></div>
              </div>
            </button>

            <!-- Voyager -->
            <button class="theme-btn" onclick="setLcarsTheme('voyager')" id="theme-btn-voyager">
              <span style="font-weight:700; font-size:1.15rem;">VOYAGER</span>
              <span style="font-size:0.8rem; color:#aaa;">USS VOYAGER NCC-74656</span>
              <div class="theme-swatches">
                <div class="theme-swatch" style="background-color:#55a7ff;"></div>
                <div class="theme-swatch" style="background-color:#fb9004;"></div>
                <div class="theme-swatch" style="background-color:#ffbb33;"></div>
                <div class="theme-swatch" style="background-color:#828cad;"></div>
                <div class="theme-swatch" style="background-color:#2288ff;"></div>
              </div>
            </button>
          </div>

          <!-- LCARS RECHTEVERWALTUNG // COMMAND CODE ZUGRIFFSKONTROLLE -->
          <div class="lcars-card" id="configPermissionsCard" style="margin-top: 1.25rem; margin-bottom: 1.25rem; width: 100%; border-left: 6px solid var(--c-primary);">
            <div class="card-head" style="display:flex; justify-content:space-between; align-items:center;">
              <div style="display:flex; align-items:center; gap:0.6rem;">
                <span class="card-head-icon" id="permHeadIcon">🔒</span>
                <span class="card-head-title" style="color:var(--c-primary); font-size:1.15rem;">LCARS SICHERHEITSPROTOKOLL // RECHTEVERWALTUNG</span>
              </div>
              <span id="permStatusBadge" class="badge-status" style="background-color: var(--c-red); color: #fff;">GESPERRT // STUFE 1</span>
            </div>

            <p style="color:var(--c-gold); font-size:0.9rem; margin-bottom:1rem;">
              ZUGANGSKONTROLLE FÜR SENSIBLE BEREICHE. GESPERRTE BEREICHE WERDEN ERST NACH EINGABE DES COMMAND CODES IN DER NAVIGATION ANGEZEIGT (INITIAL-CODE: 0901).
            </p>

            <!-- Locked State View (Command Code Input & Keypad) -->
            <div id="permLockedView" style="display:block; background:rgba(0,0,0,0.5); border:1px solid rgba(235,148,58,0.3); border-radius:8px; padding:1.25rem;">
              <div style="max-width: 420px; margin: 0 auto; text-align: center;">
                <div style="font-family:var(--font-family); font-size:1.25rem; color:var(--c-primary); letter-spacing:0.08em; margin-bottom:0.5rem; text-transform:uppercase;">
                  AUTHORISIERUNG ERFORDERLICH
                </div>
                <div style="font-family:var(--mono-family); font-size:0.85rem; color:#aaa; margin-bottom:1.2rem;">
                  COMMAND CODE EINGEBEN UM DIE RECHTEVERWALTUNG ZU ENTSPERREN
                </div>

                <div style="display:flex; gap:0.5rem; justify-content:center; align-items:center; margin-bottom:1rem;">
                  <input type="password" id="configPinInput" maxlength="10" placeholder="••••" class="lcars-input" style="width:180px; font-size:1.6rem; text-align:center; letter-spacing:0.3em; font-family:var(--mono-family);" onkeydown="if(event.key==='Enter') verifyConfigPin();">
                  <button type="button" class="left-action-btn" onclick="verifyConfigPin()" style="padding:0.6rem 1.2rem; font-size:0.95rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
                    <span>🔓</span> <span>LOGIN</span>
                  </button>
                </div>

                <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:0.4rem; max-width:210px; margin:0 auto 1rem auto;">
                  <button type="button" class="keypad-btn" onclick="appendConfigPin('1')">1</button>
                  <button type="button" class="keypad-btn" onclick="appendConfigPin('2')">2</button>
                  <button type="button" class="keypad-btn" onclick="appendConfigPin('3')">3</button>
                  <button type="button" class="keypad-btn" onclick="appendConfigPin('4')">4</button>
                  <button type="button" class="keypad-btn" onclick="appendConfigPin('5')">5</button>
                  <button type="button" class="keypad-btn" onclick="appendConfigPin('6')">6</button>
                  <button type="button" class="keypad-btn" onclick="appendConfigPin('7')">7</button>
                  <button type="button" class="keypad-btn" onclick="appendConfigPin('8')">8</button>
                  <button type="button" class="keypad-btn" onclick="appendConfigPin('9')">9</button>
                  <button type="button" class="keypad-btn keypad-special" onclick="clearConfigPin()">CLR</button>
                  <button type="button" class="keypad-btn" onclick="appendConfigPin('0')">0</button>
                  <button type="button" class="keypad-btn keypad-special" onclick="verifyConfigPin()">ENTER</button>
                </div>

                <div id="configPinError" style="display:none; color:var(--c-red); font-family:var(--mono-family); font-size:0.85rem; margin-top:0.5rem;">
                  ZUGRIFF VERWEIGERT // UNGÜLTIGER COMMAND CODE
                </div>
              </div>
            </div>

            <!-- Unlocked Management View -->
            <div id="permUnlockedView" style="display:none;">
              <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:0.75rem 1rem; margin-bottom:1.25rem;">
                <div style="display:flex; align-items:center; gap:0.6rem;">
                  <span style="color:#44dd88; font-size:1.2rem;">●</span>
                  <span style="font-family:var(--mono-family); font-size:0.9rem; color:#44dd88; font-weight:700;">COMMAND TERMINAL ENTSPERRT // SICHERHEITSSTUFE ALPHA</span>
                </div>
                <button type="button" class="left-action-btn" onclick="lockPermissionsSession()" style="padding:0.35rem 0.8rem; font-size:0.8rem; border-color:var(--c-red); color:var(--c-red);">
                  <span>🔒</span> <span>JETZT SPERREN</span>
                </button>
              </div>

              <form id="permissionsForm" onsubmit="savePermissionsConfig(event)">
                <!-- Bereichs-Sperren Grid -->
                <div style="margin-bottom: 1.5rem;">
                  <div style="font-family:var(--font-family); font-size:1.05rem; font-weight:700; color:var(--c-primary); letter-spacing:0.05em; margin-bottom:0.6rem; text-transform:uppercase;">
                    1. BEREICHS-SPERREN KONFIGURIEREN (SICHTBARKEIT IN NAVIGATION)
                  </div>
                  <div style="font-size:0.85rem; color:#aaa; margin-bottom:0.75rem;">
                    Aktivieren Sie die Checkbox für Bereiche, die gesperrt sein sollen. Gesperrte Bereiche werden erst in der Navigationsleiste angezeigt, wenn der Command Code eingegeben wurde.
                  </div>

                  <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:0.75rem;">
                    <!-- SYSTEM -->
                    <label class="perm-checkbox-card">
                      <input type="checkbox" id="permLock_system" value="system" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name">SYSTEM</span>
                        <span class="perm-desc">System Status &amp; Sensor Verlauf</span>
                      </div>
                    </label>

                    <!-- SERVICES -->
                    <label class="perm-checkbox-card">
                      <input type="checkbox" id="permLock_services" value="services" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name">SERVICES</span>
                        <span class="perm-desc">Web-Services &amp; Scanner</span>
                      </div>
                    </label>

                    <!-- KI-AGENTEN -->
                    <label class="perm-checkbox-card">
                      <input type="checkbox" id="permLock_agents" value="agents" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name">KI-AGENTEN</span>
                        <span class="perm-desc">9Router, Hermes, IDE</span>
                      </div>
                    </label>

                    <!-- KI-INFO -->
                    <label class="perm-checkbox-card">
                      <input type="checkbox" id="permLock_ai-info" value="ai-info" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name">KI-INFO</span>
                        <span class="perm-desc">Telemetrie &amp; Modell-Charts</span>
                      </div>
                    </label>

                    <!-- CONFIG -->
                    <label class="perm-checkbox-card">
                      <input type="checkbox" id="permLock_config" value="config" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name">CONFIG</span>
                        <span class="perm-desc">System &amp; Alarm Einstellungen</span>
                      </div>
                    </label>

                    <!-- FANTASY -->
                    <label class="perm-checkbox-card">
                      <input type="checkbox" id="permLock_fantasy" value="fantasy" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name">FANTASY</span>
                        <span class="perm-desc">ESPN Fantasy Football</span>
                      </div>
                    </label>

                    <!-- SOLAR -->
                    <label class="perm-checkbox-card">
                      <input type="checkbox" id="permLock_solar" value="solar" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name">SOLAR</span>
                        <span class="perm-desc">Balkonsolar Energiemanagement</span>
                      </div>
                    </label>

                    <!-- HOME ASSISTANT -->
                    <label class="perm-checkbox-card">
                      <input type="checkbox" id="permLock_homeassistant" value="homeassistant" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name">ASSISTANT</span>
                        <span class="perm-desc">Smart Home Steuerung</span>
                      </div>
                    </label>

                    <!-- ZYKLUS (NEW) -->
                    <label class="perm-checkbox-card" style="border-color:var(--c-secondary);">
                      <input type="checkbox" id="permLock_cycle" value="cycle" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name" style="color:var(--c-secondary);">ZYKLUS</span>
                        <span class="perm-desc">Partnerinnen-Zyklus Tracker</span>
                      </div>
                    </label>

                    <!-- PULSECAST (NEW) -->
                    <label class="perm-checkbox-card" style="border-color:var(--c-butterscotch);">
                      <input type="checkbox" id="permLock_pulsecast" value="pulsecast" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name" style="color:var(--c-butterscotch);">PULSECAST</span>
                        <span class="perm-desc">Media &amp; Download Hub</span>
                      </div>
                    </label>

                    <!-- GEMINI LIVE / SUBRAUM COMM (NEW) -->
                    <label class="perm-checkbox-card" style="border-color:var(--c-secondary);">
                      <input type="checkbox" id="permLock_gemini_live" value="gemini_live" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name" style="color:var(--c-secondary);">SUBRAUM COMM</span>
                        <span class="perm-desc">Gemini 3.8 Live SST Audio-Relay</span>
                      </div>
                    </label>

                    <!-- PIMMEL REMOTE NODE -->
                    <label class="perm-checkbox-card" style="border-color:#8899ff;">
                      <input type="checkbox" id="permLock_pimmel" value="pimmel" class="perm-lock-cb">
                      <div class="perm-card-info">
                        <span class="perm-name" style="color:#8899ff;">PIMMEL</span>
                        <span class="perm-desc">Remote Node // 9Router Hub (100.88.215.98)</span>
                      </div>
                    </label>
                  </div>
                </div>

                <!-- Command Code Ändern -->
                <div style="margin-bottom:1.5rem; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:1rem;">
                  <div style="font-family:var(--font-family); font-size:1.05rem; font-weight:700; color:var(--c-gold); letter-spacing:0.05em; margin-bottom:0.6rem; text-transform:uppercase;">
                    2. COMMAND CODE ÄNDERN (OPTIONAL)
                  </div>
                  <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:1rem;">
                    <div class="config-field">
                      <label for="permNewCode" style="font-size:0.85rem; font-weight:700; color:var(--c-gold); margin-bottom:0.35rem; display:block;">
                        NEUER COMMAND CODE
                      </label>
                      <input type="password" id="permNewCode" placeholder="Leer lassen falls unverändert" class="lcars-input">
                    </div>
                    <div class="config-field">
                      <label for="permNewCodeConfirm" style="font-size:0.85rem; font-weight:700; color:var(--c-gold); margin-bottom:0.35rem; display:block;">
                        NEUEN CODE WIEDERHOLEN
                      </label>
                      <input type="password" id="permNewCodeConfirm" placeholder="Wiederholen" class="lcars-input">
                    </div>
                  </div>
                </div>

                <!-- Save Button & Feedback -->
                <div style="display:flex; justify-content:flex-end; gap:0.75rem; align-items:center;">
                  <div id="permSaveFeedback" style="display:none; font-family:var(--mono-family); font-size:0.85rem; color:#44dd88;"></div>
                  <button type="submit" class="left-action-btn" id="btnSavePerm" style="padding:0.5rem 1.25rem; font-size:0.9rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
                    <span>💾</span> <span>RECHTE-KONFIGURATION SPEICHERN</span>
                  </button>
                </div>
              </form>

              <!-- 3. LCARS BENUTZER- & ZUGRIFFSVERWALTUNG (*.PIMMEL.SITE) -->
              <div style="margin-top:2rem; border-top:2px solid var(--c-primary); padding-top:1.5rem;">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.75rem; margin-bottom:1rem;">
                  <div>
                    <div style="font-family:var(--font-family); font-size:1.25rem; font-weight:700; color:var(--c-primary); letter-spacing:0.06em; text-transform:uppercase;">
                      3. LCARS BENUTZER- &amp; ZUGRIFFSVERWALTUNG (*.PIMMEL.SITE)
                    </div>
                    <div style="font-family:var(--mono-family); font-size:0.85rem; color:#aaa;">
                      Zentrale Accounts für geschützte Dienste via Cloudflare Named Tunnel (cast, tele, mat, head, port)
                    </div>
                  </div>
                  <div style="display:flex; gap:0.5rem;">
                    <button type="button" class="left-action-btn" onclick="openNewUserModal()" style="padding:0.45rem 1rem; font-size:0.85rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
                      <span>➕</span> <span>NEUER BENUTZER</span>
                    </button>
                    <button type="button" class="left-action-btn" onclick="loadLcarsUsers(); loadLcarsAuditLog();" style="padding:0.45rem 0.8rem; font-size:0.85rem; border-color:var(--c-gold); color:var(--c-gold);">
                      <span>⟳</span> <span>AKTUALISIEREN</span>
                    </button>
                  </div>
                </div>

                <!-- Benutzer-Tabelle -->
                <div style="overflow-x:auto; background:rgba(0,0,0,0.4); border:1px solid rgba(235,148,58,0.25); border-radius:6px; margin-bottom:1.5rem;">
                  <table class="data-table" style="width:100%; border-collapse:collapse; font-size:0.85rem;" id="lcarsUsersTable">
                    <thead>
                      <tr style="background:rgba(235,148,58,0.15); border-bottom:1px solid rgba(235,148,58,0.3); text-align:left; font-family:var(--mono-family); color:var(--c-gold);">
                        <th style="padding:0.6rem 0.75rem;">STATUS</th>
                        <th style="padding:0.6rem 0.75rem;">BENUTZER</th>
                        <th style="padding:0.6rem 0.75rem;">NAME / NOTIZ</th>
                        <th style="padding:0.6rem 0.75rem;">BERECHTIGTE DIENSTE</th>
                        <th style="padding:0.6rem 0.75rem;">LETZTER LOGIN</th>
                        <th style="padding:0.6rem 0.75rem; text-align:right;">AKTIONEN</th>
                      </tr>
                    </thead>
                    <tbody id="lcarsUsersTableBody">
                      <tr><td colspan="6" style="padding:1.5rem; text-align:center; color:#888; font-family:var(--mono-family);">Lade Benutzerdaten...</td></tr>
                    </tbody>
                  </table>
                </div>

                <!-- LCARS Audit-Log Terminal -->
                <div style="margin-top:1.5rem; background:#040407; border:1px solid rgba(136,153,255,0.3); border-radius:6px; padding:1rem;">
                  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.6rem;">
                    <span style="font-family:var(--mono-family); font-size:0.85rem; color:var(--c-blue); font-weight:700; letter-spacing:0.05em;">
                      🛡️ LCARS SUBRAUM AUTHENTIFIZIERUNGS-LOG (LETZTE 25 EREIGNISSE)
                    </span>
                    <button type="button" class="left-action-btn" onclick="loadLcarsAuditLog()" style="padding:0.2rem 0.6rem; font-size:0.75rem; border-color:var(--c-blue); color:var(--c-blue);">
                      <span>⟳</span> <span>LOG NEU LADEN</span>
                    </button>
                  </div>
                  <div id="lcarsAuditLogList" style="max-height:220px; overflow-y:auto; font-family:var(--mono-family); font-size:0.8rem; line-height:1.45; background:#000; padding:0.6rem; border-radius:4px; border:1px solid #1a1a24;">
                    <div style="color:#666;">Keine Authentifizierungsereignisse protokolliert.</div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- HOME ASSISTANT INTEGRATION CONFIG -->
          <div class="lcars-card" style="margin-top: 1.25rem; margin-bottom: 1.25rem; width: 100%; border-left: 6px solid var(--c-secondary);">
            <div class="card-head">
              <span class="card-head-title" style="color:var(--c-secondary); font-size:1.15rem;">HOME ASSISTANT // SMART HOME INTEGRATION</span>
              <span class="card-head-icon">🏠</span>
            </div>
            <p style="color:var(--c-gold); font-size:0.9rem; margin-bottom:1rem;">
              VERBINDEN SIE IHR HOME ASSISTANT SYSTEM. SOBALD DIE ZUGANGSDATEN EINGEGEBEN SIND, ERSCHEINT IN DER LINKEN LEISTE EIN BUTTON ZUR INTERAKTIVEN STEUERUNG ALLER RÄUME UND OBJEKTE.
            </p>

            <form id="homeAssistantForm" onsubmit="saveHomeAssistantConfig(event)">
              <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-bottom: 1.25rem;">

                <!-- URL -->
                <div class="config-field">
                  <label for="cfgHaUrl" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-primary);">
                    <span>🌐 HOME ASSISTANT URL</span>
                  </label>
                  <input type="text" id="cfgHaUrl" value="http://127.0.0.1:8123" class="lcars-input" placeholder="http://127.0.0.1:8123" required>
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Standard: http://127.0.0.1:8123</div>
                </div>

                <!-- Name in Sidebar -->
                <div class="config-field">
                  <label for="cfgHaName" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-gold);">
                    <span>🏷️ BUTTON-NAME (LINKE LEISTE)</span>
                  </label>
                  <input type="text" id="cfgHaName" value="Assistant" class="lcars-input" placeholder="Assistant" required>
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">z.B. Assistant, Smart Home, Quartier</div>
                </div>

                <!-- Username -->
                <div class="config-field">
                  <label for="cfgHaUser" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-secondary);">
                    <span>👤 BENUTZERNAME (OPTIONAL)</span>
                  </label>
                  <input type="text" id="cfgHaUser" value="cb" class="lcars-input" placeholder="cb">
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Home Assistant Benutzername</div>
                </div>

                <!-- Password -->
                <div class="config-field">
                  <label for="cfgHaPass" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-red);">
                    <span>🔒 PASSWORT (OPTIONAL)</span>
                  </label>
                  <input type="password" id="cfgHaPass" value="mymaajen" class="lcars-input" placeholder="••••••••">
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Wird sicher im Backend verwendet</div>
                </div>

                <!-- Long Lived Token -->
                <div class="config-field" style="grid-column: 1 / -1;">
                  <label for="cfgHaToken" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-secondary);">
                    <span>🔑 LANGLEBIGER ZUGANGS-TOKEN (BEARER)</span>
                    <span id="cfgHaTokenStatus" style="font-size:0.75rem; color:#44dd88;">● TOKEN HINTERLEGT</span>
                  </label>
                  <input type="password" id="cfgHaToken" class="lcars-input" placeholder="eyJhbGciOi... (optional wenn Benutzer/Passwort angegeben)">
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">
                    Alternativ: In HA unter Profil &gt; Sicherheit &gt; Langlebige Zugangs-Token erstellen
                  </div>
                </div>

                <!-- Enabled Checkbox -->
                <div class="config-field" style="grid-column: 1 / -1; display:flex; align-items:center; gap:0.6rem; padding-top:0.25rem;">
                  <input type="checkbox" id="cfgHaEnabled" checked style="transform:scale(1.3); cursor:pointer; accent-color:var(--c-secondary);">
                  <label for="cfgHaEnabled" style="font-size:0.92rem; font-weight:700; color:var(--c-text); cursor:pointer;">
                    HOME ASSISTANT INTEGRATION AKTIVIEREN &amp; IN LINKER LEISTE ANZEIGEN
                  </label>
                </div>

              </div>

              <!-- Status Box & Action Buttons -->
              <div style="background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:0.85rem 1rem; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.75rem;">
                <div>
                  <div style="font-size:0.75rem; color:#888; text-transform:uppercase;">Verbindungs-Status</div>
                  <div style="display:flex; align-items:center; gap:0.5rem; margin-top:0.25rem;">
                    <span id="cfgHaBadge" class="badge-status badge-online">ONLINE</span>
                    <span id="cfgHaStatusMsg" style="font-size:0.8rem; color:#bbb;">Bereit</span>
                  </div>
                </div>

                <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
                  <button type="button" class="left-action-btn" onclick="testHomeAssistantConnection()" id="btnTestHa" style="padding:0.4rem 0.8rem; font-size:0.82rem;">
                    <span>⚡</span> <span>VERBINDUNG TESTEN</span>
                  </button>
                  <button type="submit" class="left-action-btn" id="btnSaveHa" style="padding:0.4rem 1rem; font-size:0.85rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                    <span>💾</span> <span>EINSTELLUNGEN SPEICHERN</span>
                  </button>
                </div>
              </div>

              <div id="cfgHaSaveMsg" style="display:none; font-family:var(--mono-family); font-size:0.85rem; color:var(--c-secondary); margin-top:0.5rem;"></div>
            </form>
          </div>

          <!-- ALARM & SCHWELLWERTE (RED ALERT TRIGGER) -->
          <div class="lcars-card" style="margin-top: 1.25rem; margin-bottom: 1.25rem; width: 100%;">
            <div class="card-head">
              <span class="card-head-title" style="color:var(--c-primary); font-size:1.15rem;">ALARM-SCHWELLWERTE // AUTOMATISCHER ROTER ALARM</span>
              <span class="card-head-icon">🚨</span>
            </div>
            <p style="color:var(--c-gold); font-size:0.9rem; margin-bottom:1rem;">
              RED ALERT WIRD AUTOMATISCH AKTIVIERT, WENN EINER DER 4 WERTE LÄNGER ALS DIE EINGESTELLTE DAUER ÜBER DEM SCHWELLWERT LIEGT ODER DER 9ROUTER GATEWAY AUSFÄLLT.
            </p>

            <form id="thresholdsForm" onsubmit="saveThresholds(event)">
              <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-bottom: 1.25rem;">

                <!-- CPU Schwellwert -->
                <div class="config-field">
                  <label for="cfgCpuThresh" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-primary);">
                    <span>⚡ CPU SCHWELLWERT</span>
                    <span id="cfgCpuThreshVal">90%</span>
                  </label>
                  <input type="number" id="cfgCpuThresh" min="10" max="100" step="1" value="90" class="lcars-input" required oninput="document.getElementById('cfgCpuThreshVal').textContent = this.value + '%'">
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Aktuell: <span id="curCpuLive">--%</span></div>
                </div>

                <!-- RAM Schwellwert -->
                <div class="config-field">
                  <label for="cfgRamThresh" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-secondary);">
                    <span>💾 RAM SCHWELLWERT</span>
                    <span id="cfgRamThreshVal">90%</span>
                  </label>
                  <input type="number" id="cfgRamThresh" min="10" max="100" step="1" value="90" class="lcars-input" required oninput="document.getElementById('cfgRamThreshVal').textContent = this.value + '%'">
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Aktuell: <span id="curRamLive">--%</span></div>
                </div>

                <!-- Speicher/Disk Schwellwert -->
                <div class="config-field">
                  <label for="cfgDiskThresh" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-blue);">
                    <span>💽 SPEICHER (ROOT /) SCHWELLWERT</span>
                    <span id="cfgDiskThreshVal">90%</span>
                  </label>
                  <input type="number" id="cfgDiskThresh" min="10" max="100" step="1" value="90" class="lcars-input" required oninput="document.getElementById('cfgDiskThreshVal').textContent = this.value + '%'">
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Aktuell: <span id="curDiskLive">--%</span></div>
                </div>

                <!-- Temperatur Schwellwert -->
                <div class="config-field">
                  <label for="cfgTempThresh" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-red);">
                    <span>🌡️ TEMPERATUR SCHWELLWERT</span>
                    <span id="cfgTempThreshVal">80°C</span>
                  </label>
                  <input type="number" id="cfgTempThresh" min="30" max="105" step="1" value="80" class="lcars-input" required oninput="document.getElementById('cfgTempThreshVal').textContent = this.value + '°C'">
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Aktuell: <span id="curTempLive">--°C</span> (Limit ~85°C)</div>
                </div>

                <!-- Haltezeit / Dauer -->
                <div class="config-field">
                  <label for="cfgDuration" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-gold);">
                    <span>⏱️ ALARM-HALTEZEIT (DAUER)</span>
                    <span id="cfgDurationVal">60 Sek</span>
                  </label>
                  <input type="number" id="cfgDuration" min="5" max="600" step="5" value="60" class="lcars-input" required oninput="document.getElementById('cfgDurationVal').textContent = this.value + ' Sek'">
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Standard: 60s (1 Minute kontinuierlich über Limit)</div>
                </div>

                <!-- Gateway Check Interval -->
                <div class="config-field">
                  <label for="cfgGwInterval" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-primary);">
                    <span>⚙️ 9ROUTER TEST-INTERVALL</span>
                    <span id="cfgGwIntervalVal">10 Min</span>
                  </label>
                  <input type="number" id="cfgGwInterval" min="1" max="60" step="1" value="10" class="lcars-input" required oninput="document.getElementById('cfgGwIntervalVal').textContent = this.value + ' Min'">
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Standard: Alle 10 Minuten Gateway prüfen</div>
                </div>

                <!-- Gateway URL -->
                <div class="config-field" style="grid-column: 1 / -1;">
                  <label for="cfgGwUrl" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-primary);">
                    <span>🌐 9ROUTER GATEWAY URL // ENDPUNKT</span>
                  </label>
                  <input type="text" id="cfgGwUrl" value="http://127.0.0.1:20128" class="lcars-input" required>
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Lokaler 9Router Proxy & KI-Gateway Dienst (Port 20128)</div>
                </div>

                <!-- Antigravity IDE URL -->
                <div class="config-field" style="grid-column: 1 / -1;">
                  <label for="cfgIdeUrl" style="display:flex; justify-content:space-between; font-size:0.85rem; font-weight:700; margin-bottom:0.35rem; color:var(--c-gold);">
                    <span>🚀 ANTIGRAVITY IDE URL // ENTWICKLUNGSUMGEBUNG</span>
                  </label>
                  <input type="text" id="cfgIdeUrl" value="https://antigravity.google.com/r/f9e18040-ab9a-4f0c-9d4a-2b4f5281af22-v2?p=c%2F3a633d37-def3-419b-ab1e-8498973ae694%3Fsection%3Dc15db4e2-c36e-442e-850b-0d28e944e2c6" class="lcars-input">
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">Cloud-Link zur Google Antigravity Agentic IDE für dieses Dashboard</div>
                </div>

              </div>

              <!-- Live Gateway & Alert Status Box -->
              <div style="background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:0.85rem 1rem; margin-bottom:1.25rem; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.75rem;">
                <div>
                  <div style="font-size:0.75rem; color:#888; text-transform:uppercase;">9Router Gateway Status</div>
                  <div style="display:flex; align-items:center; gap:0.5rem; margin-top:0.25rem;">
                    <span id="cfgGwBadge" class="badge-status badge-online">ONLINE</span>
                    <span id="cfgGwTime" style="font-size:0.8rem; color:#bbb;">Letzter Test: --:--:--</span>
                    <span id="cfgGwErr" style="font-size:0.8rem; color:var(--c-red);"></span>
                  </div>
                </div>

                <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
                  <button type="button" class="left-action-btn" onclick="testGatewayNow()" id="btnTestGw" style="padding:0.4rem 0.8rem; font-size:0.82rem;">
                    <span>⚙️</span> <span>GATEWAY JETZT TESTEN</span>
                  </button>
                  <button type="button" class="left-action-btn" onclick="resetDefaultThresholds()" style="padding:0.4rem 0.8rem; font-size:0.82rem; border-color:#888; color:#bbb;">
                    <span>↺</span> <span>STANDARDS</span>
                  </button>
                  <button type="submit" class="left-action-btn" id="btnSaveThresholds" style="padding:0.4rem 1rem; font-size:0.85rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
                    <span>💾</span> <span>SCHWELLWERTE SPEICHERN</span>
                  </button>
                </div>
              </div>

              <div id="cfgSaveMsg" style="display:none; font-family:var(--mono-family); font-size:0.85rem; color:var(--c-primary); margin-top:0.5rem;"></div>
            </form>
          </div>

          <!-- Weitere Optionen -->
          <div class="readout-grid">
            <div class="lcars-card" style="grid-column: 1 / -1;">
              <div class="card-head-title">LCARS SPRACH-KOMMUNIKATION & SUBRAUM COMM-LINK</div>
              <p style="font-size:0.88rem; color:var(--c-gold); margin:0.6rem 0;">
                BIDIREKTIONALE SPRACHSTEUERUNG IM STAR TREK LCARS STIL. STEUERE DAS DASHBOARD ODER SPRECHE DIREKT MIT 9ROUTER / HERMES AGENTEN.
              </p>
              <div style="display:flex; flex-wrap:wrap; gap:0.75rem; align-items:center; margin-top:0.75rem;">
                <button class="left-action-btn" id="btnToggleVoiceIn" onclick="toggleVoiceInputSetting()">
                  <span>🎙️</span> <span id="cfgVoiceInLabel">SPRACHEINGABE: AKTIV</span>
                </button>
                <button class="left-action-btn" id="btnToggleVoiceOut" onclick="toggleVoiceOutputSetting()">
                  <span>🔊</span> <span id="cfgVoiceOutLabel">SPRACHAUSGABE: AKTIV</span>
                </button>
                <button class="left-action-btn" id="btnToggleWakeWord" onclick="toggleWakeWordSetting()">
                  <span>👂</span> <span id="cfgWakeWordLabel">WAKE-WORD 'COMPUTER': AUS</span>
                </button>
                <button class="left-action-btn" onclick="testLcarsVoice()">
                  <span>▶</span> <span>COMPUTER-STIMME TESTEN</span>
                </button>
                <button class="left-action-btn" onclick="playLcarsChirp()">
                  <span>🔔</span> <span>COMM-CHIRP TESTEN</span>
                </button>
                <button class="left-action-btn" onclick="requestMicPermission()">
                  <span>🔐</span> <span>MIKROFON-FREIGABE ANFORDERN</span>
                </button>
              </div>
              <div style="margin-top:0.85rem; display:flex; align-items:center; gap:0.75rem; flex-wrap:wrap;">
                <label style="font-family:var(--mono-family); font-size:0.82rem; color:var(--c-secondary);">BEVORZUGTE STIMME:</label>
                <select id="cfgVoiceSelect" class="lcars-select" onchange="onVoiceSelectChange(this.value)" style="max-width:340px; font-size:0.82rem; padding: 0.35rem 0.6rem;"></select>
              </div>
            </div>

            <div class="lcars-card">
              <div class="card-head-title">AUDIO & SOUND-EFFEKTE</div>
              <p style="font-size:0.88rem; color:var(--c-gold); margin:0.6rem 0;">
                AUTHENTISCHE SYSTEM-BEEPS VIA WEB AUDIO API SYNTHESIZER.
              </p>
              <button class="left-action-btn" onclick="toggleAudio(); playLcarsBeep(880, 1760);">
                <span>🔊</span> <span id="cfgAudioLabel">SOUND EFFEKTE UMSCHALTEN</span>
              </button>
            </div>

            <div class="lcars-card">
              <div class="card-head-title">VOLLBILD-MODUS (FULLSCREEN)</div>
              <p style="font-size:0.88rem; color:var(--c-gold); margin:0.6rem 0;">
                OPTIMIERT FÜR BROWSER, TERMINAL & WANDMONITORE.
              </p>
              <button class="left-action-btn" onclick="toggleFullscreen()">
                <span>⛶</span> <span>FULLSCREEN AKTIVIEREN / BEENDEN</span>
              </button>
            </div>

            <div class="lcars-card">
              <div class="card-head-title">TELEMETRIE REFRESH-RATE</div>
              <p style="font-size:0.88rem; color:var(--c-gold); margin:0.6rem 0;">
                AKTUELL: <span id="refreshRateDisplay">3 SEKUNDEN</span>
              </p>
              <div style="display:flex; gap:0.35rem;">
                <button class="left-action-btn" onclick="setRefreshInterval(2000)">2s</button>
                <button class="left-action-btn" onclick="setRefreshInterval(3000)">3s</button>
                <button class="left-action-btn" onclick="setRefreshInterval(5000)">5s</button>
                <button class="left-action-btn" onclick="setRefreshInterval(10000)">10s</button>
              </div>
            </div>
          </div>
        </section>

        <!-- KATEGORIE 6: ESPN FANTASY FOOTBALL (INCOMPLETE PASS) -->
        <section class="lcars-section" id="section-fantasy">
          <div class="lcars-header-bar">
            <span class="lcars-pill-tag">ESPN // LIVE METRIKEN</span>
            <h2 id="fantasySectionTitle">LCARS SUBRAUM RELAY // INCOMPLETE PASS LIGA</h2>
            <div style="margin-left:auto; display:flex; align-items:center; gap:0.6rem; flex-wrap:wrap;">
              <!-- 3-STUFEN-MODUS-SCHALTER -->
              <div class="fantasy-mode-selector" style="display:inline-flex; align-items:center; background:rgba(0,0,0,0.6); border:1px solid rgba(255,255,255,0.18); border-radius:14px; padding:2px; gap:2px;">
                <button type="button" id="btn-fantasy-mode-manual" onclick="setFantasyMode('manual')" class="fantasy-mode-btn active" style="font-size:0.75rem; padding:0.25rem 0.65rem; border-radius:12px; border:none; cursor:pointer; font-family:var(--font-family); font-weight:700; letter-spacing:0.04em; background:var(--c-butterscotch); color:#000; transition:all 0.2s ease;">
                  MANUELL
                </button>
                <button type="button" id="btn-fantasy-mode-semi" onclick="setFantasyMode('semi')" class="fantasy-mode-btn" style="font-size:0.75rem; padding:0.25rem 0.65rem; border-radius:12px; border:none; cursor:pointer; font-family:var(--font-family); font-weight:700; letter-spacing:0.04em; background:transparent; color:#888; transition:all 0.2s ease;">
                  SEMI-AUTO
                </button>
                <button type="button" id="btn-fantasy-mode-full" onclick="setFantasyMode('full')" class="fantasy-mode-btn" style="font-size:0.75rem; padding:0.25rem 0.65rem; border-radius:12px; border:none; cursor:pointer; font-family:var(--font-family); font-weight:700; letter-spacing:0.04em; background:transparent; color:#888; transition:all 0.2s ease;">
                  FULL-AUTO
                </button>
              </div>

              <!-- 5-STUFEN-RISIKOREGLER -->
              <div class="fantasy-risk-selector" style="display:inline-flex; align-items:center; background:rgba(0,0,0,0.6); border:1px solid rgba(255,255,255,0.18); border-radius:14px; padding:2px; gap:2px;">
                <span style="font-size:0.68rem; color:var(--c-gold); font-family:var(--font-family); font-weight:700; padding:0 0.35rem; letter-spacing:0.04em;">RISIKO:</span>
                <button type="button" id="btn-fantasy-risk-1" onclick="setFantasyRiskLevel(1)" class="fantasy-risk-btn" style="font-size:0.7rem; padding:0.22rem 0.45rem; border-radius:10px; border:none; cursor:pointer; font-family:var(--font-family); font-weight:700; background:transparent; color:#888; transition:all 0.2s ease;" title="1: Ultra-Konservativ (High Floor, minimales Risiko)">1: FLOOR</button>
                <button type="button" id="btn-fantasy-risk-2" onclick="setFantasyRiskLevel(2)" class="fantasy-risk-btn" style="font-size:0.7rem; padding:0.22rem 0.45rem; border-radius:10px; border:none; cursor:pointer; font-family:var(--font-family); font-weight:700; background:transparent; color:#888; transition:all 0.2s ease;" title="2: Konservativ">2: KONS</button>
                <button type="button" id="btn-fantasy-risk-3" onclick="setFantasyRiskLevel(3)" class="fantasy-risk-btn active" style="font-size:0.7rem; padding:0.22rem 0.45rem; border-radius:10px; border:none; cursor:pointer; font-family:var(--font-family); font-weight:700; background:var(--c-butterscotch); color:#000; transition:all 0.2s ease;" title="3: Ausgewogen (Standard)">3: AUSG</button>
                <button type="button" id="btn-fantasy-risk-4" onclick="setFantasyRiskLevel(4)" class="fantasy-risk-btn" style="font-size:0.7rem; padding:0.22rem 0.45rem; border-radius:10px; border:none; cursor:pointer; font-family:var(--font-family); font-weight:700; background:transparent; color:#888; transition:all 0.2s ease;" title="4: Offensiv (Ceiling / Matchup-Upside)">4: OFF</button>
                <button type="button" id="btn-fantasy-risk-5" onclick="setFantasyRiskLevel(5)" class="fantasy-risk-btn" style="font-size:0.7rem; padding:0.22rem 0.45rem; border-radius:10px; border:none; cursor:pointer; font-family:var(--font-family); font-weight:700; background:transparent; color:#888; transition:all 0.2s ease;" title="5: Boom-or-Bust (Maximales Upside)">5: BOOM</button>
              </div>

              <!-- FLASH TOGGLE BUTTON -->
              <button type="button" id="fantasyFlashToggleBtn" onclick="toggleFantasyFlash(event)" style="font-size:0.75rem; color:#44dd88; background:rgba(68,221,136,0.15); border:1px solid rgba(68,221,136,0.4); padding:0.25rem 0.65rem; border-radius:12px; font-family:var(--mono-family); cursor:pointer; letter-spacing:0.04em; transition:all 0.2s ease;" title="Lichtsignal bei Score aktivieren / deaktivieren">
                ⚡ FLASH: AN
              </button>

              <button id="fantasyTestFlashBtn" onclick="testFantasyFlash(event)" style="font-size:0.75rem; color:var(--c-primary); background:rgba(235,148,58,0.15); border:1px solid rgba(235,148,58,0.4); padding:0.25rem 0.65rem; border-radius:12px; font-family:var(--mono-family); cursor:pointer; letter-spacing:0.04em; transition:all 0.2s ease;" title="Flash-Signal auf light.esstisch testen">
                ⚡ TEST FLASH
              </button>
              <span id="fantasyCountdownBadge" style="font-size:0.8rem; color:#44dd88; background:rgba(68,221,136,0.15); border:1px solid rgba(68,221,136,0.4); padding:0.25rem 0.75rem; border-radius:12px; font-family:var(--mono-family); letter-spacing:0.04em;">
                ● REFRESH IN 30S
              </span>
            </div>
          </div>

          <!-- KI TOKEN & MODELL STATUSZEILE -->
          <div id="fantasyAiStatsStrip" style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:0.5rem; margin-top:0.6rem; padding:0.35rem 0.85rem; background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.08); border-radius:8px; font-size:0.75rem; font-family:var(--mono-family); color:#aaa;">
            <div style="display:flex; align-items:center; gap:0.5rem;">
              <span style="color:var(--c-blue); font-weight:700;">KI:</span>
              <span id="fantasyAiModelText">Gemini 3.8 Flash | ~1.2k Tokens / Run (&lt;0,03ct)</span>
            </div>
            <div id="fantasyAiRiskStatus" style="color:var(--c-gold); font-weight:700;">
              STRATEGIE: 3: AUSGEWOGEN (STANDARD)
            </div>
          </div>

          <!-- VORSCHLAG-CONTAINER (SEMI-MODUS PROPOSAL BANNER) -->
          <div id="fantasyProposalBanner" class="lcars-card" style="display:none; margin-top:1rem; border-left:4px solid var(--c-gold); background:rgba(237, 179, 120, 0.08); border-top:1px solid rgba(237, 179, 120, 0.3); border-right:1px solid rgba(237, 179, 120, 0.2); border-bottom:1px solid rgba(237, 179, 120, 0.2); border-radius:8px; padding:1rem 1.25rem;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem;">
              <div style="flex:1; min-width:280px;">
                <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.35rem;">
                  <span style="font-size:0.75rem; color:var(--c-gold); font-weight:800; letter-spacing:0.08em; background:rgba(237,179,120,0.2); padding:2px 8px; border-radius:10px;">
                    ⚠️ KI-EMPFEHLUNG // FREIGABE ERFORDERLICH
                  </span>
                  <span id="fantasyProposalConfidence" style="font-size:0.75rem; color:var(--c-blue); font-family:var(--mono-family);">
                    KONFIDENZ: --
                  </span>
                </div>
                <div id="fantasyProposalReason" style="font-size:1.05rem; font-weight:700; color:#fff; margin-bottom:0.35rem;">
                  --
                </div>
                <div id="fantasyProposalDetails" style="font-size:0.85rem; color:var(--c-butterscotch); font-family:var(--mono-family);">
                  --
                </div>
              </div>
              <div style="display:flex; gap:0.6rem; align-items:center;">
                <button type="button" id="fantasyProposalApplyBtn" style="font-size:0.8rem; font-weight:700; color:#000; background:#44dd88; border:none; padding:0.45rem 1.1rem; border-radius:14px; cursor:pointer; font-family:var(--font-family); letter-spacing:0.05em; transition:all 0.2s ease;" title="Vorschlag genehmigen und via ESPN anwenden">
                  ⚡ FREIGEBEN
                </button>
                <button type="button" id="fantasyProposalDismissBtn" style="font-size:0.8rem; font-weight:700; color:#fff; background:rgba(235,58,58,0.25); border:1px solid var(--c-red); padding:0.45rem 1.1rem; border-radius:14px; cursor:pointer; font-family:var(--font-family); letter-spacing:0.05em; transition:all 0.2s ease;" title="Vorschlag ablehnen / verwerfen">
                  ✕ ABLEHNEN
                </button>
              </div>
            </div>
          </div>

          <!-- STATUS & OVERVIEW CARDS -->
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:1rem; margin-top:1rem; width:100%;">
            <div class="lcars-card">
              <div class="card-head-title">MEIN TEAM</div>
              <div style="font-size:1.4rem; font-weight:700; color:var(--c-primary); letter-spacing:0.04em; margin-top:0.3rem;" id="fantasyCardTeamName">
                NORDERSTEDT RAILSGUNS
              </div>
              <div style="font-size:0.85rem; color:#aaa; margin-top:0.25rem;">
                Liga: <strong style="color:var(--c-gold);" id="fantasyCardLeagueName">Incomplete Pass</strong> (16 Teams)
              </div>
            </div>

            <div class="lcars-card">
              <div class="card-head-title">LIGA POSITION & BILANZ</div>
              <div style="display:flex; align-items:baseline; gap:0.6rem; margin-top:0.3rem;">
                <span style="font-size:1.6rem; font-weight:700; color:var(--c-gold);" id="fantasyCardRank">RANG #--</span>
                <span style="font-size:0.9rem; color:#aaa;" id="fantasyCardRecord">(0-0-0)</span>
              </div>
              <div style="font-size:0.85rem; color:#aaa; margin-top:0.25rem;">
                Gesamtpunkte: <strong style="color:var(--c-blue);" id="fantasyCardTotalPoints">-- PTS</strong>
              </div>
            </div>

            <div class="lcars-card">
              <div class="card-head-title">SPIELTAGS-STATUS</div>
              <div style="font-size:1.4rem; font-weight:700; color:var(--c-secondary); margin-top:0.3rem;" id="fantasyCardWeek">
                WEEK 1
              </div>
              <div style="font-size:0.85rem; color:#44dd88; margin-top:0.25rem;" id="fantasyCardMatchupStatus">
                ● LIVE IN PROGRESS
              </div>
            </div>
          </div>

          <!-- MATCHUP CARD -->
          <div class="lcars-card" style="margin-top:1.25rem;">
            <div class="card-head-title">AKTUELLER SPIELTAG // MATCHUP DUELL</div>
            <div id="fantasyMatchupContainer" style="margin-top:0.8rem;">
              <div style="display:grid; grid-template-columns: 1fr auto 1fr; gap:1rem; align-items:center; text-align:center;">
                
                <!-- My Team -->
                <div style="padding:1rem; background:rgba(235,148,58,0.08); border:1px solid rgba(235,148,58,0.3); border-radius:10px;">
                  <div style="font-size:0.8rem; color:var(--c-primary); font-weight:700; letter-spacing:0.06em;">MEIN TEAM</div>
                  <div style="font-size:1.3rem; font-weight:700; color:#fff; margin:0.3rem 0;" id="fantasyMatchupMyName">Norderstedt Railsguns</div>
                  <div style="font-size:2.4rem; font-weight:800; color:var(--c-gold); font-family:var(--mono-family);" id="fantasyMatchupMyScore">0.0</div>
                  <div style="font-size:0.85rem; color:#aaa; margin-top:0.2rem;">
                    Prognose: <span style="color:var(--c-blue);" id="fantasyMatchupMyProj">--</span> PTS
                  </div>
                  <div style="font-size:0.85rem; color:var(--c-primary); font-weight:700; margin-top:0.4rem;">
                    Siegchance: <span id="fantasyMatchupMyWinProb">--</span>%
                  </div>
                </div>

                <!-- VS Badge -->
                <div style="display:flex; flex-direction:column; align-items:center; justify-content:center;">
                  <span style="font-size:1.4rem; font-weight:800; color:var(--c-red); font-family:var(--font-family); letter-spacing:0.1em;">VS</span>
                  <span style="font-size:0.75rem; color:#888; margin-top:0.2rem;" id="fantasyMatchupWeekBadge">WEEK 1</span>
                </div>

                <!-- Opponent Team -->
                <div style="padding:1rem; background:rgba(136,153,255,0.08); border:1px solid rgba(136,153,255,0.3); border-radius:10px;">
                  <div style="font-size:0.8rem; color:var(--c-blue); font-weight:700; letter-spacing:0.06em;">GEGNER</div>
                  <div style="font-size:1.3rem; font-weight:700; color:#fff; margin:0.3rem 0;" id="fantasyMatchupOppName">Opponent</div>
                  <div style="font-size:2.4rem; font-weight:800; color:var(--c-gold); font-family:var(--mono-family);" id="fantasyMatchupOppScore">0.0</div>
                  <div style="font-size:0.85rem; color:#aaa; margin-top:0.2rem;">
                    Prognose: <span style="color:var(--c-blue);" id="fantasyMatchupOppProj">--</span> PTS
                  </div>
                  <div style="font-size:0.85rem; color:var(--c-blue); font-weight:700; margin-top:0.4rem;">
                    Siegchance: <span id="fantasyMatchupOppWinProb">--</span>%
                  </div>
                </div>

              </div>

              <!-- Win Probability Progress Bar -->
              <div style="margin-top:1.2rem;">
                <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:0.3rem; font-family:var(--mono-family);">
                  <span style="color:var(--c-primary);" id="fantasyProbLabelMy">Norderstedt Railsguns: 50%</span>
                  <span style="color:var(--c-blue);" id="fantasyProbLabelOpp">Gegner: 50%</span>
                </div>
                <div style="height:10px; width:100%; background:rgba(255,255,255,0.1); border-radius:5px; display:flex; overflow:hidden;">
                  <div id="fantasyProbBarMy" style="width:50%; background:var(--c-primary); transition:width 0.4s ease;"></div>
                  <div id="fantasyProbBarOpp" style="width:50%; background:var(--c-blue); transition:width 0.4s ease;"></div>
                </div>
              </div>
            </div>
          </div>

          <!-- ROSTER & STANDINGS 2-COLUMN GRID -->
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap:1.25rem; margin-top:1.25rem; width:100%;">
            
            <!-- KADER & AUFSTELLUNG -->
            <div class="lcars-card">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
                <div class="card-head-title" style="margin-bottom:0;">AUFSTELLUNG & KADER</div>
                <span class="lcars-pill-tag" style="font-size:0.75rem;">STARTERS & BENCH</span>
              </div>
              <div style="overflow-x:auto; max-height:480px; overflow-y:auto;">
                <table style="width:100%; border-collapse:collapse; font-size:0.86rem; text-align:left;">
                  <thead>
                    <tr style="border-bottom:2px solid var(--c-primary); color:var(--c-primary); font-family:var(--font-family); letter-spacing:0.06em; position:sticky; top:0; background:var(--c-card-bg); z-index:1;">
                      <th style="padding:0.4rem 0.3rem;">SLOT</th>
                      <th style="padding:0.4rem 0.4rem;">SPIELER</th>
                      <th style="padding:0.4rem 0.3rem;">TEAM</th>
                      <th style="padding:0.4rem 0.3rem;">STATUS</th>
                      <th style="padding:0.4rem 0.4rem; text-align:right;">PROJ</th>
                      <th style="padding:0.4rem 0.4rem; text-align:right;">LIVE</th>
                    </tr>
                  </thead>
                  <tbody id="fantasyRosterBody">
                    <tr><td colspan="6" style="padding:1rem; text-align:center; color:#888;">Lade Kaderdaten...</td></tr>
                  </tbody>
                </table>
              </div>
            </div>

            <!-- LIGA-TABELLE (STANDINGS) -->
            <div class="lcars-card">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
                <div class="card-head-title" style="margin-bottom:0;">LIGA-TABELLE // STANDINGS</div>
                <span class="lcars-pill-tag" style="font-size:0.75rem;">16 TEAMS</span>
              </div>
              <div style="overflow-x:auto; max-height:480px; overflow-y:auto;">
                <table style="width:100%; border-collapse:collapse; font-size:0.86rem; text-align:left;">
                  <thead>
                    <tr style="border-bottom:2px solid var(--c-primary); color:var(--c-primary); font-family:var(--font-family); letter-spacing:0.06em; position:sticky; top:0; background:var(--c-card-bg); z-index:1;">
                      <th style="padding:0.4rem 0.3rem;">#</th>
                      <th style="padding:0.4rem 0.4rem;">TEAM</th>
                      <th style="padding:0.4rem 0.4rem; text-align:center;">W-L-T</th>
                      <th style="padding:0.4rem 0.4rem; text-align:right;">PF</th>
                      <th style="padding:0.4rem 0.4rem; text-align:right;">PA</th>
                    </tr>
                  </thead>
                  <tbody id="fantasyStandingsBody">
                    <tr><td colspan="5" style="padding:1rem; text-align:center; color:#888;">Lade Tabelle...</td></tr>
                  </tbody>
                </table>
              </div>
            </div>

          </div>

        </section>

        <!-- KATEGORIE 7: HOME ASSISTANT HAUSSTEUERUNG -->
        <section class="lcars-section" id="section-homeassistant">
          <div class="lcars-header-bar">
            <h2 id="haSectionTitle">LCARS HAUSSTEUERUNG // HOME ASSISTANT</h2>
            <div style="display:flex; align-items:center; gap:0.6rem; flex-wrap:wrap;">
              <span id="haLiveBadge" class="badge-status badge-online">ONLINE</span>
              <span id="haStatsBadge" style="font-size:0.82rem; color:var(--c-primary); font-family:var(--mono-family); font-weight:700;">
                0 RÄUME // 0 OBJEKTE
              </span>
              <span id="haCountdownBadge" style="font-size:0.75rem; color:#888; font-family:var(--mono-family);">
                AUTO-REFRESH: 15s
              </span>
              <button class="left-action-btn" onclick="loadHomeAssistantData(true)" style="padding:0.25rem 0.65rem; font-size:0.8rem;">
                <span>⟳</span> <span>REFRESH</span>
              </button>
            </div>
          </div>

          <!-- Room Filter Tabs & Domain Search Bar -->
          <div style="margin: 0.9rem 0; display:flex; flex-wrap:wrap; gap:0.6rem; align-items:center; justify-content:space-between; background:rgba(0,0,0,0.35); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:0.6rem 0.8rem;">
            <!-- Dynamic Room Pills -->
            <div id="haRoomFilterBar" style="display:flex; flex-wrap:wrap; gap:0.4rem; align-items:center;">
              <button class="left-action-btn active-room-filter" onclick="setHaRoomFilter('all')" id="ha-room-btn-all" style="padding:0.3rem 0.75rem; font-size:0.82rem; border-color:var(--c-primary); color:var(--c-primary);">
                ALLE RÄUME
              </button>
            </div>

            <!-- Domain filter & Search -->
            <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
              <select id="haDomainFilter" onchange="filterHaEntities()" class="lcars-input" style="padding:0.35rem 0.65rem; font-size:0.82rem; width:auto;">
                <option value="all">ALLE TYPEN</option>
                <option value="automation">⚡ AUTOMATIONEN</option>
                <option value="light">💡 LICHTER</option>
                <option value="switch">🔌 SCHALTER / STECKDOSEN</option>
                <option value="climate">🌡️ KLIMA &amp; HEIZUNG</option>
                <option value="cover">🪟 ROLLOS &amp; JALOUSIEN</option>
                <option value="media_player">🎵 MEDIEN-PLAYER</option>
                <option value="vacuum">🤖 SAUGROBOTER</option>
                <option value="sensor">📊 SENSOREN</option>
                <option value="controllable">⚙️ NUR STEUERBARE</option>
              </select>
              <input type="text" id="haSearchInput" oninput="filterHaEntities()" placeholder="Objekt suchen..." class="lcars-input" style="padding:0.35rem 0.65rem; font-size:0.82rem; width:150px;">
            </div>
          </div>

          <!-- Container for Room Cards -->
          <div id="haRoomsContainer" style="display:flex; flex-direction:column; gap:1.25rem; margin-top:0.8rem;">
            <div style="padding:2rem; text-align:center; color:#888; font-family:var(--mono-family);">
              Lade Home Assistant Daten...
            </div>
          </div>
        </section>

        <!-- KATEGORIE 8: BALKONSOLAR & ENERGIE (DACHTERRASSE) -->
        <section class="lcars-section" id="section-solar">
          <div class="lcars-header-bar">
            <h2>LCARS ENERGIE-MANAGEMENT // BALKONSOLAR DACHTERRASSE</h2>
            <div style="display:flex; align-items:center; gap:0.6rem; flex-wrap:wrap;">
              <span id="solarLiveBadge" class="badge-status badge-online">ONLINE</span>
              <span id="solarCloudBadge" style="font-size:0.82rem; color:var(--c-gold); font-family:var(--mono-family); font-weight:700;">
                ANKER CLOUD: ONLINE // ECOTRACKER: ONLINE
              </span>
              <span id="solarCountdownBadge" style="font-size:0.75rem; color:#888; font-family:var(--mono-family);">
                AUTO-REFRESH: 15s
              </span>
              <button class="left-action-btn" onclick="loadSolarData(true)" style="padding:0.25rem 0.65rem; font-size:0.8rem;">
                <span>⟳</span> <span>REFRESH</span>
              </button>
            </div>
          </div>

          <!-- LCARS EPS ENERGIEFLUSS POWER-FLOW DIAGRAMM -->
          <div class="solar-flow-card">
            <div class="card-head">
              <span class="card-head-title" style="color:var(--c-gold); font-size:1.05rem;">
                ⚡ EPS POWER-GRID // AKTUELLER ENERGIEFLUSS DACHTERRASSE
              </span>
              <span class="lcars-pill-tag" id="solarFlowSummaryTag" style="background-color:var(--c-gold); color:#000;">
                LIVE NETZBEZUG
              </span>
            </div>
            <div class="solar-flow-grid">
              <!-- Node 1: Solar Erzeugung -->
              <div class="flow-node node-solar">
                <div class="flow-node-title">
                  <span style="color:var(--c-gold);">☀️ PHOTOVOLTAIK</span>
                  <span id="nodeSolarStatus" style="font-size:0.7rem; color:#888;">4 STRINGS</span>
                </div>
                <div class="flow-node-val" id="nodeSolarVal" style="color:var(--c-gold);">0 W</div>
                <div class="flow-node-sub" id="nodeSolarSub">PV Erzeugung Live</div>
              </div>

              <!-- Node 2: Speicher / Akku -->
              <div class="flow-node node-bat">
                <div class="flow-node-title">
                  <span style="color:var(--c-secondary);">🔋 SOLARBANK SPEICHER</span>
                  <span id="nodeBatStatus" style="font-size:0.7rem; color:var(--c-gold);">STANDBY</span>
                </div>
                <div class="flow-node-val" id="nodeBatVal" style="color:var(--c-secondary);">5%</div>
                <div class="flow-node-sub" id="nodeBatSub">80 Wh / 1600 Wh (14 °C)</div>
              </div>

              <!-- Node 3: Hausbedarf -->
              <div class="flow-node node-house">
                <div class="flow-node-title">
                  <span style="color:var(--c-primary);">🏠 HAUSNETZ BEDARF</span>
                  <span id="nodeHouseStatus" style="font-size:0.7rem; color:var(--c-primary);">AKTUELL</span>
                </div>
                <div class="flow-node-val" id="nodeHouseVal" style="color:var(--c-primary);">272 W</div>
                <div class="flow-node-sub" id="nodeHouseSub">Aktueller Gesamtverbrauch</div>
              </div>

              <!-- Node 4: Öffentliches Netz -->
              <div class="flow-node node-grid">
                <div class="flow-node-title">
                  <span style="color:var(--c-blue);">🌐 STROMNETZ (ECOTRACKER)</span>
                  <span id="nodeGridStatus" style="font-size:0.7rem; color:#44dd88;">STATUS OK</span>
                </div>
                <div class="flow-node-val" id="nodeGridVal" style="color:var(--c-blue);">272 W</div>
                <div class="flow-node-sub" id="nodeGridSub">Netzbezug // 0 W Einspeisung</div>
              </div>
            </div>
          </div>

          <!-- LCARS READOUT GRID (4 DETAILLIERTE TELEMETRIE-KARTEN) -->
          <div class="readout-grid" style="margin-bottom:1.25rem;">
            <!-- KARTE 1: PHOTOVOLTAIK (PV STRINGS & ERTRAG) -->
            <div class="lcars-card card-gold">
              <div class="card-head">
                <span class="card-head-title" style="color:var(--c-gold);">☀️ PHOTOVOLTAIK ERZEUGUNG</span>
                <span class="card-head-icon">☀️</span>
              </div>
              <div class="card-metric" id="cardSolarVal" style="color:var(--c-gold);">0 W</div>
              <div class="card-metric-sub" id="cardSolarSub">Gesamt-Solarleistung</div>
              
              <!-- 4 PV Strings Bar Readout -->
              <div style="margin-top:0.8rem; display:flex; flex-direction:column; gap:0.4rem;">
                <div>
                  <div style="display:flex; justify-content:space-between; font-size:0.75rem; font-family:var(--mono-family); margin-bottom:2px;">
                    <span>PV STRING 1</span>
                    <span id="pv1Val">0 W</span>
                  </div>
                  <div class="lcars-bar-track"><div class="lcars-bar-fill" id="pv1Bar" style="width:0%; background-color:var(--c-gold);"></div></div>
                </div>
                <div>
                  <div style="display:flex; justify-content:space-between; font-size:0.75rem; font-family:var(--mono-family); margin-bottom:2px;">
                    <span>PV STRING 2</span>
                    <span id="pv2Val">0 W</span>
                  </div>
                  <div class="lcars-bar-track"><div class="lcars-bar-fill" id="pv2Bar" style="width:0%; background-color:var(--c-gold);"></div></div>
                </div>
                <div>
                  <div style="display:flex; justify-content:space-between; font-size:0.75rem; font-family:var(--mono-family); margin-bottom:2px;">
                    <span>PV STRING 3</span>
                    <span id="pv3Val">0 W</span>
                  </div>
                  <div class="lcars-bar-track"><div class="lcars-bar-fill" id="pv3Bar" style="width:0%; background-color:var(--c-gold);"></div></div>
                </div>
                <div>
                  <div style="display:flex; justify-content:space-between; font-size:0.75rem; font-family:var(--mono-family); margin-bottom:2px;">
                    <span>PV STRING 4</span>
                    <span id="pv4Val">0 W</span>
                  </div>
                  <div class="lcars-bar-track"><div class="lcars-bar-fill" id="pv4Bar" style="width:0%; background-color:var(--c-gold);"></div></div>
                </div>
              </div>

              <!-- Ertrag & Umwelt Stats -->
              <div style="margin-top:0.9rem; padding-top:0.6rem; border-top:1px solid rgba(255,255,255,0.08);">
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Ertrag Gesamt:</span>
                  <span class="solar-detail-val" id="solYieldTotal">-- kWh</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">CO₂ Einsparung:</span>
                  <span class="solar-detail-val" id="solCo2Saved">-- kg</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Kostenersparnis:</span>
                  <span class="solar-detail-val" id="solCostSaved">-- €</span>
                </div>
              </div>
            </div>

            <!-- KARTE 2: ENERGIESPEICHER / AKKU -->
            <div class="lcars-card card-violet">
              <div class="card-head">
                <span class="card-head-title" style="color:var(--c-secondary);">🔋 SOLARBANK SPEICHER</span>
                <span class="card-head-icon">🔋</span>
              </div>
              <div class="card-metric" id="cardBatVal" style="color:var(--c-secondary);">5%</div>
              <div class="card-metric-sub" id="cardBatSub">Akkustand (SoC)</div>
              
              <!-- Akku Gauge Bar -->
              <div class="lcars-bar-track" style="margin-top:0.4rem; height:12px;">
                <div class="lcars-bar-fill" id="cardBatBar" style="width:5%; background-color:var(--c-secondary);"></div>
              </div>

              <!-- Akku Details -->
              <div style="margin-top:0.8rem; display:flex; flex-direction:column;">
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Gespeicherte Energie:</span>
                  <span class="solar-detail-val" id="solEnergyWh">-- Wh / 1600 Wh</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Ladeleistung:</span>
                  <span class="solar-detail-val" id="solBatCharge">0 W</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Entladeleistung:</span>
                  <span class="solar-detail-val" id="solBatDischarge">0 W</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Zelltemperatur:</span>
                  <span class="solar-detail-val" id="solBatTemp">-- °C</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">SoC Grenzen (Min/Max):</span>
                  <span class="solar-detail-val" id="solSocLimits">5% / 100%</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Akkuheizung:</span>
                  <span class="solar-detail-val" id="solBatHeating">Inaktiv (0 W)</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Betriebszustand:</span>
                  <span class="solar-detail-val" id="solOpState">--</span>
                </div>
              </div>
            </div>

            <!-- KARTE 3: HAUSNETZ & ECOTRACKER ZÄHLER -->
            <div class="lcars-card card-blue">
              <div class="card-head">
                <span class="card-head-title" style="color:var(--c-blue);">🏠 HAUSNETZ &amp; ZÄHLER</span>
                <span class="card-head-icon">⚡</span>
              </div>
              <div class="card-metric" id="cardHouseVal" style="color:var(--c-blue);">-- W</div>
              <div class="card-metric-sub">Aktueller Stromverbrauch Haus</div>
              
              <div style="margin-top:0.8rem; display:flex; flex-direction:column;">
                <div class="solar-detail-row">
                  <span class="solar-detail-label">AC Hausabgabe (Inverter):</span>
                  <span class="solar-detail-val" id="solAcOutput">0 W</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">DC Ausgangsleistung:</span>
                  <span class="solar-detail-val" id="solDcOutput">0 W</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">EcoTracker Netzbezug:</span>
                  <span class="solar-detail-val" id="solGridUsage">-- W</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Netzeinspeisung:</span>
                  <span class="solar-detail-val" id="solGridFeedIn">0 W</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">AC Steckdose:</span>
                  <span class="solar-detail-val" id="solAcSocket">0 W</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Einspeisevorgabe:</span>
                  <span class="solar-detail-val" id="solFeedTarget">-- W</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Abgabelimit:</span>
                  <span class="solar-detail-val" id="solFeedLimit">800 W</span>
                </div>
              </div>
            </div>

            <!-- KARTE 4: SYSTEM-TELEMETRIE & DIREKTSTEUERUNG -->
            <div class="lcars-card card-almond">
              <div class="card-head">
                <span class="card-head-title" style="color:var(--c-almond);">⚙️ TELEMETRIE &amp; STEUERUNG</span>
                <span class="card-head-icon">📡</span>
              </div>
              
              <!-- Quick Info List -->
              <div style="margin-top:0.4rem; display:flex; flex-direction:column;">
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Benutzermodus:</span>
                  <span class="solar-detail-val" id="solMode">--</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Anker Cloud Status:</span>
                  <span class="solar-detail-val" id="solCloudState">Online</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">EcoTracker Cloud:</span>
                  <span class="solar-detail-val" id="solEcoCloudState">Online</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">WiFi Speicher / Tracker:</span>
                  <span class="solar-detail-val" id="solWifiState">Verbunden / Verbunden</span>
                </div>
                <div class="solar-detail-row">
                  <span class="solar-detail-label">Letzter MQTT Sync:</span>
                  <span class="solar-detail-val" id="solMqttTime" style="font-size:0.75rem;">--</span>
                </div>
              </div>

              <!-- Quick Toggles & Triggers -->
              <div style="margin-top:0.9rem; padding-top:0.6rem; border-top:1px solid rgba(255,255,255,0.08); display:flex; flex-direction:column; gap:0.5rem;">
                <div style="display:flex; gap:0.4rem; flex-wrap:wrap;">
                  <button id="btnToggleFeed" class="left-action-btn" onclick="toggleSolarFeedSwitch()" style="flex:1; min-width:130px; padding:0.35rem 0.6rem; font-size:0.78rem;">
                    NETZEINSPEISUNG: AN
                  </button>
                  <button id="btnToggleLed" class="left-action-btn" onclick="toggleSolarLedSwitch()" style="flex:1; min-width:130px; padding:0.35rem 0.6rem; font-size:0.78rem;">
                    LED LICHT: AUS
                  </button>
                </div>
                <div style="display:flex; gap:0.4rem; flex-wrap:wrap;">
                  <button class="left-action-btn" onclick="triggerSolarAction('button.christophs_energiespeicher_mqtt_echtzeitdaten', 'MQTT Echtzeit')" style="flex:1; min-width:110px; padding:0.35rem 0.6rem; font-size:0.78rem;">
                    ⚡ MQTT ECHTZEIT
                  </button>
                  <button class="left-action-btn" onclick="triggerSolarAction('button.christophs_energiespeicher_details_aktualisieren', 'Details Sync')" style="flex:1; min-width:110px; padding:0.35rem 0.6rem; font-size:0.78rem;">
                    🔄 DETAILS SYNC
                  </button>
                  <button class="left-action-btn" onclick="triggerSolarAction('button.ecotracker_mqtt_echtzeitdaten', 'EcoTracker')" style="flex:1; min-width:110px; padding:0.35rem 0.6rem; font-size:0.78rem;">
                    📡 ECOTRACKER
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- DACHTERRASSE ENTITIES BROWSER (INTERAKTIVE LISTE ALLER OBJEKTE) -->
          <div class="ha-room-card" style="border-left-color:var(--c-gold); margin-top:1rem;">
            <div class="ha-room-header">
              <div class="ha-room-title" style="color:var(--c-gold);">
                <span>☀️</span>
                <span>DACHTERRASSE // ALLE HOME ASSISTANT OBJEKTE</span>
              </div>
              <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
                <select id="solarDomainFilter" onchange="filterSolarEntities()" class="lcars-input" style="padding:0.35rem 0.65rem; font-size:0.82rem; width:auto;">
                  <option value="all">ALLE OBJEKTE</option>
                  <option value="sensor">📊 SENSOREN</option>
                  <option value="switch">🔌 SCHALTER</option>
                  <option value="button">🔘 TASTER</option>
                  <option value="number">🔢 ZAHLEN / LIMITS</option>
                  <option value="select">📋 AUSWAHL / MODI</option>
                  <option value="binary_sensor">👁️ STATUS-SENSOREN</option>
                  <option value="controllable">⚙️ NUR STEUERBARE</option>
                </select>
                <input type="text" id="solarSearchInput" oninput="filterSolarEntities()" placeholder="Objekt suchen..." class="lcars-input" style="padding:0.35rem 0.65rem; font-size:0.82rem; width:150px;">
              </div>
            </div>

            <!-- Entities Grid -->
            <div id="solarEntitiesGrid" class="ha-entities-grid">
              <div style="padding:2rem; text-align:center; color:#888; font-family:var(--mono-family); grid-column:1/-1;">
                Lade Dachterassen-Objekte...
              </div>
            </div>
          </div>
        </section>

        <!-- KATEGORIE: ZYKLUS-TRACKER // PARTNERINNEN-ZYKLUS -->
        <section class="lcars-section" id="section-cycle">
          <div class="lcars-header-bar">
            <h2>BIO-TELEMETRIE // PARTNERINNEN-ZYKLUS TRACKER</h2>
            <span class="lcars-pill-tag">MULTI-MONITORING 1-30 TAGE // VERGLEICHSGRAPH</span>
          </div>

          <!-- Empty State (when no partners exist) -->
          <div id="cycleEmptyState" class="lcars-card" style="text-align:center; padding:3rem 1.5rem; display:none;">
            <div style="font-size:3rem; margin-bottom:0.8rem;">🧬</div>
            <div style="font-family:var(--font-family); font-size:1.4rem; color:var(--c-secondary); letter-spacing:0.06em; margin-bottom:0.5rem; text-transform:uppercase;">
              KEINE PARTNERINNEN-DATEN ERFASST
            </div>
            <p style="color:#aaa; max-width:520px; margin:0 auto 1.5rem auto; font-size:0.9rem;">
              Legen Sie eine Partnerin mit Namen, Zyklusdauer und letztem Periodenbeginn an, um den Zyklusverlauf, Phasen und den 1-30 Tage Graphen mit Live-Highlighting anzuzeigen.
            </p>
            <button class="left-action-btn" onclick="openAddPartnerModal()" style="padding:0.6rem 1.5rem; font-size:1rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
              <span>➕</span> <span>ERSTE PARTNERIN ANLEGEN</span>
            </button>
          </div>

          <!-- Active Content (when partners exist) -->
          <div id="cycleActiveContent" style="display:none;">

            <!-- 1. DIREKT OBEN UNTER ZYKLUS: DER MULTI-PARTNERINNEN VERGLEICHSGRAPH (1-30 TAGE) -->
            <div class="lcars-card" style="margin-bottom:1.25rem; padding:1.15rem; width:100%; border-top:4px solid var(--c-secondary);">
              <div class="card-head" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.75rem;">
                <div style="display:flex; align-items:center; gap:0.6rem;">
                  <span class="card-head-title" style="color:var(--c-primary); font-size:1.2rem; font-weight:700; letter-spacing:0.05em;">
                    ZYKLUSVERLÄUFE ALLER PARTNERINNEN // TAGE 1 BIS 30
                  </span>
                  <span class="card-head-icon">📈</span>
                </div>
                <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
                  <span id="multiCycleCountBadge" class="lcars-pill-tag" style="background:rgba(255,255,255,0.1); color:#fff; font-size:0.78rem;">
                    ALLE PARTNERINNEN
                  </span>
                  <button class="left-action-btn" onclick="openAddPartnerModal()" style="padding:0.35rem 0.85rem; font-size:0.82rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                    <span>➕</span> <span>PARTNERIN ANLEGEN</span>
                  </button>
                </div>
              </div>

              <!-- Badges aller Partnerinnen mit Farbcode, aktuellem Tag & Phase -->
              <div id="cycleMultiPartnerBadges" style="display:flex; gap:0.5rem; flex-wrap:wrap; align-items:center; margin-bottom:0.85rem;">
                <!-- Populated dynamically via JS -->
              </div>

              <!-- Canvas for Multi-Partner Chart.js Graph (1-30 Days) -->
              <div style="position:relative; width:100%; height:320px; margin-bottom:0.6rem;">
                <canvas id="cycleChart"></canvas>
              </div>

              <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; font-size:0.75rem; color:#888; font-family:var(--mono-family); border-top:1px solid rgba(255,255,255,0.08); padding-top:0.45rem;">
                <span>💡 Klicke auf eine Partnerin in den Badges oder der Legende, um ihr Detailprofil aufzurufen oder Kurven zu filtern. Große weiße Punkte = Aktueller Tag (★ HEUTE).</span>
                <span id="cycleGraphScaleInfo" style="color:var(--c-gold);">BEREICH: TAGE 1 BIS 30 // LIVE-HIGHLIGHT</span>
              </div>
            </div>

            <!-- 2. DETAILANSICHT // EINZELPROFIL-TELEMETRIE -->
            <div class="lcars-card" style="margin-bottom:1.25rem; border-left:6px solid var(--c-secondary); padding:1.1rem;">
              <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.75rem; margin-bottom:1rem;">
                <div style="display:flex; align-items:center; gap:0.75rem;">
                  <span style="font-size:1.8rem;">🧬</span>
                  <div>
                    <div style="display:flex; align-items:baseline; gap:0.6rem; flex-wrap:wrap;">
                      <div style="font-family:var(--font-family); font-size:1.6rem; font-weight:700; color:#fff; letter-spacing:0.06em; text-transform:uppercase;" id="activePartnerName">
                        PARTNERIN
                      </div>
                      <span id="activePartnerPhaseTag" style="font-family:var(--mono-family); font-size:0.82rem; color:var(--c-gold); font-weight:700;">
                        ★ AKTIVES PROFIL
                      </span>
                    </div>
                    <div style="font-family:var(--mono-family); font-size:0.8rem; color:#aaa;" id="activePartnerSub">
                      ZYKLUS: 28 TAGE // PERIODE: 5 TAGE
                    </div>
                  </div>
                </div>

                <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
                  <button class="left-action-btn" onclick="triggerNewCycleToday()" title="Setzt den ersten Tag des neuen Zyklus auf das heutige Datum" style="padding:0.4rem 0.8rem; font-size:0.82rem; border-color:var(--c-red); color:var(--c-red);">
                    <span>🩸</span> <span>NEUER ZYKLUS HEUTE</span>
                  </button>
                  <button class="left-action-btn" onclick="openEditPartnerModal()" style="padding:0.4rem 0.8rem; font-size:0.82rem;">
                    <span>✏️</span> <span>BEARBEITEN</span>
                  </button>
                  <button class="left-action-btn" onclick="confirmDeletePartner()" style="padding:0.4rem 0.8rem; font-size:0.82rem; border-color:#888; color:#bbb;">
                    <span>🗑️</span> <span>LÖSCHEN</span>
                  </button>
                </div>
              </div>

              <!-- Partner Tabs / Pills to switch inspected profile -->
              <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap; margin-bottom:1rem; padding-bottom:0.75rem; border-bottom:1px solid rgba(255,255,255,0.08);">
                <span style="font-size:0.75rem; color:#888; font-family:var(--mono-family); text-transform:uppercase;">DETAILPROFIL WÄHLEN:</span>
                <div id="cyclePartnerPills" style="display:flex; gap:0.5rem; flex-wrap:wrap; align-items:center;">
                  <!-- Populated dynamically via JS -->
                </div>
              </div>

              <!-- Vitals Grid -->
              <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:1rem;">
                <!-- Aktueller Tag -->
                <div class="telemetry-box" style="background:rgba(0,0,0,0.4); border:1px solid var(--c-secondary); border-radius:6px; padding:0.85rem;">
                  <div style="font-size:0.75rem; color:var(--c-secondary); font-family:var(--mono-family); text-transform:uppercase;">
                    AKTUELLES STADIUM // HEUTE
                  </div>
                  <div style="display:flex; align-items:baseline; gap:0.4rem; margin-top:0.25rem;">
                    <span style="font-size:2.2rem; font-weight:700; font-family:var(--font-family); color:#ffffff;" id="dispCurrentDay">TAG 14</span>
                    <span style="font-size:1rem; color:#aaa; font-family:var(--mono-family);" id="dispCycleDuration">/ 28 TAGE</span>
                  </div>
                  <div style="margin-top:0.4rem; background:rgba(255,255,255,0.1); border-radius:4px; height:8px; overflow:hidden;">
                    <div id="dispCycleProgress" style="background:var(--c-secondary); height:100%; width:50%; transition:width 0.4s ease;"></div>
                  </div>
                </div>

                <!-- Aktuelle Phase -->
                <div class="telemetry-box" style="background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.15); border-radius:6px; padding:0.85rem;">
                  <div style="font-size:0.75rem; color:var(--c-gold); font-family:var(--mono-family); text-transform:uppercase;">
                    AKTUELLE PHASE &amp; BIORHYTHMUS
                  </div>
                  <div style="margin-top:0.35rem;">
                    <span id="dispPhaseBadge" class="badge-status" style="background:#baa4e5; color:#000; font-weight:700; font-size:0.9rem;">
                      EISPRUNG / OVULATION
                    </span>
                  </div>
                  <div id="dispPhaseDesc" style="font-size:0.78rem; color:#bbb; margin-top:0.4rem; line-height:1.25;">
                    LH- &amp; Östrogen-Peak. Höchste Fruchtbarkeit &amp; Energie.
                  </div>
                </div>

                <!-- Fruchtbarkeit & Countdown -->
                <div class="telemetry-box" style="background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.15); border-radius:6px; padding:0.85rem;">
                  <div style="font-size:0.75rem; color:var(--c-almond); font-family:var(--mono-family); text-transform:uppercase;">
                    FRUCHTBARKEIT &amp; NÄCHSTE PERIODE
                  </div>
                  <div style="font-size:1.15rem; font-weight:700; color:#fff; font-family:var(--font-family); margin-top:0.25rem;" id="dispFertilityStatus">
                    SEHR HOCH (MAXIMAL)
                  </div>
                  <div style="font-size:0.8rem; color:#bbb; font-family:var(--mono-family); margin-top:0.3rem;">
                    In <span id="dispDaysUntilNext" style="color:var(--c-gold); font-weight:700;">14</span> Tagen (<span id="dispNextDate">29.09.2026</span>)
                  </div>
                </div>
              </div>

              <!-- LCARS 1-30 DAY TAPE (INTERACTIVE TIMELINE) FÜR DAS DETAILPROFIL -->
              <div style="margin-top:1.25rem; border-top:1px solid rgba(255,255,255,0.1); padding-top:1rem;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem; flex-wrap:wrap; gap:0.4rem;">
                  <span style="font-family:var(--font-family); font-size:0.95rem; font-weight:700; color:var(--c-gold); letter-spacing:0.06em; text-transform:uppercase;">
                    LCARS 30-TAGE BAND // TAGES-INSPEKTOR (<span id="tapePartnerName">PARTNERIN</span>)
                  </span>
                  <span style="font-size:0.75rem; color:#888; font-family:var(--mono-family);">
                    KLICKE AUF EINEN TAG FÜR DETAILINFOS
                  </span>
                </div>

                <div id="cycleTapeContainer" class="cycle-tape-row">
                  <!-- 30 interactive day cells populated via JS -->
                </div>

                <!-- Day Inspector Card -->
                <div id="dayInspectorCard" style="margin-top:0.75rem; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:0.65rem 0.9rem; font-family:var(--mono-family); font-size:0.85rem; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
                  <div>
                    <span id="inspectDayLabel" style="font-weight:700; color:var(--c-secondary);">TAG 14</span>:
                    <span id="inspectPhaseLabel" style="color:#fff;">EISPRUNG // OVULATION</span>
                    <span id="inspectTodayBadge" style="color:var(--c-gold); margin-left:0.5rem; font-weight:700;">(★ HEUTE)</span>
                  </div>
                  <div id="inspectInfoText" style="color:#aaa; font-size:0.8rem;">
                    LH- &amp; Östrogen-Peak! Maximale Fruchtbarkeit, hohe Energie, gesteigerte Libido.
                  </div>
                </div>
              </div>
            </div>

            <!-- 3. Partnerinnen Übersicht & Schnell-Wechsler Grid -->
            <div id="allPartnersOverviewCard" class="lcars-card" style="margin-bottom:1.25rem;">
              <div class="card-head-title" style="margin-bottom:0.75rem;">ALLE ERFASSTEN PARTNERINNEN // ÜBERSICHTSKARTEN</div>
              <div id="partnersGrid" style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:0.75rem;">
                <!-- Cards per partner -->
              </div>
            </div>
          </div>
        </section>

        <!-- KATEGORIE: PULSECAST // MEDIA & DOWNLOAD HUB -->
        <section class="lcars-section" id="section-pulsecast">
          <div class="lcars-header-bar" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
            <div style="display:flex; align-items:center; gap:0.8rem;">
              <h2>LCARS PULSECAST // MEDIA &amp; DOWNLOAD HUB</h2>
              <span id="pulsecastOnlineBadge" class="lcars-pill-tag" style="background:var(--c-butterscotch); color:#000;">RELAY INITIALISIERUNG...</span>
            </div>
            <div style="display:flex; gap:0.5rem; align-items:center;">
              <span id="pulsecastActiveSpeedBadge" class="lcars-pill-tag" style="background:rgba(255,255,255,0.08); color:var(--c-butterscotch); font-family:var(--mono-family); font-size:0.82rem;">0.00 MB/s</span>
              <button class="left-action-btn" onclick="refreshPulsecastData(true)" style="padding:0.35rem 0.85rem; font-size:0.82rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;">
                <span>🔄</span> <span>AKTUALISIEREN</span>
              </button>
            </div>
          </div>

          <!-- Command Code Gate View (when locked & unauthorized) -->
          <div id="pulsecastGateView" class="lcars-card" style="text-align:center; padding:3rem 1.5rem; display:none; border-top:4px solid var(--c-butterscotch);">
            <div style="font-size:3rem; margin-bottom:0.8rem;">🔒</div>
            <div style="font-family:var(--font-family); font-size:1.35rem; color:var(--c-butterscotch); letter-spacing:0.06em; margin-bottom:0.5rem; text-transform:uppercase;">
              ZUGANG GESPERRT // LEVEL 1 SICHERHEITSPROTOKOLL
            </div>
            <p style="color:#bbb; max-width:520px; margin:0 auto 1.5rem auto; font-size:0.95rem;">
              Der Zugriff auf den Bereich PULSECAST erfordert die Autorisierung mit dem LCARS Command Code.
            </p>
            <button class="left-action-btn" onclick="openAuthModal()" style="padding:0.6rem 1.5rem; font-size:1rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;">
              <span>🔐</span> <span>COMMAND CODE EINGEBEN</span>
            </button>
          </div>

          <!-- Offline Notice (when port 3000 unreachable) -->
          <div id="pulsecastOfflineNotice" class="lcars-card" style="text-align:center; padding:2.5rem 1.5rem; display:none; border-top:4px solid var(--c-red); background:rgba(207,79,79,0.1);">
            <div style="font-size:3rem; margin-bottom:0.8rem;">📡⚠️</div>
            <div style="font-family:var(--font-family); font-size:1.3rem; color:var(--c-red); letter-spacing:0.06em; margin-bottom:0.5rem; text-transform:uppercase;">
              SUBRAUM-RELAY ZU PULSECAST OFFLINE
            </div>
            <p style="color:#ddd; max-width:560px; margin:0 auto 1.2rem auto; font-size:0.92rem; font-family:var(--mono-family);">
              Keine Verbindung zum PulseCast Hub auf Port 3000 (http://127.0.0.1:3000). Bitte stellen Sie sicher, dass der Node.js Dienst (xdcc-load-cast) aktiv ist.
            </p>
            <button class="left-action-btn" onclick="refreshPulsecastData(true)" style="padding:0.5rem 1.2rem; font-size:0.9rem; border-color:var(--c-gold); color:var(--c-gold); font-weight:700;">
              <span>🔄</span> <span>VERBINDUNG ERNEUT TESTEN</span>
            </button>
          </div>

          <!-- Active Content (when authorized & online) -->
          <div id="pulsecastActiveContent" style="display:none;">
            <!-- Subnav Tabs -->
            <div style="display:flex; gap:0.75rem; margin-bottom:1.25rem; flex-wrap:wrap;">
              <button type="button" class="lcars-subnav-pill active" id="pulsecast-tab-btn-downloads" onclick="switchPulsecastSubtab('downloads')">
                <span>⬇️</span> <span>DOWNLOADS</span>
                <span id="pulsecastDownloadsCountBadge" style="background:rgba(0,0,0,0.5); padding:2px 8px; border-radius:12px; font-size:0.75rem; margin-left:4px;">0</span>
              </button>
              <button type="button" class="lcars-subnav-pill" id="pulsecast-tab-btn-local" onclick="switchPulsecastSubtab('local')">
                <span>📁</span> <span>LOKAL</span>
                <span id="pulsecastLocalCountBadge" style="background:rgba(0,0,0,0.5); padding:2px 8px; border-radius:12px; font-size:0.75rem; margin-left:4px;">0</span>
              </button>
              <button type="button" class="lcars-subnav-pill" id="pulsecast-tab-btn-catalog" onclick="switchPulsecastSubtab('catalog')">
                <span>📺</span> <span>KATALOG-BROWSER</span>
              </button>
              <button type="button" class="lcars-subnav-pill" id="pulsecast-tab-btn-xdcc" onclick="switchPulsecastSubtab('xdcc')">
                <span>🔍</span> <span>XDCC-SUCHE</span>
              </button>
            </div>

            <!-- SUBVIEW 1: DOWNLOADS -->
            <div id="pulsecast-subview-downloads" class="pulsecast-subview" style="display:block;">
              <div class="lcars-card" style="margin-bottom:1.25rem; padding:1rem; border-top:3px solid var(--c-butterscotch);">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.75rem; margin-bottom:0.75rem;">
                  <div style="display:flex; align-items:center; gap:0.6rem;">
                    <span style="color:var(--c-primary); font-size:1.15rem; font-weight:700; letter-spacing:0.05em;">
                      AKTIVE TRANSFERS &amp; WARTESCHLANGE
                    </span>
                    <span style="font-size:1.1rem;">📥</span>
                  </div>
                  <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
                    <span id="pulsecastQueueSummary" class="lcars-pill-tag" style="background:rgba(255,255,255,0.08); color:#fff; font-size:0.78rem;">
                      0 TRANSFERS
                    </span>
                  </div>
                </div>

                <!-- Live Downloads Container -->
                <div id="pulsecastDownloadsList" style="display:flex; flex-direction:column; gap:0.75rem;">
                  <!-- Dynamically populated download items -->
                </div>

                <!-- Empty state for downloads -->
                <div id="pulsecastDownloadsEmpty" style="text-align:center; padding:2.5rem 1rem; color:#888; font-family:var(--mono-family); font-size:0.9rem; display:none;">
                  KEINE AKTIVEN ODER GESPEICHERTEN DOWNLOADS VORHANDEN
                </div>
              </div>
            </div>

            <!-- SUBVIEW 1b: LOKALE MEDIEN -->
            <div id="pulsecast-subview-local" class="pulsecast-subview" style="display:none;">
              <div class="lcars-card" style="margin-bottom:1.25rem; padding:1rem; border-top:3px solid var(--c-butterscotch);">
                <!-- Filter Bar -->
                <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:0.75rem; margin-bottom:1rem; background:rgba(0,0,0,0.3); padding:0.75rem; border-radius:6px; border:1px solid rgba(255,255,255,0.06);">
                  <!-- Type Filter (Alle / Filme / Serien) -->
                  <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
                    <button type="button" class="lcars-pill-btn active" id="local-pill-all" onclick="setPulsecastLocalCategory('Lokal')" style="height:38px; padding:0 1.2rem; font-size:0.85rem; background:var(--c-butterscotch); color:#000; font-weight:700;">
                      📁 ALLE <span id="pulsecastLocalCountAll" style="margin-left:4px; font-size:0.75rem; opacity:0.85;"></span>
                    </button>
                    <button type="button" class="lcars-pill-btn" id="local-pill-Filme" onclick="setPulsecastLocalCategory('Lokal_Filme')" style="height:38px; padding:0 1.2rem; font-size:0.85rem; background:rgba(0,0,0,0.5); color:var(--c-primary); border:1px solid var(--c-primary);">
                      🎬 FILME <span id="pulsecastLocalCountFilme" style="margin-left:4px; font-size:0.75rem; opacity:0.85;"></span>
                    </button>
                    <button type="button" class="lcars-pill-btn" id="local-pill-Serien" onclick="setPulsecastLocalCategory('Lokal_Serien')" style="height:38px; padding:0 1.2rem; font-size:0.85rem; background:rgba(0,0,0,0.5); color:var(--c-secondary); border:1px solid var(--c-secondary);">
                      📺 SERIEN <span id="pulsecastLocalCountSerien" style="margin-left:4px; font-size:0.75rem; opacity:0.85;"></span>
                    </button>
                  </div>

                  <!-- Ansichts-Umschalter: Raster vs Liste -->
                  <div style="display:flex; gap:0.4rem; align-items:center;">
                    <button type="button" class="lcars-pill-btn active" id="pulsecastLocalViewGridBtn" onclick="setPulsecastLocalViewMode('grid')" style="height:38px; padding:0 0.9rem; font-size:0.82rem; background:var(--c-gold); color:#000; font-weight:700;" title="Kachel-Rasteransicht">
                      <span>⊞</span> <span>RASTER</span>
                    </button>
                    <button type="button" class="lcars-pill-btn" id="pulsecastLocalViewListBtn" onclick="setPulsecastLocalViewMode('list')" style="height:38px; padding:0 0.9rem; font-size:0.82rem; background:rgba(0,0,0,0.5); color:#aaa; border:1px solid rgba(255,255,255,0.2);" title="Kompakte Tabellen-/Listenansicht">
                      <span>☰</span> <span>LISTE</span>
                    </button>
                  </div>

                  <!-- Schnellsuche -->
                  <div style="display:flex; align-items:center; gap:0.4rem; min-width:240px; flex:1; max-width:380px;">
                    <input type="text" id="pulsecastLocalSearchInput" placeholder="Lokale Medien suchen..." class="lcars-input" style="height:38px; font-size:0.85rem; flex:1;" onkeydown="if(event.key==='Enter') pulsecastLocalSearchTrigger();">
                    <button type="button" class="left-action-btn" onclick="pulsecastLocalSearchTrigger()" style="height:38px; padding:0 0.9rem; font-size:0.82rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch);" title="Suche ausführen">
                      <span>🔍</span>
                    </button>
                    <button type="button" class="left-action-btn" onclick="pulsecastLocalSearchClear()" style="height:38px; padding:0 0.6rem; font-size:0.82rem; border-color:#888; color:#888;" title="Filter zurücksetzen">
                      ✕
                    </button>
                  </div>
                </div>

                <!-- Ladeanzeige -->
                <div id="pulsecastLocalLoading" style="text-align:center; padding:3rem 1rem; display:none;">
                  <div style="font-family:var(--font-family); font-size:1.1rem; color:var(--c-butterscotch); letter-spacing:0.06em; margin-bottom:0.5rem;">
                    DATENKASKADE WIRD GELADEN...
                  </div>
                  <div style="font-family:var(--mono-family); font-size:0.85rem; color:#888;">
                    Lokales Medienarchiv wird synchronisiert
                  </div>
                </div>

                <!-- Ansicht 1: Kachel-Raster -->
                <div id="pulsecastLocalGrid" class="pulsecast-catalog-grid" style="min-height:280px; display:grid;">
                  <!-- Dynamisch befüllte Kacheln -->
                </div>

                <!-- Ansicht 2: Kompakte Listenansicht (Tabelle) -->
                <div id="pulsecastLocalListContainer" style="overflow-x:auto; display:none;">
                  <table class="services-table" id="pulsecastLocalTable" style="width:100%;">
                    <thead>
                      <tr style="position:sticky; top:0; background:#111; z-index:2;">
                        <th style="width:55px; text-align:center;">TYP</th>
                        <th>DATEINAME / TITEL</th>
                        <th style="width:90px;">FORMAT</th>
                        <th style="width:110px; color:#44dd88;">GRÖSSE</th>
                        <th style="width:145px; color:var(--c-gold);">ÄNDERUNG</th>
                        <th style="width:140px; text-align:right;">AKTION</th>
                      </tr>
                    </thead>
                    <tbody id="pulsecastLocalTableBody">
                      <!-- Dynamisch befüllte Zeilen -->
                    </tbody>
                  </table>
                </div>

                <!-- Empty State -->
                <div id="pulsecastLocalEmpty" style="text-align:center; padding:3rem 1rem; color:#888; font-family:var(--mono-family); display:none;">
                  KEINE LOKALEN MEDIEN FÜR DIESE FILTERUNG VORHANDEN
                </div>

                <!-- Paginierungs-Leiste -->
                <div id="pulsecastLocalPaginationBar" style="display:flex; justify-content:center; align-items:center; gap:0.75rem; margin-top:1.5rem; flex-wrap:wrap;">
                  <button type="button" class="left-action-btn" id="pulsecastLocalPrevPageBtn" onclick="pulsecastLocalChangePage(-1)" style="padding:0.4rem 1.1rem; font-size:0.85rem;">
                    ◀ VORHERIGE
                  </button>
                  <span id="pulsecastLocalPageIndicator" style="font-family:var(--mono-family); font-size:0.9rem; color:var(--c-gold); padding:0 0.5rem;">
                    SEITE 1 VON 1 (0 EINTRÄGE)
                  </span>
                  <button type="button" class="left-action-btn" id="pulsecastLocalNextPageBtn" onclick="pulsecastLocalChangePage(1)" style="padding:0.4rem 1.1rem; font-size:0.85rem;">
                    NÄCHSTE ▶
                  </button>
                </div>
              </div>
            </div>

            <!-- SUBVIEW 2: KATALOG-BROWSER -->
            <div id="pulsecast-subview-catalog" class="pulsecast-subview" style="display:none;">
              <div class="lcars-card" style="margin-bottom:1.25rem; padding:1rem; border-top:3px solid var(--c-primary);">
                <!-- Filter Bar -->
                <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:0.75rem; margin-bottom:1rem; background:rgba(0,0,0,0.3); padding:0.75rem; border-radius:6px; border:1px solid rgba(255,255,255,0.06);">
                  <!-- Type Filter (Filme / Serien) -->
                  <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
                    <button type="button" class="lcars-pill-btn active" id="cat-pill-Filme" onclick="setPulsecastCatalogCategory('Filme')" style="height:38px; padding:0 1.2rem; font-size:0.85rem; background:var(--c-primary); color:#000;">
                      🎬 FILME <span id="pulsecastCountFilme" style="margin-left:4px; font-size:0.75rem; opacity:0.85;"></span>
                    </button>
                    <button type="button" class="lcars-pill-btn" id="cat-pill-Serien" onclick="setPulsecastCatalogCategory('Serien')" style="height:38px; padding:0 1.2rem; font-size:0.85rem; background:rgba(0,0,0,0.5); color:var(--c-secondary); border:1px solid var(--c-secondary);">
                      📺 SERIEN <span id="pulsecastCountSerien" style="margin-left:4px; font-size:0.75rem; opacity:0.85;"></span>
                    </button>
                    <button type="button" class="lcars-pill-btn" id="cat-pill-all" onclick="setPulsecastCatalogCategory('all')" style="height:38px; padding:0 1rem; font-size:0.85rem; background:rgba(0,0,0,0.5); color:#aaa; border:1px solid rgba(255,255,255,0.2);">
                      📁 ALLE
                    </button>
                  </div>

                  <!-- Subcategory Dropdown (dynamisch aus PulseCast geladen) -->
                  <div style="display:flex; align-items:center; gap:0.5rem; flex:1; min-width:240px; max-width:380px;">
                    <label style="font-size:0.8rem; color:var(--c-gold); font-weight:700; white-space:nowrap;">KATEGORIE:</label>
                    <select id="pulsecastSubcatSelect" class="lcars-input" style="flex:1; height:38px; padding:0 0.6rem; font-size:0.85rem; cursor:pointer;" onchange="onPulsecastSubcatChanged()">
                      <option value="all">ALLE KATEGORIEN</option>
                    </select>
                  </div>

                  <!-- Quick Search -->
                  <div style="display:flex; align-items:center; gap:0.4rem; min-width:260px;">
                    <input type="text" id="pulsecastCatalogSearchInput" placeholder="Titel / Darsteller suchen..." class="lcars-input" style="height:38px; font-size:0.85rem; flex:1;" onkeydown="if(event.key==='Enter') pulsecastCatalogSearchTrigger();">
                    <button type="button" class="left-action-btn" onclick="pulsecastCatalogSearchTrigger()" style="height:38px; padding:0 0.9rem; font-size:0.82rem; border-color:var(--c-primary); color:var(--c-primary);">
                      <span>🔍</span>
                    </button>
                    <button type="button" class="left-action-btn" onclick="pulsecastCatalogSearchClear()" style="height:38px; padding:0 0.6rem; font-size:0.82rem; border-color:#888; color:#888;" title="Filter zurücksetzen">
                      ✕
                    </button>
                  </div>
                </div>

                <!-- Catalog Loading -->
                <div id="pulsecastCatalogLoading" style="text-align:center; padding:3rem 1rem; display:none;">
                  <div style="font-family:var(--font-family); font-size:1.1rem; color:var(--c-primary); letter-spacing:0.06em; margin-bottom:0.5rem;">
                    DATENKASKADE WIRD GELADEN...
                  </div>
                  <div style="font-family:var(--mono-family); font-size:0.85rem; color:#888;">
                    Katalogabfrage über Subraum-Relay aktiv
                  </div>
                </div>

                <!-- Catalog Grid -->
                <div id="pulsecastCatalogGrid" class="pulsecast-catalog-grid" style="min-height:280px;">
                  <!-- Dynamically populated cards -->
                </div>

                <!-- Catalog Empty State -->
                <div id="pulsecastCatalogEmpty" style="text-align:center; padding:3rem 1rem; color:#888; font-family:var(--mono-family); display:none;">
                  KEINE MEDIENEINTRÄGE FÜR DIE AUSGEWÄHLTEN FILTER GEFUNDEN
                </div>

                <!-- Pagination Bar -->
                <div id="pulsecastPaginationBar" style="display:flex; justify-content:center; align-items:center; gap:0.75rem; margin-top:1.5rem; flex-wrap:wrap;">
                  <button type="button" class="left-action-btn" id="pulsecastPrevPageBtn" onclick="pulsecastChangePage(-1)" style="padding:0.4rem 1.1rem; font-size:0.85rem;">
                    ◀ VORHERIGE
                  </button>
                  <span id="pulsecastPageIndicator" style="font-family:var(--mono-family); font-size:0.9rem; color:var(--c-gold); padding:0 0.5rem;">
                    SEITE 1 VON 1 (0 EINTRÄGE)
                  </span>
                  <button type="button" class="left-action-btn" id="pulsecastNextPageBtn" onclick="pulsecastChangePage(1)" style="padding:0.4rem 1.1rem; font-size:0.85rem;">
                    NÄCHSTE ▶
                  </button>
                </div>
              </div>
            </div>

            <!-- SUBVIEW 3: XDCC-SUCHE -->
            <div id="pulsecast-subview-xdcc" class="pulsecast-subview" style="display:none;">
              <div class="lcars-card" style="margin-bottom:1.25rem; padding:1.15rem; border-top:3px solid var(--c-blue);">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.75rem; margin-bottom:1rem;">
                  <div style="display:flex; align-items:center; gap:0.6rem;">
                    <span id="pulsecastXdccHeaderTitle" style="color:var(--c-blue); font-size:1.2rem; font-weight:700; letter-spacing:0.05em;">
                      KLASSISCHE IRC &amp; XDCC PAKET-SUCHE
                    </span>
                    <span style="font-size:1.1rem;">📡</span>
                  </div>
                  <!-- Quellenauswahl: XDCC.EU Relay vs Movie Gods -->
                  <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
                    <span style="font-size:0.78rem; color:var(--c-gold); font-family:var(--font-family); font-weight:700;">QUELLE:</span>
                    <button type="button" class="lcars-pill-btn active" id="pulsecast-source-pill-xdcc" onclick="setPulsecastXdccSource('xdcc')" style="height:32px; padding:0 0.9rem; font-size:0.8rem; background:var(--c-blue); color:#000; font-weight:700;">
                      XDCC.EU RELAY
                    </button>
                    <button type="button" class="lcars-pill-btn" id="pulsecast-source-pill-moviegods" onclick="setPulsecastXdccSource('moviegods')" style="height:32px; padding:0 0.9rem; font-size:0.8rem; background:rgba(0,0,0,0.5); color:var(--c-secondary); border:1px solid var(--c-secondary); font-weight:700;">
                      MOVIE GODS (IRC)
                    </button>
                  </div>
                </div>

                <!-- Movie Gods Top-Downloads Box (nur aktiv wenn Quelle == moviegods) -->
                <div id="pulsecastMoviegodsTopDlContainer" style="display:none; background:rgba(180,100,255,0.06); border:1px solid var(--c-secondary); border-radius:6px; padding:0.9rem; margin-bottom:1rem;">
                  <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.6rem; margin-bottom:0.6rem;">
                    <div style="display:flex; align-items:center; gap:0.5rem;">
                      <span style="color:var(--c-secondary); font-weight:700; font-family:var(--font-family); font-size:0.95rem;">
                        ⭐ MOVIE GODS TOP-DOWNLOADS / FAVORITEN
                      </span>
                    </div>
                    <button type="button" class="left-action-btn" id="pulsecastLoadTopDlBtn" onclick="loadPulsecastMoviegodsTopDl()" style="padding:0.35rem 1rem; font-size:0.82rem; border-color:var(--c-gold); color:var(--c-gold); font-weight:700;">
                      ⭐ TOP-DOWNLOADS / FAVORITEN LADEN
                    </button>
                  </div>

                  <!-- TopDL Status & Loading -->
                  <div id="pulsecastTopDlStatus" style="font-family:var(--mono-family); font-size:0.82rem; color:var(--c-gold); display:none; margin-bottom:0.5rem;"></div>

                  <!-- TopDL Results List/Table -->
                  <div id="pulsecastTopDlTableContainer" style="display:none; max-height:280px; overflow-y:auto; border:1px solid rgba(255,255,255,0.1); border-radius:4px;">
                    <table class="services-table" style="width:100%; margin:0;">
                      <thead>
                        <tr style="position:sticky; top:0; background:#111; z-index:2;">
                          <th style="width:85px; color:var(--c-gold);">GETS</th>
                          <th>DATEINAME</th>
                          <th style="width:90px; color:#44dd88;">GRÖSSE</th>
                          <th style="width:130px; text-align:right;">AKTION</th>
                        </tr>
                      </thead>
                      <tbody id="pulsecastTopDlTbody">
                      </tbody>
                    </table>
                  </div>
                </div>

                <!-- Search Input Bar -->
                <div style="display:flex; gap:0.6rem; align-items:center; margin-bottom:1rem; flex-wrap:wrap;">
                  <input type="text" id="pulsecastXdccInput" placeholder="Suchbegriff (z.B. Star Trek, Linux, 1080p, 2026)..." class="lcars-input" style="flex:1; min-width:240px; height:42px; font-size:0.95rem;" onkeydown="if(event.key==='Enter') pulsecastXdccSearch();">
                  <button type="button" class="left-action-btn" onclick="pulsecastXdccSearch()" style="height:42px; padding:0 1.5rem; font-size:0.95rem; border-color:var(--c-blue); color:var(--c-blue); font-weight:700;">
                    <span>🚀</span> <span>IRC SCAN STARTEN</span>
                  </button>
                </div>

                <!-- Quick suggestion chips -->
                <div style="display:flex; gap:0.4rem; align-items:center; flex-wrap:wrap; margin-bottom:1.2rem; font-size:0.8rem;">
                  <span style="color:#888; font-family:var(--mono-family);">SCHNELLFILTER:</span>
                  <button type="button" class="lcars-tag-btn" onclick="pulsecastXdccChipSearch('Star Trek')">Star Trek</button>
                  <button type="button" class="lcars-tag-btn" onclick="pulsecastXdccChipSearch('Section 31')">Section 31</button>
                  <button type="button" class="lcars-tag-btn" onclick="pulsecastXdccChipSearch('Strange New Worlds')">Strange New Worlds</button>
                  <button type="button" class="lcars-tag-btn" onclick="pulsecastXdccChipSearch('1080p German')">1080p German</button>
                  <button type="button" class="lcars-tag-btn" onclick="pulsecastXdccChipSearch('2160p')">2160p UHD</button>
                </div>

                <!-- Search Status & Indicator -->
                <div id="pulsecastXdccStatus" style="font-family:var(--mono-family); font-size:0.85rem; color:var(--c-gold); margin-bottom:0.75rem; display:none;"></div>

                <!-- Results Table / List -->
                <div id="pulsecastXdccResultsContainer" style="overflow-x:auto;">
                  <table class="services-table" id="pulsecastXdccTable" style="display:none; width:100%;">
                    <thead>
                      <tr>
                        <th style="width:140px;">BOT NAME</th>
                        <th style="width:75px;">PACK #</th>
                        <th>DATEINAME</th>
                        <th style="width:95px;">GRÖSSE</th>
                        <th style="width:180px;">SERVER / NETZ</th>
                        <th style="width:120px; text-align:right;">AKTION</th>
                      </tr>
                    </thead>
                    <tbody id="pulsecastXdccTbody">
                      <!-- Results rows -->
                    </tbody>
                  </table>
                </div>

                <!-- XDCC Empty / Placeholder -->
                <div id="pulsecastXdccEmpty" style="text-align:center; padding:2.5rem 1rem; color:#777; font-family:var(--mono-family); font-size:0.9rem;">
                  GEBEN SIE EINEN SUCHBEGRIFF EIN, UM DIE XDCC-BOTS IM IRC ZU DURCHSUCHEN
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- KATEGORIE: GEMINI 3.8 LIVE // SUBRAUM COMM -->
        <section class="lcars-section" id="section-gemini_live">
          <div class="card-header" style="border-bottom: 2px solid var(--c-secondary); margin-bottom: 1rem; padding-bottom: 0.5rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
            <div style="display: flex; align-items: center; gap: 0.75rem;">
              <span style="font-size: 1.5rem;">📡</span>
              <div>
                <h2 style="margin: 0; font-size: 1.35rem; color: var(--c-secondary); letter-spacing: 0.08em; text-transform: uppercase;">
                  LCARS SUBRAUM-KOMMUNIKATION // GEMINI 3.8 LIVE
                </h2>
                <div style="font-size: 0.75rem; color: #888; font-family: var(--mono-family);">
                  Multimodale SST-Echtzeit-Schnittstelle (Bidirektionales PCM-Streaming)
                </div>
              </div>
            </div>
            <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
              <span id="geminiLiveStatusBadge" class="lcars-pill-tag" style="background: var(--c-red); color: #fff; font-weight: 700;">
                GETRENNT
              </span>
              <span id="geminiLiveModelBadge" class="lcars-pill-tag" style="background: rgba(255,255,255,0.08); color: var(--c-secondary); font-family: var(--mono-family); font-size: 0.8rem;">
                MODEL: GEMINI-3.8-LIVE
              </span>
              <span id="geminiLiveModeBadge" class="lcars-pill-tag" style="background: rgba(255,255,255,0.08); color: var(--c-gold); font-family: var(--mono-family); font-size: 0.8rem;">
                MODUS: PTT
              </span>
            </div>
          </div>

          <!-- GATE VIEW (GESPERRT DURCH COMMAND CODE) -->
          <div id="geminiLiveGateView" class="lcars-card" style="text-align: center; padding: 3rem 1.5rem; display: none; border-top: 4px solid var(--c-secondary);">
            <div style="font-size: 2.5rem; margin-bottom: 1rem;">🔒</div>
            <div style="font-family: var(--font-family); font-size: 1.25rem; font-weight: 700; color: var(--c-secondary); letter-spacing: 0.08em; margin-bottom: 0.5rem;">
              SUBRAUM COMM GESPERRT // COMMAND CODE AUTORISIERUNG ERFORDERLICH
            </div>
            <div style="font-size: 0.9rem; color: #ccc; max-width: 540px; margin: 0 auto 1.5rem auto; line-height: 1.5;">
              Der direkte Subraum-Sprachkanal zu Google Gemini 3.8 Live erfordert Autorisierung mit dem LCARS Command Code.
            </div>
            <button class="left-action-btn" onclick="openAuthModal('gemini_live')" style="padding: 0.5rem 1.5rem; font-size: 0.95rem; border-color: var(--c-secondary); color: var(--c-secondary); font-weight: 700;">
              COMMAND CODE EINGEBEN
            </button>
          </div>

          <!-- ACTIVE CONTENT -->
          <div id="geminiLiveActiveContent" style="display: none;">
            <!-- TOOLBAR / CONTROL STRIP -->
            <div class="lcars-card" style="margin-bottom: 1rem; padding: 0.75rem 1rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.75rem; border-left: 4px solid var(--c-secondary);">
              <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                <button type="button" class="lcars-pill-btn active" id="btnGeminiModePtt" onclick="setGeminiLiveMode('ptt')" style="height: 36px; padding: 0 1rem; font-size: 0.82rem; font-weight: 700; background: var(--c-secondary); color: #000;">
                  🎙️ PUSH-TO-TALK
                </button>
                <button type="button" class="lcars-pill-btn" id="btnGeminiModeLive" onclick="setGeminiLiveMode('live')" style="height: 36px; padding: 0 1rem; font-size: 0.82rem; font-weight: 700; background: rgba(0,0,0,0.5); color: var(--c-gold); border: 1px solid var(--c-gold);">
                  📡 DAUERHAFT LIVE
                </button>
              </div>

              <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                <button type="button" class="left-action-btn" id="btnGeminiToggleChannel" onclick="toggleGeminiLiveChannel()" style="height: 36px; padding: 0 1.2rem; font-size: 0.85rem; font-weight: 700; border-color: #44dd88; color: #44dd88;">
                  ▶ SUBRAUM-KANAL ÖFFNEN
                </button>
                <button type="button" class="left-action-btn" id="btnGeminiMute" onclick="toggleGeminiLiveMute()" style="height: 36px; padding: 0 0.9rem; font-size: 0.82rem; border-color: var(--c-gold); color: var(--c-gold);">
                  🎤 MIKROFON: AN
                </button>
                <button type="button" class="left-action-btn" onclick="clearGeminiLiveTranscript()" style="height: 36px; padding: 0 0.8rem; font-size: 0.82rem; border-color: #888; color: #888;" title="Transkript leeren">
                  🗑 LEEREN
                </button>
              </div>
            </div>

            <!-- MAIN DUAL GRID -->
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1rem; margin-bottom: 1rem;">
              
              <!-- LEFT CARD: LCARS AUDIO-VISUALIZER & SPRECH-STEUERUNG -->
              <div class="lcars-card" style="padding: 1.25rem; display: flex; flex-direction: column; gap: 1.25rem;">
                <div style="font-family: var(--font-family); font-size: 1rem; font-weight: 700; color: var(--c-secondary); letter-spacing: 0.06em; text-transform: uppercase; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 0.5rem;">
                  AUDIO-PEGEL &amp; FREQUENZ-METRIK
                </div>

                <!-- Input VU Meter -->
                <div>
                  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem; font-size: 0.78rem; font-family: var(--mono-family);">
                    <span style="color: var(--c-primary); font-weight: 700;">EINGANG (MIKROFON 16 kHz):</span>
                    <span id="geminiInLevelText" style="color: var(--c-gold);">0%</span>
                  </div>
                  <div id="geminiInMeter" class="lcars-meter-track" style="display: flex; gap: 3px; height: 22px; background: rgba(0,0,0,0.6); padding: 3px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.1);">
                    <!-- 12 Segment Bars -->
                  </div>
                </div>

                <!-- Output VU Meter -->
                <div>
                  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem; font-size: 0.78rem; font-family: var(--mono-family);">
                    <span style="color: var(--c-secondary); font-weight: 700;">AUSGANG (GEMINI LIVE 24 kHz):</span>
                    <span id="geminiOutLevelText" style="color: var(--c-gold);">0%</span>
                  </div>
                  <div id="geminiOutMeter" class="lcars-meter-track" style="display: flex; gap: 3px; height: 22px; background: rgba(0,0,0,0.6); padding: 3px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.1);">
                    <!-- 12 Segment Bars -->
                  </div>
                </div>

                <!-- PTT Action Button / Live Status Indicator -->
                <div style="margin-top: auto; padding-top: 0.5rem;">
                  <div id="geminiPttActionArea">
                    <button type="button" id="btnGeminiPttAction" class="lcars-btn" style="width: 100%; height: 60px; font-size: 1.05rem; font-weight: 800; letter-spacing: 0.08em; background: rgba(186,164,229,0.15); color: var(--c-secondary); border: 2px solid var(--c-secondary); border-radius: 6px; cursor: pointer; transition: all 0.1s ease; user-select: none;">
                      🎙️ SPRECHEN (GEDRÜCKT HALTEN / LEERTASTE)
                    </button>
                    <div style="text-align: center; font-size: 0.72rem; color: #777; font-family: var(--mono-family); margin-top: 0.4rem;">
                      Halten zum Sprechen • Loslassen zum Verarbeiten • Barge-In aktiv
                    </div>
                  </div>

                  <div id="geminiLiveStatusArea" style="display: none; text-align: center; padding: 1rem; background: rgba(68,221,136,0.06); border: 1px solid rgba(68,221,136,0.25); border-radius: 6px;">
                    <div id="geminiLivePulseDot" style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #44dd88; margin-right: 6px; box-shadow: 0 0 8px #44dd88; animation: lcarsPulse 1.5s infinite;"></div>
                    <span style="font-family: var(--mono-family); font-size: 0.85rem; color: #44dd88; font-weight: 700;">
                      SUBRAUM-KANAL LIVE // MIKROFON DAUERHAFT AKTIV
                    </span>
                    <div style="font-size: 0.72rem; color: #aaa; margin-top: 0.35rem;">
                      Sprechen Sie frei. Das Modell erkennt Sprachpausen und antwortet in Echtzeit.
                    </div>
                  </div>
                </div>

              </div>

              <!-- RIGHT CARD: LIVE-TRANSKRIPTION & SUBRAUM-LOG -->
              <div class="lcars-card" style="padding: 1.25rem; display: flex; flex-direction: column; min-height: 420px;">
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 0.5rem; margin-bottom: 0.75rem;">
                  <span style="font-family: var(--font-family); font-size: 1rem; font-weight: 700; color: var(--c-gold); letter-spacing: 0.06em; text-transform: uppercase;">
                    NEURAL SUBRAUM LOGBUCH // TRANSKRIPT
                  </span>
                  <span id="geminiLiveTurnIndicator" style="font-size: 0.75rem; color: #888; font-family: var(--mono-family);">
                    BEREIT // ZUHÖREN
                  </span>
                </div>

                <!-- Log Output Window -->
                <div id="geminiLiveTranscript" style="flex: 1; background: rgba(0, 0, 0, 0.75); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 6px; padding: 0.75rem 1rem; overflow-y: auto; max-height: 320px; font-family: var(--mono-family); font-size: 0.85rem; line-height: 1.5; display: flex; flex-direction: column; gap: 0.6rem;">
                  <div style="color: #666; font-style: italic;">
                    [SYSTEM] Subraum-Relay initialisiert. Klicken Sie auf 'SUBRAUM-KANAL ÖFFNEN' um die Session zu starten.
                  </div>
                </div>

                <!-- Quick Text Message Input (Fallback) -->
                <div style="display: flex; gap: 0.5rem; margin-top: 0.75rem;">
                  <input type="text" id="geminiLiveTextInput" placeholder="Textnachricht an Bordcomputer senden..." class="lcars-input" style="flex: 1; height: 38px; font-size: 0.85rem;" onkeydown="if(event.key==='Enter') sendGeminiLiveText();">
                  <button type="button" class="left-action-btn" onclick="sendGeminiLiveText()" style="height: 38px; padding: 0 1rem; border-color: var(--c-secondary); color: var(--c-secondary); font-weight: 700;">
                    SENDEN
                  </button>
                </div>
              </div>

            </div>
          </div>
        </section>

        <!-- KATEGORIE: REMOTE NODE PIMMEL (100.88.215.98) -->
        <section class="lcars-section" id="section-pimmel">
          <!-- Header Bar -->
          <div class="lcars-header-bar" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
            <div style="display:flex; align-items:center; gap:0.75rem;">
              <h2>REMOTE NODE // PIMMEL ODN-HUB (100.88.215.98)</h2>
              <span id="pimmelNodeStatusBadge" class="badge-status badge-online" style="font-size:0.85rem; padding:0.25rem 0.75rem;">● ONLINE</span>
            </div>
            <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
              <span id="pimmelLastSyncText" style="font-family:var(--mono-family); font-size:0.78rem; color:#aaa;">SYNC: --:--:--</span>
              <button type="button" class="left-action-btn" onclick="fetchPimmelStats(true)" style="padding:0.35rem 0.8rem; font-size:0.82rem; border-color:var(--c-blue); color:var(--c-blue);">
                <span>⟳</span> <span>REFRESH</span>
              </button>
              <button type="button" class="left-action-btn" id="btnPimmelSyncDb" onclick="triggerPimmelSync()" style="padding:0.35rem 0.8rem; font-size:0.82rem; border-color:var(--c-gold); color:var(--c-gold);">
                <span>⚡</span> <span>SYNC DB</span>
              </button>
            </div>
          </div>

          <!-- Offline Warning Alert (hidden by default) -->
          <div id="pimmelOfflineAlert" style="display:none; margin-bottom:1rem; padding:0.8rem 1.2rem; background:rgba(207,79,79,0.15); border:1px solid var(--c-red); border-left:6px solid var(--c-red); border-radius:4px; font-family:var(--mono-family); font-size:0.85rem; color:#ff8888;">
            ⚠️ <strong>LCARS WARNUNG:</strong> Remote Node PiMMEL nicht erreichbar oder SSH-Verbindung unterbrochen. Lokale Cache-Daten werden angezeigt.
            <span id="pimmelOfflineErrMsg" style="display:block; margin-top:0.3rem; opacity:0.8;"></span>
          </div>

          <!-- READOUT GRID 1: PIMMEL HOST VITALS -->
          <div class="readout-grid">
            <!-- CPU -->
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">CPU AUSLASTUNG</span>
                <span class="card-head-icon">⚡</span>
              </div>
              <div class="card-metric" id="pimmelCpuVal">--%</div>
              <div class="card-metric-sub" id="pimmelCpuSub">Kerne: 4 | Load: -- / -- / --</div>
              <div class="lcars-bar-track">
                <div class="lcars-bar-fill" id="pimmelCpuBar" style="width: 0%;"></div>
              </div>
            </div>

            <!-- RAM -->
            <div class="lcars-card card-violet">
              <div class="card-head">
                <span class="card-head-title">RAM BELEGUNG</span>
                <span class="card-head-icon">💾</span>
              </div>
              <div class="card-metric" id="pimmelRamVal">--%</div>
              <div class="card-metric-sub" id="pimmelRamSub">-- GB / -- GB</div>
              <div class="lcars-bar-track">
                <div class="lcars-bar-fill" id="pimmelRamBar" style="width: 0%; background:var(--c-secondary);"></div>
              </div>
            </div>

            <!-- SoC Temp -->
            <div class="lcars-card card-blue">
              <div class="card-head">
                <span class="card-head-title">SOC TEMPERATUR</span>
                <span class="card-head-icon">🌡️</span>
              </div>
              <div class="card-metric" id="pimmelTempVal">--.- °C</div>
              <div class="card-metric-sub" id="pimmelTempSub">Raspberry Pi 4 ARM64</div>
              <div class="badge-status badge-online" id="pimmelTempBadge">NORMAL // NOMINAL</div>
            </div>

            <!-- Root Disk -->
            <div class="lcars-card card-almond">
              <div class="card-head">
                <span class="card-head-title">DISK STORAGE (ROOT)</span>
                <span class="card-head-icon">💿</span>
              </div>
              <div class="card-metric" id="pimmelDiskVal">--%</div>
              <div class="card-metric-sub" id="pimmelDiskSub">-- GB / -- GB</div>
              <div class="lcars-bar-track">
                <div class="lcars-bar-fill" id="pimmelDiskBar" style="width: 0%; background:var(--c-almond);"></div>
              </div>
            </div>

            <!-- Uptime -->
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">NODE UPTIME</span>
                <span class="card-head-icon">⏱️</span>
              </div>
              <div class="card-metric" id="pimmelUptimeVal" style="font-size:1.4rem;">-- Std -- Min</div>
              <div class="card-metric-sub" id="pimmelBootTimeSub">Boot: --</div>
              <div class="badge-status badge-online">ONLINE STABIL</div>
            </div>

            <!-- Hermes Gateway -->
            <div class="lcars-card card-violet">
              <div class="card-head">
                <span class="card-head-title">HERMES GATEWAY</span>
                <span class="card-head-icon">🤖</span>
              </div>
              <div class="card-metric" id="pimmelHermesStatus" style="font-size:1.2rem; color:var(--c-secondary);">AKTIV</div>
              <div class="card-metric-sub">User Systemd Service</div>
              <div class="badge-status badge-online" id="pimmelHermesBadge">ONLINE // RUNNING</div>
            </div>

            <!-- PM2 9Router -->
            <div class="lcars-card card-blue">
              <div class="card-head">
                <span class="card-head-title">PM2 9ROUTER DAEMON</span>
                <span class="card-head-icon">⚙️</span>
              </div>
              <div class="card-metric" id="pimmelPm2Status" style="font-size:1.2rem; color:var(--c-blue);">ONLINE</div>
              <div class="card-metric-sub" id="pimmelPm2Sub">PID: -- | Restarts: 0 | Mem: -- MB</div>
              <div class="badge-status badge-online" id="pimmelPm2Badge">v0.5.75 // RUNNING</div>
            </div>
          </div>

          <!-- READOUT GRID 2: 9ROUTER TELEMETRIE TILES -->
          <div class="readout-grid" style="margin-top: 1rem;">
            <!-- Requests -->
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">9ROUTER ANFRAGEN</span>
                <span class="card-head-icon">⚡</span>
              </div>
              <div class="card-metric" id="pimmel9rRequests">--</div>
              <div class="card-metric-sub">ODN Hub PiMMEL (Port 20128)</div>
              <div class="badge-status badge-online">REMOTE PROXY</div>
            </div>

            <!-- Token Volumen -->
            <div class="lcars-card card-violet">
              <div class="card-head">
                <span class="card-head-title">TOKEN VOLUMEN</span>
                <span class="card-head-icon">🔢</span>
              </div>
              <div class="card-metric" id="pimmel9rTokens">--M</div>
              <div class="card-metric-sub" id="pimmel9rPromptCompl">Prompt: -- | Compl: --</div>
              <div class="card-metric-sub" id="pimmel9rCached" style="color:var(--c-blue);">Cached: --</div>
              <div class="badge-status badge-online">TOKEN FLOW</div>
            </div>

            <!-- Cache Ersparnis -->
            <div class="lcars-card card-blue">
              <div class="card-head">
                <span class="card-head-title">CACHE ERSPARNIS</span>
                <span class="card-head-icon">🛡️</span>
              </div>
              <div class="card-metric" id="pimmel9rCacheRate" style="color:var(--c-blue);">--%</div>
              <div class="card-metric-sub" id="pimmel9rSavedTokens">Tokens gespart: --</div>
              <div class="card-metric-sub" id="pimmel9rSavedCost" style="color:var(--c-accent);">Kosten gespart: ~$--</div>
              <div class="badge-status badge-online">EFFIZIENZ PEAK</div>
            </div>

            <!-- Gesamtkosten -->
            <div class="lcars-card card-almond">
              <div class="card-head">
                <span class="card-head-title">GESAMTKOSTEN</span>
                <span class="card-head-icon">💳</span>
              </div>
              <div class="card-metric" id="pimmel9rTotalCost">$--</div>
              <div class="card-metric-sub" id="pimmel9rUncachedCost">Ohne Cache: ~$--</div>
              <div class="card-metric-sub" id="pimmel9rCostSavingsSub">Netto-Ersparnis: ~$--</div>
              <div class="badge-status badge-online">ROUTING SPENDINGS</div>
            </div>

            <!-- Provider & Modelle -->
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">PROVIDER &amp; MODELLE</span>
                <span class="card-head-icon">🛰️</span>
              </div>
              <div class="card-metric" id="pimmel9rProviderCount">--</div>
              <div class="card-metric-sub" id="pimmel9rProvidersList">Aktive Upstreams</div>
              <div class="badge-status badge-online">INTEGRIERT</div>
            </div>
          </div>

          <!-- GRAFISCHE ANALYSE: DUAL CHARTS -->
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap:1rem; margin-top:1.25rem; width:100%; min-width:0;">
            <!-- Pimmel 24h Sensor History Chart -->
            <div class="lcars-card" style="padding:1.1rem; width:100%; min-width:0;">
              <div class="card-head" style="margin-bottom:0.75rem; justify-content:space-between; flex-wrap:wrap; gap:0.4rem;">
                <span class="card-head-title" style="color:var(--c-primary); font-size:1.05rem;">PIMMEL 24H SENSOR HISTORIE</span>
                <div style="display:flex; gap:0.3rem; flex-wrap:wrap;">
                  <button type="button" class="left-action-btn pimmel-range-btn" onclick="setPimmelHistoryRange('10m')">10m</button>
                  <button type="button" class="left-action-btn pimmel-range-btn" onclick="setPimmelHistoryRange('30m')">30m</button>
                  <button type="button" class="left-action-btn pimmel-range-btn active-range" onclick="setPimmelHistoryRange('1h')">1h</button>
                  <button type="button" class="left-action-btn pimmel-range-btn" onclick="setPimmelHistoryRange('12h')">12h</button>
                  <button type="button" class="left-action-btn pimmel-range-btn" onclick="setPimmelHistoryRange('24h')">24h</button>
                </div>
              </div>
              <div style="display:flex; gap:0.35rem; flex-wrap:wrap; margin-bottom:0.75rem;">
                <button type="button" class="left-action-btn" onclick="togglePimmelDataset(0)" id="pdsBtn0" style="padding:0.2rem 0.6rem; font-size:0.75rem; border-color:var(--c-primary); color:var(--c-primary);">TEMPERATUR</button>
                <button type="button" class="left-action-btn" onclick="togglePimmelDataset(1)" id="pdsBtn1" style="padding:0.2rem 0.6rem; font-size:0.75rem; border-color:var(--c-blue); color:var(--c-blue);">CPU %</button>
                <button type="button" class="left-action-btn" onclick="togglePimmelDataset(2)" id="pdsBtn2" style="padding:0.2rem 0.6rem; font-size:0.75rem; border-color:var(--c-secondary); color:var(--c-secondary);">RAM %</button>
              </div>
              <div style="position:relative; width:100%; height:260px; min-width:0; overflow:hidden;">
                <canvas id="pimmelHistoryCanvas"></canvas>
              </div>
            </div>

            <!-- 9Router Model Token Distribution Doughnut Chart -->
            <div class="lcars-card" style="min-height:320px; display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; min-width:0; padding:1.1rem;">
              <div class="card-head" style="align-self:flex-start; width:100%; margin-bottom:0.75rem;">
                <span class="card-head-title" style="color:var(--c-secondary); font-size:1.05rem;">9ROUTER MODELL-TOKEN VERTEILUNG</span>
                <span class="card-head-icon">📊</span>
              </div>
              <div style="position:relative; width:100%; max-width:320px; height:260px; min-width:0;">
                <canvas id="pimmelModelCanvas"></canvas>
              </div>
            </div>
          </div>

          <!-- 9ROUTER TRANSMISSIONS-HISTORIE & TOKEN-FLOW -->
          <div class="lcars-card" style="margin-top:1.25rem; padding:1.1rem; width:100%; min-width:0;">
            <div class="card-head" style="margin-bottom:0.75rem;">
              <span class="card-head-title" style="color:var(--c-primary); font-size:1.1rem;">9ROUTER TRANSMISSIONS-HISTORIE &amp; TOKEN-FLOW</span>
              <span class="card-head-icon">📈</span>
            </div>
            <div style="position:relative; width:100%; height:260px; min-width:0;">
              <canvas id="pimmelTimelineCanvas"></canvas>
            </div>
          </div>

          <!-- 9ROUTER TRANSMISSIONS-LOG // LETZTE REQUESTS TABELLE -->
          <div class="lcars-card" style="margin-top:1.25rem; width:100%; min-width:0; overflow-x:auto;">
            <div class="card-head" style="margin-bottom:0.75rem; justify-content:space-between; flex-wrap:wrap; gap:0.5rem;">
              <div style="display:flex; align-items:center; gap:0.5rem;">
                <span class="card-head-title">9ROUTER TRANSMISSIONS-LOG // LETZTE REQUESTS</span>
                <span class="card-head-icon">📋</span>
              </div>
              <button type="button" class="left-action-btn" onclick="fetchPimmelStats(true)" title="Daten aktualisieren" style="padding:0.25rem 0.6rem; font-size:0.8rem;">
                <span>⟳ REFRESH</span>
              </button>
            </div>
            <div style="width:100%; overflow-x:auto;">
              <table class="services-table" style="width:100%; border-collapse:collapse; font-size:0.86rem; text-align:left;">
                <thead>
                  <tr style="border-bottom:2px solid var(--c-primary); color:var(--c-primary); font-family:var(--font-family); letter-spacing:0.06em;">
                    <th style="padding:0.5rem 0.4rem;">ID</th>
                    <th style="padding:0.5rem 0.4rem;">ZEITPUNKT</th>
                    <th style="padding:0.5rem 0.4rem;">PROVIDER</th>
                    <th style="padding:0.5rem 0.4rem;">MODELL</th>
                    <th style="padding:0.5rem 0.4rem;">PROMPT</th>
                    <th style="padding:0.5rem 0.4rem;">COMPL</th>
                    <th style="padding:0.5rem 0.4rem;">CACHED</th>
                    <th style="padding:0.5rem 0.4rem;">TOTAL</th>
                    <th style="padding:0.5rem 0.4rem;">KOSTEN</th>
                    <th style="padding:0.5rem 0.4rem;">STATUS</th>
                  </tr>
                </thead>
                <tbody id="pimmelHistoryTableBody">
                  <tr>
                    <td colspan="10" style="padding:0.8rem; text-align:center; color:#888;">Warte auf Datenübertragung...</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- PM2 9ROUTER LIVE CONSOLE LOGS -->
          <div class="lcars-card" style="margin-top:1.25rem; width:100%; min-width:0; padding:1.1rem; border-top:3px solid var(--c-primary);">
            <div class="card-head" style="margin-bottom:0.75rem; justify-content:space-between; flex-wrap:wrap; gap:0.5rem;">
              <div style="display:flex; align-items:center; gap:0.6rem;">
                <span style="font-size:1.2rem;">📟</span>
                <div>
                  <span class="card-head-title" style="color:var(--c-primary); font-size:1.05rem;">PM2 9ROUTER LIVE CONSOLE LOGS</span>
                  <div style="font-family:var(--mono-family); font-size:0.72rem; color:#888;">~/.pm2/logs/9router-out.log (PiMMEL Node)</div>
                </div>
              </div>
              <div style="display:flex; align-items:center; gap:0.4rem; flex-wrap:wrap;">
                <button type="button" class="left-action-btn" id="btnPimmelLogAutoScroll" onclick="togglePimmelLogAutoScroll()" style="padding:0.25rem 0.7rem; font-size:0.78rem; border-color:var(--c-blue); color:var(--c-blue);">
                  <span>⬇️ AUTO-SCROLL: AN</span>
                </button>
                <select id="pimmelLogLineSelect" class="lcars-input" style="height:30px; font-size:0.78rem; padding:0 0.4rem;" onchange="fetchPimmelLogs()">
                  <option value="30">30 ZEILEN</option>
                  <option value="60" selected>60 ZEILEN</option>
                  <option value="100">100 ZEILEN</option>
                </select>
                <button type="button" class="left-action-btn" onclick="fetchPimmelLogs()" style="padding:0.25rem 0.6rem; font-size:0.78rem; border-color:var(--c-primary); color:var(--c-primary);">
                  <span>⟳ LOGS LADEN</span>
                </button>
                <button type="button" class="left-action-btn" onclick="copyPimmelLogs()" style="padding:0.25rem 0.6rem; font-size:0.78rem; border-color:var(--c-gold); color:var(--c-gold);">
                  <span>📋 KOPIEREN</span>
                </button>
              </div>
            </div>

            <!-- Terminal Container -->
            <div id="pimmelLogTerminal" style="background:#07070b; border:1px solid rgba(255,255,255,0.12); border-radius:6px; padding:0.85rem 1rem; max-height:360px; overflow-y:auto; font-family:var(--mono-family); font-size:0.82rem; line-height:1.55; white-space:pre-wrap; word-break:break-word; color:#e0e0e0;">
              <div style="color:#888; font-style:italic;">[SYSTEM] Initialisiere Terminal-Verbindung zu PiMMEL...</div>
            </div>
          </div>
        </section>

        <!-- LCARS PULSECAST SERIES EPISODES MODAL -->
        <div id="pulsecastSeriesModal" class="ha-modal-overlay" style="display:none;" onclick="handlePulsecastSeriesModalBackdropClick(event)">
          <div class="ha-modal-content" onclick="event.stopPropagation()" style="max-width:820px; width:95%; max-height:90vh; display:flex; flex-direction:column;">
            <div class="ha-modal-header" style="background:var(--c-secondary); color:#000;">
              <div style="display:flex; align-items:center; gap:0.6rem; min-width:0;">
                <span style="font-size:1.4rem; flex-shrink:0;">📺</span>
                <div style="min-width:0;">
                  <div id="pulsecastModalSeriesTitle" style="font-size:1.15rem; font-weight:700; color:#000; text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                    Serientitel
                  </div>
                  <div id="pulsecastModalSeriesMeta" style="font-size:0.75rem; color:#111; font-family:var(--mono-family);">
                    Staffeln &amp; Episodenübersicht
                  </div>
                </div>
              </div>
              <button class="ha-modal-close-btn" onclick="closePulsecastSeriesModal()">✕ SCHLIESSEN</button>
            </div>
            <div class="ha-modal-body" style="padding:1rem; overflow-y:auto; flex:1;">
              <!-- Batch Download / Season Controls -->
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem; flex-wrap:wrap; gap:0.5rem; background:rgba(0,0,0,0.3); padding:0.6rem 0.8rem; border-radius:6px; border:1px solid rgba(255,255,255,0.06);">
                <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
                  <label style="font-size:0.8rem; color:var(--c-gold); font-weight:700;">STAFFEL:</label>
                  <select id="pulsecastSeasonFilterSelect" class="lcars-input" style="height:32px; padding:0 0.6rem; font-size:0.82rem;" onchange="filterPulsecastEpisodesBySeason()">
                    <option value="all">ALLE STAFFELN</option>
                  </select>
                </div>
                <div style="display:flex; gap:0.5rem;">
                  <button type="button" class="left-action-btn" onclick="downloadAllVisibleEpisodes()" style="padding:0.35rem 0.9rem; font-size:0.82rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                    <span>⬇️</span> <span>ANGEZEIGTE EPISODEN HERUNTERLADEN</span>
                  </button>
                </div>
              </div>

              <!-- Episodes List -->
              <div id="pulsecastEpisodesLoading" style="text-align:center; padding:2rem; color:var(--c-primary); font-family:var(--font-family);">
                EPISODENDATEN WERDEN AUS XTREAM-CODES GELADEN...
              </div>
              <div id="pulsecastEpisodesList" style="display:flex; flex-direction:column; gap:0.5rem;">
                <!-- Dynamically populated episode items -->
              </div>
            </div>
          </div>
        </div>

        <!-- LCARS PULSECAST MEDIA PLAYER MODAL -->
        <div id="pulsecastPlayerModal" class="ha-modal-overlay" style="display:none;" onclick="handlePulsecastPlayerModalBackdropClick(event)">
          <div class="ha-modal-content" onclick="event.stopPropagation()" style="max-width:860px; width:95%; max-height:92vh; display:flex; flex-direction:column; background:#000; border:2px solid var(--c-butterscotch); border-radius:8px; overflow:hidden; box-shadow:0 0 25px rgba(255,153,0,0.35);">
            <!-- Header -->
            <div class="ha-modal-header" style="background:var(--c-butterscotch); color:#000; padding:0.6rem 1rem; display:flex; justify-content:space-between; align-items:center;">
              <div style="display:flex; align-items:center; gap:0.6rem; min-width:0;">
                <span style="font-size:1.3rem; flex-shrink:0;">▶</span>
                <div style="min-width:0;">
                  <div id="pulsecastPlayerModalTitle" style="font-family:var(--font-family); font-size:1.1rem; font-weight:700; color:#000; text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                    MEDIENDATEI WIEDERGABE
                  </div>
                  <div id="pulsecastPlayerModalMeta" style="font-size:0.72rem; color:#222; font-family:var(--mono-family); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                    LCARS DIRECT HTTP RANGE STREAM
                  </div>
                </div>
              </div>
              <button type="button" class="ha-modal-close-btn" onclick="closePulsecastPlayerModal()" style="border-color:#000; color:#000; font-weight:700;">✕</button>
            </div>

            <!-- Modal Body -->
            <div style="padding:1.2rem; overflow-y:auto; flex:1; display:flex; flex-direction:column; gap:1.2rem;">
              
              <!-- Stream URL Box & Copy / Open Tab -->
              <div style="background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:0.75rem 1rem;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.4rem; flex-wrap:wrap; gap:0.4rem;">
                  <span style="font-size:0.75rem; color:var(--c-gold); font-family:var(--font-family); font-weight:700; letter-spacing:0.06em;">DIREKTE HTTP RANGE STREAM-URL (NATIVE STREAMING)</span>
                  <span id="pulsecastCopySuccessBadge" style="display:none; color:#44dd88; font-family:var(--mono-family); font-size:0.75rem; font-weight:700;">✓ IN DIE ZWISCHENABLAGE KOPIERT!</span>
                </div>
                <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
                  <input type="text" id="pulsecastPlayerStreamUrlInput" readonly style="flex:1; min-width:240px; background:rgba(0,0,0,0.6); color:#fff; border:1px solid rgba(255,255,255,0.2); border-radius:4px; padding:0.45rem 0.7rem; font-family:var(--mono-family); font-size:0.8rem;" onclick="this.select();">
                  <button type="button" class="left-action-btn" onclick="copyPulsecastStreamUrl()" style="padding:0.45rem 0.9rem; font-size:0.8rem; border-color:var(--c-gold); color:var(--c-gold); font-weight:700; white-space:nowrap;">
                    📋 STREAM-URL KOPIEREN
                  </button>
                  <button type="button" class="left-action-btn" onclick="openPulsecastStreamInTab()" style="padding:0.45rem 0.9rem; font-size:0.8rem; border-color:var(--c-blue); color:var(--c-blue); font-weight:700; white-space:nowrap;">
                    ↗ IM NEUEN TAB ÖFFNEN
                  </button>
                </div>
              </div>

              <!-- 1-Click M3U Stream-Datei Box (Empfohlen für Desktop & Mobile) -->
              <div style="background:rgba(68,221,136,0.08); border:1px solid rgba(68,221,136,0.35); border-left:5px solid #44dd88; border-radius:6px; padding:0.85rem 1rem;">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.6rem;">
                  <a id="pulsecastM3uDownloadBtn" href="#" download class="lcars-btn" onclick="playLcarsBeep(1400, 1800)" style="flex:1; min-width:260px; display:inline-flex; align-items:center; justify-content:center; gap:0.6rem; background:#44dd88; color:#000; font-weight:800; font-size:0.95rem; font-family:var(--font-family); text-decoration:none; padding:0.7rem 1.2rem; border-radius:4px; box-shadow:0 0 15px rgba(68,221,136,0.4); text-transform:uppercase; letter-spacing:0.05em; transition:all 0.2s ease;">
                    <span style="font-size:1.15rem;">📥</span> VLC / MEDIA PLAYER STREAM-DATEI (.M3U)
                  </a>
                </div>
                <div style="font-size:0.75rem; color:#aaffcc; font-family:var(--mono-family); margin-top:0.45rem;">
                  💡 1-Klick: Öffnet sofort VLC / PotPlayer / IINA auf Ihrem Computer mit nativem AC3/DTS-Mehrkanalton.
                </div>
              </div>

              <!-- Player Selection Grid -->
              <div>
                <div style="font-family:var(--font-family); font-size:0.85rem; color:var(--c-butterscotch); font-weight:700; letter-spacing:0.06em; margin-bottom:0.6rem;">
                  IN LOKALEM MEDIA PLAYER ÖFFNEN:
                </div>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:0.6rem;">
                  <!-- VLC Media Player -->
                  <a id="playerBtnVlc" href="#" class="lcars-card" title="(Nur wenn App-Protokoll im Browser/OS registriert ist)" style="display:flex; align-items:center; gap:0.6rem; padding:0.75rem; text-decoration:none; border:1px solid var(--c-primary); border-left:4px solid var(--c-primary); cursor:pointer; background:rgba(255,153,0,0.06);">
                    <span style="font-size:1.6rem;">🟠</span>
                    <div>
                      <div style="color:var(--c-primary); font-weight:700; font-size:0.85rem; font-family:var(--font-family);">VLC MEDIA PLAYER</div>
                      <div style="color:#888; font-size:0.68rem; font-family:var(--mono-family);">vlc:// URL-Schema (Nur wenn App-Protokoll registriert)</div>
                    </div>
                  </a>

                  <!-- PotPlayer (Windows) -->
                  <a id="playerBtnPotPlayer" href="#" class="lcars-card" title="(Nur wenn App-Protokoll im Browser/OS registriert ist)" style="display:flex; align-items:center; gap:0.6rem; padding:0.75rem; text-decoration:none; border:1px solid var(--c-blue); border-left:4px solid var(--c-blue); cursor:pointer; background:rgba(0,170,255,0.06);">
                    <span style="font-size:1.6rem;">🟡</span>
                    <div>
                      <div style="color:var(--c-blue); font-weight:700; font-size:0.85rem; font-family:var(--font-family);">POTPLAYER (WIN)</div>
                      <div style="color:#888; font-size:0.68rem; font-family:var(--mono-family);">potplayer:// URL-Schema (Nur wenn App-Protokoll registriert)</div>
                    </div>
                  </a>

                  <!-- IINA (macOS) -->
                  <a id="playerBtnIina" href="#" class="lcars-card" title="(Nur wenn App-Protokoll im Browser/OS registriert ist)" style="display:flex; align-items:center; gap:0.6rem; padding:0.75rem; text-decoration:none; border:1px solid var(--c-secondary); border-left:4px solid var(--c-secondary); cursor:pointer; background:rgba(180,100,255,0.06);">
                    <span style="font-size:1.6rem;">🟣</span>
                    <div>
                      <div style="color:var(--c-secondary); font-weight:700; font-size:0.85rem; font-family:var(--font-family);">IINA (macOS)</div>
                      <div style="color:#888; font-size:0.68rem; font-family:var(--mono-family);">iina://weblink?url= (Nur wenn App-Protokoll registriert)</div>
                    </div>
                  </a>

                  <!-- nPlayer (Mobile) -->
                  <a id="playerBtnNplayer" href="#" class="lcars-card" title="(Nur wenn App-Protokoll im Browser/OS registriert ist)" style="display:flex; align-items:center; gap:0.6rem; padding:0.75rem; text-decoration:none; border:1px solid #44dd88; border-left:4px solid #44dd88; cursor:pointer; background:rgba(68,221,136,0.06);">
                    <span style="font-size:1.6rem;">📱</span>
                    <div>
                      <div style="color:#44dd88; font-weight:700; font-size:0.85rem; font-family:var(--font-family);">nPLAYER (MOBILE)</div>
                      <div style="color:#888; font-size:0.68rem; font-family:var(--mono-family);">nplayer- URL-Schema (Nur wenn App-Protokoll registriert)</div>
                    </div>
                  </a>

                  <!-- Web-Player (Browser) Button -->
                  <button type="button" class="lcars-card" onclick="togglePulsecastWebPlayer(true)" style="display:flex; align-items:center; gap:0.6rem; padding:0.75rem; border:1px solid var(--c-gold); border-left:4px solid var(--c-gold); cursor:pointer; background:rgba(255,204,0,0.06); text-align:left;">
                    <span style="font-size:1.6rem;">🌐</span>
                    <div>
                      <div style="color:var(--c-gold); font-weight:700; font-size:0.85rem; font-family:var(--font-family);">WEB-PLAYER (BROWSER)</div>
                      <div style="color:#888; font-size:0.7rem; font-family:var(--mono-family);">LCARS HTML5 Player + Auto-Transcode</div>
                    </div>
                  </button>
                </div>
              </div>

              <!-- Integrated HTML5 Video Player Container -->
              <div id="pulsecastWebPlayerContainer" style="display:none; background:#000; border:1px solid rgba(255,255,255,0.15); border-radius:6px; padding:0.75rem; flex-direction:column; gap:0.6rem;">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
                  <span style="font-family:var(--font-family); font-size:0.85rem; color:var(--c-gold); font-weight:700;">
                    LCARS HTML5 BROWSER-STREAM (AUTOPLAY)
                  </span>
                  <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
                    <button type="button" id="pulsecastAudioModeToggle" class="left-action-btn" onclick="togglePulsecastAudioMode()" style="padding:0.25rem 0.7rem; font-size:0.75rem; font-weight:700; border-radius:4px; border:1px solid #44dd88; color:#44dd88; background:rgba(68,221,136,0.15); white-space:nowrap;">
                      🔊 TON: AAC (KOMPATIBEL)
                    </button>
                    <button type="button" class="left-action-btn" onclick="togglePulsecastWebPlayer(false)" style="border-color:#888; color:#888; padding:0.25rem 0.6rem; font-size:0.75rem;">
                      PLAYER SCHLIESSEN
                    </button>
                  </div>
                </div>

                <!-- Audio Status Notice -->
                <div id="pulsecastAudioStatusHint" style="font-size:0.75rem; font-family:var(--mono-family); padding:0.35rem 0.6rem; border-radius:4px; background:rgba(255,255,255,0.03); color:#44dd88; border:1px solid rgba(255,255,255,0.08);">
                  🔊 Audio-Transcoding aktiv (AAC Stereo/5.1 für ruckelfreien Browser-Ton)
                </div>

                <video id="pulsecastWebPlayerVideo" controls autoplay playsinline style="width:100%; max-height:55vh; background:#000; border-radius:4px; outline:none;"></video>
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.4rem; font-size:0.72rem; color:#888; font-family:var(--mono-family);">
                  <span>💡 Umschalten auf ORIGINAL, falls Datei bereits AAC/MP3-Ton besitzt.</span>
                  <span>Tipp: Für MKV / Mehrkanal-Ton wird die .M3U-Datei in VLC empfohlen.</span>
                </div>
              </div>

            </div>
          </div>
        </div>


        <!-- LCARS PARTNER FORM MODAL (ANLEGEN / BEARBEITEN) -->
        <div id="partnerFormModal" class="ha-modal-overlay" style="display:none;" onclick="handlePartnerModalBackdropClick(event)">
          <div class="ha-modal-content" onclick="event.stopPropagation()" style="max-width:480px;">
            <div class="ha-modal-header" style="background:var(--c-secondary); color:#000;">
              <div style="display:flex; align-items:center; gap:0.6rem;">
                <span style="font-size:1.3rem;">🧬</span>
                <div id="partnerModalTitle" style="font-size:1.15rem; font-weight:700; text-transform:uppercase; font-family:var(--font-family);">
                  PARTNERIN ANLEGEN
                </div>
              </div>
              <button class="ha-modal-close-btn" onclick="closePartnerModal()">✕</button>
            </div>
            <div class="ha-modal-body" style="padding:1.25rem;">
              <form id="partnerForm" onsubmit="savePartnerData(event)">
                <input type="hidden" id="formPartnerId" value="">

                <div class="config-field" style="margin-bottom:1rem;">
                  <label for="formPartnerName" style="display:block; font-size:0.85rem; font-weight:700; color:var(--c-secondary); margin-bottom:0.35rem;">
                    👤 NAME DER PARTNERIN
                  </label>
                  <input type="text" id="formPartnerName" class="lcars-input" placeholder="z.B. Sarah" required>
                </div>

                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:0.75rem; margin-bottom:1rem;">
                  <div class="config-field">
                    <label for="formCycleDuration" style="display:block; font-size:0.85rem; font-weight:700; color:var(--c-gold); margin-bottom:0.35rem;">
                      ⏱️ ZYKLUSDAUER (TAGE)
                    </label>
                    <input type="number" id="formCycleDuration" min="15" max="60" value="28" class="lcars-input" required>
                    <div style="font-size:0.72rem; color:#888; margin-top:0.25rem;">Standard: 28 Tage (15-60)</div>
                  </div>

                  <div class="config-field">
                    <label for="formPeriodDuration" style="display:block; font-size:0.85rem; font-weight:700; color:var(--c-red); margin-bottom:0.35rem;">
                      🩸 PERIODENDAUER (TAGE)
                    </label>
                    <input type="number" id="formPeriodDuration" min="1" max="15" value="5" class="lcars-input" required>
                    <div style="font-size:0.72rem; color:#888; margin-top:0.25rem;">Standard: 5 Tage (1-15)</div>
                  </div>
                </div>

                <div class="config-field" style="margin-bottom:1rem;">
                  <label for="formStartDate" style="display:block; font-size:0.85rem; font-weight:700; color:var(--c-primary); margin-bottom:0.35rem;">
                    📅 LETZTER PERIODENBEGINN (STARTDATUM)
                  </label>
                  <input type="date" id="formStartDate" class="lcars-input" required>
                  <div style="font-size:0.72rem; color:#888; margin-top:0.25rem;">Erster Tag der letzten Menstruation</div>
                </div>

                <div class="config-field" style="margin-bottom:1.25rem;">
                  <label for="formNotes" style="display:block; font-size:0.85rem; font-weight:700; color:#aaa; margin-bottom:0.35rem;">
                    📝 NOTIZEN / BESONDERHEITEN (OPTIONAL)
                  </label>
                  <textarea id="formNotes" class="lcars-input" style="height:60px; resize:vertical;" placeholder="z.B. Zyklusschwankungen, Pille, Wohlbefinden..."></textarea>
                </div>

                <div style="display:flex; justify-content:flex-end; gap:0.75rem;">
                  <button type="button" class="left-action-btn" onclick="closePartnerModal()" style="padding:0.4rem 0.9rem;">ABBRECHEN</button>
                  <button type="submit" class="left-action-btn" style="padding:0.4rem 1.2rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                    💾 SPEICHERN
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>

        <!-- LCARS USER FORM MODAL (ANLEGEN / BEARBEITEN) -->
        <div id="userFormModal" class="ha-modal-overlay" style="display:none;" onclick="if(event.target===this) closeUserFormModal()">
          <div class="ha-modal-content" onclick="event.stopPropagation()" style="max-width:540px; width:95%; background:#08080d; border:2px solid var(--c-primary); border-radius:8px; overflow:hidden;">
            <div class="ha-modal-header" style="background:var(--c-primary); color:#000; padding:0.6rem 1rem; display:flex; justify-content:space-between; align-items:center;">
              <div style="display:flex; align-items:center; gap:0.6rem;">
                <span style="font-size:1.3rem;">👤</span>
                <div id="userFormModalTitle" style="font-size:1.15rem; font-weight:700; text-transform:uppercase; font-family:var(--font-family);">
                  NEUER LCARS BENUTZER
                </div>
              </div>
              <button type="button" class="ha-modal-close-btn" onclick="closeUserFormModal()" style="border-color:#000; color:#000; font-weight:700;">✕</button>
            </div>
            <div class="ha-modal-body" style="padding:1.25rem;">
              <form id="userForm" onsubmit="submitUserForm(event)">
                <input type="hidden" id="formUserId" value="">

                <div class="config-field" style="margin-bottom:1rem;">
                  <label for="formUserUsername" style="display:block; font-size:0.85rem; font-weight:700; color:var(--c-primary); margin-bottom:0.35rem;">
                    BENUTZERNAME (OFFICER ID)
                  </label>
                  <input type="text" id="formUserUsername" class="lcars-input" placeholder="z.B. user1, gast" required pattern="[a-zA-Z0-9_-]+" title="Nur Buchstaben, Zahlen, Bindestrich und Unterstrich">
                </div>

                <div id="formUserPasswordGroup" class="config-field" style="margin-bottom:1rem;">
                  <label for="formUserPassword" style="display:block; font-size:0.85rem; font-weight:700; color:var(--c-gold); margin-bottom:0.35rem;">
                    PASSWORT (MIN. 6 ZEICHEN)
                  </label>
                  <input type="password" id="formUserPassword" class="lcars-input" placeholder="••••••••••••" minlength="6">
                </div>

                <div class="config-field" style="margin-bottom:1rem;">
                  <label for="formUserDisplayName" style="display:block; font-size:0.85rem; font-weight:700; color:var(--c-secondary); margin-bottom:0.35rem;">
                    ANZEIGENAME / VOLLER NAME (OPTIONAL)
                  </label>
                  <input type="text" id="formUserDisplayName" class="lcars-input" placeholder="z.B. Cdr. Data">
                </div>

                <!-- Berechtigungs-Matrix -->
                <div style="margin-bottom:1.2rem; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:0.85rem;">
                  <div style="font-size:0.85rem; font-weight:700; color:var(--c-gold); margin-bottom:0.5rem; text-transform:uppercase;">
                    🛡️ ZUGRIFFSBERECHTIGUNGEN (*.PIMMEL.SITE)
                  </div>
                  <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:0.5rem;">
                    <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
                      <input type="checkbox" id="userSvc_all" value="*" class="user-svc-cb" onchange="handleUserSvcAllToggle()">
                      <span style="font-family:var(--mono-family); font-size:0.85rem; color:var(--c-primary); font-weight:700;">★ Alle Dienste (*)</span>
                    </label>
                    <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
                      <input type="checkbox" id="userSvc_pulsecast" value="pulsecast" class="user-svc-cb">
                      <span style="font-family:var(--mono-family); font-size:0.82rem;">PulseCast (cast)</span>
                    </label>
                    <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
                      <input type="checkbox" id="userSvc_telemetryvault" value="telemetryvault" class="user-svc-cb">
                      <span style="font-family:var(--mono-family); font-size:0.82rem;">Telemetry (tele)</span>
                    </label>
                    <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
                      <input type="checkbox" id="userSvc_matter" value="matter" class="user-svc-cb">
                      <span style="font-family:var(--mono-family); font-size:0.82rem;">Matter Server (mat)</span>
                    </label>
                    <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
                      <input type="checkbox" id="userSvc_headroom" value="headroom" class="user-svc-cb">
                      <span style="font-family:var(--mono-family); font-size:0.82rem;">Headroom AI (head)</span>
                    </label>
                    <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
                      <input type="checkbox" id="userSvc_cups" value="cups" class="user-svc-cb">
                      <span style="font-family:var(--mono-family); font-size:0.82rem;">CUPS Drucker (port)</span>
                    </label>
                  </div>
                </div>

                <div class="config-field" style="margin-bottom:1rem;">
                  <label for="formUserNotes" style="display:block; font-size:0.85rem; font-weight:700; color:#aaa; margin-bottom:0.35rem;">
                    NOTIZEN / ZWECK
                  </label>
                  <input type="text" id="formUserNotes" class="lcars-input" placeholder="z.B. Familie, Freund, Smart TV">
                </div>

                <div style="margin-bottom:1.25rem;">
                  <label style="display:flex; align-items:center; gap:0.6rem; cursor:pointer;">
                    <input type="checkbox" id="formUserIsActive" checked>
                    <span style="font-family:var(--mono-family); font-size:0.85rem; color:#44dd88;">BENUTZERKONTO AKTIV (ZUGRIFF ERLAUBT)</span>
                  </label>
                </div>

                <div style="display:flex; justify-content:flex-end; gap:0.75rem;">
                  <button type="button" class="left-action-btn" onclick="closeUserFormModal()" style="padding:0.4rem 0.9rem;">ABBRECHEN</button>
                  <button type="submit" class="left-action-btn" style="padding:0.4rem 1.2rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
                    💾 SPEICHERN
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>

        <!-- LCARS USER PASSWORD RESET MODAL -->
        <div id="userPasswordModal" class="ha-modal-overlay" style="display:none;" onclick="if(event.target===this) closeChangePasswordModal()">
          <div class="ha-modal-content" onclick="event.stopPropagation()" style="max-width:420px; width:95%; background:#08080d; border:2px solid var(--c-gold); border-radius:8px; overflow:hidden;">
            <div class="ha-modal-header" style="background:var(--c-gold); color:#000; padding:0.6rem 1rem; display:flex; justify-content:space-between; align-items:center;">
              <div style="display:flex; align-items:center; gap:0.6rem;">
                <span style="font-size:1.3rem;">🔑</span>
                <div style="font-size:1.15rem; font-weight:700; text-transform:uppercase; font-family:var(--font-family);">
                  PASSWORT ÄNDERN
                </div>
              </div>
              <button type="button" class="ha-modal-close-btn" onclick="closeChangePasswordModal()" style="border-color:#000; color:#000; font-weight:700;">✕</button>
            </div>
            <div class="ha-modal-body" style="padding:1.25rem;">
              <form onsubmit="submitChangeUserPassword(event)">
                <input type="hidden" id="pwdModalUserId" value="">
                <div style="margin-bottom:1rem; font-family:var(--mono-family); font-size:0.9rem; color:#ccc;">
                  Passwort für Benutzer <strong style="color:var(--c-gold);" id="pwdModalUsername"></strong> neu setzen:
                </div>
                <div class="config-field" style="margin-bottom:1.25rem;">
                  <label for="pwdModalNewPassword" style="display:block; font-size:0.85rem; font-weight:700; color:var(--c-gold); margin-bottom:0.35rem;">
                    NEUES PASSWORT (MIN. 6 ZEICHEN)
                  </label>
                  <input type="password" id="pwdModalNewPassword" class="lcars-input" placeholder="••••••••••••" minlength="6" required>
                </div>
                <div style="display:flex; justify-content:flex-end; gap:0.75rem;">
                  <button type="button" class="left-action-btn" onclick="closeChangePasswordModal()" style="padding:0.4rem 0.9rem;">ABBRECHEN</button>
                  <button type="submit" class="left-action-btn" style="padding:0.4rem 1.2rem; border-color:var(--c-gold); color:var(--c-gold); font-weight:700;">
                    🔑 PASSWORT AKTUALISIEREN
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>

        <!-- LCARS GLOBAL COMMAND CODE AUTH MODAL -->
        <div id="lcarsAuthModal" class="ha-modal-overlay" style="display:none;" onclick="handleAuthModalBackdropClick(event)">
          <div class="ha-modal-content" onclick="event.stopPropagation()" style="max-width:380px;">
            <div class="ha-modal-header" style="background:var(--c-primary); color:#000;">
              <div style="display:flex; align-items:center; gap:0.6rem;">
                <span style="font-size:1.3rem;">🔐</span>
                <div style="font-size:1.1rem; font-weight:700; text-transform:uppercase; font-family:var(--font-family);">
                  LCARS COMMAND CODE
                </div>
              </div>
              <button class="ha-modal-close-btn" onclick="closeAuthModal()">✕</button>
            </div>
            <div class="ha-modal-body" style="text-align:center; padding:1.25rem 1rem;">
              <div style="font-family:var(--mono-family); font-size:0.82rem; color:var(--c-gold); margin-bottom:1rem;">
                ZUGANG ZU GESPERRTEN BEREICHEN FREISCHALTEN
              </div>
              <div style="display:flex; justify-content:center; gap:0.5rem; margin-bottom:1rem;">
                <input type="password" id="modalPinInput" maxlength="10" placeholder="••••" class="lcars-input" style="width:160px; font-size:1.6rem; text-align:center; letter-spacing:0.3em; font-family:var(--mono-family);" onkeydown="if(event.key==='Enter') verifyModalPin();">
                <button type="button" class="left-action-btn" onclick="verifyModalPin()" style="padding:0.4rem 1rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
                  ENTER
                </button>
              </div>

              <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:0.4rem; max-width:210px; margin:0 auto 1rem auto;">
                <button type="button" class="keypad-btn" onclick="appendModalPin('1')">1</button>
                <button type="button" class="keypad-btn" onclick="appendModalPin('2')">2</button>
                <button type="button" class="keypad-btn" onclick="appendModalPin('3')">3</button>
                <button type="button" class="keypad-btn" onclick="appendModalPin('4')">4</button>
                <button type="button" class="keypad-btn" onclick="appendModalPin('5')">5</button>
                <button type="button" class="keypad-btn" onclick="appendModalPin('6')">6</button>
                <button type="button" class="keypad-btn" onclick="appendModalPin('7')">7</button>
                <button type="button" class="keypad-btn" onclick="appendModalPin('8')">8</button>
                <button type="button" class="keypad-btn" onclick="appendModalPin('9')">9</button>
                <button type="button" class="keypad-btn keypad-special" onclick="clearModalPin()">CLR</button>
                <button type="button" class="keypad-btn" onclick="appendModalPin('0')">0</button>
                <button type="button" class="keypad-btn keypad-special" onclick="verifyModalPin()">OK</button>
              </div>

              <div id="modalPinError" style="display:none; color:var(--c-red); font-family:var(--mono-family); font-size:0.85rem; margin-top:0.5rem;">
                ZUGRIFF VERWEIGERT // CODE UNGÜLTIG
              </div>
            </div>
          </div>
        </div>

        <!-- LCARS ENTITY CONTROL MODAL -->
        <div id="haControlModal" class="ha-modal-overlay" style="display:none;" onclick="handleModalBackdropClick(event)">
          <div class="ha-modal-content" onclick="event.stopPropagation()">
            <div class="ha-modal-header">
              <div style="display:flex; align-items:center; gap:0.6rem; min-width:0;">
                <span id="haModalIcon" style="font-size:1.5rem; flex-shrink:0;">💡</span>
                <div style="min-width:0;">
                  <div id="haModalTitle" style="font-size:1.2rem; font-weight:700; color:#000; text-transform:uppercase; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">Gerätename</div>
                  <div id="haModalEntityId" style="font-size:0.75rem; color:#222; font-family:var(--mono-family); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">light.device</div>
                </div>
              </div>
              <button class="ha-modal-close-btn" onclick="closeHaControlModal()">✕ SCHLIESSEN</button>
            </div>
            <div class="ha-modal-body" id="haModalBody">
              <!-- Dynamically populated per domain -->
            </div>
          </div>
        </div>

      </main>
    </div>
  </div>

  <!-- UNTERER RAHMEN -->
  <div class="wrap" style="margin-top: 4px;">
    <div style="width: var(--lfw); flex-shrink: 0;"></div>
    <div style="flex:1; min-width: 0;">
      <div class="bar-panel-bottom">
        <div class="bar-6"></div>
        <div class="bar-7"></div>
        <div class="bar-8"></div>
        <div class="bar-9"></div>
        <div class="bar-10"></div>
      </div>
    </div>
  </div>

  <footer>
    <div>SYSTEM TERMINAL 47 // HOST: {{ stats.hostname }} // STAND: <span id="footerTimestamp">{{ stats.timestamp }}</span></div>
    <div>TERMINAL ARCHITEKTUR // AGY DASHBOARD</div>
  </footer>
</section>

<!-- ==========================================================================
     JAVASCRIPT: SYSTEM CONTROLLER, CHARTS, THEMES & APIS
     ========================================================================== -->
<script>
  const initialStats = {{ stats_json | safe }};
  var historyChart = null;
  var hermesChart = null;
  var nineRouterTimelineChart = null;
  var nineRouterModelChart = null;
  var currentNineRouterData = initialStats?.nine_router || null;
  var activeRange = '1h';
  var currentHistorySamples = initialStats?.history_samples || [];
  var visibleDatasets = [true, true, true, false]; // 0: Temp, 1: CPU, 2: Throttle, 3: RAM
  var currentCategory = 'system';
  var lastServicesFingerprint = '';
  var lastNrConnFingerprint = '';
  var lastNrHistoryFingerprint = '';
  var latestDiscoveredServers = initialStats?.discovered_servers || [];

  // Sound Engine (Web Audio API Synthesizer)
  let audioContext = null;
  let soundEnabled = (localStorage.getItem('lcars-sound') !== 'false');

  function getAudioCtx() {
    if (!audioContext) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        try {
          audioContext = new AudioCtx({ sampleRate: 16000 });
        } catch (e) {
          try {
            audioContext = new AudioCtx();
          } catch (e2) {
            audioContext = null;
          }
        }
      }
    }
    return audioContext;
  }

  function playLcarsBeep(f1 = 880, f2 = 1760) {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      if (ctx.state === 'suspended') ctx.resume();

      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(f1, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(f2, ctx.currentTime + 0.08);

      gain.gain.setValueAtTime(0.04, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.08);

      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.08);
    } catch (e) {
      // Audio optional
    }
  }

  function playLcarsChirp() {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      if (ctx.state === 'suspended') ctx.resume();
      const now = ctx.currentTime;
      // Tone 1: E5 (659Hz) -> A5 (880Hz)
      const osc1 = ctx.createOscillator();
      const gain1 = ctx.createGain();
      osc1.type = 'sine';
      osc1.frequency.setValueAtTime(659, now);
      osc1.frequency.exponentialRampToValueAtTime(880, now + 0.07);
      gain1.gain.setValueAtTime(0.06, now);
      gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.075);
      osc1.connect(gain1);
      gain1.connect(ctx.destination);
      osc1.start(now);
      osc1.stop(now + 0.08);

      // Tone 2: E6 (1318Hz) -> A6 (1760Hz)
      const osc2 = ctx.createOscillator();
      const gain2 = ctx.createGain();
      osc2.type = 'sine';
      osc2.frequency.setValueAtTime(1318, now + 0.075);
      osc2.frequency.exponentialRampToValueAtTime(1760, now + 0.16);
      gain2.gain.setValueAtTime(0.07, now + 0.075);
      gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.165);
      osc2.connect(gain2);
      gain2.connect(ctx.destination);
      osc2.start(now + 0.075);
      osc2.stop(now + 0.17);
    } catch(e) {}
  }

  function playLcarsAcknowledge() {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      if (ctx.state === 'suspended') ctx.resume();
      const now = ctx.currentTime;
      // Star Trek affirmative chime: Dual-tone B5 (988Hz) + E6 (1318Hz)
      [988, 1318].forEach(freq => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, now);
        gain.gain.setValueAtTime(0.05, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.22);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.23);
      });
    } catch(e) {}
  }

  function playLcarsError() {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      if (ctx.state === 'suspended') ctx.resume();
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(240, now);
      osc.frequency.setValueAtTime(180, now + 0.12);
      gain.gain.setValueAtTime(0.06, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.26);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 0.27);
    } catch(e) {}
  }

  function toggleAudio() {
    soundEnabled = !soundEnabled;
    localStorage.setItem('lcars-sound', soundEnabled);
    updateAudioUI();
    if (soundEnabled) playLcarsBeep(880, 1760);
  }

  function updateAudioUI() {
    const label = soundEnabled ? 'AUDIO AN' : 'AUDIO AUS';
    const icon = soundEnabled ? '🔊' : '🔇';
    const btn = document.getElementById('audioBtn');
    const cfgLabel = document.getElementById('cfgAudioLabel');
    if (btn) btn.innerHTML = `<span>${icon}</span> <span>${label}</span>`;
    if (cfgLabel) cfgLabel.textContent = soundEnabled ? 'SOUND EFFEKTE: AKTIV' : 'SOUND EFFEKTE: STUMM';
  }

  // Stardate Berechnung gemäß offizieller LCARS-Formel (thelcars.com)
  function calculateStardate(d = new Date()) {
    const currentHour = d.getHours();
    let dateForCalc = new Date(d);
    if (currentHour === 0) {
      dateForCalc.setDate(dateForCalc.getDate() + 1);
    }
    const firstNum = dateForCalc.getFullYear() - 1946;
    const startOfYear = new Date(dateForCalc.getFullYear(), 0, 1);
    const diffTime = Math.abs(dateForCalc - startOfYear);
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    const calculatedSecondNumber = Math.floor(diffDays * 2.732);
    const secondNum = String(calculatedSecondNumber).padStart(3, '0');
    let finalNum;
    if (currentHour === 0) {
      finalNum = "0";
    } else if (currentHour >= 1 && currentHour <= 9) {
      finalNum = String(currentHour).padStart(2, '0');
    } else if (currentHour >= 10 && currentHour <= 11) {
      finalNum = String(currentHour);
    } else if (currentHour >= 12 && currentHour <= 21) {
      let hour12 = currentHour % 12;
      if (hour12 === 0) hour12 = 12;
      finalNum = String(hour12);
    } else {
      finalNum = String(currentHour);
    }
    return `${firstNum}${secondNum}.${finalNum}`;
  }

  function updateStardate() {
    const stardateStr = calculateStardate();
    const elBanner = document.getElementById('stardateValue');
    if (elBanner) elBanner.textContent = stardateStr;
    const elTop = document.getElementById('topStardateVal');
    if (elTop) elTop.textContent = stardateStr;
  }
  setInterval(updateStardate, 1000);
  updateStardate();

  // Fullscreen Handler
  function toggleFullscreen() {
    playLcarsBeep(1200, 1600);
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(err => {
        console.warn("Fullscreen abgelehnt:", err);
      });
    } else {
      if (document.exitFullscreen) document.exitFullscreen();
    }
  }

  document.addEventListener('fullscreenchange', () => {
    const isFs = !!document.fullscreenElement;
    const icon = document.getElementById('fsIcon');
    const label = document.getElementById('fsLabel');
    if (icon && label) {
      icon.textContent = isFs ? '🗗' : '⛶';
      label.textContent = isFs ? 'FENSTER' : 'VOLLBILD';
    }
  });

  // 10 KATEGORIEN NAVIGATION (OHNE ZAHLEN)
  const CATEGORY_NAMES = {
    'system': 'SYSTEM & SENSOR VERLAUF',
    'services': 'SERVICES & PROZESS-SCANNER',
    'agents': 'LCARS SUBRAUM COMM-LINK // KI-AGENTEN',
    'ai-info': 'KI-INFO // 9ROUTER & NEURAL TELEMETRIE',
    'config': 'SYSTEM CONFIG & FARBMODI',
    'fantasy': 'ESPN FANTASY FOOTBALL // INCOMPLETE PASS',
    'solar': 'LCARS ENERGIE-MANAGEMENT // BALKONSOLAR',
    'homeassistant': 'LCARS HAUSSTEUERUNG // HOME ASSISTANT',
    'cycle': 'LCARS BIO-TELEMETRIE // PARTNERINNEN-ZYKLUS',
    'pulsecast': 'LCARS PULSECAST // MEDIA & DOWNLOAD HUB',
    'gemini_live': 'LCARS SUBRAUM-KOMMUNIKATION // GEMINI 3.8 LIVE',
    'pimmel': 'REMOTE NODE // PIMMEL ODN-HUB (100.88.215.98)'
  };

  function switchCategory(catId) {
    if (typeof isCategoryLocked === 'function' && isCategoryLocked(catId)) {
      pendingUnlockCategory = catId;
      openAuthModal();
      return;
    }

    if (catId !== 'pulsecast' && typeof stopPulsecastPolling === 'function') {
      stopPulsecastPolling();
    }

    playLcarsBeep(980, 1400);
    currentCategory = catId;

    document.querySelectorAll('.lcars-pill-btn').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById('btn-cat-' + catId);
    if (activeBtn) activeBtn.classList.add('active');

    document.querySelectorAll('.lcars-section').forEach(sec => sec.classList.remove('active-section'));
    const activeSec = document.getElementById('section-' + catId);
    if (activeSec) activeSec.classList.add('active-section');

    const banner = document.getElementById('bannerSectionTitle');
    if (banner && CATEGORY_NAMES[catId]) {
      banner.textContent = CATEGORY_NAMES[catId];
    }

    // Chart Resizing & Re-Render
    if (catId === 'system') {
      setTimeout(() => {
        if (historyChart) {
          historyChart.resize();
          historyChart.update('none');
        } else {
          initHistoryChart();
        }
      }, 60);
    }
    if (catId === 'services') {
      if (latestDiscoveredServers && latestDiscoveredServers.length > 0) {
        updateServicesCards(latestDiscoveredServers);
      }
    }
    if (catId === 'agents') {
      setTimeout(() => {
        if (activeAgentSubgroup === '9router') {
          loadChatModels();
          const inp = document.getElementById('lcarsChatInput');
          if (inp) inp.focus();
        } else if (activeAgentSubgroup === 'hermes') {
          loadHermesProfiles();
          const inp = document.getElementById('hermesChatInput');
          if (inp) inp.focus();
        } else if (activeAgentSubgroup === 'ide') {
          loadIdeConfig();
        }
      }, 60);
    }
    if (catId === 'ai-info') {
      setTimeout(() => {
        initNineRouterCharts();
        initHermesChart();
      }, 60);
    }
    if (catId === 'fantasy') {
      setTimeout(() => {
        fantasyCountdownSeconds = 30;
        updateFantasyCountdownUI();
        loadFantasyData(false);
      }, 60);
    }
    if (catId === 'solar') {
      setTimeout(() => {
        solarCountdownSeconds = 15;
        updateSolarCountdownUI();
        loadSolarData(false);
      }, 60);
    }
    if (catId === 'homeassistant') {
      setTimeout(() => {
        haCountdownSeconds = 15;
        updateHaCountdownUI();
        loadHomeAssistantData(false);
      }, 60);
    }
    if (catId === 'cycle') {
      setTimeout(() => {
        loadCycleData();
      }, 60);
    }
    if (catId === 'pulsecast') {
      setTimeout(() => {
        initPulsecastSection();
      }, 60);
    }
    if (catId === 'gemini_live') {
      setTimeout(() => {
        initGeminiLiveSection();
      }, 60);
    }
    if (catId === 'pimmel') {
      fetchPimmelStats(true);
      setTimeout(() => {
        if (pimmelHistoryChart) pimmelHistoryChart.resize();
        if (pimmelModelChart) pimmelModelChart.resize();
        if (pimmelTimelineChart) pimmelTimelineChart.resize();
        initPimmelCharts();
      }, 60);
    }
  }

  // ESPN FANTASY CONTROLLER
  let isFantasyLoading = false;
  let fantasyCountdownSeconds = 30;

  function updateFantasyCountdownUI() {
    const badge = document.getElementById('fantasyCountdownBadge');
    if (!badge) return;
    if (isFantasyLoading) {
      badge.textContent = '● SYNC...';
      badge.style.color = 'var(--c-gold)';
      badge.style.borderColor = 'var(--c-gold)';
      badge.style.background = 'rgba(237, 179, 120, 0.15)';
    } else {
      badge.textContent = `● REFRESH IN ${fantasyCountdownSeconds}S`;
      badge.style.color = '#44dd88';
      badge.style.borderColor = 'rgba(68, 221, 136, 0.4)';
      badge.style.background = 'rgba(68, 221, 136, 0.15)';
    }
  }

  window.testFantasyFlash = async function(e) {
    if (e) e.preventDefault();
    const btn = document.getElementById('fantasyTestFlashBtn');
    if (!btn) return;
    const origText = btn.textContent;
    btn.textContent = '⚡ FLASHING...';
    btn.style.color = 'var(--c-gold)';
    btn.style.borderColor = 'var(--c-gold)';
    btn.disabled = true;
    try {
      const resp = await fetch('/api/fantasy/test-flash');
      const data = await resp.json();
      if (data.success) {
        btn.textContent = '✓ FLASH OK';
        btn.style.color = '#44dd88';
        btn.style.borderColor = '#44dd88';
      } else {
        btn.textContent = '✗ FEHLER';
        btn.style.color = 'var(--c-red)';
        btn.style.borderColor = 'var(--c-red)';
      }
    } catch (err) {
      btn.textContent = '✗ NETZWERK';
      btn.style.color = 'var(--c-red)';
      btn.style.borderColor = 'var(--c-red)';
    }
    setTimeout(() => {
      btn.textContent = origText;
      btn.style.color = 'var(--c-primary)';
      btn.style.borderColor = 'rgba(235,148,58,0.4)';
      btn.disabled = false;
    }, 2200);
  };

  // ESPN KI-MANAGER STEUERUNG & SETTINGS
  let currentFantasyMode = 'manual';
  let currentFantasyRiskLevel = 3;
  let currentFlashEnabled = true;

  const FANTASY_RISK_LABELS = {
    1: '1: ULTRA-KONSERVATIV (FLOOR)',
    2: '2: KONSERVATIV',
    3: '3: AUSGEWOGEN (STANDARD)',
    4: '4: OFFENSIV (CEILING)',
    5: '5: BOOM-OR-BUST (MAX UPSIDE)'
  };

  function updateFantasyModeButtons(mode) {
    const modes = ['manual', 'semi', 'full'];
    modes.forEach(m => {
      const btn = document.getElementById(`btn-fantasy-mode-${m}`);
      if (!btn) return;
      if (m === mode) {
        btn.classList.add('active');
        if (m === 'manual') {
          btn.style.background = 'var(--c-butterscotch)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 10px rgba(218,165,32,0.4)';
        } else if (m === 'semi') {
          btn.style.background = 'var(--c-gold)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 10px rgba(237,179,120,0.6)';
        } else if (m === 'full') {
          btn.style.background = 'var(--c-primary)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 12px rgba(235,148,58,0.7)';
        }
      } else {
        btn.classList.remove('active');
        btn.style.background = 'transparent';
        btn.style.color = '#888';
        btn.style.boxShadow = 'none';
      }
    });
  }

  function updateFantasyRiskButtons(level) {
    const lvl = parseInt(level, 10) || 3;
    currentFantasyRiskLevel = lvl;
    for (let i = 1; i <= 5; i++) {
      const btn = document.getElementById(`btn-fantasy-risk-${i}`);
      if (!btn) continue;
      if (i === lvl) {
        btn.classList.add('active');
        if (i === 1 || i === 2) {
          btn.style.background = 'var(--c-blue, #6688cc)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 10px rgba(102,136,204,0.6)';
        } else if (i === 3) {
          btn.style.background = 'var(--c-butterscotch, #cc9933)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 10px rgba(218,165,32,0.4)';
        } else if (i === 4) {
          btn.style.background = 'var(--c-primary, #eb943a)';
          btn.style.color = '#000';
          btn.style.boxShadow = '0 0 10px rgba(235,148,58,0.6)';
        } else if (i === 5) {
          btn.style.background = 'var(--c-red, #eb4444)';
          btn.style.color = '#fff';
          btn.style.boxShadow = '0 0 12px rgba(235,68,68,0.7)';
        }
      } else {
        btn.classList.remove('active');
        btn.style.background = 'transparent';
        btn.style.color = '#888';
        btn.style.boxShadow = 'none';
      }
    }
    const statusEl = document.getElementById('fantasyAiRiskStatus');
    if (statusEl) {
      statusEl.textContent = `STRATEGIE: ${FANTASY_RISK_LABELS[lvl] || ('STUFE ' + lvl)}`;
    }
  }

  window.setFantasyRiskLevel = async function(level) {
    const lvl = parseInt(level, 10);
    if (!lvl || lvl < 1 || lvl > 5) return;
    updateFantasyRiskButtons(lvl);
    try {
      const resp = await fetch('/api/espn/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ risk_level: lvl })
      });
      const res = await resp.json();
      if (res.status === 'ok' || res.risk_level) {
        updateFantasyRiskButtons(res.risk_level || lvl);
      }
    } catch (e) {
      console.error('Fehler beim Setzen des Risk-Levels:', e);
    }
  };

  function updateFantasyFlashToggle(enabled) {
    currentFlashEnabled = Boolean(enabled);
    const btn = document.getElementById('fantasyFlashToggleBtn');
    if (!btn) return;
    if (currentFlashEnabled) {
      btn.textContent = '⚡ FLASH: AN';
      btn.style.color = '#44dd88';
      btn.style.background = 'rgba(68,221,136,0.15)';
      btn.style.borderColor = 'rgba(68,221,136,0.5)';
      btn.title = 'Flash-Lichtsignal bei Score aktiviert (Klicken zum Ausschalten)';
    } else {
      btn.textContent = '⚡ FLASH: AUS';
      btn.style.color = '#888';
      btn.style.background = 'rgba(255,255,255,0.05)';
      btn.style.borderColor = 'rgba(255,255,255,0.15)';
      btn.title = 'Flash-Lichtsignal deaktiviert (Klicken zum Einschalten)';
    }
  }

  window.toggleFantasyFlash = async function(event) {
    if (event) event.stopPropagation();
    const nextState = !currentFlashEnabled;
    updateFantasyFlashToggle(nextState);
    try {
      const resp = await fetch('/api/espn/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ flash_enabled: nextState })
      });
      const res = await resp.json();
      if (res.status === 'ok' || res.flash_enabled !== undefined) {
        updateFantasyFlashToggle(res.flash_enabled !== undefined ? res.flash_enabled : nextState);
      }
    } catch (e) {
      console.error('Fehler beim Umschalten des Flash-Signals:', e);
    }
  };

  function updateFantasyAiStats(stats) {
    const modelEl = document.getElementById('fantasyAiModelText');
    if (!modelEl || !stats) return;
    const tokens = (stats.last_token_usage && stats.last_token_usage.total_tokens) ? stats.last_token_usage.total_tokens : 1200;
    const tokenStr = tokens >= 1000 ? (tokens / 1000).toFixed(1) + 'k' : tokens;
    const cost = stats.estimated_cost_usd || 0.0002;
    const costCt = (cost * 100).toFixed(2).replace('.', ',');
    modelEl.textContent = `Gemini 3.8 Flash | ~${tokenStr} Tokens / Run (<${costCt}ct)`;
  }

  window.setFantasyMode = async function(mode) {
    if (!mode) return;
    updateFantasyModeButtons(mode);
    try {
      const resp = await fetch('/api/espn/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode })
      });
      const res = await resp.json();
      if (res.status === 'ok' || res.success) {
        currentFantasyMode = res.mode || mode;
        updateFantasyModeButtons(currentFantasyMode);
        loadFantasyData(false);
      } else {
        console.error('Fehler beim Setzen des Fantasy-Modus:', res.message || res.error);
        loadFantasyData(false);
      }
    } catch (e) {
      console.error('Netzwerkfehler beim Setzen des Fantasy-Modus:', e);
      loadFantasyData(false);
    }
  };

  window.applyFantasyProposal = async function(proposalId) {
    if (!proposalId) return;
    const applyBtn = document.getElementById('fantasyProposalApplyBtn');
    const dismissBtn = document.getElementById('fantasyProposalDismissBtn');
    if (applyBtn) {
      applyBtn.disabled = true;
      applyBtn.textContent = '⚡ AUSFÜHREN...';
      applyBtn.style.opacity = '0.7';
    }
    if (dismissBtn) dismissBtn.disabled = true;

    try {
      const resp = await fetch(`/api/espn/proposals/${encodeURIComponent(proposalId)}/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const res = await resp.json();
      if (res.success) {
        if (applyBtn) {
          applyBtn.textContent = '✓ FREIGEGEBEN';
          applyBtn.style.background = '#44dd88';
          applyBtn.style.color = '#000';
        }
        setTimeout(() => {
          loadFantasyData(true);
        }, 1200);
      } else {
        alert(`Fehler beim Ausführen des Vorschlags: ${res.error || res.message || 'Safeguard-Abweisung'}`);
        if (applyBtn) {
          applyBtn.disabled = false;
          applyBtn.textContent = '⚡ FREIGEBEN';
          applyBtn.style.opacity = '1';
        }
        if (dismissBtn) dismissBtn.disabled = false;
      }
    } catch (e) {
      alert(`Netzwerkfehler: ${e.message}`);
      if (applyBtn) {
        applyBtn.disabled = false;
        applyBtn.textContent = '⚡ FREIGEBEN';
        applyBtn.style.opacity = '1';
      }
      if (dismissBtn) dismissBtn.disabled = false;
    }
  };

  window.dismissFantasyProposal = async function(proposalId) {
    if (!proposalId) return;
    const banner = document.getElementById('fantasyProposalBanner');
    if (banner) banner.style.display = 'none';
    try {
      await fetch(`/api/espn/proposals/${encodeURIComponent(proposalId)}/dismiss`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      loadFantasyData(true);
    } catch (e) {
      console.error('Fehler beim Verwerfen des Vorschlags:', e);
      loadFantasyData(false);
    }
  };

  async function loadFantasyData(force = false) {
    if (isFantasyLoading) return;
    isFantasyLoading = true;
    updateFantasyCountdownUI();

    try {
      const url = force ? '/api/fantasy/refresh' : '/api/fantasy';
      const resp = await fetch(url);
      const data = await resp.json();

      if (data.status === 'error') {
        const badge = document.getElementById('fantasyCountdownBadge');
        if (badge) {
          badge.textContent = '● FEHLER BEIM ABRUF';
          badge.style.color = 'var(--c-red)';
          badge.style.borderColor = 'var(--c-red)';
        }
        isFantasyLoading = false;
        return;
      }

      // Modus-Button anhand von data.mode markieren
      if (data.mode) {
        currentFantasyMode = data.mode;
        updateFantasyModeButtons(data.mode);
      }
      if (data.risk_level !== undefined) {
        updateFantasyRiskButtons(data.risk_level);
      }
      if (data.flash_enabled !== undefined) {
        updateFantasyFlashToggle(data.flash_enabled);
      }
      if (data.ai_stats) {
        updateFantasyAiStats(data.ai_stats);
      }

      // Aktiven Vorschlag rendern
      const banner = document.getElementById('fantasyProposalBanner');
      if (banner) {
        const prop = data.active_proposal;
        if (prop && prop.status === 'pending') {
          banner.style.display = 'block';
          const reasonEl = document.getElementById('fantasyProposalReason');
          const detailsEl = document.getElementById('fantasyProposalDetails');
          const confEl = document.getElementById('fantasyProposalConfidence');
          const applyBtn = document.getElementById('fantasyProposalApplyBtn');
          const dismissBtn = document.getElementById('fantasyProposalDismissBtn');

          if (reasonEl) reasonEl.textContent = prop.reason || 'Strategische Aufstellungsoptimierung empfohlen.';

          const escapeFn = typeof escapeHtml === 'function' ? escapeHtml : (s => String(s || ''));
          let detailsText = '';
          if (prop.player_in_name && prop.player_out_name) {
            detailsText = `🔄 TAUSCH: <strong>${escapeFn(prop.player_in_name)}</strong> (Bank ➔ Start) für <strong>${escapeFn(prop.player_out_name)}</strong> (Start ➔ Bank)`;
          } else if (prop.moves && prop.moves.length > 0) {
            detailsText = `🔄 TAUSCH: ${prop.moves.length} Spielerwechsel`;
          }
          if (prop.projected_gain != null && prop.projected_gain !== 0) {
            const gainSign = prop.projected_gain > 0 ? '+' : '';
            detailsText += ` | Erwarteter Zuwachs: ${gainSign}${Number(prop.projected_gain).toFixed(1)} PTS`;
          }
          if (detailsEl) detailsEl.innerHTML = detailsText;

          if (confEl) {
            const confPct = Math.round((prop.confidence || 0.9) * 100);
            confEl.textContent = `KONFIDENZ: ${confPct}%`;
          }

          if (applyBtn) {
            applyBtn.disabled = false;
            applyBtn.textContent = '⚡ FREIGEBEN';
            applyBtn.style.opacity = '1';
            applyBtn.style.background = '#44dd88';
            applyBtn.style.color = '#000';
            applyBtn.onclick = () => applyFantasyProposal(prop.id);
          }
          if (dismissBtn) {
            dismissBtn.disabled = false;
            dismissBtn.textContent = '✕ ABLEHNEN';
            dismissBtn.onclick = () => dismissFantasyProposal(prop.id);
          }
        } else {
          banner.style.display = 'none';
        }
      }

      // League & Team info
      if (document.getElementById('fantasySectionTitle') && data.league_name) {
        document.getElementById('fantasySectionTitle').textContent = `LCARS SUBRAUM RELAY // ${data.league_name.toUpperCase()} LIGA`;
      }
      if (document.getElementById('fantasyCardTeamName')) {
        document.getElementById('fantasyCardTeamName').textContent = data.team_name || 'NORDERSTEDT RAILSGUNS';
      }
      if (document.getElementById('fantasyCardLeagueName')) {
        document.getElementById('fantasyCardLeagueName').textContent = data.league_name || 'Incomplete Pass';
      }
      if (document.getElementById('fantasyCardRank')) {
        document.getElementById('fantasyCardRank').textContent = `RANG #${data.my_rank || '--'}`;
      }

      // My team record & points in standings
      const myTeamStanding = (data.standings || []).find(s => s.is_my_team);
      if (myTeamStanding) {
        if (document.getElementById('fantasyCardRecord')) {
          document.getElementById('fantasyCardRecord').textContent = `(${myTeamStanding.wins}-${myTeamStanding.losses}-${myTeamStanding.ties})`;
        }
        if (document.getElementById('fantasyCardTotalPoints')) {
          document.getElementById('fantasyCardTotalPoints').textContent = `${Number(myTeamStanding.points_for).toFixed(1)} PTS`;
        }
      }

      if (document.getElementById('fantasyCardWeek')) {
        document.getElementById('fantasyCardWeek').textContent = `WEEK ${data.current_week || 1}`;
      }

      // Matchup
      if (data.matchup) {
        const m = data.matchup;
        if (document.getElementById('fantasyMatchupWeekBadge')) {
          document.getElementById('fantasyMatchupWeekBadge').textContent = `WEEK ${m.week}`;
        }
        if (document.getElementById('fantasyMatchupMyName')) {
          document.getElementById('fantasyMatchupMyName').textContent = m.my_team.name;
        }
        if (document.getElementById('fantasyMatchupMyScore')) {
          document.getElementById('fantasyMatchupMyScore').textContent = Number(m.my_team.score).toFixed(1);
        }
        if (document.getElementById('fantasyMatchupMyProj')) {
          document.getElementById('fantasyMatchupMyProj').textContent = m.my_team.projected != null ? Number(m.my_team.projected).toFixed(1) : '--';
        }
        if (document.getElementById('fantasyMatchupMyWinProb')) {
          document.getElementById('fantasyMatchupMyWinProb').textContent = m.my_team.win_prob;
        }

        if (document.getElementById('fantasyMatchupOppName')) {
          document.getElementById('fantasyMatchupOppName').textContent = m.opponent.name;
        }
        if (document.getElementById('fantasyMatchupOppScore')) {
          document.getElementById('fantasyMatchupOppScore').textContent = Number(m.opponent.score).toFixed(1);
        }
        if (document.getElementById('fantasyMatchupOppProj')) {
          document.getElementById('fantasyMatchupOppProj').textContent = m.opponent.projected != null ? Number(m.opponent.projected).toFixed(1) : '--';
        }
        if (document.getElementById('fantasyMatchupOppWinProb')) {
          document.getElementById('fantasyMatchupOppWinProb').textContent = m.opponent.win_prob;
        }

        // Win prob bar
        if (document.getElementById('fantasyProbLabelMy')) {
          document.getElementById('fantasyProbLabelMy').textContent = `${m.my_team.name}: ${m.my_team.win_prob}%`;
        }
        if (document.getElementById('fantasyProbLabelOpp')) {
          document.getElementById('fantasyProbLabelOpp').textContent = `${m.opponent.name}: ${m.opponent.win_prob}%`;
        }
        if (document.getElementById('fantasyProbBarMy')) {
          document.getElementById('fantasyProbBarMy').style.width = `${Math.max(5, Math.min(95, m.my_team.win_prob))}%`;
        }
        if (document.getElementById('fantasyProbBarOpp')) {
          document.getElementById('fantasyProbBarOpp').style.width = `${Math.max(5, Math.min(95, m.opponent.win_prob))}%`;
        }
      }

      // Roster Table
      const rosterBody = document.getElementById('fantasyRosterBody');
      if (rosterBody && data.roster) {
        let html = '';
        let benchStarted = false;
        data.roster.forEach(p => {
          if (!p.is_starter && !benchStarted) {
            benchStarted = true;
            html += `<tr style="border-top:2px solid var(--c-primary); background:rgba(255,255,255,0.03);">
              <td colspan="6" style="padding:0.4rem; font-size:0.75rem; color:var(--c-gold); font-weight:700; letter-spacing:0.08em;">-- BENCH & RESERVES --</td>
            </tr>`;
          }

          let injBadge = '<span style="color:#44dd88; font-size:0.75rem;">AKTIV</span>';
          if (p.injury === 'QUESTIONABLE') {
            injBadge = '<span style="color:var(--c-gold); font-weight:700; font-size:0.75rem;">QUESTIONABLE</span>';
          } else if (p.injury === 'DOUBTFUL') {
            injBadge = '<span style="color:var(--c-butterscotch); font-weight:700; font-size:0.75rem;">DOUBTFUL</span>';
          } else if (p.injury === 'OUT') {
            injBadge = '<span style="color:var(--c-red); font-weight:700; font-size:0.75rem;">OUT</span>';
          } else if (p.injury === 'INJURY_RESERVE') {
            injBadge = '<span style="color:var(--c-secondary); font-weight:700; font-size:0.75rem;">IR</span>';
          }

          const slotColor = p.is_starter ? 'var(--c-primary)' : '#888';
          const pointsColor = (p.actual > 0) ? 'var(--c-gold)' : '#aaa';
          const isRecentScore = !!p.is_recently_scored;
          const rowClass = isRecentScore ? 'class="player-scored-highlight"' : '';
          const scoreBadge = isRecentScore 
            ? `<span style="display:inline-block; margin-left:6px; background:#44dd88; color:#000; font-weight:800; font-size:0.7rem; padding:1px 5px; border-radius:4px; vertical-align:middle;" title="Punkte vor ca. ${p.gain_minutes_ago || 1} Min. erhalten">▲ +${p.score_gain || ''}</span>` 
            : '';

          html += `<tr ${rowClass} style="border-bottom:1px solid rgba(255,255,255,0.06); font-family:var(--mono-family);">
            <td style="padding:0.4rem 0.3rem; font-weight:700; color:${slotColor};">${p.slot}</td>
            <td style="padding:0.4rem 0.4rem; font-family:var(--font-family); font-weight:600; color:#fff;">${p.name}${scoreBadge}</td>
            <td style="padding:0.4rem 0.3rem; color:var(--c-blue);">${p.pro_team}</td>
            <td style="padding:0.4rem 0.3rem;">${injBadge}</td>
            <td style="padding:0.4rem 0.4rem; text-align:right; color:#888;">${p.projected != null ? Number(p.projected).toFixed(1) : '--'}</td>
            <td style="padding:0.4rem 0.4rem; text-align:right; font-weight:700; color:${isRecentScore ? '#44dd88' : pointsColor}; font-size:0.95rem;">${Number(p.actual).toFixed(1)}</td>
          </tr>`;
        });
        rosterBody.innerHTML = html;
      }

      // Standings Table
      const standingsBody = document.getElementById('fantasyStandingsBody');
      if (standingsBody && data.standings) {
        let html = '';
        data.standings.forEach((s, idx) => {
          const isMe = s.is_my_team;
          const rowBg = isMe ? 'background:rgba(235,148,58,0.18); border-left:3px solid var(--c-primary);' : 'border-bottom:1px solid rgba(255,255,255,0.06);';
          const nameColor = isMe ? 'var(--c-gold)' : '#fff';
          const fontW = isMe ? 'font-weight:700;' : '';

          html += `<tr style="${rowBg} font-family:var(--mono-family);">
            <td style="padding:0.4rem 0.3rem; color:var(--c-primary); font-weight:700;">#${idx + 1}</td>
            <td style="padding:0.4rem 0.4rem; font-family:var(--font-family); ${fontW} color:${nameColor};">
              ${s.name} ${isMe ? '<span style="font-size:0.75rem; color:var(--c-primary); margin-left:4px;">★ MEIN TEAM</span>' : ''}
            </td>
            <td style="padding:0.4rem 0.4rem; text-align:center; color:#ccc;">${s.wins}-${s.losses}-${s.ties}</td>
            <td style="padding:0.4rem 0.4rem; text-align:right; font-weight:700; color:var(--c-blue);">${Number(s.points_for).toFixed(1)}</td>
            <td style="padding:0.4rem 0.4rem; text-align:right; color:#888;">${Number(s.points_against).toFixed(1)}</td>
          </tr>`;
        });
        standingsBody.innerHTML = html;
      }

    } catch (e) {
      console.error('ESPN Fantasy Ladefehler:', e);
      const badge = document.getElementById('fantasyCountdownBadge');
      if (badge) {
        badge.textContent = '● FEHLER BEIM ABRUF';
        badge.style.color = 'var(--c-red)';
        badge.style.borderColor = 'var(--c-red)';
      }
    } finally {
      isFantasyLoading = false;
      fantasyCountdownSeconds = 30;
      updateFantasyCountdownUI();
    }
  }

  // Auto-Refresh für Fantasy (sekündlicher Countdown, alle 30s Refresh)
  let fantasyAutoRefreshTimer = null;
  function startFantasyAutoRefresh() {
    if (fantasyAutoRefreshTimer) clearInterval(fantasyAutoRefreshTimer);
    fantasyCountdownSeconds = 30;
    updateFantasyCountdownUI();

    fantasyAutoRefreshTimer = setInterval(() => {
      const sec = document.getElementById('section-fantasy');
      const isVisible = sec && sec.classList.contains('active-section') && !document.hidden;

      if (!isVisible) {
        fantasyCountdownSeconds = 30;
        return;
      }

      fantasyCountdownSeconds--;
      if (fantasyCountdownSeconds <= 0) {
        fantasyCountdownSeconds = 30;
        updateFantasyCountdownUI();
        loadFantasyData(false);
      } else {
        updateFantasyCountdownUI();
      }
    }, 1000);
  }

  // ===========================================================================
  // HOME ASSISTANT CONTROLLER
  // ===========================================================================
  let haCachedData = null;
  let haActiveRoomFilter = 'all';
  let haCountdownSeconds = 15;
  let isHaLoading = false;
  let haAutoRefreshTimer = null;

  function updateHaCountdownUI() {
    const badge = document.getElementById('haCountdownBadge');
    if (!badge) return;
    if (isHaLoading) {
      badge.textContent = '● LÄDT DATEN...';
      badge.style.color = 'var(--c-primary)';
    } else {
      badge.textContent = `AUTO-REFRESH: ${haCountdownSeconds}S`;
      badge.style.color = '#888';
    }
  }

  async function checkHomeAssistantConfig() {
    try {
      const resp = await fetch('/api/homeassistant/config');
      if (!resp.ok) return;
      const data = await resp.json();

      const btn = document.getElementById('btn-cat-homeassistant');
      const cfgName = (data.name || 'ASSISTANT').toUpperCase();

      if (data.configured && data.enabled) {
        if (btn) {
          btn.style.display = 'flex';
          btn.textContent = cfgName;
        }
        CATEGORY_NAMES['homeassistant'] = 'LCARS HAUSSTEUERUNG // ' + cfgName;
        const secTitle = document.getElementById('haSectionTitle');
        if (secTitle) secTitle.textContent = 'LCARS HAUSSTEUERUNG // ' + cfgName;
      } else {
        if (btn) btn.style.display = 'none';
      }

      // Populate form in Config section
      const urlInp = document.getElementById('cfgHaUrl');
      const nameInp = document.getElementById('cfgHaName');
      const userInp = document.getElementById('cfgHaUser');
      const passInp = document.getElementById('cfgHaPass');
      const tokInp = document.getElementById('cfgHaToken');
      const enInp = document.getElementById('cfgHaEnabled');
      const badge = document.getElementById('cfgHaBadge');
      const tokStatus = document.getElementById('cfgHaTokenStatus');
      const statusMsg = document.getElementById('cfgHaStatusMsg');

      if (urlInp && data.url) urlInp.value = data.url;
      if (nameInp && data.name) nameInp.value = data.name;
      if (userInp && data.username) userInp.value = data.username;
      if (passInp && data.has_creds) passInp.value = '********';
      if (tokInp && data.token_masked) tokInp.placeholder = data.token_masked;
      if (enInp) enInp.checked = data.enabled !== false;

      if (badge && statusMsg) {
        if (data.configured) {
          badge.className = 'badge-status badge-online';
          badge.textContent = 'ONLINE';
          statusMsg.textContent = 'Verbindung konfiguriert';
        } else {
          badge.className = 'badge-status badge-warn';
          badge.textContent = 'UNVOLLSTÄNDIG';
          statusMsg.textContent = 'Zugangsdaten fehlen oder deaktiviert';
        }
      }

      if (tokStatus) {
        if (data.has_token) {
          tokStatus.textContent = '● TOKEN AKTIV (' + (data.token_masked || 'GÜLTIG') + ')';
          tokStatus.style.color = '#44dd88';
        } else if (data.has_creds) {
          tokStatus.textContent = '● BENUTZER: ' + data.username;
          tokStatus.style.color = 'var(--c-primary)';
        } else {
          tokStatus.textContent = 'KEIN TOKEN';
          tokStatus.style.color = '#888';
        }
      }
    } catch(e) {
      console.warn('Home Assistant Config Check Error:', e);
    }
  }

  async function saveHomeAssistantConfig(e) {
    if (e) e.preventDefault();
    playLcarsBeep(980, 1400);

    const url = document.getElementById('cfgHaUrl')?.value?.trim();
    const name = document.getElementById('cfgHaName')?.value?.trim() || 'Assistant';
    const username = document.getElementById('cfgHaUser')?.value?.trim() || '';
    const password = document.getElementById('cfgHaPass')?.value?.trim() || '';
    const token = document.getElementById('cfgHaToken')?.value?.trim() || '';
    const enabled = document.getElementById('cfgHaEnabled')?.checked ?? true;

    const payload = { url, name, username, password, token, enabled };
    const msgEl = document.getElementById('cfgHaSaveMsg');

    try {
      const resp = await fetch('/api/config/homeassistant', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const res = await resp.json();
      if (res.success) {
        if (msgEl) {
          msgEl.style.display = 'block';
          msgEl.style.color = 'var(--c-primary)';
          msgEl.textContent = '✓ HOME ASSISTANT KONFIGURATION ERFOLGREICH GESPEICHERT';
          setTimeout(() => { msgEl.style.display = 'none'; }, 4000);
        }
        playLcarsBeep(1400, 2100);
        await checkHomeAssistantConfig();
        loadHomeAssistantData(true);
      } else {
        throw new Error(res.error || 'Fehler beim Speichern');
      }
    } catch(err) {
      if (msgEl) {
        msgEl.style.display = 'block';
        msgEl.style.color = 'var(--c-red)';
        msgEl.textContent = '✗ FEHLER: ' + err.message;
      }
      playLcarsBeep(440, 220);
    }
  }

  async function testHomeAssistantConnection() {
    playLcarsBeep(980, 1400);
    const btn = document.getElementById('btnTestHa');
    if (btn) btn.disabled = true;

    const badge = document.getElementById('cfgHaBadge');
    const msg = document.getElementById('cfgHaStatusMsg');
    if (badge) {
      badge.className = 'badge-status badge-warn';
      badge.textContent = 'TESTET...';
    }

    const url = document.getElementById('cfgHaUrl')?.value?.trim();
    const username = document.getElementById('cfgHaUser')?.value?.trim();
    const password = document.getElementById('cfgHaPass')?.value?.trim();
    const token = document.getElementById('cfgHaToken')?.value?.trim();

    try {
      const resp = await fetch('/api/homeassistant/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, username, password, token })
      });
      const data = await resp.json();
      if (data.success) {
        if (badge) {
          badge.className = 'badge-status badge-online';
          badge.textContent = 'ONLINE';
        }
        if (msg) msg.textContent = '✓ ' + (data.message || 'Verbindung erfolgreich');
        playLcarsBeep(1400, 2100);
      } else {
        if (badge) {
          badge.className = 'badge-status badge-offline';
          badge.textContent = 'FEHLER';
        }
        if (msg) msg.textContent = '✗ ' + (data.error || 'Fehlgeschlagen');
        playLcarsBeep(440, 220);
      }
    } catch(err) {
      if (badge) {
        badge.className = 'badge-status badge-offline';
        badge.textContent = 'OFFLINE';
      }
      if (msg) msg.textContent = '✗ ' + err.message;
      playLcarsBeep(440, 220);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function loadHomeAssistantData(force = false) {
    if (isHaLoading && !force) return;
    isHaLoading = true;
    updateHaCountdownUI();

    try {
      const resp = await fetch('/api/homeassistant/data');
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();

      if (data.success) {
        haCachedData = data;
        renderHaRooms();
        const liveBadge = document.getElementById('haLiveBadge');
        const statsBadge = document.getElementById('haStatsBadge');
        if (liveBadge) {
          liveBadge.className = 'badge-status badge-online';
          liveBadge.textContent = '● ONLINE';
        }
        if (statsBadge && data.stats) {
          statsBadge.textContent = `${data.stats.areas_count} RÄUME // ${data.stats.total_entities} ENTITIES (${data.stats.active_count} AKTIV)`;
        }
      } else {
        const container = document.getElementById('haRoomsContainer');
        if (container) {
          container.innerHTML = `
            <div class="ha-room-card" style="border-left-color:var(--c-red); text-align:center; padding:2.5rem 1.5rem;">
              <div style="font-size:2.2rem; margin-bottom:0.6rem;">⚠️</div>
              <div style="font-size:1.3rem; font-weight:700; color:var(--c-red); margin-bottom:0.5rem;">HOME ASSISTANT NICHT ERREICHBAR</div>
              <div style="color:#bbb; font-family:var(--mono-family); max-width:500px; margin:0 auto 1.2rem auto;">${escapeHtml(data.error || 'Bitte Zugangsdaten in der Config prüfen.')}</div>
              <button class="left-action-btn" onclick="switchCategory('config')" style="display:inline-flex; border-color:var(--c-gold); color:var(--c-gold);">
                ⚙️ ZUR CONFIG GEHEN
              </button>
            </div>
          `;
        }
        const liveBadge = document.getElementById('haLiveBadge');
        if (liveBadge) {
          liveBadge.className = 'badge-status badge-offline';
          liveBadge.textContent = 'OFFLINE';
        }
      }
    } catch(err) {
      console.warn('loadHomeAssistantData error:', err);
      const liveBadge = document.getElementById('haLiveBadge');
      if (liveBadge) {
        liveBadge.className = 'badge-status badge-offline';
        liveBadge.textContent = 'OFFLINE';
      }
    } finally {
      isHaLoading = false;
      haCountdownSeconds = 15;
      updateHaCountdownUI();
    }
  }

  function setHaRoomFilter(roomId) {
    haActiveRoomFilter = roomId;
    playLcarsBeep(980, 1400);

    document.querySelectorAll('#haRoomFilterBar button').forEach(b => {
      b.classList.remove('active-room-filter');
      b.style.borderColor = 'rgba(255,255,255,0.2)';
      b.style.color = '#bbb';
    });
    const curBtn = document.getElementById('ha-room-btn-' + roomId);
    if (curBtn) {
      curBtn.classList.add('active-room-filter');
      curBtn.style.borderColor = 'var(--c-primary)';
      curBtn.style.color = 'var(--c-primary)';
    }
    filterHaEntities();
  }

  function filterHaEntities() {
    const domain = document.getElementById('haDomainFilter')?.value || 'all';
    const search = (document.getElementById('haSearchInput')?.value || '').toLowerCase().trim();

    document.querySelectorAll('.ha-room-card').forEach(roomCard => {
      const roomId = roomCard.getAttribute('data-room-id');
      const roomMatch = (haActiveRoomFilter === 'all' || haActiveRoomFilter === roomId);

      let visibleInRoom = 0;
      roomCard.querySelectorAll('.ha-entity-tile').forEach(tile => {
        const tDomain = tile.getAttribute('data-domain');
        const tName = (tile.getAttribute('data-name') || '').toLowerCase();
        const tId = (tile.getAttribute('data-entity-id') || '').toLowerCase();
        const tControllable = tile.getAttribute('data-controllable') === 'true';

        let domainMatch = true;
        if (domain === 'controllable') {
          domainMatch = tControllable;
        } else if (domain !== 'all') {
          domainMatch = (tDomain === domain);
        }

        const searchMatch = !search || tName.includes(search) || tId.includes(search);

        if (domainMatch && searchMatch) {
          tile.style.display = 'flex';
          visibleInRoom++;
        } else {
          tile.style.display = 'none';
        }
      });

      if (roomMatch && visibleInRoom > 0) {
        roomCard.style.display = 'block';
      } else {
        roomCard.style.display = 'none';
      }
    });
  }

  function renderHaRooms() {
    if (!haCachedData) return;
    const areas = haCachedData.areas || [];
    const unassigned = haCachedData.unassigned || [];
    const container = document.getElementById('haRoomsContainer');
    const filterBar = document.getElementById('haRoomFilterBar');

    if (!container || !filterBar) return;

    // 1. Build Filter Tabs
    let filterHtml = `
      <button class="left-action-btn ${haActiveRoomFilter === 'all' ? 'active-room-filter' : ''}" onclick="setHaRoomFilter('all')" id="ha-room-btn-all" style="padding:0.3rem 0.75rem; font-size:0.82rem; ${haActiveRoomFilter === 'all' ? 'border-color:var(--c-primary); color:var(--c-primary);' : 'border-color:rgba(255,255,255,0.2); color:#bbb;'}">
        ALLE RÄUME (${areas.length})
      </button>
    `;

    areas.forEach(a => {
      const isActive = (haActiveRoomFilter === a.id);
      filterHtml += `
        <button class="left-action-btn ${isActive ? 'active-room-filter' : ''}" onclick="setHaRoomFilter('${escapeHtml(a.id)}')" id="ha-room-btn-${escapeHtml(a.id)}" style="padding:0.3rem 0.75rem; font-size:0.82rem; ${isActive ? 'border-color:var(--c-primary); color:var(--c-primary);' : 'border-color:rgba(255,255,255,0.2); color:#bbb;'}">
          ${escapeHtml(a.icon || '🚪')} ${escapeHtml(a.name.toUpperCase())} (${a.entities.length})
        </button>
      `;
    });

    if (unassigned.length > 0) {
      const isUn = (haActiveRoomFilter === 'unassigned');
      filterHtml += `
        <button class="left-action-btn ${isUn ? 'active-room-filter' : ''}" onclick="setHaRoomFilter('unassigned')" id="ha-room-btn-unassigned" style="padding:0.3rem 0.75rem; font-size:0.82rem; ${isUn ? 'border-color:var(--c-primary); color:var(--c-primary);' : 'border-color:rgba(255,255,255,0.2); color:#bbb;'}">
          🌐 WEITERE (${unassigned.length})
        </button>
      `;
    }

    filterBar.innerHTML = filterHtml;

    // 2. Build Room Cards & Entity Grids
    let roomsHtml = '';

    areas.forEach(a => {
      const lightEntities = a.entities.filter(e => e.domain === 'light');
      const hasLights = lightEntities.length > 0;
      const anyLightOn = lightEntities.some(e => e.state === 'on');

      roomsHtml += `
        <div class="ha-room-card" data-room-id="${escapeHtml(a.id)}">
          <div class="ha-room-header">
            <div class="ha-room-title">
              <span>${escapeHtml(a.icon || '🚪')}</span>
              <span>${escapeHtml(a.name)}</span>
              <span style="font-size:0.8rem; color:var(--c-gold); font-family:var(--mono-family); font-weight:normal; margin-left:0.5rem;">
                (${a.active_count > 0 ? a.active_count + ' AKTIV / ' : ''}${a.entities.length} OBJEKTE)
              </span>
            </div>
            <div style="display:flex; align-items:center; gap:0.5rem;">
              ${hasLights ? `
                <button class="left-action-btn" onclick="toggleRoomLights('${escapeHtml(a.id)}', ${!anyLightOn})" style="padding:0.25rem 0.65rem; font-size:0.75rem; border-color:${anyLightOn ? '#44dd88' : 'rgba(255,255,255,0.2)'}; color:${anyLightOn ? '#44dd88' : '#bbb'};">
                  💡 ${anyLightOn ? 'LICHTER AUS' : 'LICHTER AN'}
                </button>
              ` : ''}
            </div>
          </div>
          <div class="ha-entities-grid">
            ${a.entities.map(e => renderHaEntityTile(e)).join('')}
          </div>
        </div>
      `;
    });

    if (unassigned.length > 0) {
      roomsHtml += `
        <div class="ha-room-card" data-room-id="unassigned" style="border-left-color:var(--c-gold);">
          <div class="ha-room-header">
            <div class="ha-room-title">
              <span>🌐</span>
              <span>WEITERE GERÄTE &amp; SENSOREN</span>
              <span style="font-size:0.8rem; color:var(--c-gold); font-family:var(--mono-family); font-weight:normal; margin-left:0.5rem;">
                (${unassigned.length} OBJEKTE)
              </span>
            </div>
          </div>
          <div class="ha-entities-grid">
            ${unassigned.map(e => renderHaEntityTile(e)).join('')}
          </div>
        </div>
      `;
    }

    container.innerHTML = roomsHtml;
    filterHaEntities();
  }

  function renderHaEntityTile(e) {
    const isAuto = (e.domain === 'automation');
    const isOn = ['on', 'open', 'playing', 'cleaning'].includes(e.state);
    const isActiveState = (isOn || (e.domain === 'vacuum' && e.state !== 'docked') || (e.domain === 'climate' && e.state !== 'off'));
    
    let displayState = e.state;
    if (isAuto) {
      displayState = (e.state === 'on') ? 'AKTIV' : 'INAKTIV';
    } else if (e.state === 'on') displayState = 'AN';
    else if (e.state === 'off') displayState = 'AUS';
    else if (e.state === 'docked') displayState = 'DOCK';
    else if (e.state === 'cleaning') displayState = 'REINIGT';
    else if (e.state === 'open') displayState = 'OFFEN';
    else if (e.state === 'closed') displayState = 'ZU';
    else if (e.controls?.unit) displayState = `${e.state} ${e.controls.unit}`;

    const badgeClass = isOn ? 'ha-tile-state-badge state-on' : (isActiveState ? 'ha-tile-state-badge state-active' : 'ha-tile-state-badge');

    return `
      <div class="ha-entity-tile ${isActiveState ? 'entity-active' : ''}" 
           data-entity-id="${escapeHtml(e.entity_id)}"
           data-domain="${escapeHtml(e.domain)}"
           data-name="${escapeHtml(e.friendly_name)}"
           data-controllable="${e.controllable}"
           onclick="openHaControlModal('${escapeHtml(e.entity_id)}')">
        <div class="ha-tile-top">
          <div style="min-width:0;">
            <div class="ha-tile-name" title="${escapeHtml(e.friendly_name)}">${escapeHtml(e.friendly_name)}</div>
            <div class="ha-tile-id" title="${escapeHtml(e.entity_id)}">${escapeHtml(e.entity_id)}</div>
          </div>
          <span style="font-size:1.3rem; flex-shrink:0;">${escapeHtml(e.icon)}</span>
        </div>
        <div class="ha-tile-bottom">
          <span class="${badgeClass}">${escapeHtml(displayState)}</span>
          ${isAuto ? `
            <div style="display:flex; gap:0.35rem; align-items:center;" onclick="event.stopPropagation()">
              <button class="ha-tile-toggle-btn" style="border-color:var(--c-primary); color:var(--c-primary); padding:0.2rem 0.5rem; font-size:0.75rem;" 
                      onclick="triggerHaAutomation(event, '${escapeHtml(e.entity_id)}')" title="Automation jetzt ausführen">
                ▶ START
              </button>
              <button class="ha-tile-toggle-btn ${isOn ? 'btn-active' : ''}" 
                      onclick="toggleHaEntity(event, '${escapeHtml(e.entity_id)}', '${escapeHtml(e.domain)}')" title="${isOn ? 'Deaktivieren' : 'Aktivieren'}">
                ${isOn ? 'AUS' : 'AN'}
              </button>
            </div>
          ` : (e.controls?.can_toggle ? `
            <button class="ha-tile-toggle-btn ${isOn ? 'btn-active' : ''}" 
                    onclick="toggleHaEntity(event, '${escapeHtml(e.entity_id)}', '${escapeHtml(e.domain)}')">
              ${isOn ? 'AUS' : 'AN'}
            </button>
          ` : (e.controllable ? `
            <span style="font-size:0.75rem; color:var(--c-primary); font-family:var(--mono-family); font-weight:700;">
              STEUERN ▶
            </span>
          ` : ''))}
        </div>
      </div>
    `;
  }

  async function triggerHaAutomation(event, entityId) {
    if (event) event.stopPropagation();
    playLcarsBeep(1400, 1800);
    await callHaService('automation', 'trigger', { entity_id: entityId });
  }

  async function toggleHaEntity(event, entityId, domain) {
    if (event) event.stopPropagation();
    playLcarsBeep(1200, 1600);

    const srv = (domain === 'light' || domain === 'switch' || domain === 'input_boolean' || domain === 'automation') ? 'toggle' : 'toggle';
    await callHaService(domain, srv, { entity_id: entityId });
  }

  async function toggleRoomLights(roomId, turnOn) {
    playLcarsBeep(1200, 1600);
    if (!haCachedData) return;
    const area = haCachedData.areas.find(a => a.id === roomId);
    if (!area) return;

    const lights = area.entities.filter(e => e.domain === 'light');
    const srv = turnOn ? 'turn_on' : 'turn_off';

    for (const l of lights) {
      callHaService('light', srv, { entity_id: l.entity_id });
    }
  }

  function openHaControlModal(entityId) {
    if (!haCachedData) return;
    playLcarsBeep(980, 1400);

    let found = null;
    for (const a of (haCachedData.areas || [])) {
      found = a.entities.find(e => e.entity_id === entityId);
      if (found) break;
    }
    if (!found && haCachedData.unassigned) {
      found = haCachedData.unassigned.find(e => e.entity_id === entityId);
    }
    if (!found) return;

    const modal = document.getElementById('haControlModal');
    const iconEl = document.getElementById('haModalIcon');
    const titleEl = document.getElementById('haModalTitle');
    const eidEl = document.getElementById('haModalEntityId');
    const bodyEl = document.getElementById('haModalBody');

    if (!modal || !bodyEl) return;

    iconEl.textContent = found.icon || '💡';
    titleEl.textContent = found.friendly_name;
    eidEl.textContent = found.entity_id;

    const ctrl = found.controls || {};
    const isOn = ['on', 'open', 'playing', 'cleaning'].includes(found.state);
    let controlsHtml = '';

    // Status Banner
    controlsHtml += `
      <div style="background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:0.85rem 1rem; margin-bottom:1.25rem; display:flex; justify-content:space-between; align-items:center;">
        <div>
          <div style="font-size:0.75rem; color:#888; text-transform:uppercase;">Aktueller Status</div>
          <div style="font-size:1.3rem; font-weight:700; color:${isOn ? '#44dd88' : 'var(--c-primary)'}; font-family:var(--mono-family); margin-top:0.2rem;">
            ${escapeHtml(found.state.toUpperCase())} ${ctrl.unit ? escapeHtml(ctrl.unit) : ''}
          </div>
        </div>
        ${ctrl.can_toggle ? `
          <div style="display:flex; gap:0.5rem;">
            <button class="left-action-btn" onclick="callHaService('${found.domain}', 'turn_off', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.4rem 0.9rem; font-size:0.85rem; border-color:${!isOn ? 'var(--c-red)' : '#666'}; color:${!isOn ? '#fff' : '#888'}; background:${!isOn ? 'rgba(207,79,79,0.3)' : 'transparent'};">
              AUS
            </button>
            <button class="left-action-btn" onclick="callHaService('${found.domain}', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.4rem 0.9rem; font-size:0.85rem; border-color:${isOn ? '#44dd88' : '#666'}; color:${isOn ? '#000' : '#888'}; background:${isOn ? '#44dd88' : 'transparent'}; font-weight:700;">
              AN
            </button>
          </div>
        ` : ''}
      </div>
    `;

    // 1. LIGHT CONTROLS
    if (found.domain === 'light') {
      const curBri = ctrl.brightness_pct ?? (isOn ? 100 : 0);
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>💡 HELLIGKEIT</span>
            <span id="haBriVal" style="font-family:var(--mono-family);">${curBri}%</span>
          </div>
          <input type="range" min="1" max="100" value="${curBri}" class="ha-slider" 
                 oninput="document.getElementById('haBriVal').textContent = this.value + '%'"
                 onchange="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', brightness_pct: parseInt(this.value)})">
        </div>

        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>🌡️ FARBTEMPERATUR</span>
            <span id="haTempLabel">WEISS-TÖNE</span>
          </div>
          <div style="display:flex; gap:0.4rem; margin-top:0.4rem;">
            <button class="left-action-btn" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', color_temp_kelvin: 2700})" style="flex:1; padding:0.35rem; font-size:0.75rem; border-color:#ffaa44; color:#ffaa44;">
              WARM (2700K)
            </button>
            <button class="left-action-btn" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', color_temp_kelvin: 4000})" style="flex:1; padding:0.35rem; font-size:0.75rem; border-color:#ffeecc; color:#ffeecc;">
              NEUTRAL (4000K)
            </button>
            <button class="left-action-btn" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', color_temp_kelvin: 6500})" style="flex:1; padding:0.35rem; font-size:0.75rem; border-color:#88bbff; color:#88bbff;">
              KALT (6500K)
            </button>
          </div>
        </div>

        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>🎨 FARBAUSWAHL (RGB)</span>
          </div>
          <div style="display:flex; gap:0.6rem; flex-wrap:wrap; margin-top:0.4rem;">
            <div class="ha-color-circle" style="background:#ff3333;" title="Rot" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [255, 51, 51]})"></div>
            <div class="ha-color-circle" style="background:#ff9933;" title="Orange" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [255, 153, 51]})"></div>
            <div class="ha-color-circle" style="background:#ffff33;" title="Gelb" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [255, 255, 51]})"></div>
            <div class="ha-color-circle" style="background:#33cc33;" title="Grün" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [51, 204, 51]})"></div>
            <div class="ha-color-circle" style="background:#33ccff;" title="Cyan" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [51, 204, 255]})"></div>
            <div class="ha-color-circle" style="background:#3366ff;" title="Blau" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [51, 102, 255]})"></div>
            <div class="ha-color-circle" style="background:#cc33ff;" title="Lila" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [204, 51, 255]})"></div>
            <div class="ha-color-circle" style="background:#ffffff;" title="Weiß" onclick="callHaService('light', 'turn_on', {entity_id: '${escapeHtml(found.entity_id)}', rgb_color: [255, 255, 255]})"></div>
          </div>
        </div>
      `;
    }

    // 2. CLIMATE CONTROLS
    else if (found.domain === 'climate') {
      const curTemp = ctrl.current_temperature ?? '--';
      const targetTemp = ctrl.temperature ?? 21.0;
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div style="display:flex; justify-content:space-around; align-items:center; background:rgba(0,0,0,0.4); border-radius:8px; padding:1rem; margin-bottom:1rem;">
            <div style="text-align:center;">
              <div style="font-size:0.75rem; color:#888;">IST-TEMPERATUR</div>
              <div style="font-size:2rem; font-weight:800; color:var(--c-blue); font-family:var(--mono-family);">${curTemp}°C</div>
            </div>
            <div style="text-align:center;">
              <div style="font-size:0.75rem; color:#888;">SOLL-TEMPERATUR</div>
              <div style="font-size:2rem; font-weight:800; color:var(--c-gold); font-family:var(--mono-family);" id="haTargetTempVal">${targetTemp}°C</div>
            </div>
          </div>

          <div style="display:flex; gap:0.5rem; justify-content:center; align-items:center; margin-bottom:1rem;">
            <button class="left-action-btn" onclick="adjustClimateTemp('${escapeHtml(found.entity_id)}', -0.5)" style="padding:0.4rem 1rem; font-size:1.1rem; font-weight:700;">- 0.5°</button>
            <button class="left-action-btn" onclick="adjustClimateTemp('${escapeHtml(found.entity_id)}', +0.5)" style="padding:0.4rem 1rem; font-size:1.1rem; font-weight:700;">+ 0.5°</button>
          </div>

          <div class="ha-ctrl-label">
            <span>BETRIEBSMODUS</span>
          </div>
          <div style="display:flex; gap:0.4rem; flex-wrap:wrap;">
            ${(ctrl.hvac_modes || ['heat', 'cool', 'auto', 'off']).map(m => `
              <button class="left-action-btn" onclick="callHaService('climate', 'set_hvac_mode', {entity_id: '${escapeHtml(found.entity_id)}', hvac_mode: '${m}'})" style="padding:0.35rem 0.75rem; font-size:0.8rem; text-transform:uppercase; ${found.state === m ? 'border-color:var(--c-primary); color:var(--c-primary); font-weight:700;' : 'color:#aaa;'}">
                ${m}
              </button>
            `).join('')}
          </div>
        </div>
      `;
    }

    // 3. COVER / ROLLO CONTROLS
    else if (found.domain === 'cover') {
      const pos = ctrl.current_position ?? 50;
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>STEUERUNG</span>
          </div>
          <div style="display:flex; gap:0.6rem; justify-content:space-between; margin-bottom:1.2rem;">
            <button class="left-action-btn" onclick="callHaService('cover', 'open_cover', {entity_id: '${escapeHtml(found.entity_id)}'})" style="flex:1; padding:0.6rem; font-size:0.9rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
              ▲ ÖFFNEN
            </button>
            <button class="left-action-btn" onclick="callHaService('cover', 'stop_cover', {entity_id: '${escapeHtml(found.entity_id)}'})" style="flex:1; padding:0.6rem; font-size:0.9rem; border-color:var(--c-red); color:var(--c-red);">
              ■ STOPP
            </button>
            <button class="left-action-btn" onclick="callHaService('cover', 'close_cover', {entity_id: '${escapeHtml(found.entity_id)}'})" style="flex:1; padding:0.6rem; font-size:0.9rem; border-color:var(--c-blue); color:var(--c-blue);">
              ▼ SCHLIESSEN
            </button>
          </div>

          <div class="ha-ctrl-label">
            <span>POSITION</span>
            <span id="haCoverPosVal" style="font-family:var(--mono-family);">${pos}%</span>
          </div>
          <input type="range" min="0" max="100" value="${pos}" class="ha-slider" 
                 oninput="document.getElementById('haCoverPosVal').textContent = this.value + '%'"
                 onchange="callHaService('cover', 'set_cover_position', {entity_id: '${escapeHtml(found.entity_id)}', position: parseInt(this.value)})">
        </div>
      `;
    }

    // 4. MEDIA PLAYER CONTROLS
    else if (found.domain === 'media_player') {
      const vol = ctrl.volume_pct ?? 50;
      controlsHtml += `
        <div class="ha-ctrl-row">
          ${ctrl.media_title ? `
            <div style="background:rgba(0,0,0,0.4); border-radius:6px; padding:0.75rem; margin-bottom:1rem; text-align:center;">
              <div style="font-size:1.1rem; font-weight:700; color:#fff;">${escapeHtml(ctrl.media_title)}</div>
              <div style="font-size:0.85rem; color:var(--c-gold);">${escapeHtml(ctrl.media_artist || '')}</div>
            </div>
          ` : ''}

          <div class="ha-ctrl-label">
            <span>WIEDERGABE</span>
          </div>
          <div style="display:flex; gap:0.4rem; justify-content:center; margin-bottom:1.2rem;">
            <button class="left-action-btn" onclick="callHaService('media_player', 'media_previous_track', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.5rem 0.8rem; font-size:1rem;">⏮</button>
            <button class="left-action-btn" onclick="callHaService('media_player', 'media_play_pause', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.5rem 1.2rem; font-size:1.1rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">⏯ PLAY / PAUSE</button>
            <button class="left-action-btn" onclick="callHaService('media_player', 'media_stop', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.5rem 0.8rem; font-size:1rem;">⏹</button>
            <button class="left-action-btn" onclick="callHaService('media_player', 'media_next_track', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.5rem 0.8rem; font-size:1rem;">⏭</button>
          </div>

          <div class="ha-ctrl-label">
            <span>LAUTSTÄRKE</span>
            <span id="haVolVal" style="font-family:var(--mono-family);">${vol}%</span>
          </div>
          <input type="range" min="0" max="100" value="${vol}" class="ha-slider" 
                 oninput="document.getElementById('haVolVal').textContent = this.value + '%'"
                 onchange="callHaService('media_player', 'volume_set', {entity_id: '${escapeHtml(found.entity_id)}', volume_level: parseFloat(this.value) / 100})">
        </div>
      `;
    }

    // 5. VACUUM CONTROLS
    else if (found.domain === 'vacuum') {
      const bat = ctrl.battery_level;
      controlsHtml += `
        <div class="ha-ctrl-row">
          ${bat !== undefined ? `
            <div style="background:rgba(0,0,0,0.4); border-radius:6px; padding:0.6rem 1rem; margin-bottom:1rem; display:flex; justify-content:space-between; align-items:center;">
              <span style="font-size:0.85rem; color:#aaa;">BATTERIE</span>
              <span style="font-size:1.1rem; font-weight:700; color:${bat > 20 ? '#44dd88' : 'var(--c-red)'}; font-family:var(--mono-family);">${bat}% 🔋</span>
            </div>
          ` : ''}

          <div class="ha-ctrl-label">
            <span>REINIGUNG STEUERN</span>
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.6rem; margin-top:0.4rem;">
            <button class="left-action-btn" onclick="callHaService('vacuum', 'start', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.6rem; font-size:0.85rem; border-color:#44dd88; color:#44dd88; font-weight:700;">
              ▶ REINIGEN STARTEN
            </button>
            <button class="left-action-btn" onclick="callHaService('vacuum', 'pause', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.6rem; font-size:0.85rem; border-color:var(--c-gold); color:var(--c-gold);">
              ⏸ PAUSIEREN
            </button>
            <button class="left-action-btn" onclick="callHaService('vacuum', 'return_to_base', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.6rem; font-size:0.85rem; border-color:var(--c-blue); color:var(--c-blue); font-weight:700;">
              🏠 ZUR LADESTATION
            </button>
            <button class="left-action-btn" onclick="callHaService('vacuum', 'locate', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.6rem; font-size:0.85rem; border-color:var(--c-secondary); color:var(--c-secondary);">
              🔊 LOKALISIEREN (BEEP)
            </button>
          </div>
        </div>
      `;
    }

    // 6. NUMBER CONTROLS
    else if (found.domain === 'number' || found.domain === 'input_number') {
      const min = ctrl.min_val ?? 0;
      const max = ctrl.max_val ?? 255;
      const step = ctrl.step_val ?? 1;
      const cur = parseFloat(found.state) || min;
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>WERT EINSTELLEN</span>
            <span id="haNumVal" style="font-family:var(--mono-family);">${cur}</span>
          </div>
          <input type="range" min="${min}" max="${max}" step="${step}" value="${cur}" class="ha-slider" 
                 oninput="document.getElementById('haNumVal').textContent = this.value"
                 onchange="callHaService('${found.domain}', 'set_value', {entity_id: '${escapeHtml(found.entity_id)}', value: parseFloat(this.value)})">
        </div>
      `;
    }

    // 7. SELECT CONTROLS
    else if (found.domain === 'select' || found.domain === 'input_select') {
      const opts = ctrl.options || [];
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div class="ha-ctrl-label">
            <span>OPTION WÄHLEN</span>
          </div>
          <select class="lcars-input" style="width:100%; margin-top:0.4rem;" onchange="callHaService('${found.domain}', 'select_option', {entity_id: '${escapeHtml(found.entity_id)}', option: this.value})">
            ${opts.map(o => `
              <option value="${escapeHtml(o)}" ${found.state === o ? 'selected' : ''}>${escapeHtml(o)}</option>
            `).join('')}
          </select>
        </div>
      `;
    }

    // 8. AUTOMATION CONTROLS
    else if (found.domain === 'automation') {
      const lastTrig = ctrl.last_triggered ? ctrl.last_triggered.replace('T', ' ').slice(0, 19) : 'Nie';
      const mode = ctrl.mode || 'single';
      controlsHtml += `
        <div class="ha-ctrl-row" style="text-align:center; padding:0.5rem 0 1rem 0;">
          <button class="left-action-btn" onclick="callHaService('automation', 'trigger', {entity_id: '${escapeHtml(found.entity_id)}'})" style="width:100%; padding:0.85rem 1.25rem; font-size:1.05rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700; background:rgba(255,153,0,0.12);">
            ⚡ AUTOMATION JETZT AUSFÜHREN (TRIGGERN)
          </button>
        </div>
        <div class="ha-ctrl-row">
          <div style="background:rgba(0,0,0,0.4); border-radius:6px; padding:1rem; font-family:var(--mono-family); font-size:0.85rem; color:#ccc;">
            <div style="margin-bottom:0.5rem;"><strong style="color:var(--c-gold);">Status:</strong> ${isOn ? '<span style="color:#44dd88; font-weight:bold;">AKTIV (Eingeschaltet)</span>' : '<span style="color:#888; font-weight:bold;">INAKTIV (Deaktiviert)</span>'}</div>
            <div style="margin-bottom:0.5rem;"><strong style="color:var(--c-gold);">Modus:</strong> ${escapeHtml(mode)}</div>
            <div><strong style="color:var(--c-gold);">Zuletzt ausgeführt:</strong> ${escapeHtml(lastTrig)}</div>
          </div>
        </div>
      `;
    }

    // 9. BUTTON / SCENE / SCRIPT
    else if (['button', 'scene', 'script', 'input_button'].includes(found.domain)) {
      const srv = found.domain === 'scene' ? 'turn_on' : (found.domain === 'button' ? 'press' : 'turn_on');
      controlsHtml += `
        <div class="ha-ctrl-row" style="text-align:center; padding:1rem 0;">
          <button class="left-action-btn" onclick="callHaService('${found.domain}', '${srv}', {entity_id: '${escapeHtml(found.entity_id)}'})" style="padding:0.75rem 2rem; font-size:1.1rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
            🔘 AKTION JETZT AUSFÜHREN
          </button>
        </div>
      `;
    }

    // 9. SENSORS & ATTRIBUTES
    else {
      controlsHtml += `
        <div class="ha-ctrl-row">
          <div style="background:rgba(0,0,0,0.4); border-radius:6px; padding:1rem; font-family:var(--mono-family); font-size:0.85rem; color:#ccc;">
            <div style="margin-bottom:0.4rem;"><strong style="color:var(--c-gold);">Zustand:</strong> ${escapeHtml(found.state)} ${escapeHtml(ctrl.unit || '')}</div>
            <div style="margin-bottom:0.4rem;"><strong style="color:var(--c-gold);">Typ:</strong> ${escapeHtml(ctrl.device_class || found.domain)}</div>
            ${found.last_changed ? `<div><strong style="color:var(--c-gold);">Aktualisiert:</strong> ${escapeHtml(found.last_changed.replace('T', ' ').slice(0, 19))}</div>` : ''}
          </div>
        </div>
      `;
    }

    bodyEl.innerHTML = controlsHtml;
    modal.style.display = 'flex';
  }

  function closeHaControlModal() {
    playLcarsBeep(880, 440);
    const modal = document.getElementById('haControlModal');
    if (modal) modal.style.display = 'none';
  }

  function handleModalBackdropClick(e) {
    if (e.target && e.target.id === 'haControlModal') {
      closeHaControlModal();
    }
  }

  async function adjustClimateTemp(entityId, delta) {
    if (!haCachedData) return;
    let found = null;
    for (const a of (haCachedData.areas || [])) {
      found = a.entities.find(e => e.entity_id === entityId);
      if (found) break;
    }
    if (!found) return;

    const cur = found.controls?.temperature || 21.0;
    const next = Math.round((cur + delta) * 10) / 10;
    const labelEl = document.getElementById('haTargetTempVal');
    if (labelEl) labelEl.textContent = next + '°C';
    await callHaService('climate', 'set_temperature', { entity_id: entityId, temperature: next });
  }

  async function callHaService(domain, service, serviceData) {
    playLcarsBeep(1200, 1600);
    try {
      const resp = await fetch('/api/homeassistant/service', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain, service, service_data: serviceData })
      });
      const res = await resp.json();
      if (!res.success) {
        console.warn('Service Call Error:', res.error);
        playLcarsBeep(440, 220);
      }
      setTimeout(() => { loadHomeAssistantData(true); }, 350);
    } catch(err) {
      console.warn('callHaService error:', err);
      playLcarsBeep(440, 220);
    }
  }

  function startHaAutoRefresh() {
    if (haAutoRefreshTimer) clearInterval(haAutoRefreshTimer);
    haCountdownSeconds = 15;
    updateHaCountdownUI();

    haAutoRefreshTimer = setInterval(() => {
      const sec = document.getElementById('section-homeassistant');
      const isVisible = sec && sec.classList.contains('active-section') && !document.hidden;

      if (!isVisible) {
        haCountdownSeconds = 15;
        return;
      }

      haCountdownSeconds--;
      if (haCountdownSeconds <= 0) {
        haCountdownSeconds = 15;
        updateHaCountdownUI();
        loadHomeAssistantData(false);
      } else {
        updateHaCountdownUI();
      }
    }, 1000);
  }

  // BALKONSOLAR & DACHTERRASSE CONTROLLER
  let isSolarLoading = false;
  let solarCountdownSeconds = 15;
  let solarAutoRefreshTimer = null;
  let solarCurrentData = null;

  function updateSolarCountdownUI() {
    const badge = document.getElementById('solarCountdownBadge');
    if (!badge) return;
    if (isSolarLoading) {
      badge.textContent = '● SYNC...';
      badge.style.color = 'var(--c-gold)';
      badge.style.borderColor = 'var(--c-gold)';
      badge.style.background = 'rgba(237, 179, 120, 0.15)';
    } else {
      badge.textContent = `● REFRESH IN ${solarCountdownSeconds}S`;
      badge.style.color = 'var(--c-gold)';
      badge.style.borderColor = 'rgba(237, 179, 120, 0.4)';
      badge.style.background = 'rgba(237, 179, 120, 0.15)';
    }
  }

  async function loadSolarData(force = false) {
    if (isSolarLoading) return;
    isSolarLoading = true;
    updateSolarCountdownUI();

    try {
      const resp = await fetch('/api/solar/data');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      solarCurrentData = data;
      renderSolarUI(data);
    } catch(err) {
      console.warn('loadSolarData error:', err);
      const liveBadge = document.getElementById('solarLiveBadge');
      if (liveBadge) {
        liveBadge.textContent = 'OFFLINE';
        liveBadge.className = 'badge-status badge-offline';
      }
    } finally {
      isSolarLoading = false;
      solarCountdownSeconds = 15;
      updateSolarCountdownUI();
    }
  }

  function renderSolarUI(data) {
    if (!data || !data.success || !data.summary) return;
    const s = data.summary;

    // Badges oben
    const liveBadge = document.getElementById('solarLiveBadge');
    if (liveBadge) {
      liveBadge.textContent = 'ONLINE';
      liveBadge.className = 'badge-status badge-online';
    }

    const cloudBadge = document.getElementById('solarCloudBadge');
    if (cloudBadge) {
      const c1 = (s.cloud_state || 'online').toUpperCase();
      const c2 = (s.ecotracker_cloud || 'online').toUpperCase();
      cloudBadge.textContent = `ANKER CLOUD: ${c1} // ECOTRACKER: ${c2}`;
      cloudBadge.style.color = (c1 === 'ONLINE' && c2 === 'ONLINE') ? 'var(--c-gold)' : 'var(--c-red)';
    }

    // Top Right Badges (Header)
    const topBat = document.getElementById('topBatVal');
    if (topBat && s.battery_soc_str) topBat.textContent = s.battery_soc_str;
    const itemBat = document.getElementById('topVitalBat');
    if (itemBat && s.battery_soc !== null && s.battery_soc !== undefined) {
      itemBat.classList.toggle('vital-alert', s.battery_soc < 10);
    }

    const topHouse = document.getElementById('topHouseVal');
    if (topHouse && s.house_power_str) topHouse.textContent = s.house_power_str;

    // Power Flow Nodes
    const nSolarVal = document.getElementById('nodeSolarVal');
    if (nSolarVal) nSolarVal.textContent = s.solar_power_str || '0 W';

    const nBatVal = document.getElementById('nodeBatVal');
    if (nBatVal) nBatVal.textContent = s.battery_soc_str || '--%';

    const nBatStatus = document.getElementById('nodeBatStatus');
    if (nBatStatus) {
      nBatStatus.textContent = s.battery_status || 'STANDBY';
      nBatStatus.style.color = s.battery_status_color || 'var(--c-secondary)';
    }

    const nBatSub = document.getElementById('nodeBatSub');
    if (nBatSub) {
      nBatSub.textContent = `${s.battery_energy_wh || 0} Wh / ${s.battery_capacity_wh || 1600} Wh (${s.battery_temp_str || '--'})`;
    }

    const nHouseVal = document.getElementById('nodeHouseVal');
    if (nHouseVal) nHouseVal.textContent = s.house_power_str || '-- W';

    const nGridVal = document.getElementById('nodeGridVal');
    if (nGridVal) nGridVal.textContent = s.grid_power_str || '-- W';

    const nGridSub = document.getElementById('nodeGridSub');
    if (nGridSub) {
      if (s.grid_feed_in > 0) {
        nGridSub.textContent = `Einspeisung: ${s.grid_feed_in_str} // Bezug: 0 W`;
      } else {
        nGridSub.textContent = `Netzbezug // ${s.grid_feed_in_str || '0 W'} Einspeisung`;
      }
    }

    // Summary Tag
    const flowTag = document.getElementById('solarFlowSummaryTag');
    if (flowTag) {
      if (s.solar_power > (s.house_power || 0) && s.solar_power > 0) {
        flowTag.textContent = '⚡ SOLAR-ÜBERSCHUSS';
        flowTag.style.backgroundColor = '#44dd88';
      } else if (s.battery_discharge_power > 20) {
        flowTag.textContent = '🔋 AKKU-VERSORGUNG';
        flowTag.style.backgroundColor = 'var(--c-secondary)';
      } else if (s.grid_power > 0) {
        flowTag.textContent = '🌐 LIVE NETZBEZUG';
        flowTag.style.backgroundColor = 'var(--c-gold)';
      } else {
        flowTag.textContent = 'AUTARK';
        flowTag.style.backgroundColor = '#44dd88';
      }
    }

    // Card 1: Photovoltaik
    const cSolVal = document.getElementById('cardSolarVal');
    if (cSolVal) cSolVal.textContent = s.solar_power_str || '0 W';

    const setPvBar = (valId, barId, val) => {
      const elVal = document.getElementById(valId);
      const elBar = document.getElementById(barId);
      if (elVal) elVal.textContent = `${val} W`;
      if (elBar) elBar.style.width = Math.min(100, Math.round((val / 500) * 100)) + '%';
    };
    setPvBar('pv1Val', 'pv1Bar', s.pv1 || 0);
    setPvBar('pv2Val', 'pv2Bar', s.pv2 || 0);
    setPvBar('pv3Val', 'pv3Bar', s.pv3 || 0);
    setPvBar('pv4Val', 'pv4Bar', s.pv4 || 0);

    const elYield = document.getElementById('solYieldTotal');
    if (elYield) elYield.textContent = (s.yield_total !== null && s.yield_total !== undefined) ? `${s.yield_total} kWh` : '-- kWh';
    const elCo2 = document.getElementById('solCo2Saved');
    if (elCo2) elCo2.textContent = (s.co2_saved !== null && s.co2_saved !== undefined) ? `${s.co2_saved} kg` : '-- kg';
    const elCost = document.getElementById('solCostSaved');
    if (elCost) elCost.textContent = (s.cost_saved !== null && s.cost_saved !== undefined) ? `${Number(s.cost_saved).toFixed(2)} €` : '-- €';

    // Card 2: Akku
    const cBatVal = document.getElementById('cardBatVal');
    if (cBatVal) cBatVal.textContent = s.battery_soc_str || '--%';
    const cBatSub = document.getElementById('cardBatSub');
    if (cBatSub) {
      cBatSub.textContent = `Akkustand (${s.battery_status || 'STANDBY'})`;
      cBatSub.style.color = s.battery_status_color || 'var(--c-secondary)';
    }
    const cBatBar = document.getElementById('cardBatBar');
    if (cBatBar) {
      const pct = Math.min(100, Math.max(0, s.battery_soc || 0));
      cBatBar.style.width = pct + '%';
      if (pct < 10) cBatBar.style.backgroundColor = 'var(--c-red)';
      else if (pct < 30) cBatBar.style.backgroundColor = 'var(--c-gold)';
      else cBatBar.style.backgroundColor = 'var(--c-secondary)';
    }

    const elEnergyWh = document.getElementById('solEnergyWh');
    if (elEnergyWh) elEnergyWh.textContent = `${s.battery_energy_wh || 0} Wh / ${s.battery_capacity_wh || 1600} Wh`;
    const elBatCharge = document.getElementById('solBatCharge');
    if (elBatCharge) elBatCharge.textContent = `${s.battery_charge_power || 0} W`;
    const elBatDischarge = document.getElementById('solBatDischarge');
    if (elBatDischarge) elBatDischarge.textContent = `${s.battery_discharge_power || 0} W`;
    const elBatTemp = document.getElementById('solBatTemp');
    if (elBatTemp) elBatTemp.textContent = s.battery_temp_str || '--';
    const elSocLimits = document.getElementById('solSocLimits');
    if (elSocLimits) elSocLimits.textContent = `Min ${s.battery_soc_min || 5}% / Max ${s.battery_soc_max || 100}%`;
    const elBatHeating = document.getElementById('solBatHeating');
    if (elBatHeating) elBatHeating.textContent = s.battery_heating ? `Aktiv (${s.battery_heat_power || 0} W)` : 'Inaktiv (0 W)';
    const elOpState = document.getElementById('solOpState');
    if (elOpState) elOpState.textContent = s.operating_state || '--';

    // Card 3: Hausnetz & Zähler
    const cHouseVal = document.getElementById('cardHouseVal');
    if (cHouseVal) cHouseVal.textContent = s.house_power_str || '-- W';
    const elAcOut = document.getElementById('solAcOutput');
    if (elAcOut) elAcOut.textContent = `${s.inverter_ac_output || 0} W`;
    const elDcOut = document.getElementById('solDcOutput');
    if (elDcOut) elDcOut.textContent = `${s.inverter_dc_output || 0} W`;
    const elGridUsage = document.getElementById('solGridUsage');
    if (elGridUsage) elGridUsage.textContent = s.grid_power_str || '-- W';
    const elGridFeed = document.getElementById('solGridFeedIn');
    if (elGridFeed) elGridFeed.textContent = s.grid_feed_in_str || '0 W';
    const elAcSocket = document.getElementById('solAcSocket');
    if (elAcSocket) elAcSocket.textContent = `${s.inverter_socket || 0} W`;
    const elFeedTarget = document.getElementById('solFeedTarget');
    if (elFeedTarget) elFeedTarget.textContent = `${s.feed_target || 0} W`;
    const elFeedLimit = document.getElementById('solFeedLimit');
    if (elFeedLimit) elFeedLimit.textContent = `${s.feed_limit || 800} W`;

    // Card 4: Telemetrie & Schnellsteuerung
    const elMode = document.getElementById('solMode');
    if (elMode) elMode.textContent = s.mode || 'smartmeter';
    const elCloudState = document.getElementById('solCloudState');
    if (elCloudState) {
      elCloudState.textContent = (s.cloud_state || 'online').toUpperCase();
      elCloudState.style.color = (s.cloud_state === 'online') ? '#44dd88' : 'var(--c-red)';
    }
    const elEcoCloud = document.getElementById('solEcoCloudState');
    if (elEcoCloud) {
      elEcoCloud.textContent = (s.ecotracker_cloud || 'online').toUpperCase();
      elEcoCloud.style.color = (s.ecotracker_cloud === 'online') ? '#44dd88' : 'var(--c-red)';
    }
    const elWifiState = document.getElementById('solWifiState');
    if (elWifiState) {
      const w1 = s.wifi_storage ? 'Verbunden' : 'Getrennt';
      const w2 = s.wifi_tracker ? 'Verbunden' : 'Getrennt';
      elWifiState.textContent = `${w1} / ${w2}`;
    }
    const elMqttTime = document.getElementById('solMqttTime');
    if (elMqttTime) elMqttTime.textContent = s.mqtt_time || '--';

    // Buttons / Switch State
    const btnFeed = document.getElementById('btnToggleFeed');
    if (btnFeed) {
      btnFeed.textContent = s.grid_feed_allowed ? 'NETZEINSPEISUNG: AN' : 'NETZEINSPEISUNG: AUS';
      btnFeed.style.borderColor = s.grid_feed_allowed ? '#44dd88' : '#888';
      btnFeed.style.color = s.grid_feed_allowed ? '#44dd88' : '#888';
    }
    const btnLed = document.getElementById('btnToggleLed');
    if (btnLed) {
      btnLed.textContent = s.led_light ? 'LED LICHT: AN' : 'LED LICHT: AUS';
      btnLed.style.borderColor = s.led_light ? 'var(--c-gold)' : '#888';
      btnLed.style.color = s.led_light ? 'var(--c-gold)' : '#888';
    }

    // Entities Grid
    renderSolarEntitiesGrid(data.entities || []);
  }

  function renderSolarEntitiesGrid(entities) {
    const container = document.getElementById('solarEntitiesGrid');
    if (!container) return;

    if (!entities || entities.length === 0) {
      container.innerHTML = '<div style="padding:2rem; text-align:center; color:#888; font-family:var(--mono-family); grid-column:1/-1;">Keine Dachterassen-Objekte gefunden.</div>';
      return;
    }

    const domainFilter = document.getElementById('solarDomainFilter')?.value || 'all';
    const searchVal = (document.getElementById('solarSearchInput')?.value || '').toLowerCase().trim();

    const filtered = entities.filter(e => {
      if (domainFilter === 'controllable' && !e.controllable) return false;
      if (domainFilter !== 'all' && domainFilter !== 'controllable' && e.domain !== domainFilter) return false;
      if (searchVal) {
        const matchName = (e.friendly_name || '').toLowerCase().includes(searchVal);
        const matchId = (e.entity_id || '').toLowerCase().includes(searchVal);
        const matchState = (e.state || '').toLowerCase().includes(searchVal);
        if (!matchName && !matchId && !matchState) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      container.innerHTML = '<div style="padding:2rem; text-align:center; color:#888; font-family:var(--mono-family); grid-column:1/-1;">Keine Objekte entsprechen dem aktuellen Filter.</div>';
      return;
    }

    let html = '';
    filtered.forEach(e => {
      const isActive = ['on', 'open', 'playing', 'cleaning'].includes(e.state);
      const activeClass = isActive ? 'entity-active' : '';
      const unit = e.controls?.unit ? ` ${e.controls.unit}` : '';
      const stateDisplay = (e.state || 'unknown') + unit;

      html += `
        <div class="ha-entity-tile ${activeClass}" onclick="openHaControlModal('${e.entity_id}')" title="${e.entity_id}">
          <div class="ha-tile-top">
            <span class="ha-tile-icon">${e.icon || '⚙️'}</span>
            <span class="ha-tile-state" style="font-family:var(--mono-family); font-weight:700; font-size:0.85rem; color:${isActive ? 'var(--c-primary)' : '#ccc'};">${stateDisplay}</span>
          </div>
          <div class="ha-tile-name" style="margin-top:0.4rem; font-weight:600; font-size:0.82rem; color:#fff; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${e.friendly_name}</div>
          <div class="ha-tile-id" style="font-size:0.68rem; color:#777; font-family:var(--mono-family); text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${e.entity_id}</div>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  function filterSolarEntities() {
    if (solarCurrentData && solarCurrentData.entities) {
      renderSolarEntitiesGrid(solarCurrentData.entities);
    }
  }

  async function triggerSolarAction(entityId, label) {
    playLcarsBeep(980, 1400);
    try {
      const resp = await fetch('/api/homeassistant/service', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain: 'button',
          service: 'press',
          service_data: { entity_id: entityId }
        })
      });
      const res = await resp.json();
      if (res.success) {
        playLcarsBeep(1200, 1600);
        setTimeout(() => { loadSolarData(true); }, 400);
      } else {
        console.warn('triggerSolarAction error:', res.error);
        playLcarsBeep(440, 220);
      }
    } catch(e) {
      console.warn('triggerSolarAction exception:', e);
      playLcarsBeep(440, 220);
    }
  }

  async function toggleSolarFeedSwitch() {
    if (!solarCurrentData || !solarCurrentData.summary) return;
    const current = solarCurrentData.summary.grid_feed_allowed;
    const service = current ? 'turn_off' : 'turn_on';
    playLcarsBeep(880, 1760);
    try {
      await fetch('/api/homeassistant/service', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain: 'switch',
          service: service,
          service_data: { entity_id: 'switch.christophs_energiespeicher_erlaube_netzeinspeisung' }
        })
      });
      setTimeout(() => { loadSolarData(true); }, 400);
    } catch(e) {
      console.warn('toggleSolarFeedSwitch error:', e);
    }
  }

  async function toggleSolarLedSwitch() {
    if (!solarCurrentData || !solarCurrentData.summary) return;
    const current = solarCurrentData.summary.led_light;
    const service = current ? 'turn_off' : 'turn_on';
    playLcarsBeep(880, 1760);
    try {
      await fetch('/api/homeassistant/service', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain: 'switch',
          service: service,
          service_data: { entity_id: 'switch.christophs_energiespeicher_led_licht' }
        })
      });
      setTimeout(() => { loadSolarData(true); }, 400);
    } catch(e) {
      console.warn('toggleSolarLedSwitch error:', e);
    }
  }

  function startSolarAutoRefresh() {
    if (solarAutoRefreshTimer) clearInterval(solarAutoRefreshTimer);
    solarCountdownSeconds = 15;
    updateSolarCountdownUI();

    solarAutoRefreshTimer = setInterval(() => {
      const sec = document.getElementById('section-solar');
      const isVisible = sec && sec.classList.contains('active-section') && !document.hidden;

      if (!isVisible) {
        solarCountdownSeconds = 15;
        return;
      }

      solarCountdownSeconds--;
      if (solarCountdownSeconds <= 0) {
        solarCountdownSeconds = 15;
        updateSolarCountdownUI();
        loadSolarData(false);
      } else {
        updateSolarCountdownUI();
      }
    }, 1000);
  }

  function playRedAlertKlaxon() {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioContext();
      if (!ctx) return;
      const now = ctx.currentTime;
      for (let i = 0; i < 2; i++) {
        const start = now + (i * 0.65);
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(660, start);
        osc.frequency.exponentialRampToValueAtTime(440, start + 0.42);
        gain.gain.setValueAtTime(0.01, start);
        gain.gain.linearRampToValueAtTime(0.2, start + 0.05);
        gain.gain.exponentialRampToValueAtTime(0.001, start + 0.5);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(start);
        osc.stop(start + 0.55);
      }
    } catch(e) {
      console.warn("Red Alert Klaxon audio error:", e);
    }
  }

  // Theme Switcher (Farbmodi)
  let lastUserTheme = localStorage.getItem('lcars-theme') || 'classic';
  if (lastUserTheme === 'redalert') lastUserTheme = 'classic';
  let isCurrentlyRedAlert = false;

  function setLcarsTheme(themeName) {
    if (themeName === 'redalert') return; // Red Alert kann nicht manuell gewählt werden
    playLcarsBeep(1100, 1800);
    lastUserTheme = themeName;
    localStorage.setItem('lcars-theme', themeName);

    if (!isCurrentlyRedAlert) {
      document.documentElement.setAttribute('data-theme', themeName);
    }

    document.querySelectorAll('.theme-btn').forEach(btn => btn.classList.remove('active-theme'));
    const btn = document.getElementById('theme-btn-' + themeName);
    if (btn) btn.classList.add('active-theme');

    if (historyChart) historyChart.update();
  }

  setLcarsTheme(lastUserTheme);
  updateAudioUI();

  // Schwellwert-Initialisierung im Config-Bereich
  let thresholdsInitialized = false;
  function initThresholdsConfig(alerts) {
    if (!alerts || !alerts.thresholds) return;
    if (thresholdsInitialized) return;
    thresholdsInitialized = true;
    const th = alerts.thresholds;
    if (th.cpu_threshold !== undefined) {
      const el = document.getElementById('cfgCpuThresh');
      if (el) { el.value = th.cpu_threshold; document.getElementById('cfgCpuThreshVal').textContent = th.cpu_threshold + '%'; }
    }
    if (th.ram_threshold !== undefined) {
      const el = document.getElementById('cfgRamThresh');
      if (el) { el.value = th.ram_threshold; document.getElementById('cfgRamThreshVal').textContent = th.ram_threshold + '%'; }
    }
    if (th.disk_threshold !== undefined) {
      const el = document.getElementById('cfgDiskThresh');
      if (el) { el.value = th.disk_threshold; document.getElementById('cfgDiskThreshVal').textContent = th.disk_threshold + '%'; }
    }
    if (th.temp_threshold !== undefined) {
      const el = document.getElementById('cfgTempThresh');
      if (el) { el.value = th.temp_threshold; document.getElementById('cfgTempThreshVal').textContent = th.temp_threshold + '°C'; }
    }
    if (th.duration_seconds !== undefined) {
      const el = document.getElementById('cfgDuration');
      if (el) { el.value = th.duration_seconds; document.getElementById('cfgDurationVal').textContent = th.duration_seconds + ' Sek'; }
    }
    if (th.gateway_check_interval_seconds !== undefined) {
      const minVal = Math.round(th.gateway_check_interval_seconds / 60) || 10;
      const el = document.getElementById('cfgGwInterval');
      if (el) { el.value = minVal; document.getElementById('cfgGwIntervalVal').textContent = minVal + ' Min'; }
    }
    if (th.gateway_url) {
      const el = document.getElementById('cfgGwUrl');
      if (el) el.value = th.gateway_url;
    }
    if (th.antigravity_ide_url) {
      const elCfg = document.getElementById('cfgIdeUrl');
      if (elCfg) elCfg.value = th.antigravity_ide_url;
      const elIde = document.getElementById('ideUrlInput');
      if (elIde && (!elIde.value || elIde.value.indexOf('antigravity.google.com') !== -1)) {
        elIde.value = th.antigravity_ide_url;
      }
      const launchBtn = document.getElementById('ideLaunchBtn');
      if (launchBtn) launchBtn.href = th.antigravity_ide_url;
    }
  }

  // Red Alert Status Handler
  function updateAlertUI(alerts) {
    if (!alerts) return;

    initThresholdsConfig(alerts);

    const banner = document.getElementById('redAlertBanner');
    const reasonsEl = document.getElementById('redAlertReasons');

    if (alerts.active) {
      if (!isCurrentlyRedAlert) {
        isCurrentlyRedAlert = true;
        const cur = document.documentElement.getAttribute('data-theme');
        if (cur && cur !== 'redalert') {
          lastUserTheme = cur;
        }
        document.documentElement.setAttribute('data-theme', 'redalert');
        playRedAlertKlaxon();
      }
      if (banner) banner.style.display = 'block';
      if (reasonsEl && alerts.reasons) reasonsEl.textContent = alerts.reasons.join(' // ');
    } else {
      if (isCurrentlyRedAlert) {
        isCurrentlyRedAlert = false;
        document.documentElement.setAttribute('data-theme', lastUserTheme || 'classic');
      }
      if (banner) banner.style.display = 'none';
    }

    if (alerts.gateway) {
      const gwBadge = document.getElementById('cfgGwBadge');
      const gwTime = document.getElementById('cfgGwTime');
      const gwErr = document.getElementById('cfgGwErr');
      if (gwBadge) {
        if (alerts.gateway.status === 'ok') {
          gwBadge.className = 'badge-status badge-online';
          gwBadge.textContent = 'ONLINE';
          if (gwErr) gwErr.textContent = '';
        } else {
          gwBadge.className = 'badge-status badge-offline';
          gwBadge.textContent = 'OFFLINE';
          if (gwErr) gwErr.textContent = alerts.gateway.error ? `(${alerts.gateway.error})` : '';
        }
      }
      if (gwTime && alerts.gateway.last_check) {
        gwTime.textContent = `Letzter Test: ${alerts.gateway.last_check}`;
      }
    }
  }

  async function saveThresholds(e) {
    if (e) e.preventDefault();
    playLcarsBeep(1100, 1800);
    const cpu = parseFloat(document.getElementById('cfgCpuThresh').value);
    const ram = parseFloat(document.getElementById('cfgRamThresh').value);
    const disk = parseFloat(document.getElementById('cfgDiskThresh').value);
    const temp = parseFloat(document.getElementById('cfgTempThresh').value);
    const duration = parseInt(document.getElementById('cfgDuration').value);
    const gwIntervalMin = parseInt(document.getElementById('cfgGwInterval').value);
    const gwUrl = document.getElementById('cfgGwUrl').value.trim();
    const ideUrl = (document.getElementById('cfgIdeUrl')?.value || '').trim();

    const payload = {
      cpu_threshold: cpu,
      ram_threshold: ram,
      disk_threshold: disk,
      temp_threshold: temp,
      duration_seconds: duration,
      gateway_check_interval_seconds: gwIntervalMin * 60,
      gateway_url: gwUrl
    };
    if (ideUrl) payload.antigravity_ide_url = ideUrl;

    const msgEl = document.getElementById('cfgSaveMsg');
    try {
      const resp = await fetch('/api/config/thresholds', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await resp.json();
      if (msgEl) {
        msgEl.style.display = 'block';
        msgEl.style.color = 'var(--c-primary)';
        msgEl.textContent = '✓ SCHWELLWERTE ERFOLGREICH GESPEICHERT & AKTIVIERT';
        setTimeout(() => { msgEl.style.display = 'none'; }, 4000);
      }
      playLcarsBeep(1400, 2100);
      fetchLiveStats(true);
    } catch(err) {
      if (msgEl) {
        msgEl.style.display = 'block';
        msgEl.style.color = 'var(--c-red)';
        msgEl.textContent = '✗ FEHLER BEIM SPEICHERN: ' + err;
      }
      playLcarsBeep(440, 220);
    }
  }

  async function testGatewayNow() {
    playLcarsBeep(980, 1400);
    const btn = document.getElementById('btnTestGw');
    if (btn) btn.disabled = true;
    const badge = document.getElementById('cfgGwBadge');
    const timeEl = document.getElementById('cfgGwTime');
    const errEl = document.getElementById('cfgGwErr');
    if (badge) {
      badge.className = 'badge-status';
      badge.style.background = 'var(--c-gold)';
      badge.textContent = 'PRÜFE...';
    }

    try {
      const resp = await fetch('/api/gateway/test', { method: 'POST' });
      const data = await resp.json();
      if (badge) {
        if (data.status === 'ok') {
          badge.className = 'badge-status badge-online';
          badge.textContent = 'ONLINE';
          if (errEl) errEl.textContent = '';
          playLcarsBeep(1200, 2400);
        } else {
          badge.className = 'badge-status badge-offline';
          badge.textContent = 'OFFLINE';
          if (errEl) errEl.textContent = data.error ? `(${data.error})` : '(Fehler)';
          playLcarsBeep(440, 220);
        }
      }
      if (timeEl && data.time) {
        timeEl.textContent = `Letzter Test: ${data.time}`;
      }
    } catch(err) {
      if (badge) {
        badge.className = 'badge-status badge-offline';
        badge.textContent = 'FEHLER';
      }
      if (errEl) errEl.textContent = String(err);
      playLcarsBeep(440, 220);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function resetDefaultThresholds() {
    playLcarsBeep(880, 1320);
    document.getElementById('cfgCpuThresh').value = 90;
    document.getElementById('cfgCpuThreshVal').textContent = '90%';
    document.getElementById('cfgRamThresh').value = 90;
    document.getElementById('cfgRamThreshVal').textContent = '90%';
    document.getElementById('cfgDiskThresh').value = 90;
    document.getElementById('cfgDiskThreshVal').textContent = '90%';
    document.getElementById('cfgTempThresh').value = 80;
    document.getElementById('cfgTempThreshVal').textContent = '80°C';
    document.getElementById('cfgDuration').value = 60;
    document.getElementById('cfgDurationVal').textContent = '60 Sek';
    document.getElementById('cfgGwInterval').value = 10;
    document.getElementById('cfgGwIntervalVal').textContent = '10 Min';
    document.getElementById('cfgGwUrl').value = 'http://127.0.0.1:20128';
  }

  // Telemetrie Aktualisierung & Rendering
  function renderStats(data) {
    if (!data) return;

    // Schwellwerte für Live-Warnung ermitteln
    const cpuTh = (data.alerts?.thresholds?.cpu_threshold) ?? 90;
    const ramTh = (data.alerts?.thresholds?.ram_threshold) ?? 90;
    const diskTh = (data.alerts?.thresholds?.disk_threshold) ?? 90;
    const tempTh = (data.alerts?.thresholds?.temp_threshold) ?? 80;

    // CPU
    if (data.cpu) {
      document.getElementById('sysCpuVal').textContent = data.cpu.percent + '%';
      document.getElementById('sysCpuBar').style.width = data.cpu.percent + '%';
      document.getElementById('sysCpuCores').textContent = `Kerne: ${data.cpu.cores || 1}`;

      const topCpu = document.getElementById('topCpuVal');
      if (topCpu) topCpu.textContent = Math.round(data.cpu.percent) + '%';
      const itemCpu = document.getElementById('topVitalCpu');
      if (itemCpu) itemCpu.classList.toggle('vital-alert', data.cpu.percent >= cpuTh);
      const liveCpu = document.getElementById('curCpuLive');
      if (liveCpu) liveCpu.textContent = data.cpu.percent + '%';
    }

    // RAM
    if (data.ram) {
      document.getElementById('sysRamVal').textContent = data.ram.percent + '%';
      document.getElementById('sysRamBar').style.width = data.ram.percent + '%';
      document.getElementById('sysRamSub').textContent = `${data.ram.used_gb} GB / ${data.ram.total_gb} GB (${data.ram.available_gb} frei)`;

      const topRam = document.getElementById('topRamVal');
      if (topRam) topRam.textContent = Math.round(data.ram.percent) + '%';
      const itemRam = document.getElementById('topVitalRam');
      if (itemRam) itemRam.classList.toggle('vital-alert', data.ram.percent >= ramTh);
      const liveRam = document.getElementById('curRamLive');
      if (liveRam) liveRam.textContent = data.ram.percent + '%';
    }

    // Temp
    if (data.temperature) {
      document.getElementById('sysTempVal').textContent = data.temperature.display || '-- °C';
      if (data.temperature.value) {
        document.getElementById('sysTempBar').style.width = Math.min(100, (data.temperature.value / 85) * 100) + '%';
      }

      const topTemp = document.getElementById('topTempVal');
      if (topTemp) topTemp.textContent = Math.round(data.temperature.value || 0) + '°';
      const itemTemp = document.getElementById('topVitalTemp');
      if (itemTemp) itemTemp.classList.toggle('vital-alert', (data.temperature.value || 0) >= tempTh);
      const liveTemp = document.getElementById('curTempLive');
      if (liveTemp) liveTemp.textContent = data.temperature.display || '--';
    }

    // Throttled
    if (data.throttled) {
      const isThrottled = data.throttled.active;
      const el = document.getElementById('sysThrottledSub');
      if (el) {
        el.textContent = isThrottled ? 'WARNUNG: THROTTLED (0x1)' : `Normal (${data.throttled.raw || '0x0'})`;
        el.style.color = isThrottled ? 'var(--c-red)' : 'var(--c-gold)';
      }
    }

    // Disk
    if (data.disk) {
      document.getElementById('sysDiskVal').textContent = data.disk.percent + '%';
      document.getElementById('sysDiskBar').style.width = data.disk.percent + '%';
      document.getElementById('sysDiskSub').textContent = `${data.disk.used_gb} GB von ${data.disk.total_gb} GB`;

      const topDisk = document.getElementById('topDiskVal');
      if (topDisk) topDisk.textContent = Math.round(data.disk.percent) + '%';
      const itemDisk = document.getElementById('topVitalDisk');
      if (itemDisk) itemDisk.classList.toggle('vital-alert', data.disk.percent >= diskTh);
      const liveDisk = document.getElementById('curDiskLive');
      if (liveDisk) liveDisk.textContent = data.disk.percent + '%';
    }

    // Uptime
    if (data.uptime) {
      document.getElementById('sysUptimeVal').textContent = data.uptime.display || '--';
      if (data.uptime.boot_time) {
        document.getElementById('sysBootTimeSub').textContent = `Boot: ${data.uptime.boot_time}`;
      }
    }

    // Solar Balkonsolar (Akkustand & Hausverbrauch oben rechts)
    if (data.solar) {
      const topBat = document.getElementById('topBatVal');
      if (topBat && data.solar.battery_soc_str) {
        topBat.textContent = data.solar.battery_soc_str;
      }
      const itemBat = document.getElementById('topVitalBat');
      if (itemBat && data.solar.battery_soc !== null && data.solar.battery_soc !== undefined) {
        itemBat.classList.toggle('vital-alert', data.solar.battery_soc < 10);
      }

      const topHouse = document.getElementById('topHouseVal');
      if (topHouse && data.solar.house_power_str) {
        topHouse.textContent = data.solar.house_power_str;
      }
    }

    // Scanner Meta & Countdown
    if (data.scanner_meta) {
      document.getElementById('scanLastTime').textContent = data.scanner_meta.last_scan_time || '--';
      const sec = data.scanner_meta.next_scan_seconds || 0;
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      document.getElementById('scanCountdown').textContent = `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
    }
    if (data.discovered_servers) {
      latestDiscoveredServers = data.discovered_servers;
      const countEl = document.getElementById('scanFoundCount');
      if (countEl) countEl.textContent = data.discovered_servers.length;
      if (currentCategory === 'services' || !lastServicesFingerprint) {
        updateServicesCards(data.discovered_servers);
      }
    }

    // LAN IP
    if (data.lan_ip) {
      const el = document.getElementById('sysLanIp');
      if (el) el.textContent = data.lan_ip;
    }

    // 9Router Telemetrie Sync
    if (data.nine_router) {
      renderNineRouterStats(data.nine_router);
    }

    // Red Alert & Schwellwerte
    if (data.alerts) {
      updateAlertUI(data.alerts);
    }

    // Timestamp
    if (data.timestamp) {
      document.getElementById('footerTimestamp').textContent = data.timestamp;
    }
  }

  // Responsive Diagnostic Cards für Services & Scanner rendern
  function updateServicesCards(servers) {
    if (!servers) return;
    const fp = servers.map(s => `${s.port}:${s.pid}:${s.cloudflared_url || ''}:${s.http_status || ''}`).join('|');
    if (fp === lastServicesFingerprint) return;
    lastServicesFingerprint = fp;

    const grid = document.getElementById('servicesGrid');
    if (!grid) return;
    const lanIp = initialStats?.lan_ip || '192.168.31.210';
    let html = '';
    servers.forEach(s => {
      // Grundsatz: NIEMALS 127.0.0.1 anzeigen
      const cleanLanUrl = s.lan_url ? s.lan_url.replace("127.0.0.1", lanIp) : `http://${lanIp}:${s.port}`;
      const cleanTsUrl = s.tailscale_url ? s.tailscale_url.replace("127.0.0.1", lanIp) : '';

      const tailscaleBtn = cleanTsUrl
        ? `<a href="${cleanTsUrl}" target="_blank" class="url-chip-btn chip-ts"><span>🌐</span> <span>TS: ${cleanTsUrl}</span></a>`
        : '';

      const cfBtn = s.cloudflared_url
        ? `<a href="${s.cloudflared_url}" target="_blank" class="url-chip-btn chip-cf"><span>☁️</span> <span>CF: ${s.cloudflared_url}</span></a>`
        : `<span style="font-size:0.75rem; color:var(--c-gold); padding-left:0.2rem;">☁️ Tunnel wird initialisiert...</span>`;

      const stopActionHtml = (s.port === 5000)
        ? `<span class="system-kernel-badge">🔒 SYSTEM KERNEL</span>`
        : `<button class="btn-stop-service" onclick="stopService(${s.pid || 0}, ${s.port}, '${(s.title || 'Dienst').replace(/'/g, "\\'")}')">
             <span>🛑</span> <span>PROZESS BEENDEN</span>
           </button>`;

      html += `
        <div class="lcars-card">
          <div class="card-head">
            <span class="card-head-title">${s.title}</span>
            <span class="card-head-icon">${s.icon || '🌐'}</span>
          </div>
          <div>
            <span class="badge-status badge-online">
              PORT ${s.port} // ${s.http_status || 200} OK
            </span>
          </div>
          <div class="cmd-text-box" title="${s.cmdline || ''}">
            PID ${s.pid || 'N/A'} [${s.process_name || 'N/A'}] // ${s.cmdline || ''}
          </div>
          <div class="card-action-links">
            <a href="${cleanLanUrl}" target="_blank" class="url-chip-btn">
              <span>🏠</span> <span>LAN: ${cleanLanUrl}</span>
            </a>
            ${tailscaleBtn}
            ${cfBtn}
          </div>
          <div class="card-service-footer">
            ${stopActionHtml}
          </div>
        </div>
      `;
    });
    grid.innerHTML = html;
  }

  // Dienst über API beenden
  async function stopService(pid, port, name) {
    if (!confirm(`Möchtest du den Dienst "${name}" (Port ${port}, PID ${pid}) wirklich beenden?`)) {
      return;
    }
    playLcarsBeep(440, 220);
    try {
      const resp = await fetch('/api/services/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pid: pid, port: port })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        setTimeout(() => triggerWebserverScan(), 400);
      } else {
        alert("Fehler beim Beenden: " + (data.error || "Unbekannter Fehler"));
      }
    } catch (e) {
      alert("Netzwerkfehler beim Beenden: " + e.message);
    }
  }

  // Manueller Sofort-Scan Trigger
  async function triggerWebserverScan() {
    playLcarsBeep(1200, 2400);
    const btn = document.getElementById('btnScanTrigger');
    const label = document.getElementById('btnScanLabel');
    if (btn) btn.disabled = true;
    if (label) label.textContent = 'SCAN LÄUFT...';

    try {
      const resp = await fetch('/api/scan-webservers');
      if (resp.ok) {
        const data = await resp.json();
        if (data.discovered) updateServicesCards(data.discovered);
        fetchLiveStats(true);
      }
    } catch (e) {
      console.warn("Scan Fehler:", e);
    } finally {
      if (btn) btn.disabled = false;
      if (label) label.textContent = 'JETZT SCANNEN';
    }
  }

  // Live Stats Polling
  let refreshIntervalMs = 3000;
  let refreshTimer = null;
  let isFetchingStats = false;

  async function fetchLiveStats(playSound = false) {
    if (document.hidden) return;
    if (isFetchingStats) return;
    isFetchingStats = true;
    if (playSound) playLcarsBeep(1400, 900);
    try {
      const resp = await fetch('/api/stats');
      if (resp.ok) {
        const data = await resp.json();
        renderStats(data);
      }
    } catch (e) {
      console.warn("Telemetrie Fetch Fehler:", e);
    } finally {
      isFetchingStats = false;
    }
    if (currentCategory === 'pimmel' && typeof fetchPimmelStats === 'function') {
      fetchPimmelStats(false);
    }
  }

  function setRefreshInterval(ms) {
    refreshIntervalMs = ms;
    const rateEl = document.getElementById('refreshRateDisplay');
    if (rateEl) rateEl.textContent = (ms / 1000) + ' SEKUNDEN';
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => fetchLiveStats(false), refreshIntervalMs);
  }
  refreshTimer = setInterval(() => fetchLiveStats(false), refreshIntervalMs);

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      fetchLiveStats(false);
      if (currentCategory === 'fantasy' && typeof loadFantasyData === 'function') loadFantasyData(false);
      if (currentCategory === 'homeassistant' && typeof loadHomeAssistantData === 'function') loadHomeAssistantData(false);
      if (currentCategory === 'solar' && typeof loadSolarData === 'function') loadSolarData(false);
      if (currentCategory === 'pimmel' && typeof fetchPimmelStats === 'function') fetchPimmelStats(false);
    }
  });

  // ---------------------------------------------------------------------------
  // CHART ENGINE: DUAL CHART.JS + NATIVES LCARS CANVAS (100% GARANTIE)
  // ---------------------------------------------------------------------------
  function ensureChart(cb, attempts = 30) {
    if (typeof Chart !== 'undefined') {
      try { cb(); } catch (e) { console.error('Chart init exception:', e); }
      return;
    }
    if (attempts > 0) {
      setTimeout(() => ensureChart(cb, attempts - 1), 50);
    } else {
      console.warn('Chart.js nicht verfügbar - aktiviere nativen LCARS Canvas Renderer.');
      try { cb(); } catch (e) { console.error('Fallback exception:', e); }
    }
  }

  // 1. Systemverlauf (Line Chart)
  function initHistoryChart() {
    const canvas = document.getElementById('historyChart');
    if (!canvas) return;

    const samples = currentHistorySamples;
    const labels = samples.map(s => s.time || '');
    const temps = samples.map(s => s.temp);
    const cpus = samples.map(s => s.cpu);
    const throttles = samples.map(s => s.throttled ? s.temp : null);
    const rams = samples.map(s => s.ram);

    if (typeof Chart !== 'undefined') {
      try {
        if (historyChart) {
          historyChart.data.labels = labels;
          historyChart.data.datasets[0].data = temps;
          historyChart.data.datasets[1].data = cpus;
          historyChart.data.datasets[2].data = throttles;
          historyChart.data.datasets[3].data = rams;
          historyChart.update('none');
          return;
        }
        const existingChart = Chart.getChart(canvas);
        if (existingChart) {
          try { existingChart.destroy(); } catch (e) {}
        }
        historyChart = new Chart(canvas, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [
              {
                label: 'Temperatur (°C)',
                data: temps,
                borderColor: '#eb943a',
                backgroundColor: 'rgba(235, 148, 58, 0.12)',
                borderWidth: 2,
                tension: 0.25,
                pointRadius: labels.length > 40 ? 2 : 4,
                pointHoverRadius: 6,
                pointBackgroundColor: '#eb943a',
                fill: false,
                yAxisID: 'yTemp'
              },
              {
                label: 'CPU-Auslastung (%)',
                data: cpus,
                borderColor: '#8899ff',
                backgroundColor: 'rgba(136, 153, 255, 0.12)',
                borderWidth: 2,
                tension: 0.25,
                pointRadius: labels.length > 40 ? 2 : 4,
                pointHoverRadius: 6,
                pointBackgroundColor: '#8899ff',
                fill: false,
                yAxisID: 'yPercent'
              },
              {
                label: 'Throttling Aktiv',
                data: throttles,
                borderColor: '#cf4f4f',
                backgroundColor: '#cf4f4f',
                showLine: false,
                pointRadius: 6,
                yAxisID: 'yTemp'
              },
              {
                label: 'RAM (%)',
                data: rams,
                borderColor: '#baa4e5',
                backgroundColor: 'rgba(186, 164, 229, 0.12)',
                borderWidth: 2,
                tension: 0.25,
                pointRadius: labels.length > 40 ? 2 : 4,
                pointHoverRadius: 6,
                pointBackgroundColor: '#baa4e5',
                fill: false,
                hidden: !visibleDatasets[3],
                yAxisID: 'yPercent'
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 350 },
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: '#000',
                borderColor: '#eb943a',
                borderWidth: 1,
                titleFont: { family: 'Antonio', size: 14 },
                bodyFont: { family: 'Share Tech Mono', size: 13 }
              }
            },
            scales: {
              x: {
                grid: { color: 'rgba(255, 255, 255, 0.08)' },
                ticks: { color: '#aaa', font: { family: 'Share Tech Mono' }, maxRotation: 0 }
              },
              yPercent: {
                position: 'left',
                min: 0,
                max: 100,
                grid: { color: 'rgba(255, 255, 255, 0.08)' },
                ticks: { color: '#8899ff', font: { family: 'Share Tech Mono' }, callback: v => v + '%' }
              },
              yTemp: {
                position: 'right',
                min: 20,
                max: 85,
                grid: { drawOnChartArea: false },
                ticks: { color: '#eb943a', font: { family: 'Share Tech Mono' }, callback: v => v + '°C' }
              }
            }
          }
        });

        if (samples.length === 0) {
          fetchHistoryData(activeRange);
        }
        return;
      } catch (e) {
        console.error('Chart.js history init error:', e);
      }
    }

    // Nativer Canvas Fallback
    renderNativeHistoryChart(canvas, samples);
    if (samples.length === 0) {
      fetchHistoryData(activeRange);
    }
  }

  // Nativer HTML5 2D Canvas Fallback für History-Graph
  function renderNativeHistoryChart(canvas, samples) {
    if (!canvas) return;
    const parent = canvas.parentElement;
    const w = parent ? parent.clientWidth : 600;
    const h = parent ? parent.clientHeight : 320;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    if (!samples || samples.length === 0) {
      ctx.fillStyle = '#eb943a';
      ctx.font = '14px "Share Tech Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText('ODN DATENBANK: LADEN...', w / 2, h / 2);
      return;
    }

    const padLeft = 45;
    const padRight = 50;
    const padTop = 20;
    const padBottom = 28;
    const plotW = Math.max(10, w - padLeft - padRight);
    const plotH = Math.max(10, h - padTop - padBottom);

    // Gitterlinien
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padTop + (plotH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(padLeft + plotW, y);
      ctx.stroke();

      const pct = Math.round(100 - i * 25);
      ctx.fillStyle = '#8899ff';
      ctx.font = '11px "Share Tech Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText(pct + '%', padLeft - 6, y + 4);

      const tempVal = Math.round(85 - i * (65 / 4));
      ctx.fillStyle = '#eb943a';
      ctx.textAlign = 'left';
      ctx.fillText(tempVal + '°C', padLeft + plotW + 6, y + 4);
    }

    const n = samples.length;
    const getX = (idx) => padLeft + (n > 1 ? (idx / (n - 1)) * plotW : plotW / 2);
    const getYPercent = (val) => padTop + plotH - ((val || 0) / 100) * plotH;
    const getYTemp = (val) => padTop + plotH - (((val || 20) - 20) / 65) * plotH;

    // Zeitstempel unten
    ctx.fillStyle = '#888';
    ctx.font = '10px "Share Tech Mono", monospace';
    ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) {
      if (samples[i] && samples[i].time) {
        ctx.fillText(samples[i].time, getX(i), h - 8);
      }
    }

    // Dataset 0: Temperatur (Orange)
    if (visibleDatasets[0]) {
      ctx.beginPath();
      ctx.strokeStyle = '#eb943a';
      ctx.lineWidth = 2.5;
      samples.forEach((s, i) => {
        const x = getX(i);
        const y = getYTemp(s.temp);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.fillStyle = '#eb943a';
      samples.forEach((s, i) => {
        ctx.beginPath();
        ctx.arc(getX(i), getYTemp(s.temp), 2.5, 0, Math.PI * 2);
        ctx.fill();
      });
    }

    // Dataset 1: CPU (Blau)
    if (visibleDatasets[1]) {
      ctx.beginPath();
      ctx.strokeStyle = '#8899ff';
      ctx.lineWidth = 2;
      samples.forEach((s, i) => {
        const x = getX(i);
        const y = getYPercent(s.cpu);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.fillStyle = '#8899ff';
      samples.forEach((s, i) => {
        ctx.beginPath();
        ctx.arc(getX(i), getYPercent(s.cpu), 2.5, 0, Math.PI * 2);
        ctx.fill();
      });
    }

    // Dataset 2: Throttling (Rot)
    if (visibleDatasets[2]) {
      ctx.fillStyle = '#cf4f4f';
      samples.forEach((s, i) => {
        if (s.throttled) {
          ctx.beginPath();
          ctx.arc(getX(i), getYTemp(s.temp), 5, 0, Math.PI * 2);
          ctx.fill();
        }
      });
    }

    // Dataset 3: RAM (Lila)
    if (visibleDatasets[3]) {
      ctx.beginPath();
      ctx.strokeStyle = '#baa4e5';
      ctx.lineWidth = 2;
      samples.forEach((s, i) => {
        const x = getX(i);
        const y = getYPercent(s.ram);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
  }

  async function fetchHistoryData(range) {
    try {
      const resp = await fetch(`/api/history?range=${range}`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data.samples) return;

      currentHistorySamples = data.samples;
      const labels = data.samples.map(s => s.time);
      const temps = data.samples.map(s => s.temp);
      const cpus = data.samples.map(s => s.cpu);
      const throttles = data.samples.map(s => s.throttled ? s.temp : null);
      const rams = data.samples.map(s => s.ram);

      if (historyChart) {
        historyChart.data.labels = labels;
        historyChart.data.datasets[0].data = temps;
        historyChart.data.datasets[1].data = cpus;
        historyChart.data.datasets[2].data = throttles;
        historyChart.data.datasets[3].data = rams;
        historyChart.update();
      } else {
        const canvas = document.getElementById('historyChart');
        if (canvas) renderNativeHistoryChart(canvas, data.samples);
      }
    } catch (e) {
      console.warn("History Fetch Fehler:", e);
    }
  }

  function setChartRange(range, btnElem) {
    playLcarsBeep(1100, 1600);
    activeRange = range;
    document.querySelectorAll('.range-btn').forEach(btn => btn.classList.remove('active-range'));
    if (btnElem) {
      btnElem.classList.add('active-range');
    }
    fetchHistoryData(range);
  }

  function toggleDataset(idx) {
    visibleDatasets[idx] = !visibleDatasets[idx];
    playLcarsBeep(880, 1320);

    const btn = document.getElementById('dsBtn' + idx);
    if (btn) {
      btn.style.opacity = visibleDatasets[idx] ? '1' : '0.35';
    }

    if (historyChart) {
      historyChart.setDatasetVisibility(idx, visibleDatasets[idx]);
      historyChart.update();
    } else {
      const canvas = document.getElementById('historyChart');
      if (canvas) renderNativeHistoryChart(canvas, currentHistorySamples);
    }
  }

  // ---------------------------------------------------------------------------
  // 9ROUTER TELEMETRIE, CHARTS & TABELLEN ENGINE
  // ---------------------------------------------------------------------------
  function renderNineRouterStats(nrData) {
    if (!nrData) return;
    currentNineRouterData = nrData;

    // Totals & Metriken
    const totals = nrData.totals || {};
    const reqEl = document.getElementById('nrTotalRequests');
    if (reqEl) reqEl.textContent = totals.requests || 0;

    const tokEl = document.getElementById('nrTotalTokens');
    if (tokEl) tokEl.textContent = totals.tokens_formatted || (totals.total_tokens ? Number(totals.total_tokens).toLocaleString() : '0');

    const promptComplEl = document.getElementById('nrPromptComplTokens');
    if (promptComplEl) {
      promptComplEl.textContent = `Prompt: ${totals.prompt_formatted || totals.prompt_tokens || '0'} | Compl: ${totals.completion_formatted || totals.completion_tokens || '0'}`;
    }

    const cachedEl = document.getElementById('nrCachedTokens');
    if (cachedEl) {
      const rateStr = totals.cache_hit_rate ? ` (${totals.cache_hit_rate}% Quote)` : '';
      cachedEl.textContent = `Cached: ${totals.cached_formatted || totals.cached_tokens || '0'}${rateStr}`;
    }

    const savingsRateEl = document.getElementById('nrSavingsRate');
    if (savingsRateEl) {
      savingsRateEl.textContent = totals.cache_hit_rate_formatted || (totals.cache_hit_rate ? `${totals.cache_hit_rate}%` : '0.0%');
    }

    const savedTokensEl = document.getElementById('nrSavedTokens');
    if (savedTokensEl) {
      savedTokensEl.textContent = `Tokens gespart: ${totals.saved_tokens_formatted || totals.saved_tokens || '0'}`;
    }

    const savedCostEl = document.getElementById('nrSavedCost');
    if (savedCostEl) {
      const pct = totals.saved_cost_pct ? ` (-${totals.saved_cost_pct}%)` : '';
      savedCostEl.textContent = `Kosten gespart: ~${totals.saved_cost_formatted || '$0.00'}${pct}`;
    }

    const costEl = document.getElementById('nrTotalCost');
    if (costEl) costEl.textContent = totals.cost_formatted || `$${Number(totals.cost || 0).toFixed(4)}`;

    const uncachedCostEl = document.getElementById('nrUncachedCost');
    if (uncachedCostEl) {
      uncachedCostEl.textContent = `Ohne Cache: ~${totals.uncached_cost_formatted || '$0.00'}`;
    }

    const costSavingsSubEl = document.getElementById('nrCostSavingsSub');
    if (costSavingsSubEl) {
      costSavingsSubEl.textContent = `Ersparnis: ~${totals.saved_cost_formatted || '$0.00'}`;
    }

    const connCountEl = document.getElementById('nrActiveConnCount');
    if (connCountEl) {
      connCountEl.textContent = `${(nrData.connections || []).length}`;
    }

    const provListEl = document.getElementById('nrConnProvidersList');
    if (provListEl && nrData.connections && nrData.connections.length > 0) {
      provListEl.textContent = nrData.connections.map(c => (c.provider ? c.provider.toUpperCase() : '')).join(' • ');
    }

    // Only do heavy DOM table/list updates and chart updates if 'ai-info' or 'agents' is visible
    if (currentCategory !== 'ai-info' && currentCategory !== 'agents') {
      return;
    }

    // Provider Connections List Rendering with dirty-checking
    const connList = document.getElementById('nineRouterConnectionsList');
    if (connList && nrData.connections) {
      const connFp = nrData.connections.map(c => `${c.provider}:${c.is_active}:${c.priority}`).join('|');
      if (connFp !== lastNrConnFingerprint) {
        lastNrConnFingerprint = connFp;
        if (nrData.connections.length === 0) {
          connList.innerHTML = '<div style="color:#888; font-size:0.85rem; padding:0.5rem;">Keine Provider-Verbindungen konfiguriert.</div>';
        } else {
          let cHtml = '';
          nrData.connections.forEach(conn => {
            const badgeCls = conn.is_active ? 'badge-online' : 'badge-offline';
            const badgeTxt = conn.is_active ? 'AKTIV // VERBUNDEN' : 'INAKTIV';
            cHtml += `
              <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.5); padding:0.6rem 0.8rem; border-left:4px solid var(--c-primary); border-radius:6px; flex-wrap:wrap; gap:0.5rem;">
                <div>
                  <div style="font-weight:700; color:var(--c-primary); font-size:0.95rem;">${escapeHtml(conn.provider.toUpperCase())} // ${escapeHtml(conn.name || conn.provider)}</div>
                  <div style="font-family:var(--mono-family); font-size:0.78rem; color:#888;">Auth: ${escapeHtml((conn.auth_type || '').toUpperCase())} | Prio: ${conn.priority} ${conn.email ? '| ' + escapeHtml(conn.email) : ''}</div>
                </div>
                <div>
                  <span class="badge-status ${badgeCls}">${badgeTxt}</span>
                </div>
              </div>
            `;
          });
          connList.innerHTML = cHtml;
        }
      }
    }

    // Recent History Table Rendering with dirty-checking
    const tbody = document.getElementById('nineRouterHistoryTableBody');
    if (tbody && nrData.recent_history) {
      const histFp = (nrData.recent_history || []).map(r => `${r.id}:${r.status}:${r.total_tokens}`).join('|');
      if (histFp !== lastNrHistoryFingerprint) {
        lastNrHistoryFingerprint = histFp;
        if (nrData.recent_history.length === 0) {
          tbody.innerHTML = '<tr><td colspan="10" style="padding:0.8rem; text-align:center; color:#888;">Keine Transaktionen aufgezeichnet.</td></tr>';
        } else {
          let hHtml = '';
          nrData.recent_history.forEach(r => {
            const badgeClass = (r.status === 'ok') ? 'badge-online' : 'badge-offline';
            hHtml += `
              <tr style="border-bottom:1px solid rgba(255,255,255,0.08); font-family:var(--mono-family);">
                <td style="padding:0.45rem 0.4rem; color:var(--c-gold);">#${r.id}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:#ccc;">${escapeHtml(r.time_display)}</td>
                <td style="padding:0.45rem 0.4rem;"><span style="color:var(--c-blue); font-weight:700;">${escapeHtml((r.provider || '').toUpperCase())}</span></td>
                <td style="padding:0.45rem 0.4rem; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(r.model)}">${escapeHtml(r.model)}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap;">${Number(r.prompt_tokens || 0).toLocaleString()}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap;">${Number(r.completion_tokens || 0).toLocaleString()}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:var(--c-blue);">${Number(r.cached_tokens || 0).toLocaleString()}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap; font-weight:700; color:var(--c-primary);">${Number(r.total_tokens || 0).toLocaleString()}</td>
                <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:var(--c-gold);">${escapeHtml(r.cost_formatted || ('$' + Number(r.cost || 0).toFixed(4)))}</td>
                <td style="padding:0.45rem 0.4rem;">
                  <span class="badge-status ${badgeClass}" style="padding:0.15rem 0.4rem; font-size:0.75rem;">
                    ${escapeHtml((r.status || 'OK').toUpperCase())}
                  </span>
                </td>
              </tr>
            `;
          });
          tbody.innerHTML = hHtml;
        }
      }
    }

    // Charts nur im aktiven Tab 'ai-info' aktualisieren
    if (currentCategory === 'ai-info' && (nineRouterModelChart || nineRouterTimelineChart)) {
      initNineRouterCharts(nrData);
    }
  }

  async function fetchNineRouterStats(playSound = false) {
    if (playSound) playLcarsBeep(1400, 900);
    try {
      const resp = await fetch('/api/9router/stats');
      if (resp.ok) {
        const data = await resp.json();
        renderNineRouterStats(data);
        initNineRouterCharts(data);
      }
    } catch (e) {
      console.warn("9Router Fetch Fehler:", e);
    }
  }

  function initNineRouterCharts(nrData) {
    if (nrData) currentNineRouterData = nrData;
    const data = currentNineRouterData || initialStats?.nine_router;
    if (!data) return;

    initNineRouterModelChart(data);
    initNineRouterTimelineChart(data);
  }

  function initNineRouterModelChart(data) {
    const canvas = document.getElementById('nineRouterModelChart');
    if (!canvas) return;

    const modelsObj = data?.by_model || {};
    let labels = Object.keys(modelsObj);
    let values = labels.map(k => (modelsObj[k].promptTokens || 0) + (modelsObj[k].completionTokens || 0));
    const palette = ['#eb943a', '#baa4e5', '#8899ff', '#faad44', '#10b981', '#cf4f4f', '#06b6d4'];

    if (!labels.length || values.every(v => v === 0)) {
      labels = ['Keine Daten'];
      values = [1];
    }

    if (typeof Chart !== 'undefined') {
      try {
        if (nineRouterModelChart) {
          nineRouterModelChart.data.labels = labels;
          nineRouterModelChart.data.datasets[0].data = values;
          nineRouterModelChart.data.datasets[0].backgroundColor = palette.slice(0, labels.length);
          nineRouterModelChart.update('none');
          return;
        }
        const existing = Chart.getChart(canvas);
        if (existing) {
          try { existing.destroy(); } catch (e) {}
        }
        nineRouterModelChart = new Chart(canvas, {
          type: 'doughnut',
          data: {
            labels: labels,
            datasets: [{
              data: values,
              backgroundColor: palette.slice(0, labels.length),
              borderColor: '#000000',
              borderWidth: 2
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '68%',
            animation: { duration: 350 },
            plugins: {
              legend: {
                display: true,
                position: 'bottom',
                labels: {
                  color: '#ccc',
                  font: { family: 'Share Tech Mono', size: 11 },
                  boxWidth: 12
                }
              },
              tooltip: {
                backgroundColor: '#000',
                borderColor: '#eb943a',
                borderWidth: 1,
                titleFont: { family: 'Antonio', size: 14 },
                bodyFont: { family: 'Share Tech Mono', size: 12 },
                callbacks: {
                  label: function(ctx) {
                    const val = ctx.raw || 0;
                    return ` ${ctx.label}: ${val.toLocaleString()} Tokens`;
                  }
                }
              }
            }
          }
        });
        return;
      } catch (e) {
        console.error('9Router Model Chart error:', e);
      }
    }
  }

  function initNineRouterTimelineChart(data) {
    const canvas = document.getElementById('nineRouterTimelineChart');
    if (!canvas) return;

    const history = (data?.recent_history || []).slice().reverse();
    const labels = history.map(r => {
      if (r.time_display) {
        const parts = r.time_display.split(' ');
        return parts.length > 1 ? parts[1] : parts[0];
      }
      return '#' + r.id;
    });

    const promptToks = history.map(r => r.prompt_tokens || 0);
    const cachedToks = history.map(r => r.cached_tokens || 0);
    const complToks = history.map(r => r.completion_tokens || 0);
    const costs = history.map(r => r.cost || 0);

    if (typeof Chart !== 'undefined') {
      try {
        if (nineRouterTimelineChart) {
          nineRouterTimelineChart.data.labels = labels.length ? labels : ['Keine Daten'];
          nineRouterTimelineChart.data.datasets[0].data = promptToks.length ? promptToks : [0];
          nineRouterTimelineChart.data.datasets[1].data = cachedToks.length ? cachedToks : [0];
          nineRouterTimelineChart.data.datasets[2].data = complToks.length ? complToks : [0];
          nineRouterTimelineChart.data.datasets[3].data = costs.length ? costs : [0];
          nineRouterTimelineChart.update('none');
          return;
        }
        const existing = Chart.getChart(canvas);
        if (existing) {
          try { existing.destroy(); } catch (e) {}
        }
        nineRouterTimelineChart = new Chart(canvas, {
          type: 'bar',
          data: {
            labels: labels.length ? labels : ['Keine Daten'],
            datasets: [
              {
                label: 'Prompt Tokens',
                data: promptToks.length ? promptToks : [0],
                backgroundColor: 'rgba(235, 148, 58, 0.8)',
                borderColor: '#eb943a',
                borderWidth: 1,
                stack: 'tokens',
                yAxisID: 'y'
              },
              {
                label: 'Cached Tokens',
                data: cachedToks.length ? cachedToks : [0],
                backgroundColor: 'rgba(16, 185, 129, 0.8)',
                borderColor: '#10b981',
                borderWidth: 1,
                stack: 'tokens',
                yAxisID: 'y'
              },
              {
                label: 'Completion Tokens',
                data: complToks.length ? complToks : [0],
                backgroundColor: 'rgba(136, 153, 255, 0.8)',
                borderColor: '#8899ff',
                borderWidth: 1,
                stack: 'tokens',
                yAxisID: 'y'
              },
              {
                label: 'Kosten ($)',
                type: 'line',
                data: costs.length ? costs : [0],
                borderColor: '#faad44',
                backgroundColor: '#faad44',
                borderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 6,
                tension: 0.2,
                yAxisID: 'yCost'
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 350 },
            plugins: {
              legend: {
                display: true,
                position: 'top',
                labels: {
                  color: '#ccc',
                  font: { family: 'Share Tech Mono', size: 11 },
                  boxWidth: 12
                }
              },
              tooltip: {
                backgroundColor: '#000',
                borderColor: '#eb943a',
                borderWidth: 1,
                titleFont: { family: 'Antonio', size: 14 },
                bodyFont: { family: 'Share Tech Mono', size: 12 },
                callbacks: {
                  label: function(ctx) {
                    if (ctx.dataset.yAxisID === 'yCost') {
                      return ` Kosten: $${Number(ctx.raw || 0).toFixed(4)}`;
                    }
                    return ` ${ctx.dataset.label}: ${Number(ctx.raw || 0).toLocaleString()} Tokens`;
                  }
                }
              }
            },
            scales: {
              x: {
                stacked: true,
                grid: { color: 'rgba(255, 255, 255, 0.07)' },
                ticks: { color: '#aaa', font: { family: 'Share Tech Mono', size: 11 } }
              },
              y: {
                stacked: true,
                grid: { color: 'rgba(255, 255, 255, 0.07)' },
                ticks: {
                  color: '#aaa',
                  font: { family: 'Share Tech Mono', size: 11 },
                  callback: function(val) {
                    if (val >= 1000000) return (val / 1000000).toFixed(1) + 'M';
                    if (val >= 1000) return (val / 1000).toFixed(0) + 'k';
                    return val;
                  }
                }
              },
              yCost: {
                position: 'right',
                grid: { drawOnChartArea: false },
                ticks: {
                  color: '#faad44',
                  font: { family: 'Share Tech Mono', size: 11 },
                  callback: function(val) {
                    return '$' + Number(val).toFixed(4);
                  }
                }
              }
            }
          }
        });
        return;
      } catch (e) {
        console.error('9Router Timeline Chart error:', e);
      }
    }
  }

  // 2. Hermes Donut Chart (Modell Token-Verteilung)
  function initHermesChart() {
    const canvas = document.getElementById('hermesChart');
    if (!canvas) return;

    const models = initialStats?.hermes?.models || [];
    let labels = models.map(m => m.model);
    let data = models.map(m => m.total_tokens);
    const palette = ['#eb943a', '#baa4e5', '#8899ff', '#ea9c72', '#edb378', '#cf4f4f', '#10b981'];

    if (!data.length || data.every(v => v === 0)) {
      labels = ['Keine Daten'];
      data = [1];
    }

    if (typeof Chart !== 'undefined') {
      try {
        if (hermesChart) {
          hermesChart.data.labels = labels;
          hermesChart.data.datasets[0].data = data;
          hermesChart.data.datasets[0].backgroundColor = palette.slice(0, labels.length);
          hermesChart.update('none');
          return;
        }
        const existingChart = Chart.getChart(canvas);
        if (existingChart) {
          try { existingChart.destroy(); } catch (e) {}
        }
        hermesChart = new Chart(canvas, {
          type: 'doughnut',
          data: {
            labels: labels,
            datasets: [{
              data: data,
              backgroundColor: palette.slice(0, labels.length),
              borderColor: '#000000',
              borderWidth: 2
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '68%',
            animation: { duration: 350 },
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: '#000',
                borderColor: '#baa4e5',
                borderWidth: 1,
                titleFont: { family: 'Antonio', size: 14 },
                bodyFont: { family: 'Share Tech Mono', size: 12 }
              }
            }
          }
        });
        return;
      } catch (e) {
        console.error('Hermes Chart.js error:', e);
      }
    }

    // Nativer Canvas Fallback
    renderNativeDonutChart(canvas, models);
  }

  function renderNativeDonutChart(canvas, models) {
    if (!canvas) return;
    const parent = canvas.parentElement;
    const size = Math.min(parent ? parent.clientWidth : 260, parent ? parent.clientHeight : 260) || 260;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, size, size);

    const cx = size / 2;
    const cy = size / 2;
    const outerR = size * 0.46;
    const innerR = size * 0.32;

    const palette = ['#eb943a', '#baa4e5', '#8899ff', '#ea9c72', '#edb378', '#cf4f4f', '#10b981'];
    const total = (models || []).reduce((acc, m) => acc + (m.total_tokens || 0), 0) || 1;

    let startAngle = -Math.PI / 2;
    if (models && models.length > 0) {
      models.forEach((m, idx) => {
        const slice = ((m.total_tokens || 0) / total) * Math.PI * 2;
        if (slice <= 0) return;
        const endAngle = startAngle + slice;
        ctx.beginPath();
        ctx.arc(cx, cy, outerR, startAngle, endAngle);
        ctx.arc(cx, cy, innerR, endAngle, startAngle, true);
        ctx.closePath();
        ctx.fillStyle = palette[idx % palette.length];
        ctx.fill();
        ctx.strokeStyle = '#000000';
        ctx.lineWidth = 2;
        ctx.stroke();
        startAngle = endAngle;
      });
    } else {
      ctx.beginPath();
      ctx.arc(cx, cy, outerR, 0, Math.PI * 2);
      ctx.arc(cx, cy, innerR, Math.PI * 2, 0, true);
      ctx.closePath();
      ctx.fillStyle = '#333333';
      ctx.fill();
    }

    // Zentrierte Beschriftung
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 15px "Antonio", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('TOKENS', cx, cy - 8);

    ctx.fillStyle = '#eb943a';
    ctx.font = '12px "Share Tech Mono", monospace';
    const totalStr = total > 1000000 ? (total / 1000000).toFixed(1) + 'M' : (total / 1000).toFixed(0) + 'k';
    ctx.fillText(totalStr, cx, cy + 10);
  }

  // ---------------------------------------------------------------------------
  // LCARS KI-AGENTEN CHAT ENGINE
  // ---------------------------------------------------------------------------
  let chatHistory = [];
  let chatModels = ['ag/gemini-3-flash', 'openrouter/openrouter/free'];
  let currentChatModel = localStorage.getItem('lcars-chat-model') || 'ag/gemini-3-flash';
  let isChatGenerating = false;

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatTimeNow() {
    const d = new Date();
    return String(d.getHours()).padStart(2, '0') + ':' +
           String(d.getMinutes()).padStart(2, '0') + ':' +
           String(d.getSeconds()).padStart(2, '0');
  }

  function formatMessageText(text) {
    if (!text) return '';
    const codeBlocks = [];
    let processed = text.replace(/```([a-zA-Z0-9_-]*)\\n?([\\s\\S]*?)```/g, (match, lang, code) => {
      const idx = codeBlocks.length;
      codeBlocks.push(`<pre class="lcars-code-block"><code>${escapeHtml(code.trim())}</code></pre>`);
      return `###CODEBLOCK_${idx}###`;
    });

    processed = escapeHtml(processed);

    processed = processed.replace(/`([^`]+)`/g, (match, code) => {
      return `<code class="lcars-inline-code">${code}</code>`;
    });

    processed = processed.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');

    codeBlocks.forEach((block, idx) => {
      processed = processed.replace(`###CODEBLOCK_${idx}###`, block);
    });

    return processed;
  }

  function appendChatMessage(role, content, modelName = null, isError = false) {
    const log = document.getElementById('lcarsChatLog');
    if (!log) return;

    const msgDiv = document.createElement('div');
    const timeStr = formatTimeNow();

    let cssClass = 'lcars-msg ';
    let senderLabel = '';

    if (isError) {
      cssClass += 'lcars-msg-error';
      senderLabel = '⚠️ SYSTEM // WARNUNG';
    } else if (role === 'user') {
      cssClass += 'lcars-msg-user';
      senderLabel = '▶ USER // ODN-TERMINAL';
    } else {
      cssClass += 'lcars-msg-agent';
      senderLabel = `▶ AGENT // ${escapeHtml(modelName || currentChatModel)}`;
    }

    msgDiv.className = cssClass;
    msgDiv.innerHTML = `
      <div class="lcars-msg-header">
        <span class="lcars-msg-sender">${senderLabel}</span>
        <span class="lcars-msg-time">${timeStr}</span>
      </div>
      <div class="lcars-msg-body">${formatMessageText(content)}</div>
    `;

    log.appendChild(msgDiv);
    log.scrollTop = log.scrollHeight;
  }

  async function loadChatModels(playSound = false) {
    if (playSound) playLcarsBeep(1200, 1800);
    try {
      const resp = await fetch('/api/chat/models');
      if (resp.ok) {
        const data = await resp.json();
        let models = [];
        if (Array.isArray(data)) {
          models = data;
        } else if (Array.isArray(data.models)) {
          models = data.models;
        } else if (Array.isArray(data.data)) {
          models = data.data.map(m => typeof m === 'string' ? m : (m.id || m.name));
        }

        if (models && models.length > 0) {
          chatModels = models;
          const select = document.getElementById('chatModelSelect');
          if (select) {
            const saved = localStorage.getItem('lcars-chat-model') || currentChatModel;
            select.innerHTML = '';
            models.forEach(m => {
              const opt = document.createElement('option');
              opt.value = m;
              opt.textContent = m;
              if (m === saved) opt.selected = true;
              select.appendChild(opt);
            });
            if (select.value) {
              currentChatModel = select.value;
            }
          }
        }

        const badge = document.getElementById('chatProxyStatusBadge');
        const text = document.getElementById('chatProxyStatusText');
        if (badge && text) {
          if (data.status === 'online' || !data.status) {
            badge.className = 'badge-status badge-online';
            text.textContent = 'PROXY BEREIT';
          } else {
            badge.className = 'badge-status badge-warn';
            text.textContent = 'FALLBACK MODUS';
          }
        }
      }
    } catch (e) {
      console.warn("Konnte KI-Modelle nicht abrufen:", e);
      const badge = document.getElementById('chatProxyStatusBadge');
      const text = document.getElementById('chatProxyStatusText');
      if (badge && text) {
        badge.className = 'badge-status badge-offline';
        text.textContent = 'OFFLINE';
      }
    }
  }

  function onChatModelChange() {
    playLcarsBeep(980, 1400);
    const select = document.getElementById('chatModelSelect');
    if (select) {
      currentChatModel = select.value;
      localStorage.setItem('lcars-chat-model', currentChatModel);
      const meta = document.getElementById('chatMetaStatus');
      if (meta) meta.textContent = `MODELL GEWÄHLT: ${currentChatModel}`;
    }
  }

  function clearChatHistory() {
    playLcarsBeep(440, 220);
    chatHistory = [];
    const log = document.getElementById('lcarsChatLog');
    if (log) {
      const timeStr = formatTimeNow();
      log.innerHTML = `
        <div class="lcars-msg lcars-msg-agent">
          <div class="lcars-msg-header">
            <span class="lcars-msg-sender">▶ AGENT // SUBRAUM-COMM</span>
            <span class="lcars-msg-time">${timeStr}</span>
          </div>
          <div class="lcars-msg-body">Kanal zurückgesetzt. Neuer Dialog initialisiert. Bereit für neue Befehle.</div>
        </div>
      `;
    }
    const meta = document.getElementById('chatMetaStatus');
    if (meta) meta.textContent = 'DIALOG ZURÜCKGESETZT // BEREIT';
  }

  function sendQuickPrompt(promptText) {
    const input = document.getElementById('lcarsChatInput');
    if (input) {
      input.value = promptText;
      handleChatSubmit(null);
    }
  }

  async function handleChatSubmit(event, fromVoice = false) {
    if (event) event.preventDefault();
    if (isChatGenerating) return;

    const input = document.getElementById('lcarsChatInput');
    const sendBtn = document.getElementById('lcarsChatSendBtn');
    const sendLabel = document.getElementById('lcarsChatSendLabel');
    const loading = document.getElementById('lcarsChatLoading');
    const meta = document.getElementById('chatMetaStatus');

    if (!input) return;
    const messageText = input.value.trim();
    if (!messageText) return;

    playLcarsBeep(1200, 1600);

    chatHistory.push({ role: 'user', content: messageText });
    appendChatMessage('user', messageText);
    input.value = '';

    isChatGenerating = true;
    input.disabled = true;
    if (sendBtn) sendBtn.disabled = true;
    if (sendLabel) sendLabel.textContent = 'TRANSMITTING...';
    if (loading) loading.style.display = 'flex';
    if (meta) meta.textContent = `TRANSMISSION IN BEARBEITUNG (${currentChatModel})...`;
    if (fromVoice) setCommBadgeState('computing');

    let messagesToSend = [...chatHistory];
    if (fromVoice || isVoiceLastInput) {
      messagesToSend = [
        {
          role: 'system',
          content: 'Du bist der LCARS Hauptcomputer eines Sternenflotten-Raumschiffs. Antworte auf Deutsch, präzise, sachlich und ruhig im Star Trek Computer-Stil. Halte deine Antwort auf maximal 2 bis 3 Sätze beschränkt, ohne Markdown-Formatierungen, da deine Antwort direkt über die Sprachausgabe vorgelesen wird.'
        },
        ...chatHistory
      ];
    }

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: currentChatModel,
          messages: messagesToSend
        })
      });

      const data = await resp.json();

      if (resp.ok) {
        const assistantText = data.content ||
                              (data.message && data.message.content) ||
                              (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) ||
                              'Keine Antwort vom Agenten erhalten.';

        chatHistory.push({ role: 'assistant', content: assistantText });
        appendChatMessage('assistant', assistantText, data.model || currentChatModel);
        playLcarsAcknowledge();

        if (meta) {
          const toks = data.usage ? ` [Tokens: ${data.usage.total_tokens || 0}]` : '';
          meta.textContent = `TRANSMISSION EMPFANGEN // MODELL: ${data.model || currentChatModel}${toks}`;
        }

        if (fromVoice || (voiceOutputEnabled && isVoiceLastInput)) {
          speakLcarsText(assistantText);
        } else {
          setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
        }
      } else {
        const errorMsg = data.error || (data.message && data.message.content) || 'Unbekannter Fehler bei Kommunikation mit KI-Proxy.';
        appendChatMessage('assistant', errorMsg, currentChatModel, true);
        playLcarsError();
        if (meta) meta.textContent = `TRANSMISSIONSFEHLER (${resp.status})`;
        if (fromVoice) speakLcarsText('Fehler bei Übertragung an Subraum-Relay.');
        setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
      }
    } catch (e) {
      appendChatMessage('assistant', `Netzwerkfehler: Verbindung zum Backend fehlgeschlagen (${e.message})`, currentChatModel, true);
      playLcarsError();
      if (meta) meta.textContent = 'NETZWERKFEHLER BEI TRANSMISSION';
      if (fromVoice) speakLcarsText('Netzwerkfehler bei Subraum-Transceiver.');
      setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
    } finally {
      isChatGenerating = false;
      input.disabled = false;
      if (sendBtn) sendBtn.disabled = false;
      if (sendLabel) sendLabel.textContent = 'TRANSMIT';
      if (loading) loading.style.display = 'none';
      input.focus();
    }
  }

  // ==========================================================================
  // LCARS STAR TREK VOICE COMM-LINK ENGINE (STT, TTS, COMMANDS & VISUALIZER)
  // ==========================================================================
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let voiceRecognition = null;
  let isListening = false;
  let isVoiceLastInput = false;
  let voiceVizInterval = null;

  let voiceInputEnabled = (localStorage.getItem('lcars-voice-in') !== 'false');
  let voiceOutputEnabled = (localStorage.getItem('lcars-voice-out') !== 'false');
  let wakeWordActive = (localStorage.getItem('lcars-wakeword') === 'true');

  function initLcarsVoiceComm() {
    updateVoiceUI();
    populateVoices();
    if (wakeWordActive && voiceInputEnabled && SpeechRecognition) {
      setTimeout(() => {
        startContinuousWakeWord();
      }, 1200);
    }
  }

  function setCommBadgeState(state) {
    // state: 'idle' | 'listening' | 'speaking' | 'computing' | 'passive-listen'
    const topBadge = document.getElementById('topCommBadge');
    const topText = document.getElementById('topCommText');
    const topIcon = document.getElementById('topCommIcon');
    const chatBtn = document.getElementById('lcarsChatMicBtn');
    const chatLabel = document.getElementById('lcarsChatMicLabel');
    const hermesBtn = document.getElementById('hermesChatMicBtn');

    [topBadge, chatBtn, hermesBtn].forEach(el => {
      if (el) {
        el.classList.remove('listening', 'speaking', 'computing', 'passive-listen');
        if (state !== 'idle') el.classList.add(state);
      }
    });

    if (topText && topIcon) {
      if (state === 'listening') {
        topIcon.textContent = '🔴';
        topText.textContent = 'HÖRE ZU...';
      } else if (state === 'speaking') {
        topIcon.textContent = '🔊';
        topText.textContent = 'TRANSMITTING';
      } else if (state === 'computing') {
        topIcon.textContent = '⚙️';
        topText.textContent = 'COMPUTING...';
      } else if (state === 'passive-listen') {
        topIcon.textContent = '👂';
        topText.textContent = "WAKE: 'COMPUTER'";
      } else {
        topIcon.textContent = '🎙️';
        topText.textContent = 'COMM: BEREIT';
      }
    }

    if (chatLabel) {
      if (state === 'listening') chatLabel.textContent = 'HÖRE...';
      else if (state === 'speaking') chatLabel.textContent = 'AUDIO';
      else if (state === 'computing') chatLabel.textContent = 'WAIT...';
      else chatLabel.textContent = 'COMM';
    }
  }

  function startVoiceVisualizer(mode = 'listening') {
    stopVoiceVisualizer();
    const hud = document.getElementById('lcarsVoiceHud');
    if (hud) hud.style.display = 'flex';
    const bars = document.querySelectorAll('.lcars-voice-bar-col');
    if (!bars.length) return;

    voiceVizInterval = setInterval(() => {
      bars.forEach(bar => {
        const factor = mode === 'listening' ? 0.75 : 0.9;
        const randomH = Math.floor(Math.random() * 16 * factor) + 4;
        bar.style.height = randomH + 'px';
        if (mode === 'speaking') {
          bar.style.backgroundColor = 'var(--c-blue)';
        } else if (mode === 'listening') {
          bar.style.backgroundColor = '#ff5577';
        } else {
          bar.style.backgroundColor = 'var(--c-gold)';
        }
      });
    }, 85);
  }

  function stopVoiceVisualizer() {
    if (voiceVizInterval) {
      clearInterval(voiceVizInterval);
      voiceVizInterval = null;
    }
    const hud = document.getElementById('lcarsVoiceHud');
    if (hud && !isListening) hud.style.display = 'none';
    const bars = document.querySelectorAll('.lcars-voice-bar-col');
    bars.forEach(bar => {
      bar.style.height = '4px';
      bar.style.backgroundColor = 'var(--c-gold)';
    });
  }

  function populateVoices() {
    if (!('speechSynthesis' in window)) return;
    const voices = window.speechSynthesis.getVoices();
    const select = document.getElementById('cfgVoiceSelect');
    if (!select || !voices.length) return;

    select.innerHTML = '';
    const saved = localStorage.getItem('lcars-voice-name') || '';

    const sorted = [...voices].sort((a, b) => {
      const aDe = a.lang.startsWith('de') ? 0 : 1;
      const bDe = b.lang.startsWith('de') ? 0 : 1;
      if (aDe !== bDe) return aDe - bDe;
      return a.name.localeCompare(b.name);
    });

    sorted.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.name;
      opt.textContent = `${v.name} [${v.lang}]`;
      if (v.name === saved) opt.selected = true;
      select.appendChild(opt);
    });
  }

  if ('speechSynthesis' in window) {
    speechSynthesis.onvoiceschanged = populateVoices;
  }

  function getPreferredGermanVoice() {
    if (!('speechSynthesis' in window)) return null;
    const voices = window.speechSynthesis.getVoices();
    const saved = localStorage.getItem('lcars-voice-name');
    if (saved) {
      const match = voices.find(v => v.name === saved);
      if (match) return match;
    }
    const deVoices = voices.filter(v => v.lang.startsWith('de'));
    return deVoices.find(v => v.name.includes('Google') || v.name.includes('Katja') || v.name.includes('Hedda') || v.name.includes('Natural') || v.name.includes('Neural') || v.name.includes('Amira') || v.name.includes('Marlena')) ||
           deVoices.find(v => v.name.toLowerCase().includes('female') || v.name.toLowerCase().includes('weiblich')) ||
           deVoices[0] ||
           voices[0] || null;
  }

  function speakLcarsText(rawText, onComplete = null) {
    if (!voiceOutputEnabled) {
      if (onComplete) onComplete();
      return;
    }
    if (!('speechSynthesis' in window)) {
      if (onComplete) onComplete();
      return;
    }

    try {
      window.speechSynthesis.cancel();

      let clean = rawText.replace(/```[\\s\\S]*?```/g, 'Codeblock im Hauptfenster.');
      clean = clean.replace(/[*_`#]/g, '');
      clean = clean.replace(/\\[([^\\]]+)\\]\\([^)]+\\)/g, '$1');
      clean = clean.replace(/https?:\\/\\/\\S+/g, 'Link');
      clean = clean.replace(/%/g, ' Prozent');
      clean = clean.replace(/°C?/g, ' Grad');
      clean = clean.replace(/\\b([0-9]+)\\s*W\\b/g, '$1 Watt');
      clean = clean.replace(/\\b([0-9]+)\\s*kW\\b/g, '$1 Kilowatt');
      clean = clean.replace(/\\b([0-9]+)\\s*Wh\\b/g, '$1 Wattstunden');
      clean = clean.replace(/\\b([0-9]+)\\s*kWh\\b/g, '$1 Kilowattstunden');
      clean = clean.replace(/\\s+/g, ' ').trim();

      if (!clean) {
        if (onComplete) onComplete();
        return;
      }

      const sentences = clean.match(/[^.!?]+[.!?]+/g) || [clean];
      let spokenText = clean;
      if (sentences.length > 3) {
        spokenText = sentences.slice(0, 3).join(' ') + ' Weitere Telemetrie auf dem Hauptschirm dargestellt.';
      }

      const utterance = new SpeechSynthesisUtterance(spokenText);
      utterance.lang = 'de-DE';
      utterance.rate = 0.98;
      utterance.pitch = 1.05;

      const voice = getPreferredGermanVoice();
      if (voice) utterance.voice = voice;

      utterance.onstart = () => {
        setCommBadgeState('speaking');
        startVoiceVisualizer('speaking');
      };

      utterance.onend = () => {
        setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
        stopVoiceVisualizer();
        if (onComplete) onComplete();
      };

      utterance.onerror = () => {
        setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
        stopVoiceVisualizer();
        if (onComplete) onComplete();
      };

      playLcarsAcknowledge();
      setTimeout(() => {
        window.speechSynthesis.speak(utterance);
      }, 150);
    } catch (e) {
      console.warn("TTS Fehler:", e);
      setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
      stopVoiceVisualizer();
      if (onComplete) onComplete();
    }
  }

  function initSpeechRecognition() {
    if (!SpeechRecognition) return null;
    const rec = new SpeechRecognition();
    rec.lang = 'de-DE';
    rec.continuous = wakeWordActive;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onstart = () => {
      isListening = true;
      setCommBadgeState(wakeWordActive ? 'passive-listen' : 'listening');
    };

    rec.onresult = (event) => {
      let finalTranscript = '';
      let interimTranscript = '';
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        if (event.results[i].isFinal) {
          finalTranscript += event.results[i][0].transcript;
        } else {
          interimTranscript += event.results[i][0].transcript;
        }
      }

      const hudTranscript = document.getElementById('lcarsVoiceHudTranscript');
      if (hudTranscript) {
        hudTranscript.textContent = (finalTranscript || interimTranscript).trim();
      }

      if (wakeWordActive) {
        const lower = (finalTranscript || interimTranscript).toLowerCase();
        const match = lower.match(/\\b(computer|lcars)\\b(.*)/i);
        if (match && finalTranscript) {
          const command = match[2].trim();
          playLcarsChirp();
          if (command.length > 1) {
            handleVoiceCommand(command);
          } else {
            speakLcarsText("Bereit für Befehle.");
          }
        }
      } else {
        if (finalTranscript && finalTranscript.trim().length > 0) {
          handleVoiceCommand(finalTranscript.trim());
        }
      }
    };

    rec.onerror = (event) => {
      if (event.error !== 'no-speech' && event.error !== 'aborted') {
        console.warn("SpeechRecognition Fehler:", event.error);
        playLcarsError();
      }
      if (!wakeWordActive) {
        stopVoiceComm();
      }
    };

    rec.onend = () => {
      isListening = false;
      if (wakeWordActive && voiceInputEnabled) {
        try { rec.start(); } catch(e) {}
      } else {
        stopVoiceComm();
      }
    };

    return rec;
  }

  function startContinuousWakeWord() {
    if (!SpeechRecognition || !voiceInputEnabled || !wakeWordActive) return;
    try {
      if (voiceRecognition) {
        try { voiceRecognition.stop(); } catch(e) {}
      }
      voiceRecognition = initSpeechRecognition();
      if (voiceRecognition) {
        voiceRecognition.start();
        setCommBadgeState('passive-listen');
      }
    } catch(e) {
      console.warn("Konnte kontinuierliches Wake-Word nicht starten:", e);
    }
  }

  async function requestMicPermission() {
    playLcarsBeep(980, 1400);
    if (!window.isSecureContext) {
      playLcarsError();
      const origin = window.location.origin;
      alert("LCARS SICHERHEITSHINWEIS // KEIN HTTPS / UNSICHERER URSPRUNG:\\n\\nDer Browser sperrt das Mikrofon auf unverschlüsselten IP-Adressen (" + origin + ").\\n\\nSO GIBST DU ES FREI:\\n1) In Chrome/Edge eine neue Registerkarte öffnen:\\n   chrome://flags/#unsafely-treat-insecure-origin-as-secure\\n2) Dort genau diese Adresse eintragen:\\n   " + origin + "\\n3) Auf 'Enabled' stellen und Browser neu starten (Relaunch).\\n\\nAlternativ: Rufe das Dashboard über 'http://localhost:5000' (am Host) oder über HTTPS auf.");
      return false;
    }
    try {
      if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach(t => t.stop());
        playLcarsAcknowledge();
        alert("LCARS COMM-LINK // MIKROFON FREIGEGEBEN!\\n\\nDas Mikrofon ist jetzt autorisiert und betriebsbereit.");
        updateVoiceUI();
        return true;
      }
    } catch (e) {
      playLcarsError();
      alert("LCARS BERECHTIGUNG GEBLOCKT:\\n\\n" + e.message + "\\n\\nKlicke links neben der Webadresse auf das Schloss- oder Schieberegler-Icon und aktiviere 'Mikrofon: Zulassen'.");
      return false;
    }
  }

  function toggleVoiceListening(targetSubgroup = null) {
    if (!window.isSecureContext) {
      playLcarsError();
      const origin = window.location.origin;
      alert("LCARS SICHERHEITSHINWEIS // KEIN HTTPS / UNSICHERER URSPRUNG:\\n\\nDer Browser sperrt das Mikrofon auf unverschlüsselten IP-Adressen (" + origin + ").\\n\\nSO GIBST DU ES FREI:\\n1) In Chrome/Edge eine neue Registerkarte öffnen:\\n   chrome://flags/#unsafely-treat-insecure-origin-as-secure\\n2) Dort genau diese Adresse eintragen:\\n   " + origin + "\\n3) Auf 'Enabled' stellen und Browser neu starten (Relaunch).\\n\\nAlternativ: Rufe das Dashboard über 'http://localhost:5000' (am Host) oder über HTTPS auf.");
      return;
    }

    if (!SpeechRecognition) {
      playLcarsError();
      alert("LCARS HINWEIS: Die Web Speech API ist in diesem Browser oder unter dieser URL nicht aktiv.\\nBitte verwende Google Chrome, Edge oder Safari und stelle sicher, dass die Seite über localhost, HTTPS oder mit der Chrome-Flag aufgerufen wird.");
      return;
    }

    // Barge-in: Falls Computer gerade spricht, sofort stummschalten
    if ('speechSynthesis' in window && window.speechSynthesis.speaking) {
      window.speechSynthesis.cancel();
      setCommBadgeState('idle');
      stopVoiceVisualizer();
      playLcarsBeep(440, 220);
      return;
    }

    if (isListening && !wakeWordActive) {
      stopVoiceComm();
      playLcarsBeep(600, 300);
      return;
    }

    playLcarsChirp();
    if (targetSubgroup) {
      activeAgentSubgroup = targetSubgroup;
    }

    if (wakeWordActive) {
      try {
        if (voiceRecognition) voiceRecognition.stop();
      } catch(e) {}
    }

    voiceRecognition = initSpeechRecognition();

    const hud = document.getElementById('lcarsVoiceHud');
    const hudStatus = document.getElementById('lcarsVoiceHudStatus');
    const hudTranscript = document.getElementById('lcarsVoiceHudTranscript');
    if (hud) hud.style.display = 'flex';
    if (hudStatus) hudStatus.textContent = '● ODN SUBRAUM-COMM // HÖRE ZU...';
    if (hudTranscript) hudTranscript.textContent = 'Befehl sprechen...';

    setCommBadgeState('listening');
    startVoiceVisualizer('listening');

    try {
      voiceRecognition.start();
    } catch (e) {
      console.warn("Fehler beim Starten der Spracherkennung:", e);
    }
  }

  function stopVoiceComm(userAborted = false) {
    if (voiceRecognition && isListening) {
      try { voiceRecognition.stop(); } catch(e) {}
    }
    isListening = false;
    setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
    stopVoiceVisualizer();
    if (userAborted) {
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
      playLcarsBeep(440, 220);
    }
  }

  async function handleVoiceCommand(rawText) {
    stopVoiceComm();
    playLcarsAcknowledge();
    setCommBadgeState('computing');
    isVoiceLastInput = true;

    const cleanText = rawText.trim();
    const lower = cleanText.toLowerCase();

    // 1. Stopp / Abbruch (Barge-In)
    if (/^(stopp|halt|abbrechen|ruhe|computer ende|stille|stop|abbruch)$/i.test(lower)) {
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
      setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
      playLcarsBeep(440, 220);
      return;
    }

    // 2. Status / Systemstatus / Diagnose
    if (/(status|statusbericht|systemstatus|diagnose|vitals|systemzustand)/i.test(lower)) {
      switchCategory('system');
      const cpu = document.getElementById('topCpuVal')?.textContent || 'nominal';
      const ram = document.getElementById('topRamVal')?.textContent || 'nominal';
      const temp = document.getElementById('topTempVal')?.textContent || 'optimal';
      const disk = document.getElementById('topDiskVal')?.textContent || 'nominal';
      const resp = `Statusbericht für Terminal 47: Prozessor bei ${cpu}, Arbeitsspeicher bei ${ram}, Festplatte ${disk}, Kerntemperatur ${temp}. Alle primären Systeme arbeiten nominal.`;
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS BORDCOMPUTER');
      speakLcarsText(resp);
      return;
    }

    // 3. Roter Alarm
    if (/(roter? alarm|red alert|alarmstufe rot|gefechtsstationen|alarm auslösen)/i.test(lower)) {
      isCurrentlyRedAlert = true;
      document.documentElement.setAttribute('data-theme', 'redalert');
      playRedAlertKlaxon();
      const banner = document.getElementById('redAlertBanner');
      if (banner) banner.style.display = 'block';
      const reasonsEl = document.getElementById('redAlertReasons');
      if (reasonsEl) reasonsEl.textContent = 'MANUELL AUTORISIERT VIA LCARS SPRACHSTEUERUNG';
      const resp = "Roter Alarm autorisiert. Schutzschilde und Verteidigungsgitter aktiviert. Alle Stationen auf Gefechtsstationen.";
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS SICHERHEIT');
      speakLcarsText(resp);
      return;
    }

    // 4. Alarm aufheben
    if (/(alarm aufheben|alarm beenden|entwarnung|normaler status|gelber alarm|alarm abbrechen)/i.test(lower)) {
      isCurrentlyRedAlert = false;
      document.documentElement.setAttribute('data-theme', lastUserTheme || 'classic');
      const banner = document.getElementById('redAlertBanner');
      if (banner) banner.style.display = 'none';
      const resp = "Alarmstufe aufgehoben. Normalbetrieb wiederhergestellt.";
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS SICHERHEIT');
      speakLcarsText(resp);
      return;
    }

    // 5. Services / Webdienste
    if (/(services|dienste|webdienste|server|laufende dienste)/i.test(lower)) {
      switchCategory('services');
      const count = document.getElementById('scanFoundCount')?.textContent || 'mehrere';
      const resp = `Subraum-Verbindungen analysiert. Aktuell sind ${count} aktive Server auf den Frequenzen registriert.`;
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS SUBRAUM-COMM');
      speakLcarsText(resp);
      return;
    }

    // 6. Solar / Balkonkraftwerk / Energie
    if (/(solar|akku|batterie|energie|strom|balkonkraftwerk|hausverbrauch)/i.test(lower)) {
      switchCategory('solar');
      const bat = document.getElementById('topBatVal')?.textContent || 'nicht erfasst';
      const house = document.getElementById('topHouseVal')?.textContent || 'nicht erfasst';
      const resp = `Energie-Status: Balkonkraftwerk-Speicher liegt bei ${bat}. Aktueller Hausverbrauch beträgt ${house}.`;
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS ENERGIE-MANAGEMENT');
      speakLcarsText(resp);
      return;
    }

    // 7. Netzwerk Scan
    if (/(scan|scannen|suchlauf|netzwerk scannen|portscan)/i.test(lower)) {
      switchCategory('services');
      if (typeof triggerWebserverScan === 'function') {
        triggerWebserverScan();
      }
      const resp = "ODN-Netzwerk-Scan nach aktiven Servern und Diensten initiiert.";
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS SCANNER');
      speakLcarsText(resp);
      return;
    }

    // 8. Farbschema Wechsel
    const themeMatch = lower.match(/(farbschema|design|theme)\\s+(picard|nemesis|classic|lower decks|voyager)/i);
    if (themeMatch) {
      let th = themeMatch[2].toLowerCase().replace(/\\s+/g, '');
      setLcarsTheme(th);
      const resp = `LCARS Farbschema ${themeMatch[2].toUpperCase()} erfolgreich rekonfiguriert.`;
      appendChatMessage('user', `🎙️ "${cleanText}"`);
      appendChatMessage('assistant', resp, 'LCARS INTERFACE');
      speakLcarsText(resp);
      return;
    }

    // 9. Weiterleitung an KI-Agenten (Hermes oder 9Router)
    if (activeAgentSubgroup === 'hermes') {
      switchCategory('agents');
      const inp = document.getElementById('hermesChatInput');
      if (inp) {
        inp.value = cleanText;
        await handleHermesChatSubmit(null, true);
      }
    } else {
      switchCategory('agents');
      const inp = document.getElementById('lcarsChatInput');
      if (inp) {
        inp.value = cleanText;
        await handleChatSubmit(null, true);
      }
    }
  }

  function toggleVoiceInputSetting() {
    voiceInputEnabled = !voiceInputEnabled;
    localStorage.setItem('lcars-voice-in', voiceInputEnabled);
    if (!voiceInputEnabled) {
      stopVoiceComm();
    } else if (wakeWordActive) {
      startContinuousWakeWord();
    }
    updateVoiceUI();
    playLcarsBeep(880, 1760);
  }

  function toggleVoiceOutputSetting() {
    voiceOutputEnabled = !voiceOutputEnabled;
    localStorage.setItem('lcars-voice-out', voiceOutputEnabled);
    if (!voiceOutputEnabled && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    updateVoiceUI();
    playLcarsBeep(880, 1760);
  }

  function toggleWakeWordSetting() {
    wakeWordActive = !wakeWordActive;
    localStorage.setItem('lcars-wakeword', wakeWordActive);
    if (wakeWordActive && voiceInputEnabled) {
      startContinuousWakeWord();
    } else {
      stopVoiceComm();
    }
    updateVoiceUI();
    playLcarsBeep(980, 1400);
  }

  function onVoiceSelectChange(val) {
    localStorage.setItem('lcars-voice-name', val);
    playLcarsBeep(1100, 1600);
  }

  function testLcarsVoice() {
    speakLcarsText("LCARS Audio-Transceiver online. Subraum-Kommunikation und Sprachausgabe nominal.");
  }

  function updateVoiceUI() {
    const inLabel = document.getElementById('cfgVoiceInLabel');
    const outLabel = document.getElementById('cfgVoiceOutLabel');
    const wakeLabel = document.getElementById('cfgWakeWordLabel');
    if (inLabel) inLabel.textContent = voiceInputEnabled ? 'SPRACHEINGABE: AKTIV' : 'SPRACHEINGABE: AUS';
    if (outLabel) outLabel.textContent = voiceOutputEnabled ? 'SPRACHAUSGABE: AKTIV' : 'SPRACHAUSGABE: AUS';
    if (wakeLabel) wakeLabel.textContent = wakeWordActive ? "WAKE-WORD 'COMPUTER': AKTIV" : "WAKE-WORD 'COMPUTER': AUS";
    setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
  }

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (isListening || ('speechSynthesis' in window && window.speechSynthesis.speaking)) {
        stopVoiceComm(true);
      }
    }
  });

  // ==========================================================================
  // KI-AGENTEN SUBGRUPPEN & HERMES & ANTIGRAVITY IDE INTERFACE
  // ==========================================================================
  let activeAgentSubgroup = '9router';
  let hermesProfilesList = [];
  let currentHermesProfile = 'default';
  let isHermesGenerating = false;

  function switchAgentSubgroup(subgroup) {
    playLcarsBeep(1100, 1600);
    activeAgentSubgroup = subgroup;

    // Subnav Pills
    document.querySelectorAll('.lcars-subnav-pill').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById('subtab-btn-' + subgroup);
    if (activeBtn) activeBtn.classList.add('active');

    // Views
    document.querySelectorAll('.agent-subview').forEach(view => view.classList.remove('active-subview'));
    const activeView = document.getElementById('agent-subview-' + subgroup);
    if (activeView) activeView.classList.add('active-subview');

    if (subgroup === '9router') {
      loadChatModels();
      const inp = document.getElementById('lcarsChatInput');
      if (inp) inp.focus();
    } else if (subgroup === 'hermes') {
      loadHermesProfiles();
      const inp = document.getElementById('hermesChatInput');
      if (inp) inp.focus();
    } else if (subgroup === 'ide') {
      loadIdeConfig();
    }
  }

  // --- HERMES AGENTEN VERWALTUNG & CHAT ---
  async function loadHermesProfiles(playSound = false) {
    if (playSound) playLcarsBeep(1200, 1800);
    try {
      const resp = await fetch('/api/hermes/profiles');
      if (resp.ok) {
        const data = await resp.json();
        hermesProfilesList = data.profiles || [];
        renderHermesProfiles(hermesProfilesList);
      }
    } catch (e) {
      console.warn("Fehler beim Laden der Hermes Profile:", e);
      const grid = document.getElementById('hermesProfilesGrid');
      if (grid) grid.innerHTML = `<div style="color:var(--c-red); font-family:var(--mono-family); padding:0.5rem;">Fehler beim Laden der Profile: ${e.message}</div>`;
    }
  }

  function renderHermesProfiles(profiles) {
    const grid = document.getElementById('hermesProfilesGrid');
    const select = document.getElementById('hermesProfileSelect');
    const cloneSelect = document.getElementById('newAgentCloneFrom');

    if (select) {
      const cur = select.value || currentHermesProfile;
      select.innerHTML = '';
      profiles.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = `${p.name} (${p.display_name})`;
        if (p.name === cur) opt.selected = true;
        select.appendChild(opt);
      });
      if (select.value) currentHermesProfile = select.value;
    }

    if (cloneSelect) {
      cloneSelect.innerHTML = '';
      profiles.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = p.name;
        cloneSelect.appendChild(opt);
      });
    }

    if (!grid) return;
    if (profiles.length === 0) {
      grid.innerHTML = '<div style="color:#888; font-family:var(--mono-family); padding:1rem;">Keine Profile in ~/.hermes gefunden.</div>';
      return;
    }

    grid.innerHTML = profiles.map(p => {
      const isGw = p.gateway_running;
      const statusBadge = isGw
        ? '<span class="badge-status badge-online" style="font-size:0.75rem;"><span class="lcars-status-dot"></span> GATEWAY AKTIV</span>'
        : '<span class="badge-status badge-offline" style="font-size:0.75rem;">BEREIT / STANDBY</span>';

      const isActive = p.name === currentHermesProfile;

      return `
        <div class="hermes-card ${isActive ? 'active-card' : ''}" id="hermes-card-${p.name}">
          <div>
            <div class="hermes-card-head">
              <div class="hermes-card-name">
                <span>🤖</span> <span>${p.name.toUpperCase()}</span>
                ${p.is_default ? '<span style="font-size:0.7rem; background:rgba(235,148,58,0.25); color:var(--c-primary); padding:1px 5px; border-radius:3px;">CORE</span>' : ''}
              </div>
              ${statusBadge}
            </div>
            <div style="font-size:0.84rem; color:var(--c-gold); margin:0.4rem 0;">${p.description || p.personality || 'Autonomer System-Agent'}</div>
            <div class="hermes-card-meta">
              <div class="hermes-meta-row">
                <span class="hermes-meta-lbl">MODELL:</span>
                <span class="hermes-meta-val">${p.model || 'ag/gemini-3-flash'}</span>
              </div>
              <div class="hermes-meta-row">
                <span class="hermes-meta-lbl">PROVIDER:</span>
                <span class="hermes-meta-val">${p.provider || 'custom'}</span>
              </div>
              <div class="hermes-meta-row">
                <span class="hermes-meta-lbl">PFAD:</span>
                <span class="hermes-meta-val" style="font-size:0.72rem; opacity:0.8;">${p.path}</span>
              </div>
            </div>
          </div>
          <div style="display:flex; justify-content:flex-end; gap:0.4rem; margin-top:0.4rem;">
            <button type="button" class="left-action-btn" onclick="selectHermesProfile('${p.name}')" style="padding:0.35rem 0.8rem; font-size:0.8rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
              💬 MIT AGENT SPRECHEN
            </button>
          </div>
        </div>
      `;
    }).join('');
  }

  function selectHermesProfile(name) {
    playLcarsBeep(980, 1400);
    currentHermesProfile = name;
    const select = document.getElementById('hermesProfileSelect');
    if (select) select.value = name;
    const header = document.getElementById('hermesActiveProfileHeader');
    if (header) header.textContent = name.toUpperCase();

    document.querySelectorAll('.hermes-card').forEach(c => c.classList.remove('active-card'));
    const activeC = document.getElementById('hermes-card-' + name);
    if (activeC) activeC.classList.add('active-card');

    const inp = document.getElementById('hermesChatInput');
    if (inp) {
      inp.placeholder = `BEFEHL AN AGENT '${name.toUpperCase()}' SENDEN...`;
      inp.focus();
    }
  }

  function onHermesProfileChange() {
    const select = document.getElementById('hermesProfileSelect');
    if (select) {
      selectHermesProfile(select.value);
    }
  }

  function toggleHermesCreateForm(show) {
    playLcarsBeep(880, 1320);
    const card = document.getElementById('hermesCreateAgentCard');
    if (!card) return;
    if (show === undefined) {
      card.style.display = (card.style.display === 'none' || !card.style.display) ? 'block' : 'none';
    } else {
      card.style.display = show ? 'block' : 'none';
    }
    if (card.style.display === 'block') {
      const inp = document.getElementById('newAgentName');
      if (inp) inp.focus();
    }
  }

  async function handleCreateHermesAgent(e) {
    e.preventDefault();
    const btn = document.getElementById('btnSubmitCreateAgent');
    const status = document.getElementById('hermesCreateStatus');
    const nameInp = document.getElementById('newAgentName');
    const cloneInp = document.getElementById('newAgentCloneFrom');
    const modelInp = document.getElementById('newAgentModel');
    const descInp = document.getElementById('newAgentDesc');

    const name = nameInp ? nameInp.value.trim().toLowerCase() : '';
    if (!name) return;

    if (btn) btn.disabled = true;
    if (status) {
      status.style.display = 'block';
      status.style.color = 'var(--c-primary)';
      status.textContent = `INITIALISIERE HERMES PROFIL '${name}' (bitte kurz warten)...`;
    }

    try {
      const resp = await fetch('/api/hermes/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          clone_from: cloneInp ? cloneInp.value : 'default',
          model: modelInp ? modelInp.value.trim() : 'ag/gemini-3-flash',
          description: descInp ? descInp.value.trim() : ''
        })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        if (status) {
          status.style.color = '#10b981';
          status.textContent = `✓ Profil '${name}' erfolgreich angelegt!`;
        }
        playLcarsBeep(1200, 2400);
        await loadHermesProfiles();
        selectHermesProfile(name);
        setTimeout(() => {
          toggleHermesCreateForm(false);
          if (status) status.style.display = 'none';
        }, 1200);
      } else {
        if (status) {
          status.style.color = 'var(--c-red)';
          status.textContent = `Fehler: ${data.error || 'Profil konnte nicht erstellt werden'}`;
        }
        playLcarsBeep(440, 220);
      }
    } catch (err) {
      if (status) {
        status.style.color = 'var(--c-red)';
        status.textContent = `Netzwerkfehler: ${err.message}`;
      }
      playLcarsBeep(440, 220);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function appendHermesMessage(role, content, senderName, isError = false) {
    const log = document.getElementById('hermesChatLog');
    if (!log) return;

    const timeStr = formatTimeNow();
    const msgDiv = document.createElement('div');
    const cssClass = role === 'user'
      ? 'lcars-msg lcars-msg-user'
      : (isError ? 'lcars-msg lcars-msg-error' : 'lcars-msg lcars-msg-agent');

    const senderLabel = role === 'user' ? 'USER // ODN TERMINAL' : `HERMES // ${senderName.toUpperCase()}`;

    msgDiv.className = cssClass;
    if (role !== 'user' && !isError) {
      msgDiv.style.borderLeftColor = 'var(--c-secondary)';
      msgDiv.style.background = 'rgba(186, 164, 229, 0.08)';
    }

    msgDiv.innerHTML = `
      <div class="lcars-msg-header">
        <span class="lcars-msg-sender" style="${role !== 'user' ? 'color:var(--c-secondary);' : ''}">${senderLabel}</span>
        <span class="lcars-msg-time">${timeStr}</span>
      </div>
      <div class="lcars-msg-body">${formatMessageText(content)}</div>
    `;

    log.appendChild(msgDiv);
    log.scrollTop = log.scrollHeight;
  }

  async function handleHermesChatSubmit(e, fromVoice = false) {
    if (e) e.preventDefault();
    if (isHermesGenerating) return;

    const input = document.getElementById('hermesChatInput');
    const sendBtn = document.getElementById('hermesChatSendBtn');
    const sendLabel = document.getElementById('hermesChatSendLabel');
    const loading = document.getElementById('hermesChatLoading');
    const meta = document.getElementById('hermesMetaStatus');

    if (!input) return;
    const message = input.value.trim();
    if (!message) return;

    input.value = '';
    appendHermesMessage('user', message, currentHermesProfile);
    playLcarsBeep(880, 1320);

    isHermesGenerating = true;
    input.disabled = true;
    if (sendBtn) sendBtn.disabled = true;
    if (sendLabel) sendLabel.textContent = 'WAIT...';
    if (loading) loading.style.display = 'flex';
    if (meta) meta.textContent = `HERMES VERARBEITET ANFRAGE AN '${currentHermesProfile.toUpperCase()}'...`;
    if (fromVoice) setCommBadgeState('computing');

    try {
      const resp = await fetch('/api/hermes/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile: currentHermesProfile,
          message: message
        })
      });

      const data = await resp.json();

      if (resp.ok && data.success) {
        appendHermesMessage('assistant', data.reply || 'Keine Antwort erhalten.', currentHermesProfile);
        playLcarsAcknowledge();
        if (meta) meta.textContent = `ANTWORT EMPFANGEN // AGENT: ${currentHermesProfile.toUpperCase()}`;
        if (fromVoice || (voiceOutputEnabled && isVoiceLastInput)) {
          speakLcarsText(data.reply || 'Keine Antwort erhalten.');
        } else {
          setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
        }
      } else {
        const err = data.error || data.reply || 'Fehler bei Hermes Ausführung.';
        appendHermesMessage('assistant', err, currentHermesProfile, true);
        playLcarsError();
        if (meta) meta.textContent = 'HERMES AUSFÜHRUNGSFEHLER';
        if (fromVoice) speakLcarsText('Fehler bei Ausführung des Hermes Agenten.');
        setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
      }
    } catch (err) {
      appendHermesMessage('assistant', `Verbindungsfehler zu Hermes: ${err.message}`, currentHermesProfile, true);
      playLcarsError();
      if (meta) meta.textContent = 'NETZWERKFEHLER';
      if (fromVoice) speakLcarsText('Netzwerkfehler bei Verbindung zu Hermes.');
      setCommBadgeState(wakeWordActive ? 'passive-listen' : 'idle');
    } finally {
      isHermesGenerating = false;
      input.disabled = false;
      if (sendBtn) sendBtn.disabled = false;
      if (sendLabel) sendLabel.textContent = 'SENDEN';
      if (loading) loading.style.display = 'none';
      input.focus();
    }
  }

  function sendHermesQuickPrompt(text) {
    const inp = document.getElementById('hermesChatInput');
    if (inp) {
      inp.value = text;
      handleHermesChatSubmit(null);
    }
  }

  function clearHermesChatHistory() {
    playLcarsBeep(600, 300);
    const log = document.getElementById('hermesChatLog');
    if (log) {
      log.innerHTML = `
        <div class="lcars-msg lcars-msg-agent" style="border-left-color: var(--c-secondary); background: rgba(186, 164, 229, 0.08);">
          <div class="lcars-msg-header">
            <span class="lcars-msg-sender" style="color: var(--c-secondary);">▶ HERMES // SYSTEM COMM-LINK</span>
            <span class="lcars-msg-time">${formatTimeNow()}</span>
          </div>
          <div class="lcars-msg-body">Chat-Verlauf zurückgesetzt. Neuer Dialog mit Agent '${currentHermesProfile.toUpperCase()}'.</div>
        </div>
      `;
    }
  }

  // --- ANTIGRAVITY IDE URL & KONFIGURATION ---
  const DEFAULT_ANTIGRAVITY_IDE_URL = 'https://antigravity.google.com/r/f9e18040-ab9a-4f0c-9d4a-2b4f5281af22-v2?p=c%2F3a633d37-def3-419b-ab1e-8498973ae694%3Fsection%3Dc15db4e2-c36e-442e-850b-0d28e944e2c6';

  function parseIdeUrlParams(url) {
    try {
      const u = new URL(url);
      const runnerMatch = u.pathname.match(new RegExp('/r/([^/?#]+)'));
      const runnerId = runnerMatch ? runnerMatch[1] : '--';

      const p = u.searchParams.get('p') || '';
      let convId = '--';
      let sectionId = '--';
      if (p) {
        const decodedP = decodeURIComponent(p);
        const convMatch = decodedP.match(new RegExp('c/([^/?#]+)'));
        if (convMatch) convId = convMatch[1];
        const secMatch = decodedP.match(new RegExp('section=([^/?#&]+)'));
        if (secMatch) sectionId = secMatch[1];
      }
      return { runnerId, convId, sectionId };
    } catch (e) {
      return { runnerId: '--', convId: '--', sectionId: '--' };
    }
  }

  function loadIdeConfig() {
    const inp = document.getElementById('ideUrlInput');
    const launchBtn = document.getElementById('ideLaunchBtn');
    const cfgInp = document.getElementById('cfgIdeUrl');

    let curUrl = DEFAULT_ANTIGRAVITY_IDE_URL;
    if (inp && inp.value) curUrl = inp.value;

    if (launchBtn) launchBtn.href = curUrl;
    if (cfgInp && !cfgInp.value) cfgInp.value = curUrl;

    const parsed = parseIdeUrlParams(curUrl);
    const rEl = document.getElementById('ideRunnerId');
    if (rEl) rEl.textContent = parsed.runnerId;
    const cEl = document.getElementById('ideConvId');
    if (cEl) cEl.textContent = parsed.convId;
    const sEl = document.getElementById('ideSectionId');
    if (sEl) sEl.textContent = parsed.sectionId;
  }

  async function saveIdeUrl() {
    playLcarsBeep(980, 1400);
    const inp = document.getElementById('ideUrlInput');
    const status = document.getElementById('ideSaveStatus');
    const cfgInp = document.getElementById('cfgIdeUrl');
    const launchBtn = document.getElementById('ideLaunchBtn');

    if (!inp) return;
    const url = inp.value.trim();
    if (!url) return;

    try {
      const resp = await fetch('/api/config/ide-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        if (launchBtn) launchBtn.href = url;
        if (cfgInp) cfgInp.value = url;
        loadIdeConfig();
        if (status) {
          status.style.display = 'block';
          status.style.color = '#10b981';
          status.textContent = '✓ IDE-URL erfolgreich persistent gespeichert!';
          setTimeout(() => { status.style.display = 'none'; }, 3000);
        }
        playLcarsBeep(1200, 2400);
      } else {
        if (status) {
          status.style.display = 'block';
          status.style.color = 'var(--c-red)';
          status.textContent = 'Fehler beim Speichern der URL.';
        }
      }
    } catch (e) {
      if (status) {
        status.style.display = 'block';
        status.style.color = 'var(--c-red)';
        status.textContent = `Netzwerkfehler: ${e.message}`;
      }
    }
  }

  function copyIdeUrl() {
    playLcarsBeep(880, 1320);
    const inp = document.getElementById('ideUrlInput');
    const status = document.getElementById('ideSaveStatus');
    if (inp) {
      navigator.clipboard.writeText(inp.value).then(() => {
        if (status) {
          status.style.display = 'block';
          status.style.color = 'var(--c-gold)';
          status.textContent = '📋 URL in Zwischenablage kopiert!';
          setTimeout(() => { status.style.display = 'none'; }, 2500);
        }
      }).catch(err => {
        alert('Konnte Zwischenablage nicht öffnen: ' + err);
      });
    }
  }

  function resetIdeUrl() {
    playLcarsBeep(600, 400);
    const inp = document.getElementById('ideUrlInput');
    if (inp) {
      inp.value = DEFAULT_ANTIGRAVITY_IDE_URL;
      saveIdeUrl();
    }
  }

  // ==========================================================================
  // LCARS PERMISSIONS & COMMAND CODE CONTROLLER
  // ==========================================================================
  var currentLockedSections = ['cycle'];
  var pendingUnlockCategory = null;
  var currentAuthCode = sessionStorage.getItem('lcars_auth_code') || '0901';

  function isCategoryLocked(catId) {
    const isUnlocked = (sessionStorage.getItem('lcars_auth_unlocked') === 'true');
    return currentLockedSections.includes(catId) && !isUnlocked;
  }

  async function fetchPermissionsStatus() {
    try {
      const resp = await fetch('/api/permissions/status');
      if (resp.ok) {
        const data = await resp.json();
        if (Array.isArray(data.locked_sections)) {
          currentLockedSections = data.locked_sections;
        }
      }
    } catch (e) {
      console.warn('Fehler beim Laden der Rechtekonfiguration:', e);
    }
    applyPermissionsVisibility();
  }

  function applyPermissionsVisibility() {
    const isUnlocked = (sessionStorage.getItem('lcars_auth_unlocked') === 'true');
    const allSections = ['system', 'services', 'agents', 'ai-info', 'config', 'fantasy', 'solar', 'homeassistant', 'cycle', 'pulsecast', 'gemini_live', 'pimmel'];

    allSections.forEach(secId => {
      const btn = document.getElementById('btn-cat-' + secId);
      if (!btn) return;

      const isLocked = currentLockedSections.includes(secId);
      if (isLocked && !isUnlocked) {
        btn.style.display = 'none';
      } else {
        if (secId === 'homeassistant') {
          checkHomeAssistantConfig();
          return;
        }
        btn.style.display = '';
      }
    });

    // Update Auth button in left pillar
    const authBtnLabel = document.getElementById('authBtnLabel');
    const authBtnIcon = document.getElementById('authBtnIcon');
    const authBtn = document.getElementById('btn-auth-toggle');
    if (isUnlocked) {
      if (authBtnLabel) authBtnLabel.textContent = 'SPERREN';
      if (authBtnIcon) authBtnIcon.textContent = '🔓';
      if (authBtn) authBtn.style.backgroundColor = 'var(--c-red)';
    } else {
      if (authBtnLabel) authBtnLabel.textContent = 'CODE';
      if (authBtnIcon) authBtnIcon.textContent = '🔒';
      if (authBtn) authBtn.style.backgroundColor = 'var(--c-almond)';
    }

    // Update Config Rechteverwaltung Card
    updateConfigPermUI(isUnlocked);

    // If current category is locked and not unlocked, fallback to system
    if (!isUnlocked && currentLockedSections.includes(currentCategory)) {
      switchCategory('system');
    }
  }

  function updateConfigPermUI(isUnlocked) {
    const lockedView = document.getElementById('permLockedView');
    const unlockedView = document.getElementById('permUnlockedView');
    const badge = document.getElementById('permStatusBadge');
    const icon = document.getElementById('permHeadIcon');

    if (isUnlocked) {
      if (lockedView) lockedView.style.display = 'none';
      if (unlockedView) unlockedView.style.display = 'block';
      if (badge) {
        badge.textContent = 'ENTSPERRT // STUFE ALPHA';
        badge.style.backgroundColor = '#44dd88';
        badge.style.color = '#000000';
      }
      if (icon) icon.textContent = '🔓';

      // Check the checkboxes for currentLockedSections
      const allSections = ['system', 'services', 'agents', 'ai-info', 'config', 'fantasy', 'solar', 'homeassistant', 'cycle', 'pulsecast', 'gemini_live', 'pimmel'];
      allSections.forEach(secId => {
        const cb = document.getElementById('permLock_' + secId);
        if (cb) {
          cb.checked = currentLockedSections.includes(secId);
        }
      });
      loadLcarsUsers();
      loadLcarsAuditLog();
    } else {
      if (lockedView) lockedView.style.display = 'block';
      if (unlockedView) unlockedView.style.display = 'none';
      if (badge) {
        badge.textContent = 'GESPERRT // STUFE 1';
        badge.style.backgroundColor = 'var(--c-red)';
        badge.style.color = '#ffffff';
      }
      if (icon) icon.textContent = '🔒';
    }
  }

  function openAuthModal() {
    playLcarsBeep(880, 1400);
    const modal = document.getElementById('lcarsAuthModal');
    if (modal) {
      modal.style.display = 'flex';
      const inp = document.getElementById('modalPinInput');
      if (inp) {
        inp.value = '';
        inp.focus();
      }
      const err = document.getElementById('modalPinError');
      if (err) err.style.display = 'none';
    }
  }

  function closeAuthModal() {
    playLcarsBeep(440, 220);
    const modal = document.getElementById('lcarsAuthModal');
    if (modal) modal.style.display = 'none';
    pendingUnlockCategory = null;
  }

  function handleAuthModalBackdropClick(e) {
    if (e.target && e.target.id === 'lcarsAuthModal') {
      closeAuthModal();
    }
  }

  function toggleAuthModal() {
    const isUnlocked = (sessionStorage.getItem('lcars_auth_unlocked') === 'true');
    if (isUnlocked) {
      lockPermissionsSession();
    } else {
      openAuthModal();
    }
  }

  function appendModalPin(digit) {
    playLcarsBeep(1200, 1600);
    const inp = document.getElementById('modalPinInput');
    if (inp && inp.value.length < 10) {
      inp.value += digit;
    }
  }

  function clearModalPin() {
    playLcarsBeep(500, 300);
    const inp = document.getElementById('modalPinInput');
    if (inp) inp.value = '';
    const err = document.getElementById('modalPinError');
    if (err) err.style.display = 'none';
  }

  async function verifyModalPin() {
    const inp = document.getElementById('modalPinInput');
    const code = inp ? inp.value.trim() : '';
    if (!code) return;

    try {
      const resp = await fetch('/api/permissions/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code })
      });
      const res = await resp.json();
      if (res.valid) {
        sessionStorage.setItem('lcars_auth_unlocked', 'true');
        sessionStorage.setItem('lcars_auth_code', code);
        currentAuthCode = code;
        playLcarsAcknowledge();
        closeAuthModal();
        applyPermissionsVisibility();
        if (pendingUnlockCategory) {
          const target = pendingUnlockCategory;
          pendingUnlockCategory = null;
          switchCategory(target);
        }
      } else {
        playLcarsBeep(300, 150);
        const err = document.getElementById('modalPinError');
        if (err) err.style.display = 'block';
        if (inp) {
          inp.value = '';
          inp.focus();
        }
      }
    } catch (e) {
      alert('Fehler bei der Authentifizierung: ' + e);
    }
  }

  function appendConfigPin(digit) {
    playLcarsBeep(1200, 1600);
    const inp = document.getElementById('configPinInput');
    if (inp && inp.value.length < 10) {
      inp.value += digit;
    }
  }

  function clearConfigPin() {
    playLcarsBeep(500, 300);
    const inp = document.getElementById('configPinInput');
    if (inp) inp.value = '';
    const err = document.getElementById('configPinError');
    if (err) err.style.display = 'none';
  }

  async function verifyConfigPin() {
    const inp = document.getElementById('configPinInput');
    const code = inp ? inp.value.trim() : '';
    if (!code) return;

    try {
      const resp = await fetch('/api/permissions/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code })
      });
      const res = await resp.json();
      if (res.valid) {
        sessionStorage.setItem('lcars_auth_unlocked', 'true');
        sessionStorage.setItem('lcars_auth_code', code);
        currentAuthCode = code;
        playLcarsAcknowledge();
        applyPermissionsVisibility();
      } else {
        playLcarsBeep(300, 150);
        const err = document.getElementById('configPinError');
        if (err) err.style.display = 'block';
        if (inp) {
          inp.value = '';
          inp.focus();
        }
      }
    } catch (e) {
      alert('Fehler bei der Authentifizierung: ' + e);
    }
  }

  function lockPermissionsSession() {
    playLcarsBeep(440, 220);
    sessionStorage.removeItem('lcars_auth_unlocked');
    applyPermissionsVisibility();
  }

  async function savePermissionsConfig(e) {
    e.preventDefault();
    const btn = document.getElementById('btnSavePerm');
    const feedback = document.getElementById('permSaveFeedback');
    if (btn) btn.disabled = true;

    const checkedBoxes = document.querySelectorAll('.perm-lock-cb:checked');
    const lockedSections = Array.from(checkedBoxes).map(cb => cb.value);

    const newCodeInp = document.getElementById('permNewCode');
    const newCodeConfirmInp = document.getElementById('permNewCodeConfirm');
    const newCode = newCodeInp ? newCodeInp.value.trim() : '';
    const newCodeConfirm = newCodeConfirmInp ? newCodeConfirmInp.value.trim() : '';

    if (newCode) {
      if (newCode.length < 3) {
        alert('Der neue Command Code muss mindestens 3 Zeichen lang sein.');
        if (btn) btn.disabled = false;
        return;
      }
      if (newCode !== newCodeConfirm) {
        alert('Die eingegebenen Command Codes stimmen nicht überein.');
        if (btn) btn.disabled = false;
        return;
      }
    }

    const payload = {
      code: sessionStorage.getItem('lcars_auth_code') || currentAuthCode || '0901',
      locked_sections: lockedSections
    };
    if (newCode) {
      payload.new_code = newCode;
    }

    try {
      const resp = await fetch('/api/permissions/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const res = await resp.json();
      if (res.success) {
        playLcarsAcknowledge();
        if (newCode) {
          sessionStorage.setItem('lcars_auth_code', newCode);
          currentAuthCode = newCode;
          if (newCodeInp) newCodeInp.value = '';
          if (newCodeConfirmInp) newCodeConfirmInp.value = '';
        }
        currentLockedSections = res.locked_sections || lockedSections;
        applyPermissionsVisibility();

        if (feedback) {
          feedback.textContent = 'RECHTEKONFIGURATION ERFOLGREICH GESPEICHERT';
          feedback.style.display = 'block';
          setTimeout(() => { feedback.style.display = 'none'; }, 4000);
        }
      } else {
        alert('Fehler beim Speichern: ' + (res.error || 'Unbekannter Fehler'));
      }
    } catch (err) {
      alert('Netzwerkfehler beim Speichern: ' + err);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ==========================================================================
  // LCARS CENTRAL USER & ACCESS MANAGEMENT CONTROLLER (*.PIMMEL.SITE)
  // ==========================================================================
  var lcarsUsersList = [];

  function getLcarsAuthHeader() {
    const code = sessionStorage.getItem('lcars_auth_code') || (typeof currentAuthCode !== 'undefined' ? currentAuthCode : '0901');
    return {
      'Content-Type': 'application/json',
      'X-Command-Code': code
    };
  }

  async function loadLcarsUsers() {
    const tbody = document.getElementById('lcarsUsersTableBody');
    if (!tbody) return;
    try {
      const resp = await fetch('/api/users', { headers: getLcarsAuthHeader() });
      if (resp.ok) {
        const data = await resp.json();
        lcarsUsersList = data.users || [];
        renderLcarsUsersTable(lcarsUsersList);
      } else if (resp.status === 403) {
        tbody.innerHTML = '<tr><td colspan="6" style="padding:1.5rem; text-align:center; color:var(--c-red); font-family:var(--mono-family);">AUTORISIERUNG FEHLGESCHLAGEN // COMMAND CODE 0901 ERFORDERLICH</td></tr>';
      } else {
        tbody.innerHTML = '<tr><td colspan="6" style="padding:1.5rem; text-align:center; color:var(--c-red); font-family:var(--mono-family);">FEHLER BEIM LADEN DER BENUTZERDATEN</td></tr>';
      }
    } catch (e) {
      console.warn('loadLcarsUsers error:', e);
      tbody.innerHTML = '<tr><td colspan="6" style="padding:1.5rem; text-align:center; color:var(--c-red); font-family:var(--mono-family);">NETZWERKFEHLER BEIM LADEN</td></tr>';
    }
  }

  function renderLcarsUsersTable(users) {
    const tbody = document.getElementById('lcarsUsersTableBody');
    if (!tbody) return;
    if (!users || users.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="padding:2rem; text-align:center; color:#888; font-family:var(--mono-family);">KEINE BENUTZER ANGELEGT. KLICKEN SIE AUF "+ NEUER BENUTZER" UM DEN ERSTEN ZUGANG ZU ERSTELLEN.</td></tr>';
      return;
    }

    const serviceLabels = {
      '*': 'ALLE DIENSTE',
      'all': 'ALLE DIENSTE',
      'pulsecast': 'PULSECAST',
      'telemetryvault': 'TELEMETRY',
      'matter': 'MATTER',
      'headroom': 'HEADROOM',
      'cups': 'CUPS'
    };

    let html = '';
    users.forEach(u => {
      const isActive = u.is_active;
      const statusBadge = isActive
        ? '<span style="background:#44dd88; color:#000; padding:2px 8px; border-radius:4px; font-weight:700; font-family:var(--mono-family); font-size:0.75rem;">AKTIV</span>'
        : '<span style="background:var(--c-red); color:#fff; padding:2px 8px; border-radius:4px; font-weight:700; font-family:var(--mono-family); font-size:0.75rem;">GESPERRT</span>';

      let permsHtml = '';
      const sList = u.allowed_services || [];
      if (sList.includes('*') || sList.includes('all')) {
        permsHtml = '<span style="background:rgba(235,148,58,0.2); border:1px solid var(--c-primary); color:var(--c-primary); padding:1px 6px; border-radius:3px; font-family:var(--mono-family); font-size:0.75rem; font-weight:700;">★ ALLE DIENSTE (*)</span>';
      } else if (sList.length === 0) {
        permsHtml = '<span style="color:#666; font-size:0.75rem; font-family:var(--mono-family);">KEINE</span>';
      } else {
        permsHtml = sList.map(s => {
          const lbl = serviceLabels[s] || s.toUpperCase();
          return '<span style="background:rgba(186,164,229,0.15); border:1px solid var(--c-secondary); color:var(--c-secondary); padding:1px 6px; border-radius:3px; font-family:var(--mono-family); font-size:0.72rem; margin-right:4px;">' + escapeHtml(lbl) + '</span>';
        }).join(' ');
      }

      const lastLogin = u.last_login_at ? escapeHtml(u.last_login_at) : '<span style="color:#666;">Noch nie</span>';
      const displayName = u.display_name ? '<strong>' + escapeHtml(u.display_name) + '</strong>' : '';
      const notes = u.notes ? '<div style="font-size:0.75rem; color:#888;">' + escapeHtml(u.notes) + '</div>' : '';

      html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.06);">' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle;">' + statusBadge + '</td>' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle; font-family:var(--mono-family); font-size:0.95rem; font-weight:700; color:var(--c-gold);">' + escapeHtml(u.username) + '</td>' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle;">' + displayName + ' ' + notes + '</td>' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle;">' + permsHtml + '</td>' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle; font-family:var(--mono-family); font-size:0.8rem; color:#bbb;">' + lastLogin + '</td>' +
        '<td style="padding:0.6rem 0.75rem; vertical-align:middle; text-align:right; white-space:nowrap;">' +
          '<button type="button" class="left-action-btn" onclick="openEditUserModal(' + u.id + ')" style="padding:0.25rem 0.5rem; font-size:0.75rem; border-color:var(--c-primary); color:var(--c-primary);" title="Bearbeiten"><span>✏️</span></button> ' +
          '<button type="button" class="left-action-btn" onclick="openChangePasswordModal(' + u.id + ')" style="padding:0.25rem 0.5rem; font-size:0.75rem; border-color:var(--c-gold); color:var(--c-gold);" title="Passwort ändern"><span>🔑</span></button> ' +
          '<button type="button" class="left-action-btn" onclick="toggleUserActive(' + u.id + ', ' + (isActive ? 'true' : 'false') + ')" style="padding:0.25rem 0.5rem; font-size:0.75rem; border-color:' + (isActive ? 'var(--c-almond)' : '#44dd88') + '; color:' + (isActive ? 'var(--c-almond)' : '#44dd88') + ';" title="' + (isActive ? 'Sperren' : 'Aktivieren') + '"><span>' + (isActive ? '⛔' : '✓') + '</span></button> ' +
          '<button type="button" class="left-action-btn" onclick="deleteLcarsUser(' + u.id + ')" style="padding:0.25rem 0.5rem; font-size:0.75rem; border-color:var(--c-red); color:var(--c-red);" title="Löschen"><span>🗑️</span></button>' +
        '</td>' +
      '</tr>';
    });
    tbody.innerHTML = html;
  }

  function openNewUserModal() {
    playLcarsBeep(700, 900);
    const titleEl = document.getElementById('userFormModalTitle');
    if (titleEl) titleEl.textContent = 'NEUER LCARS BENUTZER ANLEGEN';
    document.getElementById('formUserId').value = '';
    const uInput = document.getElementById('formUserUsername');
    if (uInput) {
      uInput.value = '';
      uInput.disabled = false;
    }
    const pGroup = document.getElementById('formUserPasswordGroup');
    if (pGroup) pGroup.style.display = 'block';
    const pInput = document.getElementById('formUserPassword');
    if (pInput) {
      pInput.value = '';
      pInput.required = true;
    }
    const dInput = document.getElementById('formUserDisplayName');
    if (dInput) dInput.value = '';
    const nInput = document.getElementById('formUserNotes');
    if (nInput) nInput.value = '';
    const aInput = document.getElementById('formUserIsActive');
    if (aInput) aInput.checked = true;

    document.querySelectorAll('.user-svc-cb').forEach(cb => { cb.checked = false; });
    const allCb = document.getElementById('userSvc_all');
    if (allCb) allCb.checked = true;

    const modal = document.getElementById('userFormModal');
    if (modal) modal.style.display = 'flex';
    if (uInput) uInput.focus();
  }

  function openEditUserModal(userId) {
    const user = lcarsUsersList.find(u => u.id === userId);
    if (!user) return;
    playLcarsBeep(700, 900);
    const titleEl = document.getElementById('userFormModalTitle');
    if (titleEl) titleEl.textContent = 'BENUTZER BEARBEITEN: ' + user.username.toUpperCase();
    document.getElementById('formUserId').value = user.id;
    const uInput = document.getElementById('formUserUsername');
    if (uInput) {
      uInput.value = user.username;
      uInput.disabled = true;
    }

    const pGroup = document.getElementById('formUserPasswordGroup');
    if (pGroup) pGroup.style.display = 'none';
    const pInput = document.getElementById('formUserPassword');
    if (pInput) pInput.required = false;

    const dInput = document.getElementById('formUserDisplayName');
    if (dInput) dInput.value = user.display_name || '';
    const nInput = document.getElementById('formUserNotes');
    if (nInput) nInput.value = user.notes || '';
    const aInput = document.getElementById('formUserIsActive');
    if (aInput) aInput.checked = user.is_active;

    const svcs = user.allowed_services || [];
    const isAll = svcs.includes('*') || svcs.includes('all');
    const allCb = document.getElementById('userSvc_all');
    if (allCb) allCb.checked = isAll;
    ['pulsecast', 'telemetryvault', 'matter', 'headroom', 'cups'].forEach(s => {
      const cb = document.getElementById('userSvc_' + s);
      if (cb) cb.checked = isAll || svcs.includes(s);
    });

    const modal = document.getElementById('userFormModal');
    if (modal) modal.style.display = 'flex';
  }

  function closeUserFormModal() {
    playLcarsBeep(440, 220);
    const modal = document.getElementById('userFormModal');
    if (modal) modal.style.display = 'none';
  }

  function handleUserSvcAllToggle() {
    const allCb = document.getElementById('userSvc_all');
    if (allCb && allCb.checked) {
      document.querySelectorAll('.user-svc-cb').forEach(cb => {
        if (cb.id !== 'userSvc_all') cb.checked = true;
      });
    }
  }

  async function submitUserForm(event) {
    if (event) event.preventDefault();
    const userId = document.getElementById('formUserId').value;
    const isEdit = Boolean(userId);

    const username = document.getElementById('formUserUsername').value.trim();
    const displayName = document.getElementById('formUserDisplayName').value.trim();
    const notes = document.getElementById('formUserNotes').value.trim();
    const isActive = document.getElementById('formUserIsActive').checked;

    let services = [];
    const allCb = document.getElementById('userSvc_all');
    if (allCb && allCb.checked) {
      services = ['*'];
    } else {
      ['pulsecast', 'telemetryvault', 'matter', 'headroom', 'cups'].forEach(s => {
        const cb = document.getElementById('userSvc_' + s);
        if (cb && cb.checked) services.push(s);
      });
    }

    try {
      if (isEdit) {
        const resp = await fetch('/api/users/' + userId, {
          method: 'PUT',
          headers: getLcarsAuthHeader(),
          body: JSON.stringify({
            display_name: displayName,
            is_active: isActive,
            allowed_services: services,
            notes: notes
          })
        });
        const res = await resp.json();
        if (resp.ok && res.success) {
          playLcarsAcknowledge();
          closeUserFormModal();
          loadLcarsUsers();
          loadLcarsAuditLog();
        } else {
          alert('Fehler beim Aktualisieren: ' + (res.error || 'Unbekannt'));
        }
      } else {
        const password = document.getElementById('formUserPassword').value;
        const resp = await fetch('/api/users', {
          method: 'POST',
          headers: getLcarsAuthHeader(),
          body: JSON.stringify({
            username: username,
            password: password,
            display_name: displayName,
            allowed_services: services,
            notes: notes
          })
        });
        const res = await resp.json();
        if (resp.ok && res.success) {
          playLcarsAcknowledge();
          closeUserFormModal();
          loadLcarsUsers();
          loadLcarsAuditLog();
        } else {
          alert('Fehler beim Erstellen: ' + (res.error || 'Unbekannt'));
        }
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function toggleUserActive(userId, currentActive) {
    playLcarsBeep(600, 800);
    try {
      const resp = await fetch('/api/users/' + userId, {
        method: 'PUT',
        headers: getLcarsAuthHeader(),
        body: JSON.stringify({ is_active: !currentActive })
      });
      const res = await resp.json();
      if (resp.ok && res.success) {
        playLcarsAcknowledge();
        loadLcarsUsers();
        loadLcarsAuditLog();
      } else {
        alert('Fehler beim Ändern des Status: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  function openChangePasswordModal(userId) {
    const user = (typeof lcarsUsersList !== 'undefined' ? lcarsUsersList : []).find(u => u.id === userId);
    const username = user ? user.username : ('ID ' + userId);
    playLcarsBeep(700, 900);
    document.getElementById('pwdModalUserId').value = userId;
    const nameEl = document.getElementById('pwdModalUsername');
    if (nameEl) nameEl.textContent = username;
    const pInput = document.getElementById('pwdModalNewPassword');
    if (pInput) pInput.value = '';
    const modal = document.getElementById('userPasswordModal');
    if (modal) modal.style.display = 'flex';
    if (pInput) pInput.focus();
  }

  function closeChangePasswordModal() {
    playLcarsBeep(440, 220);
    const modal = document.getElementById('userPasswordModal');
    if (modal) modal.style.display = 'none';
  }

  async function submitChangeUserPassword(event) {
    if (event) event.preventDefault();
    const userId = document.getElementById('pwdModalUserId').value;
    const newPwd = document.getElementById('pwdModalNewPassword').value;
    if (!newPwd || newPwd.length < 6) {
      alert('Passwort muss mindestens 6 Zeichen lang sein.');
      return;
    }
    try {
      const resp = await fetch('/api/users/' + userId + '/password', {
        method: 'POST',
        headers: getLcarsAuthHeader(),
        body: JSON.stringify({ new_password: newPwd })
      });
      const res = await resp.json();
      if (resp.ok && res.success) {
        playLcarsAcknowledge();
        closeChangePasswordModal();
        loadLcarsAuditLog();
        alert('Passwort für Benutzer erfolgreich aktualisiert!');
      } else {
        alert('Fehler beim Ändern des Passworts: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function deleteLcarsUser(userId) {
    const user = (typeof lcarsUsersList !== 'undefined' ? lcarsUsersList : []).find(u => u.id === userId);
    const username = user ? user.username : ('ID ' + userId);
    playLcarsBeep(300, 150);
    if (!confirm('LCARS SICHERHEITSABFRAGE:\\n\\nSoll der Benutzer "' + username + '" wirklich gelöscht werden?')) {
      return;
    }
    try {
      const resp = await fetch('/api/users/' + userId, {
        method: 'DELETE',
        headers: getLcarsAuthHeader()
      });
      const res = await resp.json();
      if (resp.ok && res.success) {
        playLcarsAcknowledge();
        loadLcarsUsers();
        loadLcarsAuditLog();
      } else {
        alert('Fehler beim Löschen: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function loadLcarsAuditLog() {
    const box = document.getElementById('lcarsAuditLogList');
    if (!box) return;
    try {
      const resp = await fetch('/api/users/audit-log?limit=25', { headers: getLcarsAuthHeader() });
      if (resp.ok) {
        const data = await resp.json();
        const logs = data.audit_log || [];
        if (logs.length === 0) {
          box.innerHTML = '<div style="color:#666;">Keine Authentifizierungsereignisse protokolliert.</div>';
          return;
        }
        let html = '';
        logs.forEach(l => {
          let color = '#aaa';
          if (l.event.includes('SUCCESS') || l.event.includes('CREATED')) color = '#44dd88';
          else if (l.event.includes('FAILED') || l.event.includes('DENIED')) color = 'var(--c-red)';
          else if (l.event.includes('DELETED') || l.event.includes('UPDATED')) color = 'var(--c-primary)';

          const ipStr = l.ip ? ' [' + escapeHtml(l.ip) + ']' : '';
          const svcStr = l.target_service ? ' (' + escapeHtml(l.target_service) + ')' : '';
          const detailsStr = l.details ? ' - ' + escapeHtml(l.details) : '';

          html += '<div style="margin-bottom:2px;">' +
            '<span style="color:#666;">' + escapeHtml(l.timestamp) + '</span> ' +
            '<span style="color:' + color + '; font-weight:700;">' + escapeHtml(l.event) + '</span> ' +
            '<span style="color:var(--c-gold);">' + escapeHtml(l.username) + '</span>' + svcStr + ipStr +
            '<span style="color:#888;">' + detailsStr + '</span>' +
          '</div>';
        });
        box.innerHTML = html;
      }
    } catch (e) {
      console.warn('loadLcarsAuditLog error:', e);
    }
  }


  // ==========================================================================
  // LCARS PARTNERINNEN-ZYKLUS TRACKER CONTROLLER
  // ==========================================================================
  var cycleChart = null;
  var cyclePartners = [];
  var activePartnerId = null;

  const PARTNER_PALETTE = [
    { border: '#eb943a', bg: 'rgba(235, 148, 58, 0.12)', fill: 'rgba(235, 148, 58, 0.07)', name: 'LCARS Amber' },
    { border: '#baa4e5', bg: 'rgba(186, 164, 229, 0.12)', fill: 'rgba(186, 164, 229, 0.07)', name: 'LCARS Lilac' },
    { border: '#4cd964', bg: 'rgba(76, 217, 100, 0.12)', fill: 'rgba(76, 217, 100, 0.07)', name: 'LCARS Green' },
    { border: '#5ac8fa', bg: 'rgba(90, 200, 250, 0.12)', fill: 'rgba(90, 200, 250, 0.07)', name: 'LCARS Cyan' },
    { border: '#ff2d55', bg: 'rgba(255, 45, 85, 0.12)', fill: 'rgba(255, 45, 85, 0.07)', name: 'LCARS Rose' },
    { border: '#ffcc00', bg: 'rgba(255, 204, 0, 0.12)', fill: 'rgba(255, 204, 0, 0.07)', name: 'LCARS Gold' },
    { border: '#ff9500', bg: 'rgba(255, 149, 0, 0.12)', fill: 'rgba(255, 149, 0, 0.07)', name: 'LCARS Orange' },
    { border: '#af52de', bg: 'rgba(175, 82, 222, 0.12)', fill: 'rgba(175, 82, 222, 0.07)', name: 'LCARS Purple' }
  ];

  async function loadCycleData() {
    try {
      const resp = await fetch('/api/cycle/partners');
      if (resp.ok) {
        const data = await resp.json();
        cyclePartners = data.partners || [];
        renderCycleUI();
      }
    } catch (e) {
      console.warn('Fehler beim Laden der Zyklusdaten:', e);
    }
  }

  function renderCycleUI() {
    const emptyState = document.getElementById('cycleEmptyState');
    const activeContent = document.getElementById('cycleActiveContent');
    const pillsContainer = document.getElementById('cyclePartnerPills');
    const countBadge = document.getElementById('multiCycleCountBadge');

    if (!cyclePartners || cyclePartners.length === 0) {
      if (emptyState) emptyState.style.display = 'block';
      if (activeContent) activeContent.style.display = 'none';
      if (pillsContainer) pillsContainer.innerHTML = '';
      return;
    }

    if (emptyState) emptyState.style.display = 'none';
    if (activeContent) activeContent.style.display = 'block';

    if (countBadge) {
      countBadge.textContent = `${cyclePartners.length} PARTNERIN${cyclePartners.length > 1 ? 'NEN' : ''} ERFASST // VERGLEICHSGRAPH`;
    }

    // Determine active partner for detail inspect
    let activeP = cyclePartners.find(p => p.id === activePartnerId);
    if (!activeP) {
      activeP = cyclePartners[0];
      activePartnerId = activeP.id;
    }

    // 1. Render Multi-Partner Badges at the top
    renderMultiPartnerBadges();

    // 2. Render Combined Multi-Partner Chart (1-30 Days) directly at top
    renderCombinedCycleChart();

    // 3. Render Profile Switcher Pills
    if (pillsContainer) {
      pillsContainer.innerHTML = '';
      cyclePartners.forEach((p, idx) => {
        const pal = PARTNER_PALETTE[idx % PARTNER_PALETTE.length];
        const isActive = (p.id === activePartnerId);
        const pill = document.createElement('button');
        pill.className = 'left-action-btn' + (isActive ? ' active-range' : '');
        pill.style.padding = '0.35rem 0.85rem';
        pill.style.fontSize = '0.82rem';
        pill.style.borderColor = isActive ? pal.border : '#666';
        pill.style.color = isActive ? '#ffffff' : '#bbb';
        pill.innerHTML = `<span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${pal.border}; margin-right:4px;"></span> <strong>${escapeHtml(p.name)}</strong> <span style="font-size:0.75rem; color:${isActive ? 'var(--c-gold)' : '#888'};">★ Tag ${p.current_day}/${p.cycle_duration}</span>`;
        pill.onclick = () => {
          playLcarsBeep(880, 1320);
          activePartnerId = p.id;
          renderCycleUI();
        };
        pillsContainer.appendChild(pill);
      });
    }

    // 4. Render Active Partner Details (vitals, tape)
    renderActivePartner(activeP);

    // 5. Render Partners Overview Grid
    renderPartnersOverviewGrid();
  }

  function renderMultiPartnerBadges() {
    const container = document.getElementById('cycleMultiPartnerBadges');
    if (!container) return;
    container.innerHTML = '';

    cyclePartners.forEach((p, idx) => {
      const pal = PARTNER_PALETTE[idx % PARTNER_PALETTE.length];
      const isSelected = (p.id === activePartnerId);
      const badge = document.createElement('div');
      badge.style.background = isSelected ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.55)';
      badge.style.border = `1px solid ${pal.border}`;
      badge.style.borderLeft = `5px solid ${pal.border}`;
      badge.style.borderRadius = '5px';
      badge.style.padding = '0.35rem 0.75rem';
      badge.style.display = 'inline-flex';
      badge.style.alignItems = 'center';
      badge.style.gap = '0.5rem';
      badge.style.cursor = 'pointer';
      badge.style.transition = 'all 0.2s ease';
      badge.style.fontFamily = 'var(--mono-family)';
      badge.style.fontSize = '0.82rem';

      const phaseName = p.current_phase ? p.current_phase.name : '';
      badge.innerHTML = `
        <span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:${pal.border}; box-shadow:0 0 6px ${pal.border};"></span>
        <strong style="color:#ffffff; font-family:var(--font-family); font-size:0.95rem;">${escapeHtml(p.name)}</strong>
        <span style="background:${pal.border}; color:#000000; font-weight:800; padding:0.12rem 0.45rem; border-radius:3px; font-size:0.75rem;">★ HEUTE: TAG ${p.current_day}/${p.cycle_duration}</span>
        <span style="color:#ddd; font-size:0.75rem;">${escapeHtml(phaseName)}</span>
      `;
      badge.onclick = () => {
        playLcarsBeep(880, 1320);
        activePartnerId = p.id;
        renderCycleUI();
        const activeSection = document.getElementById('activePartnerName');
        if (activeSection) {
          activeSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      };
      container.appendChild(badge);
    });
  }

  function renderActivePartner(p) {
    const nameEl = document.getElementById('activePartnerName');
    const subEl = document.getElementById('activePartnerSub');
    const tapePartnerEl = document.getElementById('tapePartnerName');
    const curDayEl = document.getElementById('dispCurrentDay');
    const cycleDurEl = document.getElementById('dispCycleDuration');
    const progressEl = document.getElementById('dispCycleProgress');
    const phaseBadgeEl = document.getElementById('dispPhaseBadge');
    const phaseDescEl = document.getElementById('dispPhaseDesc');
    const fertilityEl = document.getElementById('dispFertilityStatus');
    const daysUntilEl = document.getElementById('dispDaysUntilNext');
    const nextDateEl = document.getElementById('dispNextDate');

    if (nameEl) nameEl.textContent = p.name;
    if (subEl) subEl.textContent = `ZYKLUS: ${p.cycle_duration} TAGE // BLUTUNG: ${p.period_duration} TAGE // START: ${p.start_date_formatted || p.start_date}`;
    if (tapePartnerEl) tapePartnerEl.textContent = p.name.toUpperCase();
    if (curDayEl) curDayEl.textContent = `TAG ${p.current_day}`;
    if (cycleDurEl) cycleDurEl.textContent = `/ ${p.cycle_duration} TAGE`;
    if (progressEl) progressEl.style.width = `${p.progress_percent || 50}%`;

    if (phaseBadgeEl && p.current_phase) {
      phaseBadgeEl.textContent = p.current_phase.name || 'UNBEKANNT';
      phaseBadgeEl.style.backgroundColor = p.current_phase.color || '#baa4e5';
      phaseBadgeEl.style.color = (p.current_phase.key === 'menstruation' ? '#ffffff' : '#000000');
    }
    if (phaseDescEl && p.current_phase) {
      phaseDescEl.textContent = p.current_phase.desc || '';
    }

    if (fertilityEl && p.current_phase) {
      fertilityEl.textContent = (p.current_phase.fertility || 'Normal').toUpperCase();
    }
    if (daysUntilEl) daysUntilEl.textContent = p.days_until_next_period;
    if (nextDateEl) nextDateEl.textContent = p.next_period_date;

    // Render 30-Day Tape for Active Partner
    renderCycleTape(p.days_graph || [], p.current_day);
  }

  function renderCombinedCycleChart() {
    const canvas = document.getElementById('cycleChart');
    if (!canvas) return;

    const existing = Chart.getChart(canvas);
    if (existing) {
      try { existing.destroy(); } catch (e) {}
    }

    if (!cyclePartners || cyclePartners.length === 0) return;

    const labels = Array.from({ length: 30 }, (_, i) => 'Tag ' + (i + 1));
    const datasets = [];

    cyclePartners.forEach((p, idx) => {
      const pal = PARTNER_PALETTE[idx % PARTNER_PALETTE.length];
      const isSelected = (p.id === activePartnerId);
      const curDay = p.current_day;
      const days = p.days_graph || [];

      const values = [];
      const pointRadii = [];
      const pointHoverRadii = [];
      const pointBgColors = [];
      const pointBorderColors = [];
      const pointBorderWidths = [];

      for (let d = 1; d <= 30; d++) {
        const dayObj = days.find(x => x.day === d);
        values.push(dayObj ? dayObj.curve_value : 20);

        if (d === curDay) {
          pointRadii.push(isSelected ? 11 : 9);
          pointHoverRadii.push(isSelected ? 15 : 13);
          pointBgColors.push('#ffffff');
          pointBorderColors.push(pal.border);
          pointBorderWidths.push(3.5);
        } else {
          pointRadii.push(3);
          pointHoverRadii.push(6);
          pointBgColors.push(pal.border);
          pointBorderColors.push('#000000');
          pointBorderWidths.push(1);
        }
      }

      datasets.push({
        type: 'line',
        label: `${p.name} (★ Tag ${curDay}/${p.cycle_duration})`,
        data: values,
        borderColor: pal.border,
        backgroundColor: pal.fill,
        borderWidth: isSelected ? 3.5 : 2.2,
        tension: 0.35,
        fill: true,
        pointRadius: pointRadii,
        pointHoverRadius: pointHoverRadii,
        pointBackgroundColor: pointBgColors,
        pointBorderColor: pointBorderColors,
        pointBorderWidth: pointBorderWidths,
        order: isSelected ? 1 : 2
      });
    });

    // Custom Chart.js plugin to draw vertical highlight bands, indicator lines and name tags
    const multiHighlightPlugin = {
      id: 'multiHighlightPlugin',
      beforeDatasetsDraw(chart) {
        const { ctx, chartArea, scales: { x } } = chart;
        if (!chartArea || !x || !cyclePartners || cyclePartners.length === 0) return;

        cyclePartners.forEach((p, idx) => {
          const day = p.current_day;
          if (day >= 1 && day <= 30) {
            const xPos = x.getPixelForValue(day - 1);
            const pal = PARTNER_PALETTE[idx % PARTNER_PALETTE.length];
            const isSelected = (p.id === activePartnerId);

            ctx.save();
            // Vertical glow band around current day
            const bandWidth = Math.max(16, (chartArea.width / 30) * 0.75);
            ctx.fillStyle = isSelected ? pal.bg.replace('0.12', '0.24') : pal.bg.replace('0.12', '0.12');
            ctx.fillRect(xPos - bandWidth / 2, chartArea.top, bandWidth, chartArea.height);

            // Vertical dashed indicator line
            ctx.strokeStyle = pal.border;
            ctx.lineWidth = isSelected ? 2.2 : 1.2;
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.moveTo(xPos, chartArea.top);
            ctx.lineTo(xPos, chartArea.bottom);
            ctx.stroke();

            // Marker badge tag at top
            ctx.setLineDash([]);
            const tagText = `★ ${p.name} (T${day})`;
            ctx.font = `bold ${isSelected ? '11px' : '10px'} "Share Tech Mono", monospace`;
            const textMetrics = ctx.measureText(tagText);
            const tagW = textMetrics.width + 10;
            const tagH = 15;
            const tagY = chartArea.top - tagH - 4;

            ctx.fillStyle = pal.border;
            ctx.fillRect(xPos - tagW / 2, tagY, tagW, tagH);
            ctx.fillStyle = '#000000';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(tagText, xPos, tagY + tagH / 2 + 0.5);

            ctx.restore();
          }
        });
      }
    };

    cycleChart = new Chart(canvas, {
      data: {
        labels: labels,
        datasets: datasets
      },
      plugins: [multiHighlightPlugin],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: {
          padding: {
            top: 24,
            right: 15,
            bottom: 5,
            left: 10
          }
        },
        animation: { duration: 350 },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: {
              color: '#ffffff',
              font: { family: 'Share Tech Mono', size: 12 },
              boxWidth: 14,
              padding: 14,
              usePointStyle: true,
              pointStyle: 'circle'
            }
          },
          tooltip: {
            backgroundColor: 'rgba(10, 10, 18, 0.95)',
            titleColor: '#eb943a',
            titleFont: { family: 'Antonio', size: 14 },
            bodyColor: '#ffffff',
            bodyFont: { family: 'Share Tech Mono', size: 12 },
            borderColor: 'rgba(235, 148, 58, 0.6)',
            borderWidth: 1,
            padding: 10,
            callbacks: {
              title: function(items) {
                if (!items || items.length === 0) return '';
                const dayNum = items[0].dataIndex + 1;
                const todayPartners = cyclePartners.filter(p => p.current_day === dayNum);
                let t = `TAG ${dayNum} // BIO-STATUS`;
                if (todayPartners.length > 0) {
                  t += ` ★ HEUTE: ` + todayPartners.map(p => p.name).join(', ');
                }
                return t;
              },
              label: function(item) {
                const pIdx = item.datasetIndex;
                const partner = cyclePartners[pIdx];
                if (!partner) return '';
                const dayNum = item.dataIndex + 1;
                const d = (partner.days_graph || []).find(x => x.day === dayNum);
                const isCur = (partner.current_day === dayNum);
                const phaseStr = d ? `${d.phase_name} (${d.fertility})` : '';
                return ` ${partner.name}: ${item.raw}% - ${phaseStr}${isCur ? ' ★ HEUTE!' : ''}`;
              }
            }
          }
        },
        scales: {
          x: {
            grid: {
              color: function(ctx) {
                const day = ctx.index + 1;
                const isToday = cyclePartners.some(p => p.current_day === day);
                return isToday ? 'rgba(255, 255, 255, 0.25)' : 'rgba(255, 255, 255, 0.05)';
              },
              lineWidth: function(ctx) {
                const day = ctx.index + 1;
                return cyclePartners.some(p => p.current_day === day) ? 2 : 1;
              }
            },
            ticks: {
              color: function(ctx) {
                const day = ctx.index + 1;
                const matching = cyclePartners.find(p => p.current_day === day);
                if (matching) {
                  const idx = cyclePartners.indexOf(matching);
                  return PARTNER_PALETTE[idx % PARTNER_PALETTE.length].border;
                }
                return '#888888';
              },
              font: function(ctx) {
                const day = ctx.index + 1;
                const isToday = cyclePartners.some(p => p.current_day === day);
                return {
                  family: 'Share Tech Mono',
                  size: isToday ? 12 : 10,
                  weight: isToday ? 'bold' : 'normal'
                };
              }
            }
          },
          y: {
            min: 0,
            max: 115,
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: {
              color: '#777777',
              font: { family: 'Share Tech Mono', size: 10 },
              callback: function(v) {
                if (v === 20) return 'Ruhe / Regel';
                if (v === 55) return 'Aktiv';
                if (v === 100) return 'Peak (Eisprung)';
                return '';
              }
            }
          }
        }
      }
    });
  }

  function renderCycleTape(daysGraph, currentDay) {
    const container = document.getElementById('cycleTapeContainer');
    if (!container) return;
    container.innerHTML = '';

    daysGraph.forEach(d => {
      const isCur = (d.day === currentDay);
      const cell = document.createElement('div');
      cell.className = 'cycle-tape-cell' + (isCur ? ' current-day' : '');
      cell.style.backgroundColor = isCur ? '#ffffff' : d.color;
      cell.style.color = (d.phase_key === 'menstruation' && !isCur) ? '#ffffff' : '#000000';
      cell.innerHTML = `<span>${isCur ? '★' : ''}${d.day}</span>`;
      cell.title = `Tag ${d.day}: ${d.phase_name} (${d.fertility})`;
      cell.onclick = () => {
        playLcarsBeep(700, 1050);
        inspectDay(d.day, d.phase_name, d.fertility, d.desc, isCur);
      };
      container.appendChild(cell);
    });

    // Default inspection: current day
    const curObj = daysGraph.find(d => d.day === currentDay) || daysGraph[0];
    if (curObj) {
      inspectDay(curObj.day, curObj.phase_name, curObj.fertility, curObj.desc, true);
    }
  }

  function inspectDay(day, phaseName, fertility, desc, isCur) {
    const dayLabel = document.getElementById('inspectDayLabel');
    const phaseLabel = document.getElementById('inspectPhaseLabel');
    const todayBadge = document.getElementById('inspectTodayBadge');
    const infoText = document.getElementById('inspectInfoText');

    if (dayLabel) dayLabel.textContent = `TAG ${day}`;
    if (phaseLabel) phaseLabel.textContent = `${phaseName.toUpperCase()} // FRUCHTBARKEIT: ${fertility.toUpperCase()}`;
    if (todayBadge) todayBadge.style.display = isCur ? 'inline' : 'none';
    if (infoText) infoText.textContent = desc || '';
  }

  function renderPartnersOverviewGrid() {
    const grid = document.getElementById('partnersGrid');
    if (!grid) return;
    grid.innerHTML = '';

    cyclePartners.forEach(p => {
      const isSelected = (p.id === activePartnerId);
      const card = document.createElement('div');
      card.style.background = isSelected ? 'rgba(186, 164, 229, 0.15)' : 'rgba(0,0,0,0.4)';
      card.style.border = `1px solid ${isSelected ? 'var(--c-secondary)' : 'rgba(255,255,255,0.1)'}`;
      card.style.borderRadius = '6px';
      card.style.padding = '0.85rem';
      card.style.cursor = 'pointer';
      card.style.transition = 'all 0.2s';
      card.onclick = () => {
        playLcarsBeep(880, 1320);
        activePartnerId = p.id;
        renderCycleUI();
      };

      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.4rem;">
          <strong style="font-size:1.1rem; color:#fff; font-family:var(--font-family);">${escapeHtml(p.name)}</strong>
          <span style="font-family:var(--mono-family); font-size:0.8rem; color:${p.current_phase ? p.current_phase.color : 'var(--c-secondary)'}; font-weight:700;">
            TAG ${p.current_day}/${p.cycle_duration}
          </span>
        </div>
        <div style="font-size:0.8rem; color:#bbb; font-family:var(--mono-family); margin-bottom:0.3rem;">
          Phase: <span style="color:#fff;">${p.current_phase ? p.current_phase.name : '--'}</span>
        </div>
        <div style="font-size:0.75rem; color:#888; font-family:var(--mono-family);">
          Nächste Periode in: <strong style="color:var(--c-gold);">${p.days_until_next_period} Tagen</strong> (${p.next_period_date})
        </div>
      `;
      grid.appendChild(card);
    });
  }

  function openAddPartnerModal() {
    playLcarsBeep(880, 1320);
    const modal = document.getElementById('partnerFormModal');
    const title = document.getElementById('partnerModalTitle');
    const idInp = document.getElementById('formPartnerId');
    const nameInp = document.getElementById('formPartnerName');
    const cycleInp = document.getElementById('formCycleDuration');
    const periodInp = document.getElementById('formPeriodDuration');
    const dateInp = document.getElementById('formStartDate');
    const notesInp = document.getElementById('formNotes');

    if (title) title.textContent = 'PARTNERIN ANLEGEN';
    if (idInp) idInp.value = '';
    if (nameInp) nameInp.value = '';
    if (cycleInp) cycleInp.value = '28';
    if (periodInp) periodInp.value = '5';
    if (dateInp) dateInp.value = new Date().toISOString().split('T')[0];
    if (notesInp) notesInp.value = '';

    if (modal) {
      modal.style.display = 'flex';
      if (nameInp) nameInp.focus();
    }
  }

  function openEditPartnerModal() {
    const p = cyclePartners.find(x => x.id === activePartnerId);
    if (!p) return;

    playLcarsBeep(880, 1320);
    const modal = document.getElementById('partnerFormModal');
    const title = document.getElementById('partnerModalTitle');
    const idInp = document.getElementById('formPartnerId');
    const nameInp = document.getElementById('formPartnerName');
    const cycleInp = document.getElementById('formCycleDuration');
    const periodInp = document.getElementById('formPeriodDuration');
    const dateInp = document.getElementById('formStartDate');
    const notesInp = document.getElementById('formNotes');

    if (title) title.textContent = `PARTNERIN BEARBEITEN // ${p.name.toUpperCase()}`;
    if (idInp) idInp.value = p.id;
    if (nameInp) nameInp.value = p.name;
    if (cycleInp) cycleInp.value = p.cycle_duration || 28;
    if (periodInp) periodInp.value = p.period_duration || 5;
    if (dateInp) dateInp.value = p.start_date || new Date().toISOString().split('T')[0];
    if (notesInp) notesInp.value = p.notes || '';

    if (modal) {
      modal.style.display = 'flex';
      if (nameInp) nameInp.focus();
    }
  }

  function closePartnerModal() {
    playLcarsBeep(440, 220);
    const modal = document.getElementById('partnerFormModal');
    if (modal) modal.style.display = 'none';
  }

  function handlePartnerModalBackdropClick(e) {
    if (e.target && e.target.id === 'partnerFormModal') {
      closePartnerModal();
    }
  }

  async function savePartnerData(e) {
    e.preventDefault();
    const idInp = document.getElementById('formPartnerId');
    const nameInp = document.getElementById('formPartnerName');
    const cycleInp = document.getElementById('formCycleDuration');
    const periodInp = document.getElementById('formPeriodDuration');
    const dateInp = document.getElementById('formStartDate');
    const notesInp = document.getElementById('formNotes');

    const partnerId = idInp ? idInp.value.trim() : '';
    const payload = {
      name: nameInp ? nameInp.value.trim() : '',
      cycle_duration: cycleInp ? parseInt(cycleInp.value) || 28 : 28,
      period_duration: periodInp ? parseInt(periodInp.value) || 5 : 5,
      start_date: dateInp ? dateInp.value : '',
      notes: notesInp ? notesInp.value.trim() : ''
    };

    try {
      let resp;
      if (partnerId) {
        resp = await fetch(`/api/cycle/partners/${partnerId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
      } else {
        resp = await fetch('/api/cycle/partners', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
      }
      const res = await resp.json();
      if (res.success) {
        playLcarsAcknowledge();
        closePartnerModal();
        if (res.partner && res.partner.id) {
          activePartnerId = res.partner.id;
        }
        await loadCycleData();
      } else {
        alert('Fehler beim Speichern: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function confirmDeletePartner() {
    const p = cyclePartners.find(x => x.id === activePartnerId);
    if (!p) return;

    if (!confirm(`Möchten Sie den Zyklus-Eintrag für "${p.name}" wirklich löschen?`)) {
      return;
    }

    try {
      const resp = await fetch(`/api/cycle/partners/${p.id}`, { method: 'DELETE' });
      const res = await resp.json();
      if (res.success) {
        playLcarsBeep(600, 300);
        activePartnerId = null;
        await loadCycleData();
      } else {
        alert('Fehler beim Löschen: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  async function triggerNewCycleToday() {
    const p = cyclePartners.find(x => x.id === activePartnerId);
    if (!p) return;

    if (!confirm(`Neuen Zyklus für "${p.name}" mit heutigem Datum als Tag 1 starten?`)) {
      return;
    }

    try {
      const todayStr = new Date().toISOString().split('T')[0];
      const resp = await fetch(`/api/cycle/partners/${p.id}/start-cycle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start_date: todayStr })
      });
      const res = await resp.json();
      if (res.success) {
        playLcarsAcknowledge();
        await loadCycleData();
      } else {
        alert('Fehler beim Aktualisieren: ' + (res.error || 'Unbekannt'));
      }
    } catch (err) {
      alert('Netzwerkfehler: ' + err);
    }
  }

  // Window Resize Listener
  window.addEventListener('resize', () => {
    if (historyChart) historyChart.resize();
    else {
      const c = document.getElementById('historyChart');
      if (c) renderNativeHistoryChart(c, currentHistorySamples);
    }
    if (hermesChart) hermesChart.resize();
    else {
      const c = document.getElementById('hermesChart');
      if (c) renderNativeDonutChart(c, initialStats?.hermes?.models || []);
    }
    if (nineRouterTimelineChart) nineRouterTimelineChart.resize();
    if (nineRouterModelChart) nineRouterModelChart.resize();
    if (cycleChart) cycleChart.resize();
  });

  // ==========================================================================
  // LCARS PULSECAST CONTROLLER (MEDIA & DOWNLOAD HUB)
  // ==========================================================================
  let pulsecastIsOnline = false;
  let pulsecastPollTimer = null;
  let pulsecastActiveSubtab = 'downloads';
  let pulsecastCatalogCategory = 'Filme';
  let pulsecastCatalogSubcategory = 'all';
  let pulsecastCatalogSearchQuery = '';
  let pulsecastCatalogPage = 1;
  let pulsecastCatalogTotalPages = 1;
  let pulsecastCurrentSeriesEpisodes = [];
  let pulsecastActiveSeries = null;
  let pulsecastDownloadsCache = [];
  let pulsecastXdccSource = 'xdcc';
  let currentPulsecastStreamUrl = '';
  let currentPulsecastFilename = '';
  let currentPulsecastDisplayTitle = '';
  let currentPulsecastAudioTranscode = false;
  let currentPulsecastNeedsTranscode = false;

  function escapeJsString(str) {
    if (!str) return '';
    return JSON.stringify(String(str)).slice(1, -1).replace(/'/g, "\\\\'");
  }

  function getPulsecastHeaders() {
    const code = sessionStorage.getItem('lcars_auth_code') || currentAuthCode || '0901';
    return {
      'Content-Type': 'application/json',
      'X-Command-Code': code
    };
  }

  async function checkPulsecastStatus() {
    try {
      const resp = await fetch('/api/pulsecast/status');
      if (resp.ok) {
        const data = await resp.json();
        pulsecastIsOnline = !!data.online;
      } else {
        pulsecastIsOnline = false;
      }
    } catch (e) {
      pulsecastIsOnline = false;
    }
    updatePulsecastStatusBadge();
    return pulsecastIsOnline;
  }

  function updatePulsecastStatusBadge() {
    const badge = document.getElementById('pulsecastOnlineBadge');
    if (!badge) return;
    if (pulsecastIsOnline) {
      badge.textContent = 'ONLINE // PORT 3000';
      badge.style.backgroundColor = '#44dd88';
      badge.style.color = '#000000';
    } else {
      badge.textContent = 'SUBRAUM-RELAY OFFLINE';
      badge.style.backgroundColor = 'var(--c-red)';
      badge.style.color = '#ffffff';
    }
  }

  async function initPulsecastSection() {
    const isLocked = isCategoryLocked('pulsecast');
    const gateView = document.getElementById('pulsecastGateView');
    const offlineNotice = document.getElementById('pulsecastOfflineNotice');
    const activeContent = document.getElementById('pulsecastActiveContent');

    if (isLocked) {
      if (gateView) gateView.style.display = 'block';
      if (offlineNotice) offlineNotice.style.display = 'none';
      if (activeContent) activeContent.style.display = 'none';
      stopPulsecastPolling();
      return;
    }

    if (gateView) gateView.style.display = 'none';

    await checkPulsecastStatus();
    if (!pulsecastIsOnline) {
      if (offlineNotice) offlineNotice.style.display = 'block';
      if (activeContent) activeContent.style.display = 'none';
      stopPulsecastPolling();
      return;
    }

    if (offlineNotice) offlineNotice.style.display = 'none';
    if (activeContent) activeContent.style.display = 'block';

    loadPulsecastLocalCounts();
    switchPulsecastSubtab(pulsecastActiveSubtab || 'downloads');
  }

  function refreshPulsecastData(force) {
    playLcarsBeep(1200, 1600);
    initPulsecastSection();
  }

  function switchPulsecastSubtab(subtab) {
    playLcarsBeep(1100, 1400);
    pulsecastActiveSubtab = subtab;

    document.querySelectorAll('[id^="pulsecast-tab-btn-"]').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById('pulsecast-tab-btn-' + subtab);
    if (activeBtn) activeBtn.classList.add('active');

    document.querySelectorAll('.pulsecast-subview').forEach(view => view.style.display = 'none');
    const activeView = document.getElementById('pulsecast-subview-' + subtab);
    if (activeView) activeView.style.display = 'block';

    if (subtab === 'downloads') {
      loadPulsecastDownloads();
      startPulsecastPolling();
    } else {
      stopPulsecastPolling();
    }

    if (subtab === 'local') {
      loadPulsecastLocal(pulsecastLocalPage);
    }

    if (subtab === 'catalog') {
      loadPulsecastCatalog(pulsecastCatalogPage);
    }

    if (subtab === 'xdcc') {
      const inp = document.getElementById('pulsecastXdccInput');
      if (inp) inp.focus();
    }
  }

  function startPulsecastPolling() {
    stopPulsecastPolling();
    pulsecastPollTimer = setInterval(() => {
      if (currentCategory === 'pulsecast' && pulsecastActiveSubtab === 'downloads' && pulsecastIsOnline) {
        loadPulsecastDownloads(true);
      }
    }, 2500);
  }

  function stopPulsecastPolling() {
    if (pulsecastPollTimer) {
      clearInterval(pulsecastPollTimer);
      pulsecastPollTimer = null;
    }
  }

  function formatBytes(bytes, decimals = 1) {
    if (!bytes || bytes <= 0) return '0 B';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
  }

  function formatEta(seconds) {
    if (!seconds || seconds <= 0 || !isFinite(seconds)) return '--';
    const s = Math.round(seconds);
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    if (h > 0) return `${h}h ${m % 60}m`;
    if (m > 0) return `${m}m ${s % 60}s`;
    return `${s}s`;
  }

  async function loadPulsecastDownloads(isPoll = false) {
    try {
      const resp = await fetch('/api/pulsecast/downloads', {
        headers: getPulsecastHeaders()
      });
      if (resp.status === 403) {
        initPulsecastSection();
        return;
      }
      if (!resp.ok) {
        if (resp.status === 503) {
          pulsecastIsOnline = false;
          initPulsecastSection();
        }
        return;
      }
      const downloads = await resp.json();
      pulsecastDownloadsCache = Array.isArray(downloads) ? downloads : [];
      renderPulsecastDownloads(pulsecastDownloadsCache);
    } catch (e) {
      console.warn('Fehler beim Laden der PulseCast Downloads:', e);
    }
  }

  function renderPulsecastDownloads(list) {
    const container = document.getElementById('pulsecastDownloadsList');
    const emptyEl = document.getElementById('pulsecastDownloadsEmpty');
    const badgeEl = document.getElementById('pulsecastDownloadsCountBadge');
    const summaryEl = document.getElementById('pulsecastQueueSummary');
    const speedBadge = document.getElementById('pulsecastActiveSpeedBadge');

    let totalSpeed = 0;
    let activeCount = 0;

    list.forEach(d => {
      if (d.status === 'downloading' || d.status === 'dcc_downloading') {
        activeCount++;
        totalSpeed += (d.speed || 0);
      }
    });

    if (badgeEl) badgeEl.textContent = String(list.length);
    if (summaryEl) summaryEl.textContent = `${activeCount} AKTIV // ${list.length} GESAMT`;
    if (speedBadge) {
      const mbps = (totalSpeed / (1024 * 1024)).toFixed(2);
      speedBadge.textContent = `${mbps} MB/s`;
      speedBadge.style.color = (totalSpeed > 0) ? '#44dd88' : 'var(--c-butterscotch)';
    }

    if (!container) return;

    if (!list || list.length === 0) {
      container.innerHTML = '';
      if (emptyEl) emptyEl.style.display = 'block';
      return;
    }

    if (emptyEl) emptyEl.style.display = 'none';

    let html = '';
    list.forEach(item => {
      const id = item.id || '';
      const filename = item.filename || item.offeredFilename || 'Unbekannte Datei';
      const expectedSize = item.expectedSize || 0;
      const bytesReceived = item.bytesReceived || 0;
      const pct = (expectedSize > 0) ? Math.min(100, (bytesReceived / expectedSize * 100)).toFixed(1) : 0;
      const speedMb = ((item.speed || 0) / (1024 * 1024)).toFixed(2);
      const etaStr = formatEta(item.eta);
      const status = item.status || 'unknown';

      let statusBadge = '';
      let actionButtons = '';

      if (status === 'downloading' || status === 'dcc_downloading') {
        statusBadge = '<span class="badge-status" style="background:#44dd88; color:#000;">LÄUFT</span>';
        actionButtons = `
          <button type="button" class="left-action-btn" onclick="pausePulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:var(--c-gold); color:var(--c-gold);" title="Pausieren">
            ⏸ PAUSE
          </button>
          <button type="button" class="left-action-btn" onclick="cancelPulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:var(--c-red); color:var(--c-red);" title="Abbrechen">
            ✕ ABBRECHEN
          </button>
        `;
      } else if (status === 'paused') {
        statusBadge = '<span class="badge-status" style="background:var(--c-gold); color:#000;">PAUSIERT</span>';
        actionButtons = `
          <button type="button" class="left-action-btn" onclick="resumePulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:#44dd88; color:#44dd88;" title="Fortsetzen">
            ▶ WEITER
          </button>
          <button type="button" class="left-action-btn" onclick="cancelPulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:var(--c-red); color:var(--c-red);" title="Abbrechen">
            ✕ ABBRECHEN
          </button>
        `;
      } else if (status === 'queued' || status === 'connecting') {
        statusBadge = '<span class="badge-status" style="background:var(--c-blue); color:#000;">WARTESCHLANGE</span>';
        actionButtons = `
          <button type="button" class="left-action-btn" onclick="cancelPulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:var(--c-red); color:var(--c-red);" title="Aus Warteschlange entfernen">
            ✕ ABBRECHEN
          </button>
        `;
      } else if (status === 'completed') {
        statusBadge = '<span class="badge-status" style="background:var(--c-secondary); color:#000;">FERTIG</span>';
        actionButtons = `
          <button type="button" class="left-action-btn" onclick="openPulsecastPlayerModal('${escapeJsString(filename)}', '${escapeJsString(filename)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;" title="Im lokalen Media Player öffnen">
            ▶ IN PLAYER ÖFFNEN
          </button>
          <button type="button" class="left-action-btn" onclick="deletePulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:#888; color:#aaa;" title="Aus Liste entfernen">
            🗑 ENTFERNEN
          </button>
        `;
      } else {
        statusBadge = `<span class="badge-status" style="background:var(--c-red); color:#fff;">${escapeHtml(status.toUpperCase())}</span>`;
        actionButtons = `
          <button type="button" class="left-action-btn" onclick="deletePulsecastDownload('${encodeURIComponent(id)}')" style="padding:0.3rem 0.75rem; font-size:0.78rem; border-color:#888; color:#aaa;" title="Entfernen">
            🗑 ENTFERNEN
          </button>
        `;
      }

      const icon = filename.match(/\\.(mkv|mp4|avi|webm)$/i) ? '🎬' : '📦';

      html += `
        <div style="background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:0.85rem; border-left:4px solid var(--c-butterscotch);">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.4rem;">
            <div style="display:flex; align-items:center; gap:0.5rem; min-width:0; flex:1;">
              <span style="font-size:1.15rem; flex-shrink:0;">${icon}</span>
              <span style="font-family:var(--font-family); font-weight:700; font-size:0.95rem; color:#fff; word-break:break-all;" title="${escapeHtml(filename)}">
                ${escapeHtml(filename)}
              </span>
            </div>
            <div style="display:flex; align-items:center; gap:0.5rem;">
              ${statusBadge}
              ${actionButtons}
            </div>
          </div>

          <!-- LCARS Progress Bar -->
          <div class="pulsecast-progress-container">
            <div class="pulsecast-progress-fill" style="width:${pct}%;"></div>
          </div>

          <!-- Progress Details -->
          <div style="display:flex; justify-content:space-between; align-items:center; font-family:var(--mono-family); font-size:0.8rem; color:#aaa; flex-wrap:wrap; gap:0.4rem; margin-top:0.35rem;">
            <span>${pct}% // ${formatBytes(bytesReceived)} von ${formatBytes(expectedSize)}</span>
            <div style="display:flex; gap:0.8rem; align-items:center;">
              ${(status === 'downloading' || status === 'dcc_downloading') ? `<span style="color:#44dd88;">⚡ ${speedMb} MB/s</span><span style="color:var(--c-gold);">⏳ ${etaStr}</span>` : ''}
              ${item.server ? `<span style="color:#666;">[${escapeHtml(item.server)}]</span>` : ''}
            </div>
          </div>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  async function pausePulsecastDownload(encodedId) {
    playLcarsBeep(900, 1100);
    try {
      await fetch(`/api/pulsecast/download/${encodedId}/pause`, {
        method: 'POST',
        headers: getPulsecastHeaders()
      });
      loadPulsecastDownloads();
    } catch (e) {
      console.warn('Fehler bei Pause:', e);
    }
  }

  async function resumePulsecastDownload(encodedId) {
    playLcarsBeep(1100, 1400);
    try {
      await fetch(`/api/pulsecast/download/${encodedId}/resume`, {
        method: 'POST',
        headers: getPulsecastHeaders()
      });
      loadPulsecastDownloads();
    } catch (e) {
      console.warn('Fehler bei Resume:', e);
    }
  }

  async function cancelPulsecastDownload(encodedId) {
    playLcarsBeep(400, 200);
    try {
      await fetch(`/api/pulsecast/download/${encodedId}/cancel`, {
        method: 'POST',
        headers: getPulsecastHeaders()
      });
      loadPulsecastDownloads();
    } catch (e) {
      console.warn('Fehler bei Cancel:', e);
    }
  }

  async function deletePulsecastDownload(encodedId) {
    playLcarsBeep(500, 300);
    try {
      await fetch(`/api/pulsecast/download/${encodedId}`, {
        method: 'DELETE',
        headers: getPulsecastHeaders()
      });
      loadPulsecastDownloads();
    } catch (e) {
      console.warn('Fehler bei Delete:', e);
    }
  }

  // KATALOG CONTROLLER
  function setPulsecastCatalogCategory(cat) {
    playLcarsBeep(1000, 1300);
    pulsecastCatalogCategory = cat;
    pulsecastCatalogSubcategory = 'all';
    pulsecastCatalogPage = 1;

    ['Filme', 'Serien', 'all'].forEach(c => {
      const btn = document.getElementById('cat-pill-' + c);
      if (btn) {
        if (c === cat) {
          btn.classList.add('active');
          btn.style.background = (c === 'Serien') ? 'var(--c-secondary)' : 'var(--c-primary)';
          btn.style.color = '#000';
        } else {
          btn.classList.remove('active');
          btn.style.background = 'rgba(0,0,0,0.5)';
          btn.style.color = (c === 'Serien') ? 'var(--c-secondary)' : (c === 'Filme' ? 'var(--c-primary)' : '#aaa');
        }
      }
    });

    loadPulsecastCatalog(1);
  }

  function onPulsecastSubcatChanged() {
    const sel = document.getElementById('pulsecastSubcatSelect');
    if (sel) {
      pulsecastCatalogSubcategory = sel.value;
      pulsecastCatalogPage = 1;
      loadPulsecastCatalog(1);
    }
  }

  function pulsecastCatalogSearchTrigger() {
    const inp = document.getElementById('pulsecastCatalogSearchInput');
    pulsecastCatalogSearchQuery = inp ? inp.value.trim() : '';
    pulsecastCatalogPage = 1;
    loadPulsecastCatalog(1);
  }

  function pulsecastCatalogSearchClear() {
    const inp = document.getElementById('pulsecastCatalogSearchInput');
    if (inp) inp.value = '';
    pulsecastCatalogSearchQuery = '';
    pulsecastCatalogPage = 1;
    loadPulsecastCatalog(1);
  }

  function pulsecastChangePage(delta) {
    const target = pulsecastCatalogPage + delta;
    if (target >= 1 && target <= pulsecastCatalogTotalPages) {
      playLcarsBeep(1200, 1500);
      loadPulsecastCatalog(target);
    }
  }

  async function loadPulsecastCatalog(page = 1) {
    pulsecastCatalogPage = page;
    const grid = document.getElementById('pulsecastCatalogGrid');
    const loading = document.getElementById('pulsecastCatalogLoading');
    const emptyEl = document.getElementById('pulsecastCatalogEmpty');

    if (loading) loading.style.display = 'block';
    if (grid) grid.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';

    try {
      const params = new URLSearchParams({
        category: pulsecastCatalogCategory,
        subcategory: pulsecastCatalogSubcategory,
        search: pulsecastCatalogSearchQuery,
        page: String(page),
        limit: '40'
      });

      const resp = await fetch(`/api/pulsecast/media-library?${params.toString()}`, {
        headers: getPulsecastHeaders()
      });

      if (loading) loading.style.display = 'none';

      if (resp.status === 403) {
        initPulsecastSection();
        return;
      }

      if (!resp.ok) {
        if (resp.status === 503) {
          pulsecastIsOnline = false;
          initPulsecastSection();
        }
        return;
      }

      const data = await resp.json();
      const items = data.items || [];
      pulsecastCatalogTotalPages = data.totalPages || 1;

      // Update Subcategories dropdown dynamically
      if (Array.isArray(data.availableSubcategories)) {
        updatePulsecastSubcatDropdown(data.availableSubcategories);
      }

      // Update Counts
      if (data.counts) {
        const cFilme = document.getElementById('pulsecastCountFilme');
        const cSerien = document.getElementById('pulsecastCountSerien');
        const cLocal = document.getElementById('pulsecastLocalCountBadge');
        if (cFilme && data.counts.Filme !== undefined) cFilme.textContent = `(${data.counts.Filme})`;
        if (cSerien && data.counts.Serien !== undefined) cSerien.textContent = `(${data.counts.Serien})`;
        if (cLocal && data.counts.Lokal !== undefined) cLocal.textContent = String(data.counts.Lokal);
      }

      // Update pagination UI
      updatePulsecastPaginationUI(data.currentPage || page, pulsecastCatalogTotalPages, data.totalItems || 0);

      if (items.length === 0) {
        if (grid) grid.style.display = 'none';
        if (emptyEl) emptyEl.style.display = 'block';
      } else {
        if (grid) {
          grid.style.display = 'grid';
          renderPulsecastCatalogGrid(items);
        }
      }
    } catch (e) {
      if (loading) loading.style.display = 'none';
      console.warn('Fehler beim Laden des Katalogs:', e);
    }
  }

  function updatePulsecastSubcatDropdown(subcats) {
    const sel = document.getElementById('pulsecastSubcatSelect');
    if (!sel) return;
    const currentVal = pulsecastCatalogSubcategory;
    let html = '<option value="all">ALLE KATEGORIEN</option>';
    subcats.forEach(sub => {
      if (sub === 'all') return;
      const isSel = (sub === currentVal) ? 'selected' : '';
      html += `<option value="${escapeHtml(sub)}" ${isSel}>${escapeHtml(sub)}</option>`;
    });
    sel.innerHTML = html;
  }

  function updatePulsecastPaginationUI(current, total, totalItems) {
    const indicator = document.getElementById('pulsecastPageIndicator');
    const prevBtn = document.getElementById('pulsecastPrevPageBtn');
    const nextBtn = document.getElementById('pulsecastNextPageBtn');

    if (indicator) {
      indicator.textContent = `SEITE ${current} VON ${total} (${totalItems} EINTRÄGE)`;
    }
    if (prevBtn) prevBtn.disabled = (current <= 1);
    if (nextBtn) nextBtn.disabled = (current >= total);
  }

  function renderPulsecastCatalogGrid(items) {
    const grid = document.getElementById('pulsecastCatalogGrid');
    if (!grid) return;

    let html = '';
    items.forEach(item => {
      const isSeries = !!item.isGroup || item.type === 'series' || item.category === 'Serien' || item.metadata?.type === 'series';
      const title = item.title || item.metadata?.title || item.filename || 'Ohne Titel';
      const year = item.year || item.metadata?.year || '';
      const poster = item.posterUrl || item.coverUrl || item.metadata?.posterUrl || item.metadata?.coverUrl || '';
      const subcat = item.subcategory || item.metadata?.subcategory || item.category || '';
      const seriesId = item.xtreamSeriesId || item.id || '';
      const isLocal = (item.isXtream === false || item.category === 'Lokal' || item.metadata?.category === 'Lokal') && !!item.filename;

      const safeTitle = escapeHtml(title);
      const safePoster = poster ? escapeHtml(poster) : '';
      const safeSubcat = escapeHtml(subcat);

      html += `
        <div class="pulsecast-card">
          <div style="position:relative; width:100%; aspect-ratio:2/3; background:#111; overflow:hidden;">
            ${poster ? `<img src="${safePoster}" alt="${safeTitle}" class="pulsecast-card-poster" loading="lazy" onerror="this.onerror=null; this.style.display='none'; this.nextElementSibling.style.display='flex';">` : ''}
            <div style="position:absolute; inset:0; display:${poster ? 'none' : 'flex'}; align-items:center; justify-content:center; flex-direction:column; background:rgba(0,0,0,0.6); color:#777; font-size:2.5rem;">
              ${isSeries ? '📺' : '🎬'}
              <span style="font-size:0.75rem; font-family:var(--font-family); color:var(--c-gold); margin-top:0.4rem; padding:0 0.5rem; text-align:center;">${isSeries ? 'SERIE' : 'FILM'}</span>
            </div>
            ${year ? `<span style="position:absolute; top:6px; right:6px; background:rgba(0,0,0,0.75); border:1px solid rgba(255,255,255,0.2); color:var(--c-gold); font-family:var(--mono-family); font-size:0.75rem; padding:2px 6px; border-radius:4px;">${escapeHtml(String(year))}</span>` : ''}
          </div>
          <div class="pulsecast-card-body">
            <div>
              <div style="font-family:var(--font-family); font-size:0.9rem; font-weight:700; color:#fff; line-height:1.25; margin-bottom:0.3rem; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;" title="${safeTitle}">
                ${safeTitle}
              </div>
              ${subcat ? `<div style="font-size:0.72rem; color:var(--c-butterscotch); font-family:var(--mono-family); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-bottom:0.6rem;" title="${safeSubcat}">${safeSubcat}</div>` : ''}
            </div>

            <div style="margin-top:auto;">
              ${isSeries ? `
                <button type="button" class="left-action-btn" onclick="openPulsecastSeriesEpisodes('${escapeHtml(String(seriesId))}', '${safeTitle.replace(/'/g, "\\'")}', '${safePoster}')" style="width:100%; justify-content:center; padding:0.35rem 0.6rem; font-size:0.8rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                  <span>📋</span> <span>EPISODEN</span>
                </button>
              ` : (isLocal ? `
                <button type="button" class="left-action-btn" onclick="openPulsecastPlayerModal('${escapeJsString(item.filename)}', '${safeTitle.replace(/'/g, "\\'")}')" style="width:100%; justify-content:center; padding:0.35rem 0.6rem; font-size:0.8rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;">
                  <span>▶</span> <span>IN PLAYER ÖFFNEN</span>
                </button>
              ` : `
                <button type="button" class="left-action-btn" onclick="triggerPulsecastMovieDownload('${escapeHtml(String(item.streamUrl || item.filename))}', '${safeTitle.replace(/'/g, "\\'")}')" style="width:100%; justify-content:center; padding:0.35rem 0.6rem; font-size:0.8rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
                  <span>⬇️</span> <span>DOWNLOAD</span>
                </button>
              `)}
            </div>
          </div>
        </div>
      `;
    });

    grid.innerHTML = html;
  }

  async function triggerPulsecastMovieDownload(url, title) {
    if (!url || !title) return;
    playLcarsBeep(1200, 1600);
    try {
      const resp = await fetch('/api/pulsecast/download/media', {
        method: 'POST',
        headers: getPulsecastHeaders(),
        body: JSON.stringify({ url: url, title: title })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        playLcarsAcknowledge();
        alert(`Download gestartet: ${title}`);
        switchPulsecastSubtab('downloads');
      } else {
        alert(`Download-Fehler: ${data.error || 'Unbekannter Fehler'}`);
      }
    } catch (e) {
      alert(`Download-Fehler: ${e}`);
    }
  }

  // ==========================================================================
  // LCARS PULSECAST: LOKALE MEDIEN (CONTROLLER & RENDERER)
  // ==========================================================================
  let pulsecastLocalCategory = 'Lokal';
  let pulsecastLocalViewMode = 'grid';
  let pulsecastLocalSearchQuery = '';
  let pulsecastLocalPage = 1;
  let pulsecastLocalTotalPages = 1;
  let pulsecastLocalItemsCache = [];

  async function loadPulsecastLocalCounts() {
    try {
      const resp = await fetch('/api/pulsecast/media-library?category=Lokal&limit=1', {
        headers: getPulsecastHeaders()
      });
      if (resp.ok) {
        const data = await resp.json();
        if (data.counts) {
          updatePulsecastLocalCounts(data.counts);
        }
      }
    } catch (_) {}
  }

  function setPulsecastLocalCategory(cat) {
    playLcarsBeep(1000, 1300);
    pulsecastLocalCategory = cat;

    const pills = [
      { id: 'local-pill-all', active: cat === 'Lokal', bg: 'var(--c-butterscotch)', color: '#000' },
      { id: 'local-pill-Filme', active: cat === 'Lokal_Filme', bg: 'var(--c-primary)', color: '#000' },
      { id: 'local-pill-Serien', active: cat === 'Lokal_Serien', bg: 'var(--c-secondary)', color: '#000' }
    ];
    pills.forEach(p => {
      const el = document.getElementById(p.id);
      if (el) {
        if (p.active) {
          el.classList.add('active');
          el.style.background = p.bg;
          el.style.color = p.color;
          el.style.fontWeight = '700';
        } else {
          el.classList.remove('active');
          el.style.background = 'rgba(0,0,0,0.5)';
          el.style.color = (p.id === 'local-pill-Filme') ? 'var(--c-primary)' : (p.id === 'local-pill-Serien' ? 'var(--c-secondary)' : '#aaa');
          el.style.fontWeight = 'normal';
        }
      }
    });

    pulsecastLocalPage = 1;
    loadPulsecastLocal(1);
  }

  function setPulsecastLocalViewMode(mode) {
    playLcarsBeep(1100, 1400);
    pulsecastLocalViewMode = mode;

    const gridBtn = document.getElementById('pulsecastLocalViewGridBtn');
    const listBtn = document.getElementById('pulsecastLocalViewListBtn');
    const gridContainer = document.getElementById('pulsecastLocalGrid');
    const listContainer = document.getElementById('pulsecastLocalListContainer');

    if (mode === 'grid') {
      if (gridBtn) {
        gridBtn.classList.add('active');
        gridBtn.style.background = 'var(--c-gold)';
        gridBtn.style.color = '#000';
        gridBtn.style.border = 'none';
      }
      if (listBtn) {
        listBtn.classList.remove('active');
        listBtn.style.background = 'rgba(0,0,0,0.5)';
        listBtn.style.color = '#aaa';
        listBtn.style.border = '1px solid rgba(255,255,255,0.2)';
      }
      if (gridContainer) gridContainer.style.display = (pulsecastLocalItemsCache.length > 0) ? 'grid' : 'none';
      if (listContainer) listContainer.style.display = 'none';
      renderPulsecastLocalGrid(pulsecastLocalItemsCache);
    } else {
      if (listBtn) {
        listBtn.classList.add('active');
        listBtn.style.background = 'var(--c-gold)';
        listBtn.style.color = '#000';
        listBtn.style.border = 'none';
      }
      if (gridBtn) {
        gridBtn.classList.remove('active');
        gridBtn.style.background = 'rgba(0,0,0,0.5)';
        gridBtn.style.color = '#aaa';
        gridBtn.style.border = '1px solid rgba(255,255,255,0.2)';
      }
      if (gridContainer) gridContainer.style.display = 'none';
      if (listContainer) listContainer.style.display = (pulsecastLocalItemsCache.length > 0) ? 'block' : 'none';
      renderPulsecastLocalList(pulsecastLocalItemsCache);
    }
  }

  function pulsecastLocalSearchTrigger() {
    const inp = document.getElementById('pulsecastLocalSearchInput');
    pulsecastLocalSearchQuery = inp ? inp.value.trim() : '';
    pulsecastLocalPage = 1;
    loadPulsecastLocal(1);
  }

  function pulsecastLocalSearchClear() {
    const inp = document.getElementById('pulsecastLocalSearchInput');
    if (inp) inp.value = '';
    pulsecastLocalSearchQuery = '';
    pulsecastLocalPage = 1;
    loadPulsecastLocal(1);
  }

  function pulsecastLocalChangePage(delta) {
    const target = pulsecastLocalPage + delta;
    if (target >= 1 && target <= pulsecastLocalTotalPages) {
      playLcarsBeep(1200, 1500);
      loadPulsecastLocal(target);
    }
  }

  async function loadPulsecastLocal(page = 1) {
    pulsecastLocalPage = page;
    const grid = document.getElementById('pulsecastLocalGrid');
    const listContainer = document.getElementById('pulsecastLocalListContainer');
    const loading = document.getElementById('pulsecastLocalLoading');
    const emptyEl = document.getElementById('pulsecastLocalEmpty');

    if (loading) loading.style.display = 'block';
    if (grid) grid.style.display = 'none';
    if (listContainer) listContainer.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';

    try {
      const params = new URLSearchParams({
        category: pulsecastLocalCategory,
        search: pulsecastLocalSearchQuery,
        page: String(page),
        limit: '40'
      });

      const resp = await fetch(`/api/pulsecast/media-library?${params.toString()}`, {
        headers: getPulsecastHeaders()
      });

      if (loading) loading.style.display = 'none';

      if (resp.status === 403) {
        initPulsecastSection();
        return;
      }

      if (!resp.ok) {
        if (resp.status === 503) {
          pulsecastIsOnline = false;
          initPulsecastSection();
        }
        return;
      }

      const data = await resp.json();
      const items = data.items || [];
      pulsecastLocalItemsCache = items;
      pulsecastLocalTotalPages = data.totalPages || 1;

      // Update Counts
      if (data.counts) {
        updatePulsecastLocalCounts(data.counts);
      }

      // Update pagination UI
      updatePulsecastLocalPaginationUI(data.currentPage || page, pulsecastLocalTotalPages, data.totalItems || 0);

      if (items.length === 0) {
        if (grid) grid.style.display = 'none';
        if (listContainer) listContainer.style.display = 'none';
        if (emptyEl) emptyEl.style.display = 'block';
      } else {
        if (pulsecastLocalViewMode === 'grid') {
          if (grid) {
            grid.style.display = 'grid';
            renderPulsecastLocalGrid(items);
          }
        } else {
          if (listContainer) {
            listContainer.style.display = 'block';
            renderPulsecastLocalList(items);
          }
        }
      }
    } catch (e) {
      if (loading) loading.style.display = 'none';
      console.warn('Fehler beim Laden der lokalen Medien:', e);
    }
  }

  function updatePulsecastLocalCounts(counts) {
    if (!counts) return;
    const badge = document.getElementById('pulsecastLocalCountBadge');
    const cAll = document.getElementById('pulsecastLocalCountAll');
    const cFilme = document.getElementById('pulsecastLocalCountFilme');
    const cSerien = document.getElementById('pulsecastLocalCountSerien');

    if (badge && counts.Lokal !== undefined) badge.textContent = String(counts.Lokal);
    if (cAll && counts.Lokal !== undefined) cAll.textContent = `(${counts.Lokal})`;
    if (cFilme && counts.Lokal_Filme !== undefined) cFilme.textContent = `(${counts.Lokal_Filme})`;
    if (cSerien && counts.Lokal_Serien !== undefined) cSerien.textContent = `(${counts.Lokal_Serien})`;
  }

  function updatePulsecastLocalPaginationUI(current, total, totalItems) {
    const indicator = document.getElementById('pulsecastLocalPageIndicator');
    const prevBtn = document.getElementById('pulsecastLocalPrevPageBtn');
    const nextBtn = document.getElementById('pulsecastLocalNextPageBtn');

    if (indicator) {
      indicator.textContent = `SEITE ${current} VON ${total} (${totalItems} EINTRÄGE)`;
    }
    if (prevBtn) prevBtn.disabled = (current <= 1);
    if (nextBtn) nextBtn.disabled = (current >= total);
  }

  function renderPulsecastLocalGrid(items) {
    const grid = document.getElementById('pulsecastLocalGrid');
    if (!grid) return;

    let html = '';
    items.forEach((item, idx) => {
      const isSeries = !!item.isGroup || item.category === 'Lokal_Serien' || item.subcategory === 'Serien' || item.metadata?.isSeries;
      const title = item.title || item.metadata?.title || item.filename || 'Ohne Titel';
      const year = item.year || item.metadata?.year || '';
      const poster = item.posterUrl || item.coverUrl || item.metadata?.posterUrl || item.metadata?.coverUrl || '';
      const safeTitle = escapeHtml(title);
      const safePoster = poster ? escapeHtml(poster) : '';

      let sizeInfo = '';
      if (item.sizeBytes) {
        sizeInfo = formatBytes(item.sizeBytes);
      } else if (item.files && item.files.length) {
        const totalSize = item.files.reduce((acc, f) => acc + (f.sizeBytes || 0), 0);
        sizeInfo = `${item.files.length} Folgen (${formatBytes(totalSize)})`;
      }

      html += `
        <div class="pulsecast-card" style="border-color:rgba(235,148,58,0.45);">
          <div style="position:relative; width:100%; aspect-ratio:2/3; background:#111; overflow:hidden;">
            ${poster ? `<img src="${safePoster}" alt="${safeTitle}" class="pulsecast-card-poster" loading="lazy" onerror="this.onerror=null; this.style.display='none'; this.nextElementSibling.style.display='flex';">` : ''}
            <div style="position:absolute; inset:0; display:${poster ? 'none' : 'flex'}; align-items:center; justify-content:center; flex-direction:column; background:rgba(0,0,0,0.6); color:#777; font-size:2.5rem;">
              ${isSeries ? '📺' : '🎬'}
              <span style="font-size:0.75rem; font-family:var(--font-family); color:var(--c-butterscotch); margin-top:0.4rem; padding:0 0.5rem; text-align:center;">${isSeries ? 'LOKALE SERIE' : 'LOKALER FILM'}</span>
            </div>
            ${year ? `<span style="position:absolute; top:6px; right:6px; background:rgba(0,0,0,0.75); border:1px solid rgba(255,255,255,0.2); color:var(--c-gold); font-family:var(--mono-family); font-size:0.75rem; padding:2px 6px; border-radius:4px;">${escapeHtml(String(year))}</span>` : ''}
          </div>
          <div class="pulsecast-card-body">
            <div>
              <div style="font-family:var(--font-family); font-size:0.9rem; font-weight:700; color:#fff; line-height:1.25; margin-bottom:0.3rem; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;" title="${safeTitle}">
                ${safeTitle}
              </div>
              <div style="font-size:0.72rem; color:var(--c-butterscotch); font-family:var(--mono-family); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-bottom:0.6rem;">
                ${sizeInfo ? `📁 ${escapeHtml(sizeInfo)}` : '📁 LOKALE DATEI'}
              </div>
            </div>

            <div style="margin-top:auto;">
              ${(isSeries && item.files && item.files.length) ? `
                <button type="button" class="left-action-btn" onclick="openPulsecastLocalGroupModal(${idx})" style="width:100%; justify-content:center; padding:0.35rem 0.6rem; font-size:0.8rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                  <span>📋</span> <span>EPISODEN (${item.files.length})</span>
                </button>
              ` : `
                <button type="button" class="left-action-btn" onclick="openPulsecastPlayerModal('${escapeJsString(item.filename)}', '${safeTitle.replace(/'/g, "\\'")}')" style="width:100%; justify-content:center; padding:0.35rem 0.6rem; font-size:0.8rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;">
                  <span>▶</span> <span>IN PLAYER ÖFFNEN</span>
                </button>
              `}
            </div>
          </div>
        </div>
      `;
    });

    grid.innerHTML = html;
  }

  function renderPulsecastLocalList(items) {
    const tbody = document.getElementById('pulsecastLocalTableBody');
    if (!tbody) return;

    let html = '';
    items.forEach((item, idx) => {
      const isSeries = !!item.isGroup || item.category === 'Lokal_Serien' || item.subcategory === 'Serien' || item.metadata?.isSeries;
      const title = item.title || item.metadata?.title || item.filename || 'Ohne Titel';
      const filename = item.filename || '';
      const safeTitle = escapeHtml(title);
      const safeFilename = escapeHtml(filename);

      // Dateiendung / Format ermitteln
      let format = '--';
      if (filename) {
        const extIdx = filename.lastIndexOf('.');
        if (extIdx !== -1) format = filename.slice(extIdx + 1).toUpperCase();
      } else if (isSeries) {
        format = 'SERIE';
      }

      // Größe
      let sizeStr = '--';
      if (item.sizeBytes) {
        sizeStr = formatBytes(item.sizeBytes);
      } else if (item.files && item.files.length) {
        const totalBytes = item.files.reduce((acc, f) => acc + (f.sizeBytes || 0), 0);
        sizeStr = `${formatBytes(totalBytes)} (${item.files.length} F.)`;
      }

      // Datum
      let mtimeStr = '--';
      const mtime = item.mtime || (item.files && item.files[0] ? item.files[0].mtime : 0);
      if (mtime) {
        try {
          const d = new Date(mtime);
          mtimeStr = d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }) + ' ' +
                     d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
        } catch (_) {}
      }

      const typeIcon = isSeries ? '📺' : '🎬';
      const typeColor = isSeries ? 'var(--c-secondary)' : 'var(--c-primary)';

      html += `
        <tr>
          <td style="text-align:center; font-size:1.1rem;">${typeIcon}</td>
          <td>
            <div style="font-family:var(--font-family); font-weight:700; color:#fff; font-size:0.88rem; line-height:1.2;">
              ${safeTitle}
            </div>
            ${filename ? `<div style="font-family:var(--mono-family); font-size:0.75rem; color:#888; word-break:break-all; margin-top:2px;">${safeFilename}</div>` : ''}
          </td>
          <td style="font-family:var(--mono-family); font-size:0.8rem; color:${typeColor}; font-weight:700;">
            ${escapeHtml(format)}
          </td>
          <td style="font-family:var(--mono-family); font-size:0.82rem; color:#44dd88; white-space:nowrap;">
            ${escapeHtml(sizeStr)}
          </td>
          <td style="font-family:var(--mono-family); font-size:0.78rem; color:var(--c-gold); white-space:nowrap;">
            ${escapeHtml(mtimeStr)}
          </td>
          <td style="text-align:right; white-space:nowrap;">
            ${(isSeries && item.files && item.files.length) ? `
              <button type="button" class="left-action-btn" onclick="openPulsecastLocalGroupModal(${idx})" style="padding:0.3rem 0.7rem; font-size:0.78rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                <span>📋</span> <span>EPISODEN</span>
              </button>
            ` : `
              <button type="button" class="left-action-btn" onclick="openPulsecastPlayerModal('${escapeJsString(item.filename)}', '${safeTitle.replace(/'/g, "\\'")}')" style="padding:0.3rem 0.7rem; font-size:0.78rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;">
                <span>▶</span> <span>ÖFFNEN</span>
              </button>
            `}
          </td>
        </tr>
      `;
    });

    tbody.innerHTML = html;
  }

  function openPulsecastLocalGroupModal(idx) {
    playLcarsBeep(980, 1300);
    const item = pulsecastLocalItemsCache[idx];
    if (!item || !item.files) return;

    pulsecastActiveSeries = { id: item.id || '', title: item.title, poster: item.posterUrl };
    const modal = document.getElementById('pulsecastSeriesModal');
    const modalTitle = document.getElementById('pulsecastModalSeriesTitle');
    const modalMeta = document.getElementById('pulsecastModalSeriesMeta');
    const loadingEl = document.getElementById('pulsecastEpisodesLoading');

    if (modalTitle) modalTitle.textContent = item.title;
    if (loadingEl) loadingEl.style.display = 'none';
    if (modalMeta) modalMeta.textContent = `LOKALES ARCHIV // ${item.files.length} EPISODEN VORHANDEN`;

    pulsecastCurrentSeriesEpisodes = item.files;
    populateSeasonFilter(item.files);
    renderEpisodesList(item.files);

    if (modal) modal.style.display = 'flex';
  }

  // SERIEN-EPISODEN MODAL
  async function openPulsecastSeriesEpisodes(seriesId, title, poster) {
    playLcarsBeep(980, 1300);
    pulsecastActiveSeries = { id: seriesId, title: title, poster: poster };
    const modal = document.getElementById('pulsecastSeriesModal');
    const modalTitle = document.getElementById('pulsecastModalSeriesTitle');
    const modalMeta = document.getElementById('pulsecastModalSeriesMeta');
    const listEl = document.getElementById('pulsecastEpisodesList');
    const loadingEl = document.getElementById('pulsecastEpisodesLoading');

    if (modalTitle) modalTitle.textContent = title;
    if (modalMeta) modalMeta.textContent = `SERIEN-ID: ${seriesId} // LADE EPISODEN...`;
    if (listEl) listEl.innerHTML = '';
    if (loadingEl) loadingEl.style.display = 'block';
    if (modal) modal.style.display = 'flex';

    try {
      const resp = await fetch(`/api/pulsecast/series-episodes?seriesId=${encodeURIComponent(seriesId)}`, {
        headers: getPulsecastHeaders()
      });
      if (loadingEl) loadingEl.style.display = 'none';

      if (!resp.ok) {
        const err = await resp.json();
        if (listEl) listEl.innerHTML = `<div style="color:var(--c-red); font-family:var(--mono-family); padding:1rem; text-align:center;">${escapeHtml(err.error || 'Episoden konnten nicht geladen werden')}</div>`;
        return;
      }

      const episodes = await resp.json();
      pulsecastCurrentSeriesEpisodes = Array.isArray(episodes) ? episodes : [];
      if (modalMeta) modalMeta.textContent = `${pulsecastCurrentSeriesEpisodes.length} EPISODEN VERFÜGBAR`;

      populateSeasonFilter(pulsecastCurrentSeriesEpisodes);
      renderEpisodesList(pulsecastCurrentSeriesEpisodes);
    } catch (e) {
      if (loadingEl) loadingEl.style.display = 'none';
      if (listEl) listEl.innerHTML = `<div style="color:var(--c-red); font-family:var(--mono-family); padding:1rem; text-align:center;">Fehler beim Laden: ${escapeHtml(String(e))}</div>`;
    }
  }

  function closePulsecastSeriesModal() {
    playLcarsBeep(500, 300);
    const modal = document.getElementById('pulsecastSeriesModal');
    if (modal) modal.style.display = 'none';
    pulsecastCurrentSeriesEpisodes = [];
    pulsecastActiveSeries = null;
  }

  function handlePulsecastSeriesModalBackdropClick(e) {
    if (e.target && e.target.id === 'pulsecastSeriesModal') {
      closePulsecastSeriesModal();
    }
  }

  function populateSeasonFilter(episodes) {
    const sel = document.getElementById('pulsecastSeasonFilterSelect');
    if (!sel) return;
    const seasons = new Set();
    episodes.forEach(ep => {
      const se = ep.metadata?.seasonEpisode || '';
      const m = se.match(/^S(\\d+)/i);
      if (m) seasons.add(parseInt(m[1], 10));
    });

    let html = '<option value="all">ALLE STAFFELN</option>';
    Array.from(seasons).sort((a, b) => a - b).forEach(s => {
      html += `<option value="S${s}">STAFFEL ${s}</option>`;
    });
    sel.innerHTML = html;
  }

  function filterPulsecastEpisodesBySeason() {
    const sel = document.getElementById('pulsecastSeasonFilterSelect');
    const season = sel ? sel.value : 'all';
    let filtered = pulsecastCurrentSeriesEpisodes;
    if (season !== 'all') {
      const prefix = season.toUpperCase();
      filtered = pulsecastCurrentSeriesEpisodes.filter(ep => {
        const se = (ep.metadata?.seasonEpisode || '').toUpperCase();
        return se.startsWith(prefix);
      });
    }
    renderEpisodesList(filtered);
  }

  function renderEpisodesList(episodes) {
    const listEl = document.getElementById('pulsecastEpisodesList');
    if (!listEl) return;

    if (episodes.length === 0) {
      listEl.innerHTML = '<div style="text-align:center; padding:1.5rem; color:#888; font-family:var(--mono-family);">KEINE EPISODEN GEFUNDEN</div>';
      return;
    }

    let html = '';
    episodes.forEach((ep, idx) => {
      const title = ep.metadata?.title || ep.filename || `Episode ${idx + 1}`;
      const seasonEpisode = ep.metadata?.seasonEpisode || '';
      const duration = ep.metadata?.cast?.duration || '';
      const rating = ep.metadata?.cast?.rating ? `★ ${ep.metadata.cast.rating.toFixed(1)}` : '';
      const streamUrl = ep.filename || '';

      const isLocalEp = (ep.isXtream === false || (ep.filename && !ep.filename.startsWith('http://') && !ep.filename.startsWith('https://')));

      html += `
        <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.4); border:1px solid rgba(255,255,255,0.08); border-radius:4px; padding:0.6rem 0.8rem; gap:0.5rem; flex-wrap:wrap;">
          <div style="display:flex; align-items:center; gap:0.6rem; min-width:0; flex:1;">
            ${seasonEpisode ? `<span class="badge-status" style="background:var(--c-secondary); color:#000; font-size:0.75rem; flex-shrink:0;">${escapeHtml(seasonEpisode)}</span>` : ''}
            <div style="min-width:0;">
              <div style="font-family:var(--font-family); font-weight:700; font-size:0.9rem; color:#fff; word-break:break-all;">
                ${safeTitle}
              </div>
              <div style="font-family:var(--mono-family); font-size:0.75rem; color:#888; display:flex; gap:0.8rem;">
                ${duration ? `<span>⏱ ${escapeHtml(duration)}</span>` : ''}
                ${rating ? `<span style="color:var(--c-gold);">${rating}</span>` : ''}
              </div>
            </div>
          </div>
          <div style="display:flex; gap:0.4rem; align-items:center;">
            ${isLocalEp ? `
              <button type="button" class="left-action-btn" onclick="openPulsecastPlayerModal('${escapeJsString(ep.filename)}', '${safeTitle.replace(/'/g, "\\'")}')" style="padding:0.3rem 0.6rem; font-size:0.78rem; border-color:var(--c-butterscotch); color:var(--c-butterscotch); font-weight:700;" title="Im lokalen Media Player öffnen">
                ▶ PLAYER
              </button>
            ` : ''}
            <button type="button" class="left-action-btn" onclick="downloadSingleEpisode('${safeUrl}', '${safeTitle.replace(/'/g, "\\'")}', '${seasonEpisode}')" style="padding:0.3rem 0.8rem; font-size:0.78rem; border-color:var(--c-primary); color:var(--c-primary); font-weight:700;">
              <span>⬇️</span> <span>DOWNLOAD</span>
            </button>
          </div>
        </div>
      `;
    });

    listEl.innerHTML = html;
  }

  async function downloadSingleEpisode(url, title, seasonEpisode) {
    if (!url) return;
    playLcarsBeep(1200, 1600);
    const seriesTitle = pulsecastActiveSeries ? pulsecastActiveSeries.title : '';
    const fullTitle = seasonEpisode ? `${seasonEpisode} - ${title}` : title;
    try {
      const resp = await fetch('/api/pulsecast/download/media', {
        method: 'POST',
        headers: getPulsecastHeaders(),
        body: JSON.stringify({ url: url, title: fullTitle, seriesTitle: seriesTitle })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        playLcarsAcknowledge();
        alert(`Download gestartet: ${fullTitle}`);
      } else {
        alert(`Fehler: ${data.error || 'Download fehlgeschlagen'}`);
      }
    } catch (e) {
      alert(`Fehler beim Starten: ${e}`);
    }
  }

  async function downloadAllVisibleEpisodes() {
    const sel = document.getElementById('pulsecastSeasonFilterSelect');
    const season = sel ? sel.value : 'all';
    let episodes = pulsecastCurrentSeriesEpisodes;
    if (season !== 'all') {
      const prefix = season.toUpperCase();
      episodes = pulsecastCurrentSeriesEpisodes.filter(ep => (ep.metadata?.seasonEpisode || '').toUpperCase().startsWith(prefix));
    }

    if (!episodes || episodes.length === 0) {
      alert('Keine Episoden zum Herunterladen vorhanden.');
      return;
    }

    if (!confirm(`${episodes.length} Episoden zur Download-Warteschlange hinzufügen?`)) {
      return;
    }

    playLcarsBeep(1300, 1800);
    const seriesTitle = pulsecastActiveSeries ? pulsecastActiveSeries.title : '';
    const items = episodes.map(ep => {
      const title = ep.metadata?.title || ep.filename;
      const se = ep.metadata?.seasonEpisode || '';
      return {
        url: ep.filename,
        title: se ? `${se} - ${title}` : title,
        seriesTitle: seriesTitle
      };
    });

    try {
      const resp = await fetch('/api/pulsecast/download/media', {
        method: 'POST',
        headers: getPulsecastHeaders(),
        body: JSON.stringify({ items: items })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        playLcarsAcknowledge();
        alert(`${data.count || items.length} Episoden erfolgreich in die Download-Warteschlange eingereiht!`);
        closePulsecastSeriesModal();
        switchPulsecastSubtab('downloads');
      } else {
        alert(`Batch-Download Fehler: ${data.error || 'Fehlgeschlagen'}`);
      }
    } catch (e) {
      alert(`Batch-Download Fehler: ${e}`);
    }
  }

  // XDCC SUCHE CONTROLLER
  function pulsecastXdccChipSearch(q) {
    const inp = document.getElementById('pulsecastXdccInput');
    if (inp) inp.value = q;
    pulsecastXdccSearch();
  }

  function setPulsecastXdccSource(source) {
    playLcarsBeep(1000, 1300);
    pulsecastXdccSource = source;
    const btnXdcc = document.getElementById('pulsecast-source-pill-xdcc');
    const btnMg = document.getElementById('pulsecast-source-pill-moviegods');
    const topDlBox = document.getElementById('pulsecastMoviegodsTopDlContainer');
    const headerTitle = document.getElementById('pulsecastXdccHeaderTitle');

    if (source === 'moviegods') {
      if (btnXdcc) {
        btnXdcc.classList.remove('active');
        btnXdcc.style.background = 'rgba(0,0,0,0.5)';
        btnXdcc.style.color = 'var(--c-blue)';
        btnXdcc.style.border = '1px solid var(--c-blue)';
      }
      if (btnMg) {
        btnMg.classList.add('active');
        btnMg.style.background = 'var(--c-secondary)';
        btnMg.style.color = '#000';
        btnMg.style.border = 'none';
      }
      if (topDlBox) topDlBox.style.display = 'block';
      if (headerTitle) {
        headerTitle.textContent = 'MOVIE GODS IRC SCANNER & PACKS';
        headerTitle.style.color = 'var(--c-secondary)';
      }
    } else {
      if (btnXdcc) {
        btnXdcc.classList.add('active');
        btnXdcc.style.background = 'var(--c-blue)';
        btnXdcc.style.color = '#000';
        btnXdcc.style.border = 'none';
      }
      if (btnMg) {
        btnMg.classList.remove('active');
        btnMg.style.background = 'rgba(0,0,0,0.5)';
        btnMg.style.color = 'var(--c-secondary)';
        btnMg.style.border = '1px solid var(--c-secondary)';
      }
      if (topDlBox) topDlBox.style.display = 'none';
      if (headerTitle) {
        headerTitle.textContent = 'KLASSISCHE IRC & XDCC PAKET-SUCHE';
        headerTitle.style.color = 'var(--c-blue)';
      }
    }
  }

  async function loadPulsecastMoviegodsTopDl() {
    playLcarsBeep(1200, 1600);
    const statusEl = document.getElementById('pulsecastTopDlStatus');
    const tableContainer = document.getElementById('pulsecastTopDlTableContainer');
    const tbody = document.getElementById('pulsecastTopDlTbody');
    const btn = document.getElementById('pulsecastLoadTopDlBtn');

    if (btn) btn.disabled = true;
    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.textContent = 'FRAGE MOVIEGODS TOP-DOWNLOADS AB (!topdl auf #mg-chat)...';
      statusEl.style.color = 'var(--c-gold)';
    }
    if (tableContainer) tableContainer.style.display = 'none';

    try {
      const resp = await fetch('/api/pulsecast/search?q=!topdl&source=moviegods', {
        headers: getPulsecastHeaders()
      });

      if (btn) btn.disabled = false;

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        if (statusEl) {
          statusEl.textContent = `FEHLER BEIM LADEN DER TOP-DOWNLOADS: ${err.error || 'Serverfehler'}`;
          statusEl.style.color = 'var(--c-red)';
        }
        return;
      }

      const data = await resp.json();
      const results = data.results || (Array.isArray(data) ? data : []);

      if (results.length === 0) {
        if (statusEl) {
          statusEl.textContent = 'KEINE TOP-DOWNLOADS EMPFANGEN (IRC TIMEOUT ODER KEINE DATEN)';
          statusEl.style.color = 'var(--c-gold)';
        }
        return;
      }

      if (statusEl) {
        statusEl.textContent = `${results.length} TOP-DOWNLOADS EMPFANGEN // KLICK AUF EINTRAG STARTET PACK-SCAN:`;
        statusEl.style.color = '#44dd88';
      }

      let html = '';
      results.forEach(item => {
        const gets = item.gets || '';
        const filename = item.filename || '';
        const sizeStr = item.sizeStr || '';
        const safeFilename = escapeHtml(filename);

        html += `
          <tr style="cursor:pointer;" onclick="selectMoviegodsTopDl('${escapeJsString(filename)}')" title="Diesen Release sofort suchen">
            <td style="font-family:var(--mono-family); font-weight:700; color:var(--c-gold);">${escapeHtml(gets)}</td>
            <td style="font-family:var(--mono-family); font-size:0.82rem; word-break:break-all; color:#fff;">
              ${safeFilename}
            </td>
            <td style="font-family:var(--mono-family); color:#44dd88; white-space:nowrap;">${escapeHtml(sizeStr)}</td>
            <td style="text-align:right;">
              <button type="button" class="left-action-btn" onclick="event.stopPropagation(); selectMoviegodsTopDl('${escapeJsString(filename)}')" style="padding:0.25rem 0.6rem; font-size:0.75rem; border-color:var(--c-secondary); color:var(--c-secondary); font-weight:700;">
                <span>🔍</span> <span>SUCHEN</span>
              </button>
            </td>
          </tr>
        `;
      });

      if (tbody) tbody.innerHTML = html;
      if (tableContainer) tableContainer.style.display = 'block';

    } catch (e) {
      if (btn) btn.disabled = false;
      if (statusEl) {
        statusEl.textContent = `VERBINDUNGSFEHLER: ${e}`;
        statusEl.style.color = 'var(--c-red)';
      }
    }
  }

  function selectMoviegodsTopDl(filename) {
    if (!filename) return;
    playLcarsBeep(1400, 1800);
    const inp = document.getElementById('pulsecastXdccInput');
    if (inp) inp.value = filename;
    setPulsecastXdccSource('moviegods');
    pulsecastXdccSearch();
  }

  async function pulsecastXdccSearch() {
    const inp = document.getElementById('pulsecastXdccInput');
    const query = inp ? inp.value.trim() : '';
    if (!query) {
      alert('Bitte geben Sie einen Suchbegriff ein.');
      return;
    }

    playLcarsBeep(1200, 1500);
    const statusEl = document.getElementById('pulsecastXdccStatus');
    const tableEl = document.getElementById('pulsecastXdccTable');
    const emptyEl = document.getElementById('pulsecastXdccEmpty');
    const tbody = document.getElementById('pulsecastXdccTbody');

    const isMg = (pulsecastXdccSource === 'moviegods');
    const sourceLabel = isMg ? 'MOVIE GODS IRC (#mg-chat)' : 'XDCC-NETZWERKE';

    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.textContent = `SCANNE ${sourceLabel} NACH "${query.toUpperCase()}"...`;
      statusEl.style.color = isMg ? 'var(--c-secondary)' : 'var(--c-blue)';
    }
    if (tableEl) tableEl.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';

    try {
      const resp = await fetch(`/api/pulsecast/search?q=${encodeURIComponent(query)}&source=${encodeURIComponent(pulsecastXdccSource)}`, {
        headers: getPulsecastHeaders()
      });

      if (resp.status === 403) {
        initPulsecastSection();
        return;
      }

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        if (statusEl) {
          statusEl.textContent = `SUCHFEHLER: ${err.error || 'Fehler bei der Suche'}`;
          statusEl.style.color = 'var(--c-red)';
        }
        return;
      }

      const data = await resp.json();
      const results = data.results || (Array.isArray(data) ? data : []);

      if (statusEl) {
        statusEl.textContent = `${results.length} PAKET(E) GEFUNDEN [QUELLE: ${isMg ? 'MOVIE GODS' : 'XDCC.EU'}] // FILTER: "${query.toUpperCase()}"`;
        statusEl.style.color = 'var(--c-gold)';
      }

      if (results.length === 0) {
        if (tableEl) tableEl.style.display = 'none';
        if (emptyEl) {
          emptyEl.style.display = 'block';
          emptyEl.textContent = `KEINE PAKETE FÜR "${query.toUpperCase()}" AUF ${isMg ? 'MOVIE GODS' : 'XDCC.EU'} GEFUNDEN`;
        }
        return;
      }

      if (emptyEl) emptyEl.style.display = 'none';
      if (tableEl) tableEl.style.display = 'table';

      let html = '';
      results.forEach(res => {
        const bot = res.botName || 'Bot';
        const pack = res.packNumber || '';
        const file = res.filename || '';
        const sizeStr = res.sizeStr || (res.sizeBytes ? formatBytes(res.sizeBytes) : '--');
        const server = res.server || (isMg ? 'irc.abjects.net' : '');
        const channel = res.channel || (isMg ? '#moviegods' : '');
        const sizeBytes = res.sizeBytes || 0;

        const safeBot = escapeHtml(bot);
        const safePack = escapeHtml(pack);
        const safeFile = escapeHtml(file);
        const safeServer = escapeHtml(server);
        const safeChannel = escapeHtml(channel);

        const accentColor = isMg ? 'var(--c-secondary)' : 'var(--c-blue)';

        html += `
          <tr>
            <td style="font-family:var(--font-family); font-weight:700; color:${accentColor};">${safeBot}</td>
            <td style="font-family:var(--mono-family); font-weight:700; color:var(--c-gold);">#${safePack}</td>
            <td style="font-family:var(--mono-family); font-size:0.85rem; word-break:break-all; color:#fff;" title="${safeFile}">
              ${safeFile}
            </td>
            <td style="font-family:var(--mono-family); color:#44dd88; white-space:nowrap;">${escapeHtml(sizeStr)}</td>
            <td style="font-family:var(--mono-family); font-size:0.75rem; color:#888;">
              ${safeServer ? `<div>${safeServer}</div>` : ''}
              ${safeChannel ? `<div style="color:var(--c-secondary); font-weight:700;">${safeChannel}</div>` : ''}
            </td>
            <td style="text-align:right;">
              <button type="button" class="left-action-btn" onclick="triggerPulsecastXdccDownload('${safeServer}', '${safeChannel}', '${safeBot.replace(/'/g, "\\'")}', '${safePack}', '${safeFile.replace(/'/g, "\\'")}', ${sizeBytes})" style="padding:0.3rem 0.8rem; font-size:0.8rem; border-color:${accentColor}; color:${accentColor}; font-weight:700;">
                <span>⬇️</span> <span>LADEN</span>
              </button>
            </td>
          </tr>
        `;
      });

      if (tbody) tbody.innerHTML = html;
    } catch (e) {
      if (statusEl) {
        statusEl.textContent = `VERBINDUNGSFEHLER: ${e}`;
        statusEl.style.color = 'var(--c-red)';
      }
    }
  }

  async function triggerPulsecastXdccDownload(server, channel, botName, packNumber, filename, expectedSize) {
    playLcarsBeep(1200, 1600);
    const targetChannel = channel || (pulsecastXdccSource === 'moviegods' ? '#moviegods' : '');
    const targetServer = server || (pulsecastXdccSource === 'moviegods' ? 'irc.abjects.net' : '');

    try {
      const resp = await fetch('/api/pulsecast/download/xdcc', {
        method: 'POST',
        headers: getPulsecastHeaders(),
        body: JSON.stringify({
          server: targetServer,
          channel: targetChannel,
          botName: botName,
          packNumber: packNumber,
          filename: filename,
          expectedSize: expectedSize
        })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        playLcarsAcknowledge();
        alert(`XDCC Download angefordert:\nBot: ${botName}\nPack: #${packNumber}\nChannel: ${targetChannel}\nDatei: ${filename}`);
        switchPulsecastSubtab('downloads');
      } else {
        alert(`XDCC Fehler: ${data.error || 'Fehlgeschlagen'}`);
      }
    } catch (e) {
      alert(`XDCC Fehler: ${e}`);
    }
  }

  // LCARS PLAYER MODAL CONTROLLER
  function openPulsecastPlayerModal(filename, displayTitle) {
    if (!filename) return;
    playLcarsBeep(1200, 1600);
    const modal = document.getElementById('pulsecastPlayerModal');
    const titleEl = document.getElementById('pulsecastPlayerModalTitle');
    const metaEl = document.getElementById('pulsecastPlayerModalMeta');
    const inputEl = document.getElementById('pulsecastPlayerStreamUrlInput');
    const webPlayerContainer = document.getElementById('pulsecastWebPlayerContainer');
    const videoEl = document.getElementById('pulsecastWebPlayerVideo');
    const m3uBtn = document.getElementById('pulsecastM3uDownloadBtn');

    const cleanTitle = displayTitle || filename;
    currentPulsecastFilename = filename;
    currentPulsecastDisplayTitle = cleanTitle;
    currentPulsecastAudioTranscode = false;
    currentPulsecastNeedsTranscode = false;

    if (titleEl) titleEl.textContent = cleanTitle;
    if (metaEl) metaEl.textContent = `DATEI: ${filename} // HTTP RANGE NATIVE STREAM`;

    const code = sessionStorage.getItem('lcars_auth_code') || (typeof currentAuthCode !== 'undefined' ? currentAuthCode : '0901');
    const cleanFn = (filename || '').replace(/^[/]+/, '');
    const streamUrl = `${window.location.origin}/api/pulsecast/media/stream/${encodeURI(cleanFn)}${code ? `?code=${encodeURIComponent(code)}` : ''}`;
    currentPulsecastStreamUrl = streamUrl;

    if (inputEl) inputEl.value = streamUrl;

    // 1-Click M3U playlist download URL
    const safeTitle = cleanTitle.replace(/[^\\w.-]+/g, '_').replace(/^_+|_+$/g, '') || 'stream';
    const m3uUrl = `${window.location.origin}/api/pulsecast/media/stream.m3u?filename=${encodeURIComponent(cleanFn)}${code ? `&code=${encodeURIComponent(code)}` : ''}&title=${encodeURIComponent(cleanTitle)}`;
    if (m3uBtn) {
      m3uBtn.href = m3uUrl;
      m3uBtn.download = `${safeTitle}.m3u`;
    }

    // Direct app protocol schemes
    const vlcBtn = document.getElementById('playerBtnVlc');
    if (vlcBtn) vlcBtn.href = `vlc://${streamUrl}`;

    const potBtn = document.getElementById('playerBtnPotPlayer');
    if (potBtn) potBtn.href = `potplayer://${streamUrl}`;

    const iinaBtn = document.getElementById('playerBtnIina');
    if (iinaBtn) iinaBtn.href = `iina://weblink?url=${encodeURIComponent(streamUrl)}`;

    const nplayerBtn = document.getElementById('playerBtnNplayer');
    if (nplayerBtn) nplayerBtn.href = `nplayer-${streamUrl}`;

    // Reset web player container
    if (webPlayerContainer) webPlayerContainer.style.display = 'none';
    if (videoEl) {
      videoEl.pause();
      videoEl.removeAttribute('src');
      videoEl.load();
    }

    if (modal) modal.style.display = 'flex';
  }

  function closePulsecastPlayerModal() {
    playLcarsBeep(500, 300);
    const modal = document.getElementById('pulsecastPlayerModal');
    const videoEl = document.getElementById('pulsecastWebPlayerVideo');
    if (videoEl) {
      videoEl.pause();
      videoEl.removeAttribute('src');
      videoEl.load();
    }
    currentPulsecastAudioTranscode = false;
    currentPulsecastNeedsTranscode = false;
    if (modal) modal.style.display = 'none';
  }

  function handlePulsecastPlayerModalBackdropClick(e) {
    if (e.target && e.target.id === 'pulsecastPlayerModal') {
      closePulsecastPlayerModal();
    }
  }

  async function togglePulsecastWebPlayer(show) {
    const container = document.getElementById('pulsecastWebPlayerContainer');
    const videoEl = document.getElementById('pulsecastWebPlayerVideo');
    if (!container || !videoEl) return;
    if (show) {
      playLcarsBeep(1100, 1400);
      container.style.display = 'flex';

      const code = sessionStorage.getItem('lcars_auth_code') || (typeof currentAuthCode !== 'undefined' ? currentAuthCode : '0901');
      const cleanFn = (currentPulsecastFilename || '').replace(/^[/]+/, '');

      updatePulsecastAudioUI(true, '🔍 Analysiere Audio-Codecs via PulseCast Probe...');

      let needsTranscode = false;
      try {
        const probeUrl = `/api/pulsecast/media/probe/${encodeURI(cleanFn)}${code ? `?code=${encodeURIComponent(code)}` : ''}`;
        const res = await fetch(probeUrl, {
          headers: getPulsecastHeaders()
        });
        if (res.ok) {
          const data = await res.json();
          needsTranscode = !!data.needsAudioTranscode;
        }
      } catch (err) {
        console.warn('PulseCast Probe fehlgeschlagen, nutze Standard:', err);
      }

      currentPulsecastNeedsTranscode = needsTranscode;
      currentPulsecastAudioTranscode = needsTranscode;
      loadPulsecastVideoSource(false);
    } else {
      playLcarsBeep(700, 400);
      videoEl.pause();
      videoEl.removeAttribute('src');
      videoEl.load();
      container.style.display = 'none';
    }
  }

  function loadPulsecastVideoSource(preserveTime = false) {
    const videoEl = document.getElementById('pulsecastWebPlayerVideo');
    if (!videoEl || !currentPulsecastFilename) return;

    const code = sessionStorage.getItem('lcars_auth_code') || (typeof currentAuthCode !== 'undefined' ? currentAuthCode : '0901');
    const cleanFn = (currentPulsecastFilename || '').replace(/^[/]+/, '');

    let targetSrc = '';
    if (currentPulsecastAudioTranscode) {
      targetSrc = `${window.location.origin}/api/pulsecast/media/transcode/${encodeURI(cleanFn)}${code ? `?code=${encodeURIComponent(code)}` : ''}`;
    } else {
      targetSrc = `${window.location.origin}/api/pulsecast/media/stream/${encodeURI(cleanFn)}${code ? `?code=${encodeURIComponent(code)}` : ''}`;
    }

    const prevTime = preserveTime ? (videoEl.currentTime || 0) : 0;
    videoEl.src = targetSrc;
    if (prevTime > 0) {
      videoEl.onloadedmetadata = () => {
        try { videoEl.currentTime = prevTime; } catch (_) {}
        videoEl.onloadedmetadata = null;
      };
    }
    videoEl.play().catch(e => console.log('Autoplay info:', e));
    updatePulsecastAudioUI();
  }

  function togglePulsecastAudioMode() {
    playLcarsBeep(1200, 1500);
    currentPulsecastAudioTranscode = !currentPulsecastAudioTranscode;
    loadPulsecastVideoSource(true);
  }

  function updatePulsecastAudioUI(probing = false, probingText = '') {
    const toggleBtn = document.getElementById('pulsecastAudioModeToggle');
    const hintEl = document.getElementById('pulsecastAudioStatusHint');

    if (probing) {
      if (hintEl) {
        hintEl.innerHTML = probingText || '🔍 Audio-Codecs werden geprüft...';
        hintEl.style.color = 'var(--c-gold)';
      }
      return;
    }

    if (toggleBtn) {
      if (currentPulsecastAudioTranscode) {
        toggleBtn.innerHTML = '🔊 TON: AAC (KOMPATIBEL)';
        toggleBtn.style.background = 'rgba(68,221,136,0.18)';
        toggleBtn.style.color = '#44dd88';
        toggleBtn.style.borderColor = '#44dd88';
      } else {
        toggleBtn.innerHTML = '🎬 TON: ORIGINAL (NATIV)';
        toggleBtn.style.background = 'rgba(255,153,0,0.18)';
        toggleBtn.style.color = 'var(--c-butterscotch)';
        toggleBtn.style.borderColor = 'var(--c-butterscotch)';
      }
    }

    if (hintEl) {
      if (currentPulsecastAudioTranscode) {
        hintEl.innerHTML = '🔊 Audio-Transcoding aktiv (AAC Stereo/5.1 für ruckelfreien Browser-Ton)';
        hintEl.style.color = '#44dd88';
      } else {
        if (currentPulsecastNeedsTranscode) {
          hintEl.innerHTML = '🎬 Nativer Originalstream aktiv ⚠️ (Achtung: Diese Datei enthält AC3/DTS und bleibt im Browser ohne Transcoding vermutlich stumm!)';
          hintEl.style.color = 'var(--c-primary)';
        } else {
          hintEl.innerHTML = '🎬 Nativer Originalstream aktiv (Browser-kompatibler Ton)';
          hintEl.style.color = '#888';
        }
      }
    }
  }

  function copyPulsecastStreamUrl() {
    playLcarsBeep(1300, 1700);
    if (!currentPulsecastStreamUrl) return;
    navigator.clipboard.writeText(currentPulsecastStreamUrl).then(() => {
      const badge = document.getElementById('pulsecastCopySuccessBadge');
      if (badge) {
        badge.style.display = 'inline';
        setTimeout(() => { badge.style.display = 'none'; }, 3000);
      }
    }).catch(err => {
      const inp = document.getElementById('pulsecastPlayerStreamUrlInput');
      if (inp) {
        inp.select();
        document.execCommand('copy');
        const badge = document.getElementById('pulsecastCopySuccessBadge');
        if (badge) {
          badge.style.display = 'inline';
          setTimeout(() => { badge.style.display = 'none'; }, 3000);
        }
      }
    });
  }

  function openPulsecastStreamInTab() {
    playLcarsBeep(1200, 1500);
    if (currentPulsecastStreamUrl) {
      window.open(currentPulsecastStreamUrl, '_blank');
    }
  }

  // ==========================================================================
  // GOOGLE GEMINI 3.8 LIVE // SUBRAUM COMM SST CONTROLLER
  // ==========================================================================
  let geminiLiveWs = null;
  let geminiLiveMode = 'ptt'; // 'ptt' | 'live'
  let geminiLiveMuted = false;
  let geminiLiveChannelOpen = false;
  let isPttActive = false;
  let pttAudioSent = false;
  let isSpeaking = false;
  let lastSpeechTime = 0;
  let isModelSpeaking = false;
  let geminiAudioStream = null;
  let geminiAudioSourceNode = null;
  let geminiScriptNode = null;
  let geminiMuteNode = null;
  let geminiInputAnalyser = null;
  let geminiOutputAnalyser = null;
  let geminiActiveAudioSources = [];
  let geminiNextPlaybackTime = 0;
  let geminiVizAnimId = null;
  let currentModelTurnEl = null;
  let currentUserTranscriptEl = null;
  let pttStopTimer = null;

  async function checkGeminiLiveStatus() {
    try {
      const resp = await fetch('/api/gemini-live/status');
      if (!resp.ok) return;
      const data = await resp.json();
      const modelBadge = document.getElementById('geminiLiveModelBadge');
      if (modelBadge && data.model) {
        modelBadge.textContent = 'MODEL: ' + data.model.toUpperCase();
      }
    } catch (e) {
      // Ignorieren falls Endpunkt noch nicht erreichbar
    }
  }

  function initGeminiLiveSection() {
    const isLocked = isCategoryLocked('gemini_live');
    const gateView = document.getElementById('geminiLiveGateView');
    const activeContent = document.getElementById('geminiLiveActiveContent');

    if (isLocked) {
      if (gateView) gateView.style.display = 'block';
      if (activeContent) activeContent.style.display = 'none';
      disconnectGeminiLiveWs();
      return;
    }

    if (gateView) gateView.style.display = 'none';
    if (activeContent) activeContent.style.display = 'block';

    checkGeminiLiveStatus();
    initGeminiMeterDOM();
    updateGeminiLiveModeUI();
    setupGeminiPttListeners();
  }

  function initGeminiMeterDOM() {
    const inMeter = document.getElementById('geminiInMeter');
    const outMeter = document.getElementById('geminiOutMeter');
    if (inMeter && !inMeter.children.length) {
      for (let i = 0; i < 12; i++) {
        const seg = document.createElement('div');
        seg.className = 'lcars-meter-seg';
        seg.dataset.index = i;
        inMeter.appendChild(seg);
      }
    }
    if (outMeter && !outMeter.children.length) {
      for (let i = 0; i < 12; i++) {
        const seg = document.createElement('div');
        seg.className = 'lcars-meter-seg';
        seg.dataset.index = i;
        outMeter.appendChild(seg);
      }
    }
  }

  function setGeminiLiveMode(mode) {
    if (geminiLiveMode === mode) return;
    playLcarsBeep(1200, 1600);
    geminiLiveMode = mode;
    isSpeaking = false;
    lastSpeechTime = 0;
    isPttActive = false;
    pttAudioSent = false;
    const turnInd = document.getElementById('geminiLiveTurnIndicator');
    if (turnInd && !isModelSpeaking) turnInd.textContent = 'BEREIT // ZUHÖREN';
    if (geminiLiveChannelOpen && !isModelSpeaking) {
      updateGeminiConnBadge('BEREIT // PUCK', '#44dd88', '#000');
    }
    updateGeminiLiveModeUI();
  }

  function updateGeminiLiveModeUI() {
    const btnPtt = document.getElementById('btnGeminiModePtt');
    const btnLive = document.getElementById('btnGeminiModeLive');
    const modeBadge = document.getElementById('geminiLiveModeBadge');
    const pttArea = document.getElementById('geminiPttActionArea');
    const liveArea = document.getElementById('geminiLiveStatusArea');

    if (geminiLiveMode === 'ptt') {
      if (btnPtt) {
        btnPtt.style.background = 'var(--c-secondary)';
        btnPtt.style.color = '#000';
        btnPtt.style.border = 'none';
      }
      if (btnLive) {
        btnLive.style.background = 'rgba(0,0,0,0.5)';
        btnLive.style.color = 'var(--c-gold)';
        btnLive.style.border = '1px solid var(--c-gold)';
      }
      if (modeBadge) modeBadge.textContent = 'MODUS: PTT';
      if (pttArea) pttArea.style.display = 'block';
      if (liveArea) liveArea.style.display = 'none';
    } else {
      if (btnPtt) {
        btnPtt.style.background = 'rgba(0,0,0,0.5)';
        btnPtt.style.color = 'var(--c-secondary)';
        btnPtt.style.border = '1px solid var(--c-secondary)';
      }
      if (btnLive) {
        btnLive.style.background = 'var(--c-gold)';
        btnLive.style.color = '#000';
        btnLive.style.border = 'none';
      }
      if (modeBadge) modeBadge.textContent = 'MODUS: DAUERHAFT LIVE';
      if (pttArea) pttArea.style.display = 'none';
      if (liveArea) liveArea.style.display = 'block';
    }
  }

  function toggleGeminiLiveMute() {
    geminiLiveMuted = !geminiLiveMuted;
    playLcarsBeep(880, 1320);
    const muteBtn = document.getElementById('btnGeminiMute');
    if (muteBtn) {
      if (geminiLiveMuted) {
        muteBtn.textContent = '🔇 MIKROFON: STUMM';
        muteBtn.style.borderColor = 'var(--c-red)';
        muteBtn.style.color = 'var(--c-red)';
      } else {
        muteBtn.textContent = '🎤 MIKROFON: AN';
        muteBtn.style.borderColor = 'var(--c-gold)';
        muteBtn.style.color = 'var(--c-gold)';
      }
    }
  }

  function toggleGeminiLiveChannel() {
    if (geminiLiveChannelOpen) {
      disconnectGeminiLiveWs();
    } else {
      connectGeminiLiveWs();
    }
  }

  async function connectGeminiLiveWs() {
    if (geminiLiveWs && geminiLiveWs.readyState === WebSocket.OPEN) return;

    updateGeminiConnBadge('VERBINDET...', 'var(--c-gold)', '#000');
    appendGeminiLog('system', 'Verbinde mit Subraum-Relay (/api/gemini-live/ws)...');

    const authCode = sessionStorage.getItem('lcars_auth_code') || '0901';
    const proto = (location.protocol === 'https:') ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${location.host}/api/gemini-live/ws?code=${encodeURIComponent(authCode)}`;

    try {
      geminiLiveWs = new WebSocket(wsUrl);
    } catch (e) {
      updateGeminiConnBadge('FEHLER', 'var(--c-red)', '#fff');
      appendGeminiLog('error', `WebSocket-Initialisierung fehlgeschlagen: ${e}`);
      return;
    }

    geminiLiveWs.onopen = async () => {
      geminiLiveChannelOpen = true;
      updateGeminiToggleBtn(true);
      appendGeminiLog('system', 'WebSocket-Handshake erfolgreich. Initialisiere Audio-Engine...');
      await startGeminiAudioCapture();
    };

    geminiLiveWs.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleGeminiServerMessage(msg);
      } catch (e) {
        console.warn('Gemini WS Parse-Fehler:', e);
      }
    };

    geminiLiveWs.onerror = (err) => {
      console.error('Gemini WS Fehler:', err);
      appendGeminiLog('error', 'Fehler in Subraum-Verbindung aufgetreten.');
    };

    geminiLiveWs.onclose = (event) => {
      geminiLiveChannelOpen = false;
      isPttActive = false;
      pttAudioSent = false;
      isSpeaking = false;
      lastSpeechTime = 0;
      isModelSpeaking = false;
      currentModelTurnEl = null;
      currentUserTranscriptEl = null;
      if (pttStopTimer) { clearTimeout(pttStopTimer); pttStopTimer = null; }
      updateGeminiConnBadge('GETRENNT', 'var(--c-red)', '#fff');
      updateGeminiToggleBtn(false);
      stopGeminiAudioCapture();
      stopAllGeminiAudio();
      stopGeminiVisualizer();
      appendGeminiLog('system', `Subraum-Verbindung beendet (Code ${event.code}).`);
    };
  }

  function disconnectGeminiLiveWs() {
    if (geminiLiveWs) {
      try { geminiLiveWs.close(); } catch (e) {}
      geminiLiveWs = null;
    }
    geminiLiveChannelOpen = false;
    isPttActive = false;
    pttAudioSent = false;
    isSpeaking = false;
    lastSpeechTime = 0;
    isModelSpeaking = false;
    currentModelTurnEl = null;
    currentUserTranscriptEl = null;
    if (pttStopTimer) { clearTimeout(pttStopTimer); pttStopTimer = null; }
    updateGeminiConnBadge('GETRENNT', 'var(--c-red)', '#fff');
    updateGeminiToggleBtn(false);
    stopGeminiAudioCapture();
    stopAllGeminiAudio();
    stopGeminiVisualizer();
  }

  function updateGeminiConnBadge(text, bg, fg = '#000') {
    const badge = document.getElementById('geminiLiveStatusBadge');
    if (badge) {
      badge.textContent = text;
      badge.style.backgroundColor = bg;
      badge.style.color = fg;
    }
  }

  function updateGeminiToggleBtn(isOpen) {
    const btn = document.getElementById('btnGeminiToggleChannel');
    if (!btn) return;
    if (isOpen) {
      btn.textContent = '⏹ KANAL TRENNEN';
      btn.style.borderColor = 'var(--c-red)';
      btn.style.color = 'var(--c-red)';
    } else {
      btn.textContent = '▶ SUBRAUM-KANAL ÖFFNEN';
      btn.style.borderColor = '#44dd88';
      btn.style.color = '#44dd88';
    }
  }

  function handleGeminiServerMessage(msg) {
    const type = msg.type;
    const turnInd = document.getElementById('geminiLiveTurnIndicator');

    if (type === 'setup_complete') {
      updateGeminiConnBadge('BEREIT // PUCK', '#44dd88', '#000');
      if (turnInd) turnInd.textContent = 'BEREIT // ZUHÖREN';
      appendGeminiLog('system', `Subraum-Verbindung zu Google Gemini 3.8 Live hergestellt (Modell: ${msg.model || 'gemini-3.8-live'}).`);
      playLcarsBeep(1400, 2100);
    } else if (type === 'model_audio') {
      isModelSpeaking = true;
      currentUserTranscriptEl = null;
      updateGeminiConnBadge('SPRICHT...', 'var(--c-blue)', '#000');
      if (turnInd) turnInd.textContent = 'BORDCOMPUTER SPRICHT...';
      playGeminiAudioChunk(msg.audio, msg.rate || 24000);
    } else if (type === 'user_transcript') {
      appendGeminiLog('user_transcript', msg.text);
    } else if (type === 'transcript') {
      if (msg.role === 'model') {
        appendGeminiLog('model', msg.text, true);
      } else {
        appendGeminiLog('user', msg.text, false);
      }
    } else if (type === 'interrupted') {
      stopAllGeminiAudio();
      currentModelTurnEl = null;
      currentUserTranscriptEl = null;
      isModelSpeaking = false;
      isSpeaking = false;
      updateGeminiConnBadge('UNTERBROCHEN', 'var(--c-gold)', '#000');
      if (turnInd) turnInd.textContent = 'BARGE-IN // UNTERBROCHEN';
      appendGeminiLog('system', 'Signal: Modell-Sprachausgabe durch User-Sprache unterbrochen.');
      setTimeout(() => {
        if (geminiLiveChannelOpen) {
          updateGeminiConnBadge('BEREIT // PUCK', '#44dd88', '#000');
          if (turnInd) turnInd.textContent = 'BEREIT // ZUHÖREN';
        }
      }, 1000);
    } else if (type === 'turn_complete') {
      currentModelTurnEl = null;
      currentUserTranscriptEl = null;
      if (geminiLiveChannelOpen) {
        updateGeminiConnBadge('BEREIT // PUCK', '#44dd88', '#000');
        if (turnInd) turnInd.textContent = 'BEREIT // ZUHÖREN';
      }
      if (geminiActiveAudioSources.length === 0) {
        isModelSpeaking = false;
      }
    } else if (type === 'error') {
      appendGeminiLog('error', msg.error || 'Unbekannter API-Fehler');
      playLcarsBeep(440, 220);
    }
  }

  // Web Audio API Capture & Resampling
  async function startGeminiAudioCapture() {
    if (geminiAudioStream) return;
    try {
      const audioCtx = getAudioCtx();
      if (audioCtx.state === 'suspended') await audioCtx.resume();

      geminiAudioStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: false, // Verhindert dass Sprache gefressen wird
          autoGainControl: true
        }
      });

      geminiAudioSourceNode = audioCtx.createMediaStreamSource(geminiAudioStream);

      // Input Analyser für Visualizer
      geminiInputAnalyser = audioCtx.createAnalyser();
      geminiInputAnalyser.fftSize = 64;
      geminiAudioSourceNode.connect(geminiInputAnalyser);

      // Output Analyser für Visualizer
      if (!geminiOutputAnalyser) {
        geminiOutputAnalyser = audioCtx.createAnalyser();
        geminiOutputAnalyser.fftSize = 64;
      }

      // ScriptProcessor für 16kHz PCM Stream
      const bufferSize = 2048;
      geminiScriptNode = audioCtx.createScriptProcessor(bufferSize, 1, 1);

      geminiScriptNode.onaudioprocess = (e) => {
        // Output-Puffer auf 0 setzen, um jegliche Mikrofon-Rückkopplung auf Lautsprecher zu verhindern
        for (let ch = 0; ch < e.outputBuffer.numberOfChannels; ch++) {
          e.outputBuffer.getChannelData(ch).fill(0);
        }

        if (!geminiLiveChannelOpen || geminiLiveMuted) return;

        // In PTT-Modus nur senden wenn PTT aktiv ist
        if (geminiLiveMode === 'ptt' && !isPttActive) return;

        const inputChannelData = e.inputBuffer.getChannelData(0);

        // Im Live-Modus während Modell spricht: Akustische Lautsprecher-Rückkopplung verhindern
        // Nur senden wenn Pegel signifikant ist (Barge-In durch den Nutzer)
        if (geminiLiveMode === 'live' && isModelSpeaking) {
          let sumSq = 0;
          for (let i = 0; i < inputChannelData.length; i++) {
            sumSq += inputChannelData[i] * inputChannelData[i];
          }
          const rms = Math.sqrt(sumSq / inputChannelData.length);
          if (rms < 0.05) {
            // Nur Lautsprecher-Echo / Raumgeräusch, nicht an Gemini senden
            return;
          } else {
            // Nutzer spricht aktiv dazwischen -> Modell-Audio sofort stoppen
            stopAllGeminiAudio();
            isModelSpeaking = false;
          }
        }

        // Resampling auf 16 kHz PCM mit Peak/RMS Gain
        const pcm16 = downsampleTo16k(inputChannelData, audioCtx.sampleRate);
        const base64Audio = int16ToBase64(pcm16);

        if (geminiLiveWs && geminiLiveWs.readyState === WebSocket.OPEN) {
          geminiLiveWs.send(JSON.stringify({
            type: 'audio',
            data: base64Audio
          }));
          if (isPttActive) {
            pttAudioSent = true;
          }
        }

        // Im Dauerhaft-Live-Modus: Clientseitige Stille-Erkennung (VAD)
        if (geminiLiveMode === 'live' && !isModelSpeaking) {
          let sumSquares = 0;
          for (let i = 0; i < inputChannelData.length; i++) {
            sumSquares += inputChannelData[i] * inputChannelData[i];
          }
          const rms = Math.sqrt(sumSquares / inputChannelData.length);
          const VAD_THRESHOLD = 0.018; // Audio-Pegel-Schwelle für erkannte Sprache
          const now = performance.now();

          if (rms >= VAD_THRESHOLD) {
            if (!isSpeaking) {
              isSpeaking = true;
              currentUserTranscriptEl = null;
              const turnInd = document.getElementById('geminiLiveTurnIndicator');
              if (turnInd) turnInd.textContent = 'COMMANDER SPRICHT...';
              updateGeminiConnBadge('SPRECHEN...', 'var(--c-primary)', '#000');
            }
            lastSpeechTime = now;
          } else if (isSpeaking) {
            // Pegel unter Schwelle: wenn Stille >= 750ms erreicht wird -> Turn beenden
            if (now - lastSpeechTime >= 750) {
              if (geminiLiveWs && geminiLiveWs.readyState === WebSocket.OPEN) {
                geminiLiveWs.send(JSON.stringify({ type: 'end_of_turn' }));
                const turnInd = document.getElementById('geminiLiveTurnIndicator');
                if (turnInd) turnInd.textContent = 'BORDCOMPUTER DENKT...';
                updateGeminiConnBadge('DENKT...', 'var(--c-gold)', '#000');
              }
              isSpeaking = false;
            }
          }
        }
      };

      geminiAudioSourceNode.connect(geminiScriptNode);
      // Stummer GainNode (0x) damit onaudioprocess ohne akustische Lautsprecher-Rückkopplung läuft
      geminiMuteNode = audioCtx.createGain();
      geminiMuteNode.gain.value = 0;
      geminiScriptNode.connect(geminiMuteNode);
      geminiMuteNode.connect(audioCtx.destination);

      startGeminiVisualizer();
      appendGeminiLog('system', 'Mikrofon aktiv (16 kHz PCM Audio-Graph initialisiert).');
    } catch (err) {
      appendGeminiLog('error', `Mikrofon-Zugriff verweigert oder fehlgeschlagen: ${err.message}`);
      console.error('Mikrofon Fehler:', err);
    }
  }

  function stopGeminiAudioCapture() {
    isSpeaking = false;
    lastSpeechTime = 0;
    if (geminiScriptNode) {
      try {
        geminiScriptNode.disconnect();
        geminiScriptNode.onaudioprocess = null;
      } catch (e) {}
      geminiScriptNode = null;
    }
    if (geminiMuteNode) {
      try { geminiMuteNode.disconnect(); } catch (e) {}
      geminiMuteNode = null;
    }
    if (geminiAudioSourceNode) {
      try { geminiAudioSourceNode.disconnect(); } catch (e) {}
      geminiAudioSourceNode = null;
    }
    if (geminiAudioStream) {
      try {
        geminiAudioStream.getTracks().forEach(t => t.stop());
      } catch (e) {}
      geminiAudioStream = null;
    }
  }

  // Audio Resampling von AudioContext SampleRate auf 16kHz PCM mit Peak/RMS Normalisierung & Gain
  function downsampleTo16k(inputBuffer, inSampleRate) {
    let maxAmp = 0;
    for (let i = 0; i < inputBuffer.length; i++) {
      const abs = Math.abs(inputBuffer[i]);
      if (abs > maxAmp) maxAmp = abs;
    }

    // Leichter intelligenter Software-Gain: Leise Sprache bis zu 3x anheben,
    // um unhörbar leises PCM bei der Übertragung zu verhindern. Reine Stille (<0.005) nicht künstlich verstärken.
    let gain = 1.2;
    if (maxAmp > 0.005 && maxAmp < 0.35) {
      gain = Math.min(3.0, 0.7 / maxAmp);
    }

    if (inSampleRate === 16000) {
      const output = new Int16Array(inputBuffer.length);
      for (let i = 0; i < inputBuffer.length; i++) {
        const s = Math.max(-1, Math.min(1, inputBuffer[i] * gain));
        output[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      return output;
    }
    const ratio = inSampleRate / 16000;
    const newLength = Math.round(inputBuffer.length / ratio);
    const result = new Int16Array(newLength);
    for (let i = 0; i < newLength; i++) {
      const origIndex = i * ratio;
      const indexFloor = Math.floor(origIndex);
      const indexCeil = Math.min(inputBuffer.length - 1, Math.ceil(origIndex));
      const fraction = origIndex - indexFloor;
      const sample = (1 - fraction) * inputBuffer[indexFloor] + fraction * inputBuffer[indexCeil];
      const s = Math.max(-1, Math.min(1, sample * gain));
      result[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return result;
  }

  function int16ToBase64(int16Array) {
    const uint8 = new Uint8Array(int16Array.buffer, int16Array.byteOffset, int16Array.byteLength);
    let binary = '';
    const chunkSize = 4096;
    for (let i = 0; i < uint8.length; i += chunkSize) {
      binary += String.fromCharCode.apply(null, uint8.subarray(i, i + chunkSize));
    }
    return btoa(binary);
  }

  function base64ToInt16(base64) {
    const binary = atob(base64);
    const len = binary.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
  }

  // 24kHz Audio Playback mit AudioBuffer Queueing
  function playGeminiAudioChunk(base64Audio, sampleRate = 24000) {
    const audioCtx = getAudioCtx();
    if (!audioCtx) return;
    if (audioCtx.state === 'suspended') audioCtx.resume();

    try {
      const pcm16 = base64ToInt16(base64Audio);
      if (!pcm16 || pcm16.length === 0) return;
      const float32 = new Float32Array(pcm16.length);
      for (let i = 0; i < pcm16.length; i++) {
        float32[i] = pcm16[i] / 32768.0;
      }

      const audioBuffer = audioCtx.createBuffer(1, float32.length, sampleRate);
      audioBuffer.copyToChannel(float32, 0);

      const source = audioCtx.createBufferSource();
      source.buffer = audioBuffer;

      if (!geminiOutputAnalyser) {
        geminiOutputAnalyser = audioCtx.createAnalyser();
        geminiOutputAnalyser.fftSize = 64;
      }

      source.connect(geminiOutputAnalyser);
      geminiOutputAnalyser.connect(audioCtx.destination);

      const now = audioCtx.currentTime;
      const startTime = Math.max(now, geminiNextPlaybackTime);
      source.start(startTime);
      geminiNextPlaybackTime = startTime + audioBuffer.duration;

      geminiActiveAudioSources.push(source);
      source.onended = () => {
        const idx = geminiActiveAudioSources.indexOf(source);
        if (idx !== -1) geminiActiveAudioSources.splice(idx, 1);
        if (geminiActiveAudioSources.length === 0) {
          isModelSpeaking = false;
          if (geminiLiveChannelOpen && !isSpeaking && !isPttActive) {
            updateGeminiConnBadge('BEREIT // PUCK', '#44dd88', '#000');
            const turnInd = document.getElementById('geminiLiveTurnIndicator');
            if (turnInd && turnInd.textContent === 'BORDCOMPUTER SPRICHT...') {
              turnInd.textContent = 'BEREIT // ZUHÖREN';
            }
          }
        }
      };
    } catch (e) {
      console.warn('Fehler beim Abspielen von Gemini Audio Chunk:', e);
    }
  }

  function stopAllGeminiAudio() {
    geminiActiveAudioSources.forEach(src => {
      try { src.stop(); } catch (e) {}
    });
    geminiActiveAudioSources = [];
    isModelSpeaking = false;
    const audioCtx = getAudioCtx();
    if (audioCtx) geminiNextPlaybackTime = audioCtx.currentTime;
  }

  // LCARS Dual-Visualizer Engine
  function startGeminiVisualizer() {
    if (geminiVizAnimId) return;

    function renderFrame() {
      if (currentCategory !== 'gemini_live') {
        geminiVizAnimId = requestAnimationFrame(renderFrame);
        return;
      }

      let inLevel = 0;
      let outLevel = 0;

      if (geminiInputAnalyser && (!geminiLiveMuted && (geminiLiveMode === 'live' || isPttActive))) {
        const data = new Uint8Array(geminiInputAnalyser.frequencyBinCount);
        geminiInputAnalyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i];
        inLevel = Math.min(100, Math.round((sum / data.length / 255) * 160));
      }

      if (geminiOutputAnalyser && geminiActiveAudioSources.length > 0) {
        const data = new Uint8Array(geminiOutputAnalyser.frequencyBinCount);
        geminiOutputAnalyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i];
        outLevel = Math.min(100, Math.round((sum / data.length / 255) * 160));
      }

      renderGeminiMeters(inLevel, outLevel);
      geminiVizAnimId = requestAnimationFrame(renderFrame);
    }

    geminiVizAnimId = requestAnimationFrame(renderFrame);
  }

  function stopGeminiVisualizer() {
    if (geminiVizAnimId) {
      cancelAnimationFrame(geminiVizAnimId);
      geminiVizAnimId = null;
    }
    renderGeminiMeters(0, 0);
  }

  function renderGeminiMeters(inLevel, outLevel) {
    const inText = document.getElementById('geminiInLevelText');
    const outText = document.getElementById('geminiOutLevelText');
    if (inText) inText.textContent = inLevel + '%';
    if (outText) outText.textContent = outLevel + '%';

    const inMeter = document.getElementById('geminiInMeter');
    const outMeter = document.getElementById('geminiOutMeter');

    if (inMeter) {
      const segs = inMeter.children;
      const activeCount = Math.round((inLevel / 100) * segs.length);
      for (let i = 0; i < segs.length; i++) {
        segs[i].className = 'lcars-meter-seg';
        if (i < activeCount) {
          if (i < 8) segs[i].classList.add('active-low');
          else if (i < 10) segs[i].classList.add('active-mid');
          else segs[i].classList.add('active-high');
        }
      }
    }

    if (outMeter) {
      const segs = outMeter.children;
      const activeCount = Math.round((outLevel / 100) * segs.length);
      for (let i = 0; i < segs.length; i++) {
        segs[i].className = 'lcars-meter-seg';
        if (i < activeCount) {
          if (i < 8) segs[i].classList.add('active-low');
          else if (i < 10) segs[i].classList.add('active-mid');
          else segs[i].classList.add('active-high');
        }
      }
    }
  }

  // Push-to-Talk Event Listeners (Button & Spacebar)
  function setupGeminiPttListeners() {
    const pttBtn = document.getElementById('btnGeminiPttAction');
    if (!pttBtn || pttBtn.dataset.bound === 'true') return;
    pttBtn.dataset.bound = 'true';

    const startPtt = (e) => {
      if (e) e.preventDefault();
      if (pttStopTimer) {
        clearTimeout(pttStopTimer);
        pttStopTimer = null;
      }
      if (!geminiLiveChannelOpen) {
        appendGeminiLog('error', 'Subraum-Kanal nicht geöffnet. Bitte zuerst "SUBRAUM-KANAL ÖFFNEN" anklicken.');
        return;
      }
      if (geminiLiveMode !== 'ptt' || isPttActive) return;

      const audioCtx = getAudioCtx();
      if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume().catch(() => {});
      }

      // Falls Modell noch spricht, sofort unterbrechen (Barge-In)
      if (isModelSpeaking || geminiActiveAudioSources.length > 0) {
        stopAllGeminiAudio();
        isModelSpeaking = false;
      }

      isPttActive = true;
      pttAudioSent = false;
      currentModelTurnEl = null;
      currentUserTranscriptEl = null;
      pttBtn.style.background = 'var(--c-primary)';
      pttBtn.style.color = '#000';
      pttBtn.style.borderColor = 'var(--c-primary)';
      pttBtn.textContent = '🔴 SPRECHEN... (AUFNAHME AKTIV)';
      const turnInd = document.getElementById('geminiLiveTurnIndicator');
      if (turnInd) turnInd.textContent = 'COMMANDER SPRICHT (PTT)...';
      updateGeminiConnBadge('SPRECHEN...', 'var(--c-primary)', '#000');
      playLcarsBeep(880, 1760);
    };

    const stopPtt = (e) => {
      if (e) e.preventDefault();
      if (pttStopTimer) {
        clearTimeout(pttStopTimer);
        pttStopTimer = null;
      }
      if (!isPttActive) return;
      isPttActive = false;

      pttBtn.style.background = 'rgba(186,164,229,0.15)';
      pttBtn.style.color = 'var(--c-secondary)';
      pttBtn.style.borderColor = 'var(--c-secondary)';
      pttBtn.textContent = '🎙️ SPRECHEN (GEDRÜCKT HALTEN / LEERTASTE)';
      playLcarsBeep(1200, 880);

      if (pttAudioSent) {
        if (geminiLiveWs && geminiLiveWs.readyState === WebSocket.OPEN) {
          geminiLiveWs.send(JSON.stringify({ type: 'end_of_turn' }));
          const turnInd = document.getElementById('geminiLiveTurnIndicator');
          if (turnInd) turnInd.textContent = 'BORDCOMPUTER DENKT...';
          updateGeminiConnBadge('DENKT...', 'var(--c-gold)', '#000');
        }
      } else {
        const turnInd = document.getElementById('geminiLiveTurnIndicator');
        if (turnInd && !isModelSpeaking) turnInd.textContent = 'BEREIT // ZUHÖREN';
        if (geminiLiveChannelOpen && !isModelSpeaking) {
          updateGeminiConnBadge('BEREIT // PUCK', '#44dd88', '#000');
        }
      }
      pttAudioSent = false;
    };

    pttBtn.addEventListener('mousedown', startPtt);
    pttBtn.addEventListener('mouseup', stopPtt);
    pttBtn.addEventListener('mouseleave', stopPtt);

    pttBtn.addEventListener('touchstart', startPtt, { passive: false });
    pttBtn.addEventListener('touchend', stopPtt, { passive: false });
    pttBtn.addEventListener('touchcancel', stopPtt, { passive: false });

    // Leertaste als Push-to-Talk Taste
    window.addEventListener('keydown', (e) => {
      if (e.code === 'Space' && currentCategory === 'gemini_live' && geminiLiveMode === 'ptt') {
        const activeTag = document.activeElement ? document.activeElement.tagName : '';
        if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return;
        if (!e.repeat) {
          startPtt(e);
        }
      }
    });

    window.addEventListener('keyup', (e) => {
      if (e.code === 'Space' && currentCategory === 'gemini_live' && geminiLiveMode === 'ptt') {
        const activeTag = document.activeElement ? document.activeElement.tagName : '';
        if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return;
        stopPtt(e);
      }
    });
  }

  // Transkript & Terminal Log
  function appendGeminiLog(role, text, isStreaming = false) {
    if (!text && text !== '') return;
    const box = document.getElementById('geminiLiveTranscript');
    if (!box) return;

    const timeStr = new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    if (role === 'model' && isStreaming && currentModelTurnEl) {
      currentModelTurnEl.textContent += text;
      box.scrollTop = box.scrollHeight;
      return;
    }

    if (role === 'user_transcript') {
      if (currentUserTranscriptEl) {
        currentUserTranscriptEl.textContent = text;
        box.scrollTop = box.scrollHeight;
        return;
      }
      const row = document.createElement('div');
      row.style.wordBreak = 'break-word';
      row.innerHTML = `<span style="color:#888;">[${timeStr}]</span> <span style="color:var(--c-primary); font-weight:700;">[COMMANDER (ERKANNT)]:</span> <span class="user-transcript-text" style="color:#e0f7ff; font-weight:600;">${escapeHtml(text)}</span>`;
      currentUserTranscriptEl = row.querySelector('.user-transcript-text');
      box.appendChild(row);
      box.scrollTop = box.scrollHeight;
      return;
    }

    const row = document.createElement('div');
    row.style.wordBreak = 'break-word';

    if (role === 'user') {
      currentModelTurnEl = null;
      currentUserTranscriptEl = null;
      row.innerHTML = `<span style="color:#888;">[${timeStr}]</span> <span style="color:var(--c-primary); font-weight:700;">COMMANDER:</span> <span style="color:#fff;">${escapeHtml(text)}</span>`;
    } else if (role === 'model') {
      currentUserTranscriptEl = null;
      row.innerHTML = `<span style="color:#888;">[${timeStr}]</span> <span style="color:var(--c-secondary); font-weight:700;">COMPUTER:</span> <span class="model-turn-text" style="color:#e0e8ff;">${escapeHtml(text)}</span>`;
      currentModelTurnEl = isStreaming ? row.querySelector('.model-turn-text') : null;
    } else if (role === 'error') {
      currentModelTurnEl = null;
      currentUserTranscriptEl = null;
      row.innerHTML = `<span style="color:#888;">[${timeStr}]</span> <span style="color:var(--c-red); font-weight:700;">[WARNUNG]</span> <span style="color:var(--c-red);">${escapeHtml(text)}</span>`;
    } else {
      currentModelTurnEl = null;
      currentUserTranscriptEl = null;
      row.innerHTML = `<span style="color:#888;">[${timeStr}]</span> <span style="color:var(--c-gold);">[SYSTEM]</span> <span style="color:#ccc;">${escapeHtml(text)}</span>`;
    }

    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
  }

  function sendGeminiLiveText() {
    const inp = document.getElementById('geminiLiveTextInput');
    if (!inp) return;
    const val = inp.value.trim();
    if (!val) return;

    if (!geminiLiveWs || geminiLiveWs.readyState !== WebSocket.OPEN) {
      appendGeminiLog('error', 'Keine aktive Subraum-Verbindung. Bitte öffnen Sie zuerst den Kanal.');
      return;
    }

    playLcarsBeep(1200, 1600);
    currentModelTurnEl = null;
    currentUserTranscriptEl = null;
    geminiLiveWs.send(JSON.stringify({
      type: 'text',
      text: val
    }));
    inp.value = '';
    inp.focus();
    const turnInd = document.getElementById('geminiLiveTurnIndicator');
    if (turnInd) turnInd.textContent = 'BORDCOMPUTER DENKT...';
    updateGeminiConnBadge('DENKT...', 'var(--c-gold)', '#000');
  }

  function clearGeminiLiveTranscript() {
    playLcarsBeep(880, 440);
    const box = document.getElementById('geminiLiveTranscript');
    if (box) {
      box.innerHTML = '<div style="color:#666; font-style:italic;">[SYSTEM] Subraum-Logbuch zurückgesetzt.</div>';
    }
    currentModelTurnEl = null;
    currentUserTranscriptEl = null;
  }

  // ==========================================================================
  // LCARS REMOTE NODE // PIMMEL CONTROLLER (100.88.215.98)
  // ==========================================================================
  var pimmelHistoryChart = null;
  var pimmelModelChart = null;
  var pimmelTimelineChart = null;
  var pimmelCurrentRange = '1h';
  var pimmelVisibleDatasets = [true, true, true]; // [Temp, CPU, RAM]
  var pimmelLogAutoScroll = true;
  var isFetchingPimmelStats = false;
  var isPimmelSyncing = false;

  async function fetchPimmelStats(playSound = false) {
    if (isFetchingPimmelStats) return;
    isFetchingPimmelStats = true;
    if (playSound) playLcarsBeep(1400, 900);

    try {
      const resp = await fetch('/api/pimmel/status');
      if (resp.ok) {
        const data = await resp.json();
        renderPimmelStatus(data);
      }
    } catch (err) {
      console.warn('Pimmel Status Fehler:', err);
    } finally {
      isFetchingPimmelStats = false;
    }

    // Zusätzliche Daten abrufen
    fetchPimmelHistory(pimmelCurrentRange);
    fetchPimmel9Router();
    fetchPimmelLogs();
  }

  function renderPimmelStatus(data) {
    if (!data) return;
    const node = data.node || {};
    const host = data.host_metrics || {};
    const nr = data.nine_router_summary || {};

    // Node Status Badge & Alert
    const statusBadge = document.getElementById('pimmelNodeStatusBadge');
    const alertEl = document.getElementById('pimmelOfflineAlert');
    const alertErr = document.getElementById('pimmelOfflineErrMsg');
    const lastSyncEl = document.getElementById('pimmelLastSyncText');

    if (node.online) {
      if (statusBadge) {
        statusBadge.textContent = '● ONLINE';
        statusBadge.className = 'badge-status badge-online';
      }
      if (alertEl) alertEl.style.display = 'none';
    } else {
      if (statusBadge) {
        statusBadge.textContent = '● OFFLINE';
        statusBadge.className = 'badge-status badge-offline';
      }
      if (alertEl) {
        alertEl.style.display = 'block';
        if (alertErr) alertErr.textContent = node.last_error ? `Detail: ${node.last_error}` : '';
      }
    }

    if (lastSyncEl && node.last_sync) {
      try {
        const d = new Date(node.last_sync);
        lastSyncEl.textContent = 'SYNC: ' + d.toLocaleTimeString();
      } catch (e) {
        lastSyncEl.textContent = 'SYNC: ' + (node.last_sync.split('T')[1] || '').substring(0, 8);
      }
    }

    // Host Vitals: CPU
    const cpu = host.cpu || {};
    const load = host.load || {};
    const cpuVal = document.getElementById('pimmelCpuVal');
    const cpuSub = document.getElementById('pimmelCpuSub');
    const cpuBar = document.getElementById('pimmelCpuBar');
    if (cpuVal) cpuVal.textContent = (cpu.percent !== undefined ? cpu.percent : '--') + '%';
    if (cpuSub) cpuSub.textContent = `Kerne: ${cpu.cores || 4} | Load: ${load['1m'] || '--'} / ${load['5m'] || '--'} / ${load['15m'] || '--'}`;
    if (cpuBar) cpuBar.style.width = Math.min(100, Math.max(0, cpu.percent || 0)) + '%';

    // Host Vitals: RAM
    const ram = host.ram || {};
    const ramVal = document.getElementById('pimmelRamVal');
    const ramSub = document.getElementById('pimmelRamSub');
    const ramBar = document.getElementById('pimmelRamBar');
    if (ramVal) ramVal.textContent = (ram.percent !== undefined ? ram.percent : '--') + '%';
    if (ramSub) ramSub.textContent = `${ram.used_gb || '--'} / ${ram.total_gb || '--'}`;
    if (ramBar) ramBar.style.width = Math.min(100, Math.max(0, ram.percent || 0)) + '%';

    // Host Vitals: Temp
    const temp = host.temp || {};
    const tempVal = document.getElementById('pimmelTempVal');
    const tempBadge = document.getElementById('pimmelTempBadge');
    if (tempVal) tempVal.textContent = temp.display || (temp.value ? temp.value + ' °C' : '--.- °C');
    if (tempBadge) {
      const tv = temp.value || 0;
      if (tv >= 70) {
        tempBadge.textContent = 'ALARM // HOCH';
        tempBadge.className = 'badge-status badge-offline';
      } else if (tv >= 50) {
        tempBadge.textContent = 'WARM // ACHTUNG';
        tempBadge.className = 'badge-status';
        tempBadge.style.backgroundColor = 'var(--c-gold)';
        tempBadge.style.color = '#000';
      } else {
        tempBadge.textContent = 'NORMAL // NOMINAL';
        tempBadge.className = 'badge-status badge-online';
      }
    }

    // Host Vitals: Disk
    const disk = host.disk || {};
    const diskVal = document.getElementById('pimmelDiskVal');
    const diskSub = document.getElementById('pimmelDiskSub');
    const diskBar = document.getElementById('pimmelDiskBar');
    if (diskVal) diskVal.textContent = (disk.percent !== undefined ? disk.percent : '--') + '%';
    if (diskSub) diskSub.textContent = `${disk.used_gb || '--'} / ${disk.total_gb || '--'}`;
    if (diskBar) diskBar.style.width = Math.min(100, Math.max(0, disk.percent || 0)) + '%';

    // Host Vitals: Uptime
    const uptime = host.uptime || {};
    const uptimeVal = document.getElementById('pimmelUptimeVal');
    const bootTimeSub = document.getElementById('pimmelBootTimeSub');
    if (uptimeVal) uptimeVal.textContent = uptime.display || '-- Std -- Min';
    if (bootTimeSub) bootTimeSub.textContent = 'Boot: ' + (uptime.boot_time || '--');

    // Services: Hermes Gateway
    const services = host.services || {};
    const hermes = services.hermes_gateway || {};
    const hermesStatus = document.getElementById('pimmelHermesStatus');
    const hermesBadge = document.getElementById('pimmelHermesBadge');
    if (hermesStatus) hermesStatus.textContent = (hermes.status === 'active') ? 'AKTIV' : (hermes.status || 'INAKTIV').toUpperCase();
    if (hermesBadge) {
      if (hermes.status === 'active') {
        hermesBadge.textContent = 'ONLINE // RUNNING';
        hermesBadge.className = 'badge-status badge-online';
      } else {
        hermesBadge.textContent = 'INAKTIV / FAILED';
        hermesBadge.className = 'badge-status badge-offline';
      }
    }

    // Services: PM2 9Router
    const pm2 = services.pm2_9router || {};
    const pm2Status = document.getElementById('pimmelPm2Status');
    const pm2Sub = document.getElementById('pimmelPm2Sub');
    const pm2Badge = document.getElementById('pimmelPm2Badge');
    if (pm2Status) pm2Status.textContent = (pm2.status || 'OFFLINE').toUpperCase();
    if (pm2Sub) pm2Sub.textContent = `PID: ${pm2.pid || '--'} | Restarts: ${pm2.restarts || 0} | Mem: ${pm2.memory_mb || 0} MB`;
    if (pm2Badge) {
      if (pm2.status === 'online') {
        pm2Badge.textContent = (pm2.version ? 'v' + pm2.version + ' // ' : '') + 'RUNNING';
        pm2Badge.className = 'badge-status badge-online';
      } else {
        pm2Badge.textContent = 'OFFLINE';
        pm2Badge.className = 'badge-status badge-offline';
      }
    }

    // 9Router Telemetrie Tiles
    const rReqs = document.getElementById('pimmel9rRequests');
    if (rReqs) rReqs.textContent = nr.total_requests || '0';

    const rTokens = document.getElementById('pimmel9rTokens');
    const rPromptCompl = document.getElementById('pimmel9rPromptCompl');
    const rCached = document.getElementById('pimmel9rCached');
    if (rTokens) rTokens.textContent = nr.total_tokens_formatted || '0';
    if (rPromptCompl) rPromptCompl.textContent = `Prompt: ${nr.prompt_tokens_formatted || '0'} | Compl: ${nr.completion_tokens_formatted || '0'}`;
    if (rCached) rCached.textContent = `Cached: ${nr.cached_tokens_formatted || '0'}`;

    const rCacheRate = document.getElementById('pimmel9rCacheRate');
    const rSavedCost = document.getElementById('pimmel9rSavedCost');
    const rSavedTokens = document.getElementById('pimmel9rSavedTokens');
    if (rCacheRate) rCacheRate.textContent = nr.cache_hit_rate_formatted || '0.0%';
    if (rSavedTokens) rSavedTokens.textContent = `Tokens gespart: ${nr.cached_tokens_formatted || '0'}`;
    if (rSavedCost) rSavedCost.textContent = `Kosten gespart: ~${nr.saved_cost_formatted || '$0.00'}` + (nr.saved_cost_pct_formatted ? ` (-${nr.saved_cost_pct_formatted})` : '');

    const rCost = document.getElementById('pimmel9rTotalCost');
    const rCostSavingsSub = document.getElementById('pimmel9rCostSavingsSub');
    if (rCost) rCost.textContent = nr.cost_formatted || '$0.00';
    if (rCostSavingsSub) rCostSavingsSub.textContent = `Netto-Ersparnis: ~${nr.saved_cost_formatted || '$0.00'}`;
  }

  async function fetchPimmelHistory(rangeParam = '1h') {
    try {
      const resp = await fetch(`/api/pimmel/history?range=${encodeURIComponent(rangeParam)}`);
      if (resp.ok) {
        const data = await resp.json();
        updatePimmelHistoryChart(data.samples || []);
      }
    } catch (e) {
      console.warn('Pimmel History Fehler:', e);
    }
  }

  function setPimmelHistoryRange(range) {
    pimmelCurrentRange = range;
    document.querySelectorAll('.pimmel-range-btn').forEach(btn => {
      btn.classList.remove('active-range');
      if (btn.textContent.trim().toLowerCase() === range.toLowerCase()) {
        btn.classList.add('active-range');
      }
    });
    fetchPimmelHistory(range);
  }

  function togglePimmelDataset(idx) {
    if (idx < 0 || idx >= pimmelVisibleDatasets.length) return;
    pimmelVisibleDatasets[idx] = !pimmelVisibleDatasets[idx];
    const btn = document.getElementById('pdsBtn' + idx);
    if (btn) {
      btn.style.opacity = pimmelVisibleDatasets[idx] ? '1.0' : '0.4';
    }
    if (pimmelHistoryChart) {
      pimmelHistoryChart.setDatasetVisibility(idx, pimmelVisibleDatasets[idx]);
      pimmelHistoryChart.update();
    }
  }

  function updatePimmelHistoryChart(samples) {
    if (!samples) return;
    const labels = samples.map(s => s.time || (s.ts ? new Date(s.ts * 1000).toLocaleTimeString() : ''));
    const temps = samples.map(s => s.temp || 0);
    const cpus = samples.map(s => s.cpu || 0);
    const rams = samples.map(s => s.ram || 0);

    const canvas = document.getElementById('pimmelHistoryCanvas');
    if (!canvas) return;

    ensureChart(() => {
      if (pimmelHistoryChart) {
        pimmelHistoryChart.data.labels = labels;
        pimmelHistoryChart.data.datasets[0].data = temps;
        pimmelHistoryChart.data.datasets[1].data = cpus;
        pimmelHistoryChart.data.datasets[2].data = rams;
        pimmelHistoryChart.update('none');
      } else {
        const ctx = canvas.getContext('2d');
        pimmelHistoryChart = new Chart(ctx, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [
              {
                label: 'Temperatur (°C)',
                data: temps,
                borderColor: '#cf4f4f',
                backgroundColor: 'rgba(207, 79, 79, 0.15)',
                borderWidth: 2,
                pointRadius: samples.length > 50 ? 0 : 2,
                tension: 0.2,
                yAxisID: 'y'
              },
              {
                label: 'CPU (%)',
                data: cpus,
                borderColor: '#44dd88',
                backgroundColor: 'rgba(68, 221, 136, 0.15)',
                borderWidth: 2,
                pointRadius: samples.length > 50 ? 0 : 2,
                tension: 0.2,
                yAxisID: 'y'
              },
              {
                label: 'RAM (%)',
                data: rams,
                borderColor: '#baa4e5',
                backgroundColor: 'rgba(186, 164, 229, 0.15)',
                borderWidth: 2,
                pointRadius: samples.length > 50 ? 0 : 2,
                tension: 0.2,
                yAxisID: 'y'
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: 'rgba(0,0,0,0.85)',
                titleFont: { family: 'Share Tech Mono, monospace' },
                bodyFont: { family: 'Share Tech Mono, monospace' }
              }
            },
            scales: {
              x: {
                grid: { color: 'rgba(255,255,255,0.06)' },
                ticks: { color: '#888', font: { family: 'Share Tech Mono, monospace', size: 10 }, maxRotation: 0 }
              },
              y: {
                min: 0,
                max: 100,
                grid: { color: 'rgba(255,255,255,0.08)' },
                ticks: { color: '#888', font: { family: 'Share Tech Mono, monospace', size: 10 } }
              }
            }
          }
        });
        for (let i = 0; i < pimmelVisibleDatasets.length; i++) {
          pimmelHistoryChart.setDatasetVisibility(i, pimmelVisibleDatasets[i]);
        }
        pimmelHistoryChart.update();
      }
    });
  }

  async function fetchPimmel9Router() {
    try {
      const resp = await fetch('/api/pimmel/9router');
      if (resp.ok) {
        const data = await resp.json();
        renderPimmel9RouterData(data);
      }
    } catch (e) {
      console.warn('Pimmel 9Router Fehler:', e);
    }
  }

  function renderPimmel9RouterData(data) {
    if (!data) return;

    // Active Provider Count & List
    const provCountEl = document.getElementById('pimmel9rProviderCount');
    const provListEl = document.getElementById('pimmel9rProvidersList');
    const conns = data.connections || [];
    if (provCountEl) provCountEl.textContent = conns.length;
    if (provListEl) {
      if (conns.length > 0) {
        provListEl.textContent = conns.map(c => (c.provider || '').toUpperCase()).join(' • ');
      } else {
        provListEl.textContent = 'Keine Verbindungen';
      }
    }

    // Uncached Cost Subtitle
    const uncostEl = document.getElementById('pimmel9rUncachedCost');
    const totals = data.totals || {};
    if (uncostEl) uncostEl.textContent = `Ohne Cache: ~${totals.uncached_cost_formatted || '$0.00'}`;

    // Table Body Render
    const tbody = document.getElementById('pimmelHistoryTableBody');
    const history = data.recent_history || [];
    if (tbody) {
      if (history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" style="padding:0.8rem; text-align:center; color:#888;">Keine Transaktionen aufgezeichnet.</td></tr>';
      } else {
        let rowsHtml = '';
        history.forEach(r => {
          const isOk = (r.status === 'ok');
          const badgeClass = isOk ? 'badge-online' : 'badge-offline';
          rowsHtml += `
            <tr style="border-bottom:1px solid rgba(255,255,255,0.08); font-family:var(--mono-family);">
              <td style="padding:0.45rem 0.4rem; color:var(--c-gold);">#${r.id}</td>
              <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:#ccc;">${r.time_display || ''}</td>
              <td style="padding:0.45rem 0.4rem;"><span style="color:var(--c-blue); font-weight:700;">${(r.provider || '').toUpperCase()}</span></td>
              <td style="padding:0.45rem 0.4rem; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${r.model || ''}">${r.model || ''}</td>
              <td style="padding:0.45rem 0.4rem; white-space:nowrap;">${r.prompt_tokens || 0}</td>
              <td style="padding:0.45rem 0.4rem; white-space:nowrap;">${r.completion_tokens || 0}</td>
              <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:var(--c-blue);">${r.cached_tokens || 0}</td>
              <td style="padding:0.45rem 0.4rem; white-space:nowrap; font-weight:700; color:var(--c-primary);">${r.total_tokens || 0}</td>
              <td style="padding:0.45rem 0.4rem; white-space:nowrap; color:var(--c-gold);">${r.cost_formatted || '$0.00'}</td>
              <td style="padding:0.45rem 0.4rem;">
                <span class="badge-status ${badgeClass}" style="padding:0.15rem 0.4rem; font-size:0.75rem;">
                  ${(r.status || 'OK').toUpperCase()}
                </span>
              </td>
            </tr>`;
        });
        tbody.innerHTML = rowsHtml;
      }
    }

    // Model Distribution Doughnut Chart
    renderPimmelModelChart(data.by_model || {});

    // Daily Timeline Chart
    renderPimmelTimelineChart(data.daily_timeline || []);
  }

  function renderPimmelModelChart(byModel) {
    const canvas = document.getElementById('pimmelModelCanvas');
    if (!canvas) return;

    const entries = Object.values(byModel || {});
    entries.sort((a, b) => (b.requests || 0) - (a.requests || 0));
    const topEntries = entries.slice(0, 7);

    const labels = topEntries.map(e => e.model || 'Unknown');
    const counts = topEntries.map(e => e.requests || 0);
    const colors = [
      '#baa4e5', '#8899ff', '#44dd88', '#edb378', '#ff9900', '#cf4f4f', '#66aacc'
    ];

    ensureChart(() => {
      if (pimmelModelChart) {
        pimmelModelChart.data.labels = labels;
        pimmelModelChart.data.datasets[0].data = counts;
        pimmelModelChart.update('none');
      } else {
        const ctx = canvas.getContext('2d');
        pimmelModelChart = new Chart(ctx, {
          type: 'doughnut',
          data: {
            labels: labels,
            datasets: [{
              data: counts,
              backgroundColor: colors.slice(0, counts.length),
              borderColor: '#000',
              borderWidth: 2
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                position: 'right',
                labels: {
                  color: '#ccc',
                  font: { family: 'Share Tech Mono, monospace', size: 10 },
                  boxWidth: 12
                }
              },
              tooltip: {
                backgroundColor: 'rgba(0,0,0,0.85)',
                titleFont: { family: 'Share Tech Mono, monospace' },
                bodyFont: { family: 'Share Tech Mono, monospace' }
              }
            }
          }
        });
      }
    });
  }

  function renderPimmelTimelineChart(timeline) {
    const canvas = document.getElementById('pimmelTimelineCanvas');
    if (!canvas) return;

    const labels = timeline.map(t => t.date || '');
    const requests = timeline.map(t => t.requests || 0);
    const promptTokens = timeline.map(t => t.prompt_tokens || 0);
    const cachedTokens = timeline.map(t => t.cached_tokens || 0);
    const costs = timeline.map(t => t.cost || 0.0);

    ensureChart(() => {
      if (pimmelTimelineChart) {
        pimmelTimelineChart.data.labels = labels;
        pimmelTimelineChart.data.datasets[0].data = requests;
        pimmelTimelineChart.data.datasets[1].data = cachedTokens;
        pimmelTimelineChart.data.datasets[2].data = promptTokens;
        pimmelTimelineChart.data.datasets[3].data = costs;
        pimmelTimelineChart.update('none');
      } else {
        const ctx = canvas.getContext('2d');
        pimmelTimelineChart = new Chart(ctx, {
          type: 'bar',
          data: {
            labels: labels,
            datasets: [
              {
                type: 'line',
                label: 'Requests',
                data: requests,
                borderColor: 'var(--c-primary)',
                backgroundColor: 'var(--c-primary)',
                borderWidth: 2,
                pointRadius: 3,
                yAxisID: 'yReq'
              },
              {
                label: 'Cached Tokens',
                data: cachedTokens,
                backgroundColor: '#8899ff',
                stack: 'tokens',
                yAxisID: 'yTok'
              },
              {
                label: 'Prompt Tokens',
                data: promptTokens,
                backgroundColor: 'rgba(186, 164, 229, 0.4)',
                stack: 'tokens',
                yAxisID: 'yTok'
              },
              {
                type: 'line',
                label: 'Kosten ($)',
                data: costs,
                borderColor: 'var(--c-gold)',
                borderWidth: 2,
                pointRadius: 2,
                borderDash: [4, 4],
                yAxisID: 'yCost'
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
              legend: {
                labels: { color: '#ccc', font: { family: 'Share Tech Mono, monospace', size: 10 } }
              },
              tooltip: {
                backgroundColor: 'rgba(0,0,0,0.85)',
                titleFont: { family: 'Share Tech Mono, monospace' },
                bodyFont: { family: 'Share Tech Mono, monospace' }
              }
            },
            scales: {
              x: {
                grid: { color: 'rgba(255,255,255,0.06)' },
                ticks: { color: '#888', font: { family: 'Share Tech Mono, monospace', size: 10 } }
              },
              yReq: {
                type: 'linear',
                position: 'left',
                grid: { color: 'rgba(255,255,255,0.08)' },
                ticks: { color: 'var(--c-primary)', font: { family: 'Share Tech Mono, monospace', size: 10 } }
              },
              yTok: {
                type: 'linear',
                position: 'right',
                grid: { drawOnChartArea: false },
                ticks: {
                  color: '#8899ff',
                  font: { family: 'Share Tech Mono, monospace', size: 10 },
                  callback: v => (v >= 1000000 ? (v / 1000000).toFixed(1) + 'M' : v)
                }
              },
              yCost: {
                type: 'linear',
                position: 'right',
                grid: { drawOnChartArea: false },
                ticks: {
                  color: 'var(--c-gold)',
                  font: { family: 'Share Tech Mono, monospace', size: 10 },
                  callback: v => '$' + v.toFixed(2)
                }
              }
            }
          }
        });
      }
    });
  }

  function initPimmelCharts() {
    ensureChart(() => {
      fetchPimmelHistory(pimmelCurrentRange);
      fetchPimmel9Router();
    });
  }

  async function fetchPimmelLogs() {
    const sel = document.getElementById('pimmelLogLineSelect');
    const lines = sel ? sel.value : 60;
    try {
      const resp = await fetch(`/api/pimmel/logs?lines=${lines}`);
      if (resp.ok) {
        const data = await resp.json();
        renderPimmelLogs(data.logs || '');
      }
    } catch (e) {
      console.warn('Pimmel Logs Fehler:', e);
    }
  }

  function renderPimmelLogs(rawLogs) {
    const term = document.getElementById('pimmelLogTerminal');
    if (!term) return;

    if (!rawLogs.trim()) {
      term.innerHTML = '<div style="color:#888; font-style:italic;">[KEINE LOGS VORHANDEN]</div>';
      return;
    }

    const lines = rawLogs.trim().split('\\n');
    const formattedLines = lines.map(line => {
      let l = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      if (l.includes('POST')) {
        l = `<span style="color:var(--c-gold); font-weight:700;">${l}</span>`;
      } else if (l.includes('DONE')) {
        l = `<span style="color:#44dd88;">${l}</span>`;
      } else if (l.includes('CACHE ↻')) {
        l = `<span style="color:#8899ff;">${l}</span>`;
      } else if (l.includes('ERROR') || l.includes('Fehler') || l.includes('ERR')) {
        l = `<span style="color:var(--c-red); font-weight:700;">${l}</span>`;
      } else {
        l = `<span style="color:#bbb;">${l}</span>`;
      }
      return l;
    });

    term.innerHTML = formattedLines.join('<br>');

    if (pimmelLogAutoScroll) {
      term.scrollTop = term.scrollHeight;
    }
  }

  function togglePimmelLogAutoScroll() {
    pimmelLogAutoScroll = !pimmelLogAutoScroll;
    const btn = document.getElementById('btnPimmelLogAutoScroll');
    if (btn) {
      btn.innerHTML = pimmelLogAutoScroll ? '<span>⬇️ AUTO-SCROLL: AN</span>' : '<span>⏸️ AUTO-SCROLL: AUS</span>';
      btn.style.color = pimmelLogAutoScroll ? 'var(--c-blue)' : '#888';
      btn.style.borderColor = pimmelLogAutoScroll ? 'var(--c-blue)' : '#555';
    }
    if (pimmelLogAutoScroll) {
      const term = document.getElementById('pimmelLogTerminal');
      if (term) term.scrollTop = term.scrollHeight;
    }
  }

  function copyPimmelLogs() {
    const term = document.getElementById('pimmelLogTerminal');
    if (!term) return;
    const text = term.innerText;
    navigator.clipboard.writeText(text).then(() => {
      playLcarsBeep(1600, 1800);
      alert('PM2 9Router Logs in die Zwischenablage kopiert.');
    }).catch(err => {
      alert('Kopieren fehlgeschlagen: ' + err);
    });
  }

  async function triggerPimmelSync() {
    if (isPimmelSyncing) return;
    isPimmelSyncing = true;
    playLcarsBeep(1200, 1600);

    const btn = document.getElementById('btnPimmelSyncDb');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span>⏳</span> <span>SYNCING...</span>';
    }

    try {
      const resp = await fetch('/api/pimmel/sync', { method: 'POST' });
      if (resp.ok) {
        setTimeout(() => {
          fetchPimmelStats(false);
          if (btn) {
            btn.innerHTML = '<span>✓</span> <span>GESYNCT!</span>';
            setTimeout(() => {
              btn.innerHTML = '<span>⚡</span> <span>SYNC DB</span>';
              btn.disabled = false;
            }, 2000);
          }
        }, 1200);
      }
    } catch (e) {
      alert('Fehler beim Synchronisieren: ' + e);
      if (btn) {
        btn.innerHTML = '<span>⚡</span> <span>SYNC DB</span>';
        btn.disabled = false;
      }
    } finally {
      isPimmelSyncing = false;
    }
  }

  // Initialer Boot-Ablauf
  function bootDashboard() {
    renderStats(initialStats);
    if (initialStats?.nine_router) {
      renderNineRouterStats(initialStats.nine_router);
    }
    const initialTimeEl = document.getElementById('chatInitialTime');
    if (initialTimeEl) initialTimeEl.textContent = formatTimeNow();
    loadChatModels();
    ensureChart(() => {
      initHistoryChart();
      initNineRouterCharts();
      initHermesChart();
    });
    startFantasyAutoRefresh();
    checkHomeAssistantConfig();
    startHaAutoRefresh();
    loadSolarData(false);
    startSolarAutoRefresh();
    initLcarsVoiceComm();
    fetchPermissionsStatus();
    loadCycleData();
    checkPulsecastStatus();
    checkGeminiLiveStatus();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootDashboard);
  } else {
    bootDashboard();
  }
</script>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# Fallback HTML Renderer für Basis-HTTP-Server
# ---------------------------------------------------------------------------
def render_html_fallback(stats):
    stats_json = json.dumps(stats)
    return DASHBOARD_HTML.replace("{{ stats.hostname }}", str(stats.get("hostname", "pi"))) \
                         .replace("{{ stats_json | safe }}", stats_json) \
                         .replace("{{ stats.timestamp }}", str(stats.get("timestamp", ""))) \
                         .replace("{{ stats.platform }}", str(stats.get("platform", "")))


# ---------------------------------------------------------------------------
# Server Initialisierung & Routes
# ---------------------------------------------------------------------------
if USE_FLASK:
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    try:
        from flask_sock import Sock
        sock = Sock(app)
        USE_SOCK = True
    except Exception as _sock_err:
        sock = None
        USE_SOCK = False
        print(f"[WARN] flask_sock konnte nicht initialisiert werden: {_sock_err}", file=sys.stderr)

    try:
        from websockets.sync.client import connect as ws_connect
    except Exception as _ws_err:
        ws_connect = None
        print(f"[WARN] websockets.sync.client konnte nicht importiert werden: {_ws_err}", file=sys.stderr)

    @app.after_request
    def compress_response(response):
        accept_encoding = request.headers.get("Accept-Encoding", "")
        if "gzip" not in accept_encoding.lower():
            return response
        if response.status_code < 200 or response.status_code >= 300:
            return response
        content_type = response.headers.get("Content-Type", "")
        if not any(t in content_type for t in ("text/", "application/json", "application/javascript")):
            return response
        if response.direct_passthrough:
            return response
        data = response.get_data()
        if len(data) < 256:
            return response
        compressed = gzip.compress(data, compresslevel=5)
        if len(compressed) < len(data):
            response.set_data(compressed)
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Content-Length"] = len(compressed)
        return response

    @app.route("/static/<path:filename>")
    def serve_static(filename):
        resp = send_from_directory("static", filename)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    @app.route("/chart.umd.min.js")
    def serve_chart_root():
        resp = send_from_directory("static", "chart.umd.min.js")
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    @app.route("/")
    def index():
        stats = get_system_stats(include_history=True)
        stats_json = json.dumps(stats)
        return render_template_string(DASHBOARD_HTML, stats=stats, stats_json=stats_json)

    @app.route("/api/stats")
    def api_stats():
        return jsonify(get_system_stats(include_history=False))

    @app.route("/api/alerts")
    def api_alerts():
        return jsonify(alert_monitor.get_status())

    @app.route("/api/config/thresholds", methods=["GET", "POST"])
    def api_config_thresholds():
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            res = alert_monitor.save_config(data)
            return jsonify({"success": True, "status": res})
        return jsonify(alert_monitor.get_status())

    @app.route("/api/gateway/test", methods=["POST"])
    def api_gateway_test():
        res = alert_monitor.check_gateway()
        return jsonify(res)

    @app.route("/api/9router/stats")
    def api_9router_stats():
        return jsonify(get_9router_stats())

    @app.route("/api/history")
    def api_history():
        range_param = request.args.get("range", "1h")
        range_label, seconds = parse_range_param(range_param)
        samples = history_store.get_samples(range_seconds=seconds)
        return jsonify({
            "range": range_param,
            "seconds": seconds,
            "count": len(samples),
            "samples": samples,
        })

    @app.route("/api/discovered-servers")
    def api_discovered_servers():
        return jsonify(webserver_scanner.get_results())

    @app.route("/api/scan-webservers", methods=["GET", "POST"])
    def api_scan_webservers():
        discovered = webserver_scanner.scan()
        return jsonify({
            "status": "success",
            "count": len(discovered),
            "discovered": discovered,
            "scan_time": webserver_scanner.last_scan_time,
        })

    @app.route("/api/services/stop", methods=["POST"])
    def api_stop_service():
        data = request.get_json(silent=True) or {}
        pid = data.get("pid")
        port = data.get("port")
        if not pid and not port:
            return jsonify({"success": False, "error": "PID oder Port erforderlich"}), 400

        my_pid = os.getpid()
        if (pid and int(pid) == my_pid) or (port and int(port) == 5000):
            return jsonify({"success": False, "error": "Das Dashboard selbst kann nicht beendet werden"}), 403

        stopped_processes = []
        if pid:
            try:
                pid = int(pid)
                if pid <= 1:
                    return jsonify({"success": False, "error": "Systemprozesse können nicht beendet werden"}), 403
                if psutil and psutil.pid_exists(pid):
                    p = psutil.Process(pid)
                    try:
                        p_user = p.username()
                        import getpass
                        if p_user != getpass.getuser() and os.geteuid() != 0:
                            return jsonify({"success": False, "error": f"Keine Berechtigung für Prozess von {p_user}"}), 403
                    except Exception:
                        pass
                    p.terminate()
                    try:
                        p.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        p.kill()
                    stopped_processes.append(pid)
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                return jsonify({"success": False, "error": f"Fehler beim Beenden von PID {pid}: {e}"}), 500

        if port:
            try:
                port = int(port)
                cf_tunnel_manager.stop_tunnel(port)
            except Exception:
                pass

        threading.Thread(target=webserver_scanner.scan, daemon=True).start()
        return jsonify({"success": True, "stopped_pid": pid, "port": port})

    @app.route("/api/chat/models")
    def api_chat_models():
        return jsonify(fetch_chat_models())

    @app.route("/api/chat", methods=["POST"])
    def api_chat():
        data = request.get_json(silent=True) or {}
        model = data.get("model") or "ag/gemini-3.8-flash-high"
        messages = data.get("messages")
        if not messages:
            prompt = data.get("message") or data.get("prompt") or ""
            if not prompt:
                return jsonify({"error": "messages oder message Parameter erforderlich"}), 400
            messages = [{"role": "user", "content": prompt}]
        auth_header = request.headers.get("Authorization")
        res_data, status_code = forward_chat_completion(model, messages, auth_header)
        return jsonify(res_data), status_code

    @app.route("/api/hermes/profiles", methods=["GET", "POST"])
    def api_hermes_profiles_route():
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            name = data.get("name", "")
            desc = data.get("description", "")
            clone_from = data.get("clone_from", "default")
            model = data.get("model", "")
            res = create_hermes_profile(name, desc, clone_from, model)
            return jsonify(res), (200 if res.get("success") else 400)
        return jsonify({"profiles": get_hermes_profiles()})

    @app.route("/api/hermes/chat", methods=["POST"])
    def api_hermes_chat_route():
        data = request.get_json(silent=True) or {}
        profile = data.get("profile", "default")
        message = data.get("message", "")
        res = hermes_chat_prompt(profile, message)
        return jsonify(res), (200 if res.get("success") else 500)

    @app.route("/api/config/ide-url", methods=["GET", "POST"])
    def api_ide_url_route():
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            url = data.get("url", "").strip()
            if url:
                alert_monitor.save_config({"antigravity_ide_url": url})
                return jsonify({"success": True, "url": url})
            return jsonify({"error": "URL erforderlich"}), 400
        return jsonify({"url": alert_monitor.config.get("antigravity_ide_url", "")})

    @app.route("/api/fantasy")
    def api_fantasy():
        if espn_client:
            return jsonify(espn_client.fetch(force=False))
        return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503

    @app.route("/api/fantasy/refresh", methods=["GET", "POST"])
    def api_fantasy_refresh():
        if espn_client:
            return jsonify(espn_client.fetch(force=True))
        return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503

    @app.route("/api/fantasy/test-flash", methods=["GET", "POST"])
    def api_fantasy_test_flash():
        data = request.get_json(silent=True) if request.method == "POST" else {}
        if not data:
            data = {}
        entity_id = request.args.get("entity_id") or data.get("entity_id") or "light.esstisch"
        duration_raw = request.args.get("duration") or data.get("duration") or 1.2
        try:
            duration = float(duration_raw)
        except (ValueError, TypeError):
            duration = 1.2

        if espn_client:
            res = espn_client.trigger_flash(entity_id=entity_id, duration=duration)
            return jsonify({
                "success": bool(res),
                "message": f"Flash-Signal für {entity_id} ({duration}s) ausgelöst",
                "entity_id": entity_id,
                "duration": duration
            })
        elif ha_service:
            ha_service.flash_light(entity_id=entity_id, duration=duration, async_run=True)
            return jsonify({
                "success": True,
                "message": f"Flash-Signal für {entity_id} ({duration}s) ausgelöst",
                "entity_id": entity_id,
                "duration": duration
            })
        return jsonify({"success": False, "error": "Weder espn_client noch ha_service verfügbar"}), 503

    # =========================================================================
    # ESPN FANTASY KI-MANAGER ENDPUNKTE
    # =========================================================================
    @app.route("/api/espn/mode", methods=["GET", "POST"])
    def api_espn_mode():
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            mode = data.get("mode")
            if not mode:
                return jsonify({"status": "error", "message": "Feld 'mode' erforderlich ('manual', 'semi', 'full')"}), 400
            try:
                res = espn_client.set_mode(mode)
                return jsonify({"status": "ok", **res})
            except ValueError as ve:
                return jsonify({"status": "error", "message": str(ve)}), 400
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 500
        mode = espn_client.get_mode()
        return jsonify({"status": "ok", "mode": mode})

    @app.route("/api/espn/settings", methods=["GET", "POST"])
    def api_espn_settings():
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            mode = data.get("mode")
            risk_level = data.get("risk_level")
            flash_enabled = data.get("flash_enabled")
            try:
                res = espn_client.update_settings(mode=mode, risk_level=risk_level, flash_enabled=flash_enabled)
                return jsonify(res)
            except ValueError as ve:
                return jsonify({"status": "error", "message": str(ve)}), 400
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 500
        settings = espn_client.get_settings()
        return jsonify(settings)

    @app.route("/api/espn/ai-stats", methods=["GET"])
    def api_espn_ai_stats():
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        stats = espn_client.get_ai_stats()
        return jsonify(stats)

    @app.route("/api/espn/proposals", methods=["GET"])
    def api_espn_proposals():
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        only_pending = request.args.get("pending", "false").lower() in ("true", "1")
        proposals = espn_client.get_proposals(only_pending=only_pending)
        return jsonify({"status": "ok", "proposals": proposals})

    @app.route("/api/espn/proposals/<proposal_id>/apply", methods=["POST"])
    def api_espn_proposal_apply(proposal_id):
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        res = espn_client.apply_proposal(proposal_id)
        status_code = 200 if res.get("success") else 400
        return jsonify(res), status_code

    @app.route("/api/espn/proposals/<proposal_id>/dismiss", methods=["POST"])
    def api_espn_proposal_dismiss(proposal_id):
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        found = espn_client.dismiss_proposal(proposal_id)
        if found:
            return jsonify({"status": "ok", "success": True, "dismissed": proposal_id})
        return jsonify({"status": "error", "success": False, "message": f"Vorschlag '{proposal_id}' nicht gefunden oder bereits bearbeitet."}), 404

    @app.route("/api/espn/analyze", methods=["POST"])
    def api_espn_analyze():
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        res = espn_client.analyze_roster_with_ai(force=True)
        return jsonify(res)

    @app.route("/api/espn/history", methods=["GET"])
    def api_espn_history():
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        try:
            limit = int(request.args.get("limit", 50))
        except (ValueError, TypeError):
            limit = 50
        history = espn_client.get_decision_log(limit=limit)
        return jsonify({"status": "ok", "history": history})

    @app.route("/api/espn/lineup/move", methods=["POST"])
    def api_espn_lineup_move():
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        items = data.get("items", [])
        dry_run = bool(data.get("dry_run", False))
        exec_type = "VALIDATE" if dry_run else "EXECUTE"
        val = espn_client.validate_roster_move(items)
        if not val.get("valid"):
            return jsonify({"success": False, "error": val.get("error")}), 400
        res = espn_client.execute_roster_transaction(items, execution_type=exec_type)
        status_code = 200 if res.get("success") else 400
        return jsonify(res), status_code

    @app.route("/api/homeassistant/config", methods=["GET"])
    def api_ha_config():
        if ha_service:
            return jsonify(ha_service.get_config(safe=True))
        return jsonify({"configured": False, "error": "ha_service nicht verfügbar"}), 503

    @app.route("/api/config/homeassistant", methods=["POST"])
    def api_config_ha():
        if not ha_service:
            return jsonify({"success": False, "error": "ha_service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        res = ha_service.save_config(data)
        return jsonify(res)

    @app.route("/api/homeassistant/test", methods=["POST"])
    def api_ha_test():
        if not ha_service:
            return jsonify({"success": False, "error": "ha_service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        res = ha_service.test_connection(
            url=data.get("url"),
            token=data.get("token"),
            username=data.get("username"),
            password=data.get("password")
        )
        return jsonify(res)

    @app.route("/api/homeassistant/data", methods=["GET"])
    def api_ha_data():
        if not ha_service:
            return jsonify({"success": False, "error": "ha_service nicht verfügbar"}), 503
        return jsonify(ha_service.get_rooms_and_entities())

    @app.route("/api/homeassistant/service", methods=["POST"])
    def api_ha_service():
        if not ha_service:
            return jsonify({"success": False, "error": "ha_service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        domain = data.get("domain", "")
        service = data.get("service", "")
        service_data = data.get("service_data") or data.get("data") or {}
        if not domain or not service:
            return jsonify({"success": False, "error": "Domain und Service erforderlich"}), 400
        res = ha_service.call_service(domain, service, service_data)
        return jsonify(res)

    @app.route("/api/solar/data", methods=["GET"])
    def api_solar_data():
        if not ha_service:
            return jsonify({"success": False, "error": "ha_service nicht verfügbar"}), 503
        return jsonify(ha_service.get_solar_data())

    @app.route("/api/permissions/status", methods=["GET"])
    def api_permissions_status():
        if permissions_service:
            return jsonify(permissions_service.get_public_status())
        return jsonify({"locked_sections": ["cycle"], "has_code": True})

    @app.route("/api/permissions/verify", methods=["POST"])
    def api_permissions_verify():
        if not permissions_service:
            return jsonify({"valid": False, "error": "Service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        code = data.get("code", "")
        valid = permissions_service.verify_code(code)
        return jsonify({"valid": valid, "locked_sections": permissions_service.locked_sections if valid else []})

    @app.route("/api/permissions/config", methods=["POST"])
    def api_permissions_config():
        if not permissions_service:
            return jsonify({"success": False, "error": "Service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        current_code = data.get("code") or data.get("current_code") or ""
        new_code = data.get("new_code")
        locked_sections = data.get("locked_sections")
        res = permissions_service.update_permissions(current_code, new_code=new_code, locked_sections=locked_sections)
        return jsonify(res)

    @app.route("/api/cycle/partners", methods=["GET", "POST"])
    def api_cycle_partners():
        if not cycle_service:
            return jsonify({"success": False, "error": "Service nicht verfügbar"}), 503
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            res = cycle_service.add_partner(
                name=data.get("name"),
                cycle_duration=data.get("cycle_duration", 28),
                start_date=data.get("start_date"),
                period_duration=data.get("period_duration", 5),
                notes=data.get("notes", "")
            )
            return jsonify(res)
        return jsonify({"partners": cycle_service.get_all_partners()})

    @app.route("/api/cycle/partners/<partner_id>", methods=["PUT", "DELETE"])
    def api_cycle_partner_detail(partner_id):
        if not cycle_service:
            return jsonify({"success": False, "error": "Service nicht verfügbar"}), 503
        if request.method == "DELETE":
            return jsonify(cycle_service.delete_partner(partner_id))
        data = request.get_json(silent=True) or {}
        return jsonify(cycle_service.update_partner(partner_id, data))

    @app.route("/api/cycle/partners/<partner_id>/start-cycle", methods=["POST"])
    def api_cycle_start_new(partner_id):
        if not cycle_service:
            return jsonify({"success": False, "error": "Service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        return jsonify(cycle_service.start_new_cycle(partner_id, data.get("start_date")))

    # -----------------------------------------------------------------------
    # PulseCast (Media & Download Hub) Proxy Endpoints
    # -----------------------------------------------------------------------
    PULSECAST_BASE_URL = "http://127.0.0.1:3000"

    def _pulsecast_authorized():
        if not permissions_service:
            return True
        with permissions_service.lock:
            if "pulsecast" not in permissions_service.locked_sections:
                return True
        code = (
            request.headers.get("X-Command-Code")
            or request.headers.get("X-Auth-Code")
            or request.args.get("code")
        )
        if not code and request.is_json:
            b = request.get_json(silent=True) or {}
            code = b.get("code")
        if not code:
            auth_hdr = request.headers.get("Authorization", "")
            if auth_hdr.startswith("Bearer "):
                code = auth_hdr.split(" ", 1)[1].strip()
        return permissions_service.verify_code(code)

    def _pulsecast_proxy(method, endpoint, params=None, json_data=None, timeout=12):
        if not _pulsecast_authorized():
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Command Code Autorisierung erforderlich", "locked": True}), 403
        url = f"{PULSECAST_BASE_URL}{endpoint}"
        if not requests:
            return jsonify({"success": False, "error": "requests Bibliothek nicht verfügbar"}), 500
        try:
            if method == "GET":
                r = requests.get(url, params=params, timeout=timeout)
            elif method == "POST":
                r = requests.post(url, json=json_data, timeout=timeout)
            elif method == "DELETE":
                r = requests.delete(url, params=params, timeout=timeout)
            else:
                return jsonify({"success": False, "error": f"Nicht unterstützte HTTP-Methode: {method}"}), 405

            try:
                return jsonify(r.json()), r.status_code
            except Exception:
                return r.content, r.status_code, {"Content-Type": r.headers.get("Content-Type", "application/json")}
        except (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout):
            return jsonify({"success": False, "error": "Subraum-Relay zu PulseCast offline (Port 3000)", "offline": True}), 503
        except requests.exceptions.Timeout:
            return jsonify({"success": False, "error": "PulseCast Subraum-Relay Zeitüberschreitung (Timeout)", "offline": False}), 504
        except Exception as e:
            return jsonify({"success": False, "error": f"PulseCast Proxy Fehler: {e}"}), 500

    @app.route("/api/pulsecast/status", methods=["GET"])
    def api_pulsecast_status():
        is_locked = False
        if permissions_service:
            with permissions_service.lock:
                is_locked = "pulsecast" in permissions_service.locked_sections
        online = False
        if requests:
            try:
                r = requests.get(f"{PULSECAST_BASE_URL}/api/downloads", timeout=2)
                online = (r.status_code == 200)
            except Exception:
                online = False
        return jsonify({
            "online": online,
            "locked": is_locked,
            "port": 3000,
            "host": PULSECAST_BASE_URL,
            "message": "Online" if online else "Subraum-Relay zu PulseCast offline"
        })

    @app.route("/api/pulsecast/downloads", methods=["GET"])
    def api_pulsecast_downloads():
        return _pulsecast_proxy("GET", "/api/downloads")

    @app.route("/api/pulsecast/download/<download_id>/pause", methods=["POST"])
    def api_pulsecast_download_pause(download_id):
        safe_id = urllib.parse.quote(download_id, safe="")
        return _pulsecast_proxy("POST", f"/api/download/{safe_id}/pause")

    @app.route("/api/pulsecast/download/<download_id>/resume", methods=["POST"])
    def api_pulsecast_download_resume(download_id):
        safe_id = urllib.parse.quote(download_id, safe="")
        return _pulsecast_proxy("POST", f"/api/download/{safe_id}/resume")

    @app.route("/api/pulsecast/download/<download_id>/cancel", methods=["POST"])
    def api_pulsecast_download_cancel(download_id):
        safe_id = urllib.parse.quote(download_id, safe="")
        return _pulsecast_proxy("POST", f"/api/download/{safe_id}/cancel")

    @app.route("/api/pulsecast/download/<download_id>", methods=["DELETE"])
    def api_pulsecast_download_delete(download_id):
        safe_id = urllib.parse.quote(download_id, safe="")
        delete_file = request.args.get("deleteFile", "false")
        return _pulsecast_proxy("DELETE", f"/api/download/{safe_id}", params={"deleteFile": delete_file})

    @app.route("/api/pulsecast/media-library", methods=["GET"])
    def api_pulsecast_media_library():
        params = {
            "category": request.args.get("category", "Filme"),
            "subcategory": request.args.get("subcategory", "all"),
            "search": request.args.get("search", ""),
            "page": request.args.get("page", 1),
            "limit": request.args.get("limit", 40)
        }
        return _pulsecast_proxy("GET", "/api/media-library", params=params, timeout=20)

    @app.route("/api/pulsecast/series-episodes", methods=["GET"])
    def api_pulsecast_series_episodes():
        series_id = request.args.get("seriesId")
        if not series_id:
            return jsonify({"success": False, "error": "Parameter seriesId erforderlich"}), 400
        return _pulsecast_proxy("GET", "/api/xtream/series-episodes", params={"seriesId": series_id}, timeout=20)

    @app.route("/api/pulsecast/download/media", methods=["POST"])
    def api_pulsecast_download_media():
        data = request.get_json(silent=True) or {}
        if "items" in data and isinstance(data["items"], list):
            return _pulsecast_proxy("POST", "/api/xtream/download-batch", json_data={"items": data["items"]})
        else:
            url = data.get("url")
            title = data.get("title")
            series_title = data.get("seriesTitle")
            if not url or not title:
                return jsonify({"success": False, "error": "Parameter url und title erforderlich"}), 400
            payload = {"url": url, "title": title}
            if series_title:
                payload["seriesTitle"] = series_title
            return _pulsecast_proxy("POST", "/api/xtream/download", json_data=payload)

    @app.route("/api/pulsecast/search", methods=["GET"])
    def api_pulsecast_search():
        query = request.args.get("q", "")
        if not query:
            return jsonify({"success": False, "error": "Suchbegriff (Parameter q) erforderlich"}), 400
        source = request.args.get("source", "xdcc")
        return _pulsecast_proxy("GET", "/api/search", params={"q": query, "source": source}, timeout=35)

    @app.route("/api/pulsecast/download/xdcc", methods=["POST"])
    def api_pulsecast_download_xdcc():
        data = request.get_json(silent=True) or {}
        for req_field in ["server", "channel", "botName", "packNumber", "filename"]:
            if not data.get(req_field):
                return jsonify({"success": False, "error": f"Fehlender Parameter: {req_field}"}), 400
        return _pulsecast_proxy("POST", "/api/download", json_data=data)

    def _pulsecast_stream_proxy(target_url):
        req_headers = {}
        if "Range" in request.headers:
            req_headers["Range"] = request.headers["Range"]
        if "If-Range" in request.headers:
            req_headers["If-Range"] = request.headers["If-Range"]

        params = dict(request.args)
        params.pop("code", None)

        try:
            r = requests.request(
                method=request.method,
                url=target_url,
                headers=req_headers,
                params=params,
                stream=True,
                timeout=30
            )

            forward_headers = {}
            for h in [
                "Content-Type",
                "Content-Length",
                "Content-Range",
                "Accept-Ranges",
                "Content-Disposition",
                "Cache-Control",
                "ETag",
                "Last-Modified",
            ]:
                if h in r.headers:
                    forward_headers[h] = r.headers[h]

            if "Accept-Ranges" not in forward_headers:
                forward_headers["Accept-Ranges"] = "bytes"

            if request.method == "HEAD":
                r.close()
                return Response(status=r.status_code, headers=forward_headers)

            def generate():
                try:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            yield chunk
                finally:
                    r.close()

            return Response(stream_with_context(generate()), status=r.status_code, headers=forward_headers)

        except (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout):
            return jsonify({"success": False, "error": "Subraum-Relay zu PulseCast offline (Port 3000)", "offline": True}), 503
        except requests.exceptions.Timeout:
            return jsonify({"success": False, "error": "PulseCast Subraum-Relay Zeitüberschreitung (Timeout)", "offline": False}), 504
        except Exception as e:
            return jsonify({"success": False, "error": f"PulseCast Stream Proxy Fehler: {e}"}), 500

    @app.route("/api/pulsecast/media/stream.m3u", methods=["GET"])
    def api_pulsecast_media_m3u():
        if not _pulsecast_authorized():
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Command Code Autorisierung erforderlich", "locked": True}), 403

        filename = request.args.get("filename", "").strip()
        if not filename:
            return jsonify({"success": False, "error": "Parameter filename erforderlich"}), 400

        clean_filename = filename.lstrip("/")
        title_param = request.args.get("title") or request.args.get("display_title")
        if title_param:
            display_title = title_param.strip()
        else:
            base = os.path.basename(clean_filename)
            display_title = os.path.splitext(base)[0]

        code = (
            request.args.get("code")
            or request.headers.get("X-Command-Code")
            or request.headers.get("X-Auth-Code")
            or ""
        ).strip()

        scheme = request.headers.get("X-Forwarded-Proto") or request.scheme
        host_url = f"{scheme}://{request.host}".rstrip("/")
        if not request.headers.get("X-Forwarded-Proto") and request.host_url:
            host_url = request.host_url.rstrip("/")

        safe_encoded_fn = urllib.parse.quote(clean_filename, safe="/")
        code_query = f"?code={urllib.parse.quote(code)}" if code else ""
        full_stream_url = f"{host_url}/api/pulsecast/media/stream/{safe_encoded_fn}{code_query}"

        clean_title = re.sub(r'[^\w\-\.]+', '_', display_title).strip('_') or "stream"
        m3u_content = f"#EXTM3U\n#EXTINF:-1 tvg-name=\"{display_title}\",{display_title}\n{full_stream_url}\n"

        response = Response(m3u_content, status=200, mimetype="application/x-mpegurl")
        response.headers["Content-Type"] = "application/x-mpegurl; charset=utf-8"
        response.headers["Content-Disposition"] = f'attachment; filename="{clean_title}.m3u"'
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/api/pulsecast/media/stream/<path:filename>", methods=["GET", "HEAD"])
    def api_pulsecast_media_stream(filename):
        if not _pulsecast_authorized():
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Command Code Autorisierung erforderlich", "locked": True}), 403

        safe_filename = urllib.parse.quote(filename, safe="/")
        target_url = f"{PULSECAST_BASE_URL}/api/media/stream/{safe_filename}"
        return _pulsecast_stream_proxy(target_url)

    @app.route("/api/pulsecast/media/transcode/<path:filename>", methods=["GET", "HEAD"])
    def api_pulsecast_media_transcode(filename):
        if not _pulsecast_authorized():
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Command Code Autorisierung erforderlich", "locked": True}), 403

        safe_filename = urllib.parse.quote(filename, safe="/")
        target_url = f"{PULSECAST_BASE_URL}/api/media/transcode/{safe_filename}"
        return _pulsecast_stream_proxy(target_url)

    @app.route("/api/pulsecast/media/probe/<path:filename>", methods=["GET"])
    def api_pulsecast_media_probe(filename):
        safe_filename = urllib.parse.quote(filename, safe="/")
        return _pulsecast_proxy("GET", f"/api/media/probe/{safe_filename}", timeout=15)

    # ---------------------------------------------------------------------------
    # LCARS Authentication & User Management Routes
    # ---------------------------------------------------------------------------
    if auth_proxy and not getattr(auth_proxy, "_running", False):
        try:
            auth_proxy.start_background()
        except Exception as _ap_err:
            print(f"[WARN] auth_proxy start_background Fehler: {_ap_err}", file=sys.stderr)

    def _get_lcars_cookie_domain():
        host = request.host.split(":")[0].lower()
        if host.endswith("pimmel.site"):
            return ".pimmel.site"
        return None

    def _is_lcars_user_management_authorized():
        # 1. Header or Query Code check (e.g. 0901)
        code = (
            request.headers.get("X-Command-Code")
            or request.headers.get("X-Auth-Code")
            or request.args.get("code")
        )
        if not code and request.is_json:
            b = request.get_json(silent=True) or {}
            code = b.get("code")
        if not code:
            auth_hdr = request.headers.get("Authorization", "")
            if auth_hdr.startswith("Bearer "):
                code = auth_hdr.split(" ", 1)[1].strip()
        if code:
            if permissions_service and permissions_service.verify_code(code):
                return True
            if code == "0901":
                return True

        # 2. Session check (User with all / * permissions)
        session_id = request.cookies.get("lcars_session")
        if session_id and user_service:
            s = user_service.validate_session(session_id)
            if s:
                svcs = s.get("allowed_services", [])
                if "*" in svcs or "all" in svcs:
                    return True
        return False

    @app.route("/login", methods=["GET"])
    def lcars_login():
        session_id = request.cookies.get("lcars_session")
        return_to = request.args.get("return_to", "/")
        if session_id and user_service:
            s = user_service.validate_session(session_id)
            if s:
                return redirect(return_to)
        if LOGIN_HTML:
            return Response(LOGIN_HTML, mimetype="text/html")
        return "LCARS Login nicht verfügbar", 500

    @app.route("/api/auth/login", methods=["POST"])
    def api_auth_login():
        if not user_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503

        data = request.get_json(silent=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        remember = bool(data.get("remember", False))
        return_to = data.get("return_to") or "/"

        ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.remote_addr or ""
        ua = request.headers.get("User-Agent", "")

        auth_res = user_service.authenticate(username, password, ip=ip, user_agent=ua)
        if not auth_res.get("success"):
            return jsonify({"success": False, "error": auth_res.get("error", "Zugriff verweigert")}), 401

        user_info = auth_res["user"]
        duration = getattr(user_service, "REMEMBER_SESSION_DURATION", 2592000) if remember else getattr(user_service, "DEFAULT_SESSION_DURATION", 86400)
        session_id = user_service.create_session(user_info["id"], duration_seconds=duration, ip=ip, user_agent=ua)

        resp = jsonify({
            "success": True,
            "redirect_url": return_to,
            "user": user_info
        })

        cookie_domain = _get_lcars_cookie_domain()
        is_secure = cookie_domain is not None
        resp.set_cookie(
            "lcars_session",
            session_id,
            max_age=duration,
            domain=cookie_domain,
            secure=is_secure,
            httponly=True,
            samesite="Lax",
            path="/"
        )
        return resp

    @app.route("/api/auth/logout", methods=["POST"])
    def api_auth_logout():
        session_id = request.cookies.get("lcars_session")
        if session_id and user_service:
            user_service.delete_session(session_id)

        resp = jsonify({"success": True})
        resp.delete_cookie("lcars_session", path="/")
        cookie_domain = _get_lcars_cookie_domain()
        if cookie_domain:
            resp.delete_cookie("lcars_session", domain=cookie_domain, path="/")
        return resp

    @app.route("/api/auth/me", methods=["GET"])
    def api_auth_me():
        session_id = request.cookies.get("lcars_session")
        if not session_id or not user_service:
            return jsonify({"authenticated": False, "user": None})
        session_data = user_service.validate_session(session_id)
        if not session_data:
            return jsonify({"authenticated": False, "user": None})
        return jsonify({"authenticated": True, "user": session_data})

    @app.route("/api/users", methods=["GET", "POST"])
    def api_users():
        if not user_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503
        if not _is_lcars_user_management_authorized():
            return jsonify({"success": False, "error": "LCARS Autorisierung erforderlich (Command Code 0901)"}), 403

        if request.method == "GET":
            users = user_service.list_users()
            return jsonify({"success": True, "users": users})

        # POST: Neuer Benutzer anlegen
        data = request.get_json(silent=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        display_name = (data.get("display_name") or "").strip()
        allowed_services = data.get("allowed_services", [])
        notes = (data.get("notes") or "").strip()

        res = user_service.create_user(
            username=username,
            password=password,
            display_name=display_name,
            allowed_services=allowed_services,
            notes=notes
        )
        if res.get("success"):
            return jsonify(res), 201
        return jsonify(res), 400

    @app.route("/api/users/<int:user_id>", methods=["PUT", "DELETE"])
    def api_user_detail(user_id):
        if not user_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503
        if not _is_lcars_user_management_authorized():
            return jsonify({"success": False, "error": "LCARS Autorisierung erforderlich (Command Code 0901)"}), 403

        if request.method == "DELETE":
            res = user_service.delete_user(user_id)
            if res.get("success"):
                return jsonify(res)
            return jsonify(res), 400

        # PUT: Benutzer aktualisieren
        data = request.get_json(silent=True) or {}
        display_name = data.get("display_name")
        is_active = data.get("is_active")
        allowed_services = data.get("allowed_services")
        notes = data.get("notes")

        res = user_service.update_user(
            user_id=user_id,
            display_name=display_name,
            is_active=is_active,
            allowed_services=allowed_services,
            notes=notes
        )
        if res.get("success"):
            return jsonify(res)
        return jsonify(res), 400

    @app.route("/api/users/<int:user_id>/password", methods=["POST"])
    def api_user_password(user_id):
        if not user_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503
        if not _is_lcars_user_management_authorized():
            return jsonify({"success": False, "error": "LCARS Autorisierung erforderlich (Command Code 0901)"}), 403

        data = request.get_json(silent=True) or {}
        new_password = data.get("new_password") or ""
        if not new_password or len(new_password) < 6:
            return jsonify({"success": False, "error": "Passwort muss mindestens 6 Zeichen lang sein"}), 400

        res = user_service.update_password(user_id, new_password)
        if res.get("success"):
            return jsonify(res)
        return jsonify(res), 400

    @app.route("/api/users/audit-log", methods=["GET"])
    def api_users_audit_log():
        if not user_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503
        if not _is_lcars_user_management_authorized():
            return jsonify({"success": False, "error": "LCARS Autorisierung erforderlich (Command Code 0901)"}), 403

        limit = request.args.get("limit", 50)
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = 50

        logs = user_service.get_audit_log(limit=limit)
        return jsonify({"success": True, "audit_log": logs})

    # -----------------------------------------------------------------------
    # Google Gemini 3.8 Live (Subraum Comm SST Relay) Endpoints
    # -----------------------------------------------------------------------
    def _get_gemini_live_config():
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        cfg_local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.local.json")
        res = {}
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        res.update(data.get("gemini_live", {}))
            if os.path.exists(cfg_local_path):
                with open(cfg_local_path, "r", encoding="utf-8") as f:
                    local_data = json.load(f)
                    if isinstance(local_data, dict):
                        res.update(local_data.get("gemini_live", {}))
        except Exception as e:
            print(f"[WARN] Fehler beim Lesen der gemini_live Konfiguration: {e}", file=sys.stderr)

        env_key = os.environ.get("GEMINI_LIVE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if env_key:
            res["api_key"] = env_key.strip()
        return res

    def _gemini_live_authorized(req_code=None):
        if not permissions_service:
            return True
        with permissions_service.lock:
            if "gemini_live" not in permissions_service.locked_sections:
                return True
        code = req_code
        if not code:
            code = (
                request.args.get("code")
                or request.headers.get("X-Command-Code")
                or request.headers.get("X-Auth-Code")
            )
        if not code and request.is_json:
            b = request.get_json(silent=True) or {}
            code = b.get("code")
        if not code:
            auth_hdr = request.headers.get("Authorization", "")
            if auth_hdr.startswith("Bearer "):
                code = auth_hdr.split(" ", 1)[1].strip()
        return permissions_service.verify_code(code)

    @app.route("/api/gemini-live/status", methods=["GET"])
    def api_gemini_live_status():
        is_locked = False
        if permissions_service:
            with permissions_service.lock:
                is_locked = "gemini_live" in permissions_service.locked_sections
        gl_cfg = _get_gemini_live_config()
        has_key = bool(gl_cfg.get("api_key"))
        model = gl_cfg.get("model", "gemini-3.8-live")
        return jsonify({
            "configured": has_key,
            "model": model,
            "locked": is_locked
        })

    if USE_SOCK and sock:
        @sock.route("/api/gemini-live/ws")
        def api_gemini_live_ws(ws):
            code = request.args.get("code", "")
            if not _gemini_live_authorized(code):
                try:
                    ws.send(json.dumps({
                        "type": "error",
                        "error": "LCARS Zugriff verweigert: Ungültiger Command Code (Autorisierung erforderlich)",
                        "locked": True
                    }))
                except Exception:
                    pass
                return

            gl_cfg = _get_gemini_live_config()
            api_key = gl_cfg.get("api_key", "").strip()
            model = gl_cfg.get("model", "gemini-3.8-live").strip()
            voice = gl_cfg.get("voice", "Puck").strip()
            system_instruction = gl_cfg.get(
                "system_instruction",
                "Du bist der LCARS Bordcomputer der USS Antigravity. Du interagierst direkt mit dem Commander über Subraum-Audio. Antworte stets präzise, professionell, hilfsbereit und im authentischen Ton eines Starfleet Computer-Terminals auf Deutsch."
            )
            try:
                temperature = float(gl_cfg.get("temperature", 0.6))
            except (ValueError, TypeError):
                temperature = 0.6

            if not api_key:
                try:
                    ws.send(json.dumps({
                        "type": "error",
                        "error": "Gemini Live API-Key nicht in config.json hinterlegt."
                    }))
                except Exception:
                    pass
                return

            if not ws_connect:
                try:
                    ws.send(json.dumps({
                        "type": "error",
                        "error": "websockets Client-Bibliothek nicht verfügbar auf dem Server."
                    }))
                except Exception:
                    pass
                return

            gemini_url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={api_key}"

            try:
                with ws_connect(gemini_url, open_timeout=15, close_timeout=5) as gemini_ws:
                    model_resource = model if model.startswith("models/") else f"models/{model}"
                    setup_payload = {
                        "setup": {
                            "model": model_resource,
                            "generationConfig": {
                                "responseModalities": ["AUDIO"],
                                "speechConfig": {
                                    "voiceConfig": {
                                        "prebuiltVoiceConfig": {
                                            "voiceName": voice
                                        }
                                    }
                                },
                                "temperature": temperature
                            },
                            "systemInstruction": {
                                "parts": [
                                    {"text": system_instruction}
                                ]
                            },
                            "inputAudioTranscription": {}
                        }
                    }

                    try:
                        gemini_ws.send(json.dumps(setup_payload))
                    except Exception as e:
                        try:
                            ws.send(json.dumps({
                                "type": "error",
                                "error": f"Fehler beim Senden des Gemini Setup-Frames: {e}"
                            }))
                        except Exception:
                            pass
                        return

                    stop_event = threading.Event()

                    def gemini_to_client():
                        try:
                            while not stop_event.is_set():
                                try:
                                    raw_msg = gemini_ws.recv(timeout=1.0)
                                except TimeoutError:
                                    continue
                                except Exception:
                                    break

                                if raw_msg is None:
                                    break

                                try:
                                    data = json.loads(raw_msg)
                                except Exception:
                                    continue

                                if "setupComplete" in data:
                                    print(f"[GEMINI LIVE] Session setup complete (model: {model}, voice: {voice})", flush=True)
                                    try:
                                        ws.send(json.dumps({
                                            "type": "setup_complete",
                                            "model": model,
                                            "voice": voice
                                        }))
                                    except Exception:
                                        break
                                    continue

                                in_tx_top = data.get("interimInputTranscription") or data.get("inputTranscription")
                                if in_tx_top:
                                    in_transcription = ""
                                    if isinstance(in_tx_top, dict):
                                        if "text" in in_tx_top and isinstance(in_tx_top["text"], str):
                                            in_transcription = in_tx_top["text"].strip()
                                        elif "parts" in in_tx_top and isinstance(in_tx_top["parts"], list):
                                            in_transcription = "".join(p.get("text", "") for p in in_tx_top["parts"] if isinstance(p, dict)).strip()
                                    elif isinstance(in_tx_top, str):
                                        in_transcription = in_tx_top.strip()

                                    if in_transcription:
                                        print(f"[GEMINI LIVE] User Heard: {in_transcription}", flush=True)
                                        try:
                                            ws.send(json.dumps({
                                                "type": "user_transcript",
                                                "text": in_transcription
                                            }))
                                        except Exception:
                                            stop_event.set()
                                            return

                                if "serverContent" in data:
                                    sc = data["serverContent"]
                                    interrupted = bool(sc.get("interrupted"))
                                    turn_complete = bool(sc.get("turnComplete"))

                                    if interrupted:
                                        print("[GEMINI LIVE] Model output interrupted by user", flush=True)
                                        try:
                                            ws.send(json.dumps({"type": "interrupted"}))
                                        except Exception:
                                            break

                                    # 1. Output transcription (Modell-Sprachausgabe als Text)
                                    out_tx = sc.get("outputTranscription")
                                    if out_tx:
                                        out_transcription = out_tx.get("text") if isinstance(out_tx, dict) else (out_tx if isinstance(out_tx, str) else "")
                                        if out_transcription:
                                            print(f"[GEMINI LIVE] Model Output: {out_transcription}", flush=True)
                                            try:
                                                ws.send(json.dumps({
                                                    "type": "transcript",
                                                    "role": "model",
                                                    "text": out_transcription
                                                }))
                                            except Exception:
                                                stop_event.set()
                                                return

                                    # 2. Input transcription (Transkription der User-Sprache)
                                    in_tx = sc.get("interimInputTranscription") or sc.get("inputTranscription")
                                    if in_tx:
                                        in_transcription = ""
                                        if isinstance(in_tx, dict):
                                            if "text" in in_tx and isinstance(in_tx["text"], str):
                                                in_transcription = in_tx["text"].strip()
                                            elif "parts" in in_tx and isinstance(in_tx["parts"], list):
                                                in_transcription = "".join(p.get("text", "") for p in in_tx["parts"] if isinstance(p, dict)).strip()
                                        elif isinstance(in_tx, str):
                                            in_transcription = in_tx.strip()

                                        if in_transcription:
                                            print(f"[GEMINI LIVE] User Heard: {in_transcription}", flush=True)
                                            try:
                                                ws.send(json.dumps({
                                                    "type": "user_transcript",
                                                    "text": in_transcription
                                                }))
                                            except Exception:
                                                stop_event.set()
                                                return

                                    model_turn = sc.get("modelTurn")
                                    if model_turn:
                                        parts = model_turn.get("parts", [])
                                        for part in parts:
                                            inline_data = part.get("inlineData")
                                            if inline_data:
                                                mime = inline_data.get("mimeType", "")
                                                b64_audio = inline_data.get("data", "")
                                                rate = 24000
                                                if "rate=" in mime:
                                                    try:
                                                        rate = int(mime.split("rate=")[1].split(";")[0])
                                                    except Exception:
                                                        rate = 24000
                                                try:
                                                    ws.send(json.dumps({
                                                        "type": "model_audio",
                                                        "audio": b64_audio,
                                                        "rate": rate
                                                    }))
                                                except Exception:
                                                    stop_event.set()
                                                    return

                                            text_part = part.get("text")
                                            if text_part:
                                                print(f"[GEMINI LIVE] Model Output (part.text): {text_part}", flush=True)
                                                try:
                                                    ws.send(json.dumps({
                                                        "type": "transcript",
                                                        "role": "model",
                                                        "text": text_part
                                                    }))
                                                except Exception:
                                                    stop_event.set()
                                                    return

                                    if turn_complete:
                                        print("[GEMINI LIVE] Model turn complete", flush=True)
                                        try:
                                            ws.send(json.dumps({"type": "turn_complete"}))
                                        except Exception:
                                            break

                                elif "error" in data:
                                    err_msg = data.get("error", "Unbekannter Fehler")
                                    print(f"[GEMINI LIVE] Google Gemini API Fehler: {err_msg}", flush=True)
                                    try:
                                        ws.send(json.dumps({
                                            "type": "error",
                                            "error": f"Google Gemini API Fehler: {err_msg}"
                                        }))
                                    except Exception:
                                        pass
                                    break
                        except Exception as e:
                            print(f"[GEMINI LIVE] Ausnahme in gemini_to_client: {e}", flush=True)
                        finally:
                            stop_event.set()

                    t_gemini = threading.Thread(target=gemini_to_client, daemon=True)
                    t_gemini.start()

                    turn_audio_chunks = 0
                    turn_audio_bytes = 0

                    try:
                        while not stop_event.is_set():
                            try:
                                client_raw = ws.receive(timeout=1.0)
                            except Exception:
                                break

                            if client_raw is None:
                                if hasattr(ws, "connected") and not ws.connected:
                                    break
                                continue

                            try:
                                client_data = json.loads(client_raw)
                            except Exception:
                                continue

                            msg_type = client_data.get("type")

                            if msg_type == "audio":
                                audio_b64 = client_data.get("data", "")
                                if audio_b64:
                                    turn_audio_chunks += 1
                                    raw_bytes = len(audio_b64) * 3 // 4
                                    turn_audio_bytes += raw_bytes
                                    if turn_audio_chunks == 1 or turn_audio_chunks % 50 == 0:
                                        print(f"[GEMINI LIVE] Audio stream: chunk #{turn_audio_chunks} ({raw_bytes} B, total turn bytes: {turn_audio_bytes} B)", flush=True)
                                    realtime_payload = {
                                        "realtimeInput": {
                                            "mediaChunks": [
                                                {
                                                    "mimeType": "audio/pcm;rate=16000",
                                                    "data": audio_b64
                                                }
                                            ]
                                        }
                                    }
                                    try:
                                        gemini_ws.send(json.dumps(realtime_payload))
                                    except Exception:
                                        break

                            elif msg_type in ("end_of_turn", "turn_complete"):
                                print(f"[GEMINI LIVE] User turn complete (audio chunks sent in this turn: {turn_audio_chunks})", flush=True)
                                turn_audio_chunks = 0
                                turn_audio_bytes = 0
                                turn_payload = {
                                    "clientContent": {
                                        "turnComplete": True
                                    }
                                }
                                try:
                                    gemini_ws.send(json.dumps(turn_payload))
                                except Exception as e:
                                    print(f"[GEMINI LIVE] Fehler beim Weiterleiten von turnComplete: {e}", flush=True)
                                    break

                            elif msg_type == "text":
                                text_input = client_data.get("text", "").strip()
                                if text_input:
                                    print(f"[GEMINI LIVE] User text input: {text_input[:60]}", flush=True)
                                    turn_audio_chunks = 0
                                    turn_audio_bytes = 0
                                    client_content_payload = {
                                        "clientContent": {
                                            "turns": [
                                                {
                                                    "role": "user",
                                                    "parts": [{"text": text_input}]
                                                }
                                            ],
                                            "turnComplete": True
                                        }
                                    }
                                    try:
                                        gemini_ws.send(json.dumps(client_content_payload))
                                        ws.send(json.dumps({
                                            "type": "transcript",
                                            "role": "user",
                                            "text": text_input
                                        }))
                                    except Exception:
                                        break

                            elif msg_type == "ping":
                                try:
                                    ws.send(json.dumps({"type": "pong"}))
                                except Exception:
                                    break
                    except Exception as e:
                        print(f"[GEMINI LIVE] Ausnahme in client_to_gemini: {e}", flush=True)
                        pass
                    finally:
                        stop_event.set()
            except Exception as e:
                try:
                    ws.send(json.dumps({
                        "type": "error",
                        "error": f"Verbindung zur Google Gemini Live API fehlgeschlagen: {e}"
                    }))
                except Exception:
                    pass
                return

    # -----------------------------------------------------------------------
    # Pimmel Remote Node (Raspberry Pi 4 / 9Router Hub) API Endpoints
    # -----------------------------------------------------------------------
    @app.route("/api/pimmel/status", methods=["GET"])
    def api_pimmel_status():
        if not pimmel_service:
            return jsonify({"node": {"hostname": "PiMMEL", "online": False, "last_error": "pimmel_service nicht geladen"}}), 503
        return jsonify(pimmel_service.get_status())

    @app.route("/api/pimmel/history", methods=["GET"])
    def api_pimmel_history():
        if not pimmel_service:
            return jsonify({"range": "1h", "seconds": 3600, "count": 0, "samples": []}), 503
        range_param = request.args.get("range", "1h")
        return jsonify(pimmel_service.get_history(range_param=range_param))

    @app.route("/api/pimmel/9router", methods=["GET"])
    def api_pimmel_9router():
        if not pimmel_service:
            return jsonify({"status": "offline", "error": "pimmel_service nicht geladen"}), 503
        return jsonify(pimmel_service.get_9router_data())

    @app.route("/api/pimmel/logs", methods=["GET"])
    def api_pimmel_logs():
        if not pimmel_service:
            return jsonify({"lines": 50, "count": 0, "logs": "", "error": "pimmel_service nicht geladen"}), 503
        lines = request.args.get("lines", 50)
        return jsonify(pimmel_service.get_logs(lines=lines))

    @app.route("/api/pimmel/sync", methods=["POST"])
    def api_pimmel_sync():
        if not pimmel_service:
            return jsonify({"success": False, "error": "pimmel_service nicht geladen"}), 503
        return jsonify(pimmel_service.trigger_sync())

    def run_server():
        print("[START] Starte System Dashboard Server auf http://0.0.0.0:5000 ...", flush=True)
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

else:
    class DashboardHTTPHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/stats":
                stats = get_system_stats()
                data = json.dumps(stats).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/history":
                query = urllib.parse.parse_qs(parsed.query)
                range_param = query.get("range", ["1h"])[0]
                _, seconds = parse_range_param(range_param)
                samples = history_store.get_samples(range_seconds=seconds)
                data = json.dumps({
                    "range": range_param,
                    "seconds": seconds,
                    "count": len(samples),
                    "samples": samples,
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path in ("/api/discovered-servers", "/api/scan-webservers"):
                discovered = webserver_scanner.scan() if parsed.path == "/api/scan-webservers" else webserver_scanner.discovered_servers
                data = json.dumps({"discovered": discovered}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/9router/stats":
                data = json.dumps(get_9router_stats()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/pimmel/status":
                data = json.dumps(pimmel_service.get_status() if pimmel_service else {"node": {"hostname": "PiMMEL", "online": False}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/pimmel/history":
                query = urllib.parse.parse_qs(parsed.query)
                range_param = query.get("range", ["1h"])[0]
                data = json.dumps(pimmel_service.get_history(range_param) if pimmel_service else {"count": 0, "samples": []}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/pimmel/9router":
                data = json.dumps(pimmel_service.get_9router_data() if pimmel_service else {"status": "offline"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/pimmel/logs":
                query = urllib.parse.parse_qs(parsed.query)
                lines = query.get("lines", [50])[0]
                data = json.dumps(pimmel_service.get_logs(lines) if pimmel_service else {"count": 0, "logs": ""}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/alerts":
                data = json.dumps(alert_monitor.get_status()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/config/thresholds":
                data = json.dumps(alert_monitor.get_status()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/chat/models":
                data = json.dumps(fetch_chat_models()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/hermes/profiles":
                data = json.dumps({"profiles": get_hermes_profiles()}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/config/ide-url":
                data = json.dumps({"url": alert_monitor.config.get("antigravity_ide_url", "")}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path in ("/api/fantasy", "/api/fantasy/refresh"):
                force_val = (parsed.path == "/api/fantasy/refresh")
                f_data = espn_client.fetch(force=force_val) if espn_client else {"status": "error", "message": "ESPN Service nicht verfügbar"}
                data = json.dumps(f_data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/fantasy/test-flash":
                query = urllib.parse.parse_qs(parsed.query)
                entity_id = query.get("entity_id", ["light.esstisch"])[0]
                try:
                    duration = float(query.get("duration", [1.2])[0])
                except (ValueError, TypeError):
                    duration = 1.2
                if espn_client:
                    res = espn_client.trigger_flash(entity_id=entity_id, duration=duration)
                    data = json.dumps({"success": bool(res), "message": f"Flash-Signal für {entity_id} ausgelöst", "entity_id": entity_id, "duration": duration}).encode("utf-8")
                elif ha_service:
                    ha_service.flash_light(entity_id=entity_id, duration=duration, async_run=True)
                    data = json.dumps({"success": True, "message": f"Flash-Signal für {entity_id} ausgelöst", "entity_id": entity_id, "duration": duration}).encode("utf-8")
                else:
                    data = json.dumps({"success": False, "error": "Dienst nicht verfügbar"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/espn/mode":
                mode_val = espn_client.get_mode() if espn_client else "manual"
                data = json.dumps({"status": "ok", "mode": mode_val}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/espn/settings":
                settings = espn_client.get_settings() if espn_client else {"status": "ok", "mode": "manual", "risk_level": 3, "flash_enabled": True}
                data = json.dumps(settings).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/espn/ai-stats":
                stats = espn_client.get_ai_stats() if espn_client else {
                    "status": "ok",
                    "model": "ag/gemini-3.8-flash-high via 9Router",
                    "estimated_cost_usd": 0.0002,
                    "last_token_usage": {"prompt_tokens": 950, "completion_tokens": 250, "total_tokens": 1200}
                }
                data = json.dumps(stats).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/espn/proposals":
                query = urllib.parse.parse_qs(parsed.query)
                only_pending = query.get("pending", ["false"])[0].lower() in ("true", "1")
                props = espn_client.get_proposals(only_pending=only_pending) if espn_client else []
                data = json.dumps({"status": "ok", "proposals": props}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/homeassistant/config":
                cfg = ha_service.get_config(safe=True) if ha_service else {"configured": False}
                data = json.dumps(cfg).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/homeassistant/data":
                ha_data = ha_service.get_rooms_and_entities() if ha_service else {"success": False, "error": "ha_service nicht verfügbar"}
                data = json.dumps(ha_data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/solar/data":
                solar_data = ha_service.get_solar_data() if ha_service else {"success": False, "error": "ha_service nicht verfügbar"}
                data = json.dumps(solar_data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/permissions/status":
                status = permissions_service.get_public_status() if permissions_service else {"locked_sections": ["cycle"], "has_code": True}
                data = json.dumps(status).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/cycle/partners":
                partners = cycle_service.get_all_partners() if cycle_service else []
                data = json.dumps({"partners": partners}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/gemini-live/status":
                cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
                cfg_local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.local.json")
                gl_data = {}
                try:
                    if os.path.exists(cfg_path):
                        with open(cfg_path, "r", encoding="utf-8") as f:
                            c = json.load(f)
                            if isinstance(c, dict):
                                gl_data.update(c.get("gemini_live", {}))
                    if os.path.exists(cfg_local_path):
                        with open(cfg_local_path, "r", encoding="utf-8") as f:
                            c = json.load(f)
                            if isinstance(c, dict):
                                gl_data.update(c.get("gemini_live", {}))
                except Exception:
                    pass
                env_key = os.environ.get("GEMINI_LIVE_API_KEY") or os.environ.get("GEMINI_API_KEY")
                if env_key:
                    gl_data["api_key"] = env_key.strip()
                is_locked = "gemini_live" in permissions_service.locked_sections if permissions_service else False
                has_key = bool(gl_data.get("api_key"))
                data = json.dumps({"configured": has_key, "model": gl_data.get("model", "gemini-3.8-live"), "locked": is_locked}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path.startswith("/api/pulsecast/"):
                if parsed.path == "/api/pulsecast/status":
                    online = False
                    if requests:
                        try:
                            r = requests.get("http://127.0.0.1:3000/api/downloads", timeout=2)
                            online = (r.status_code == 200)
                        except Exception:
                            online = False
                    is_locked = "pulsecast" in permissions_service.locked_sections if permissions_service else False
                    data = json.dumps({"online": online, "locked": is_locked, "port": 3000}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif parsed.path == "/api/pulsecast/media/stream.m3u":
                    qs = urllib.parse.parse_qs(parsed.query)
                    filename = qs.get("filename", [""])[0].strip()
                    if not filename:
                        err = json.dumps({"success": False, "error": "Parameter filename erforderlich"}).encode("utf-8")
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(err)))
                        self.end_headers()
                        self.wfile.write(err)
                    else:
                        clean_fn = filename.lstrip("/")
                        title_param = qs.get("title", [""])[0] or qs.get("display_title", [""])[0]
                        display_title = title_param if title_param else os.path.splitext(os.path.basename(clean_fn))[0]
                        code = qs.get("code", [""])[0]
                        host = self.headers.get("Host", "localhost:5000")
                        proto = self.headers.get("X-Forwarded-Proto", "http")
                        safe_encoded_fn = urllib.parse.quote(clean_fn, safe="/")
                        code_query = f"?code={urllib.parse.quote(code)}" if code else ""
                        full_stream_url = f"{proto}://{host}/api/pulsecast/media/stream/{safe_encoded_fn}{code_query}"
                        clean_title = re.sub(r'[^\w\-\.]+', '_', display_title).strip('_') or "stream"
                        m3u = f"#EXTM3U\n#EXTINF:-1 tvg-name=\"{display_title}\",{display_title}\n{full_stream_url}\n".encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/x-mpegurl; charset=utf-8")
                        self.send_header("Content-Disposition", f'attachment; filename="{clean_title}.m3u"')
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Content-Length", str(len(m3u)))
                        self.end_headers()
                        self.wfile.write(m3u)
                elif parsed.path.startswith("/api/pulsecast/media/stream/") or parsed.path.startswith("/api/pulsecast/media/transcode/"):
                    is_transcode = parsed.path.startswith("/api/pulsecast/media/transcode/")
                    prefix = "/api/pulsecast/media/transcode/" if is_transcode else "/api/pulsecast/media/stream/"
                    subpath = parsed.path.replace(prefix, "", 1)
                    safe_fn = urllib.parse.quote(urllib.parse.unquote(subpath), safe="/")
                    endpoint = "transcode" if is_transcode else "stream"
                    target_url = f"http://127.0.0.1:3000/api/media/{endpoint}/{safe_fn}"
                    if parsed.query:
                        target_url += f"?{parsed.query}"
                    req_headers = {}
                    if "Range" in self.headers:
                        req_headers["Range"] = self.headers["Range"]
                    if "If-Range" in self.headers:
                        req_headers["If-Range"] = self.headers["If-Range"]
                    try:
                        r = requests.get(target_url, headers=req_headers, stream=True, timeout=35)
                        self.send_response(r.status_code)
                        for h in ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Content-Disposition", "Cache-Control", "ETag", "Last-Modified"]:
                            if h in r.headers:
                                self.send_header(h, r.headers[h])
                        if "Accept-Ranges" not in r.headers:
                            self.send_header("Accept-Ranges", "bytes")
                        self.end_headers()
                        try:
                            for chunk in r.iter_content(chunk_size=65536):
                                if chunk:
                                    self.wfile.write(chunk)
                        finally:
                            r.close()
                    except Exception as e:
                        err = json.dumps({"success": False, "error": str(e), "offline": True}).encode("utf-8")
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(err)))
                        self.end_headers()
                        self.wfile.write(err)
                else:
                    subpath = parsed.path.replace("/api/pulsecast", "/api", 1)
                    if parsed.path == "/api/pulsecast/series-episodes":
                        subpath = "/api/xtream/series-episodes"
                    target_url = f"http://127.0.0.1:3000{subpath}"
                    if parsed.query:
                        target_url += f"?{parsed.query}"
                    try:
                        r = requests.get(target_url, timeout=35)
                        self.send_response(r.status_code)
                        self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                        self.send_header("Content-Length", str(len(r.content)))
                        self.end_headers()
                        self.wfile.write(r.content)
                    except Exception as e:
                        err = json.dumps({"success": False, "error": str(e), "offline": True}).encode("utf-8")
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(err)))
                        self.end_headers()
                        self.wfile.write(err)
            else:
                stats = get_system_stats()
                html = render_html_fallback(stats).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/services/stop":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                pid = data.get("pid")
                port = data.get("port")
                if pid:
                    try:
                        p = psutil.Process(int(pid))
                        p.terminate()
                    except Exception:
                        pass
                if port:
                    cf_tunnel_manager.stop_tunnel(int(port))
                threading.Thread(target=webserver_scanner.scan, daemon=True).start()
                resp = json.dumps({"success": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/chat":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                model = data.get("model") or "ag/gemini-3.8-flash-high"
                messages = data.get("messages")
                if not messages:
                    prompt = data.get("message") or data.get("prompt") or ""
                    messages = [{"role": "user", "content": prompt}] if prompt else []
                auth_header = self.headers.get("Authorization")
                res_data, status_code = forward_chat_completion(model, messages, auth_header)
                resp = json.dumps(res_data).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/config/thresholds":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                res = alert_monitor.save_config(data)
                resp = json.dumps({"success": True, "status": res}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/gateway/test":
                res = alert_monitor.check_gateway()
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/hermes/profiles":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                name = data.get("name", "")
                desc = data.get("description", "")
                clone_from = data.get("clone_from", "default")
                model = data.get("model", "")
                res = create_hermes_profile(name, desc, clone_from, model)
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200 if res.get("success") else 400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/hermes/chat":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                profile = data.get("profile", "default")
                message = data.get("message", "")
                res = hermes_chat_prompt(profile, message)
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200 if res.get("success") else 500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/config/ide-url":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                url = data.get("url", "").strip()
                if url:
                    alert_monitor.save_config({"antigravity_ide_url": url})
                    resp = json.dumps({"success": True, "url": url}).encode("utf-8")
                    status = 200
                else:
                    resp = json.dumps({"error": "URL erforderlich"}).encode("utf-8")
                    status = 400
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/config/homeassistant":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                res = ha_service.save_config(data) if ha_service else {"success": False}
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/fantasy/test-flash":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                entity_id = data.get("entity_id", "light.esstisch")
                try:
                    duration = float(data.get("duration", 1.2))
                except (ValueError, TypeError):
                    duration = 1.2
                if espn_client:
                    res = espn_client.trigger_flash(entity_id=entity_id, duration=duration)
                    resp = json.dumps({"success": bool(res), "message": f"Flash-Signal für {entity_id} ausgelöst", "entity_id": entity_id, "duration": duration}).encode("utf-8")
                elif ha_service:
                    ha_service.flash_light(entity_id=entity_id, duration=duration, async_run=True)
                    resp = json.dumps({"success": True, "message": f"Flash-Signal für {entity_id} ausgelöst", "entity_id": entity_id, "duration": duration}).encode("utf-8")
                else:
                    resp = json.dumps({"success": False, "error": "Dienst nicht verfügbar"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/espn/mode":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                mode = data.get("mode")
                if espn_client and mode:
                    try:
                        res = espn_client.set_mode(mode)
                        resp = json.dumps({"status": "ok", **res}).encode("utf-8")
                        code = 200
                    except Exception as e:
                        resp = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
                        code = 400
                elif not espn_client:
                    resp = json.dumps({"status": "error", "message": "ESPN Service nicht verfügbar"}).encode("utf-8")
                    code = 503
                else:
                    resp = json.dumps({"status": "error", "message": "Feld 'mode' erforderlich"}).encode("utf-8")
                    code = 400
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/espn/settings":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                if espn_client:
                    try:
                        res = espn_client.update_settings(
                            mode=data.get("mode"),
                            risk_level=data.get("risk_level"),
                            flash_enabled=data.get("flash_enabled")
                        )
                        resp = json.dumps(res).encode("utf-8")
                        code = 200
                    except ValueError as ve:
                        resp = json.dumps({"status": "error", "message": str(ve)}).encode("utf-8")
                        code = 400
                    except Exception as e:
                        resp = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
                        code = 500
                else:
                    resp = json.dumps({"status": "error", "message": "ESPN Service nicht verfügbar"}).encode("utf-8")
                    code = 503
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/espn/analyze":
                if espn_client:
                    res = espn_client.analyze_roster_with_ai(force=True)
                    resp = json.dumps(res).encode("utf-8")
                    code = 200
                else:
                    resp = json.dumps({"status": "error", "message": "ESPN Service nicht verfügbar"}).encode("utf-8")
                    code = 503
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path.startswith("/api/espn/proposals/") and parsed.path.endswith("/apply"):
                parts = parsed.path.split("/")
                proposal_id = parts[4] if len(parts) >= 6 else ""
                if espn_client and proposal_id:
                    res = espn_client.apply_proposal(proposal_id)
                    code = 200 if res.get("success") else 400
                    resp = json.dumps(res).encode("utf-8")
                else:
                    code = 503 if not espn_client else 400
                    resp = json.dumps({"success": False, "error": "Fehler beim Ausführen"}).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path.startswith("/api/espn/proposals/") and parsed.path.endswith("/dismiss"):
                parts = parsed.path.split("/")
                proposal_id = parts[4] if len(parts) >= 6 else ""
                if espn_client and proposal_id:
                    found = espn_client.dismiss_proposal(proposal_id)
                    code = 200 if found else 404
                    resp = json.dumps({"status": "ok" if found else "error", "success": bool(found), "dismissed": proposal_id}).encode("utf-8")
                else:
                    code = 503 if not espn_client else 400
                    resp = json.dumps({"status": "error", "success": False, "message": "Fehler"}).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/homeassistant/test":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                res = ha_service.test_connection(
                    url=data.get("url"),
                    token=data.get("token"),
                    username=data.get("username"),
                    password=data.get("password")
                ) if ha_service else {"success": False}
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/homeassistant/service":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                domain = data.get("domain", "")
                service = data.get("service", "")
                service_data = data.get("service_data") or data.get("data") or {}
                res = ha_service.call_service(domain, service, service_data) if ha_service else {"success": False}
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/permissions/verify":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try: data = json.loads(body)
                except Exception: data = {}
                code = data.get("code", "")
                valid = permissions_service.verify_code(code) if permissions_service else False
                resp = json.dumps({"valid": valid, "locked_sections": permissions_service.locked_sections if valid else []}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/permissions/config":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try: data = json.loads(body)
                except Exception: data = {}
                current_code = data.get("code") or data.get("current_code") or ""
                new_code = data.get("new_code")
                locked_sections = data.get("locked_sections")
                res = permissions_service.update_permissions(current_code, new_code=new_code, locked_sections=locked_sections) if permissions_service else {"success": False}
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path == "/api/cycle/partners":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try: data = json.loads(body)
                except Exception: data = {}
                res = cycle_service.add_partner(
                    name=data.get("name"),
                    cycle_duration=data.get("cycle_duration", 28),
                    start_date=data.get("start_date"),
                    period_duration=data.get("period_duration", 5),
                    notes=data.get("notes", "")
                ) if cycle_service else {"success": False}
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path.startswith("/api/cycle/partners/") and parsed.path.endswith("/start-cycle"):
                parts = parsed.path.split("/")
                partner_id = parts[4] if len(parts) > 4 else ""
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try: data = json.loads(body)
                except Exception: data = {}
                res = cycle_service.start_new_cycle(partner_id, data.get("start_date")) if cycle_service else {"success": False}
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path.startswith("/api/pulsecast/"):
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length > 0 else b"{}"
                subpath = parsed.path.replace("/api/pulsecast", "/api", 1)
                if parsed.path == "/api/pulsecast/download/media":
                    subpath = "/api/xtream/download"
                elif parsed.path == "/api/pulsecast/download/xdcc":
                    subpath = "/api/download"
                target_url = f"http://127.0.0.1:3000{subpath}"
                try:
                    try:
                        json_body = json.loads(body.decode("utf-8"))
                    except Exception:
                        json_body = {}
                    if parsed.path == "/api/pulsecast/download/media" and "items" in json_body:
                        target_url = "http://127.0.0.1:3000/api/xtream/download-batch"
                    r = requests.post(target_url, json=json_body, timeout=15)
                    self.send_response(r.status_code)
                    self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(r.content)))
                    self.end_headers()
                    self.wfile.write(r.content)
                except Exception as e:
                    err = json.dumps({"success": False, "error": str(e), "offline": True}).encode("utf-8")
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
            elif parsed.path == "/api/pimmel/sync":
                res = pimmel_service.trigger_sync() if pimmel_service else {"success": False}
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            else:
                self.send_response(404)
                self.end_headers()

        def do_PUT(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith("/api/cycle/partners/"):
                partner_id = parsed.path.split("/")[-1]
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                try: data = json.loads(body)
                except Exception: data = {}
                res = cycle_service.update_partner(partner_id, data) if cycle_service else {"success": False}
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            else:
                self.send_response(404)
                self.end_headers()

        def do_DELETE(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith("/api/cycle/partners/"):
                partner_id = parsed.path.split("/")[-1]
                res = cycle_service.delete_partner(partner_id) if cycle_service else {"success": False}
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif parsed.path.startswith("/api/pulsecast/"):
                subpath = parsed.path.replace("/api/pulsecast", "/api", 1)
                target_url = f"http://127.0.0.1:3000{subpath}"
                if parsed.query:
                    target_url += f"?{parsed.query}"
                try:
                    r = requests.delete(target_url, timeout=15)
                    self.send_response(r.status_code)
                    self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(r.content)))
                    self.end_headers()
                    self.wfile.write(r.content)
                except Exception as e:
                    err = json.dumps({"success": False, "error": str(e), "offline": True}).encode("utf-8")
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            sys.stdout.write(f"[{self.log_date_time_string()}] {args[0]} {args[1]} {args[2]}\n")
            sys.stdout.flush()

    def run_server():
        server_address = ("0.0.0.0", 5000)
        httpd = ThreadingHTTPServer(server_address, DashboardHTTPHandler)
        print("[START] Starte Standard-HTTP System Server auf http://0.0.0.0:5000 ...", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    run_server()
