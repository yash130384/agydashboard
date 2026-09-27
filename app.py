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
import secrets
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
    from flask import Flask, jsonify, render_template, render_template_string, request, send_from_directory, Response, stream_with_context, redirect, make_response
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
    if user_service:
        user_service.ensure_default_admins()
except Exception as _user_err:
    user_service = None
    print(f"[WARN] user_service konnte nicht importiert werden: {_user_err}", file=sys.stderr)

# Cactus Needle 3 Agent Global Instance
CACTUS_AGENT = None

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

# LCARS Knowledge Base Service Import
try:
    from knowledge_service import knowledge_service
except Exception as _kb_err:
    knowledge_service = None
    print(f"[WARN] knowledge_service konnte nicht importiert werden: {_kb_err}", file=sys.stderr)

# LCARS Research Service Import
try:
    from research_service import research_service
except Exception as _res_err:
    research_service = None
    print(f"[WARN] research_service konnte nicht importiert werden: {_res_err}", file=sys.stderr)


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


def get_hardware_model():
    """Ermittelt das genaue Hardware-Modell / Systembezeichnung (z.B. Raspberry Pi 4B, ThinkCentre M720q)."""
    # 1. Device Tree (ARM / Raspberry Pi)
    for dt_path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        if os.path.exists(dt_path):
            try:
                with open(dt_path, "r", errors="ignore") as f:
                    val = f.read().strip("\x00 \n\r\t")
                    if val:
                        return val
            except Exception:
                pass

    # 2. DMI / Sysfs (x86_64 PCs, Desktops, Laptops, Server)
    sys_vendor = ""
    if os.path.exists("/sys/class/dmi/id/sys_vendor"):
        try:
            with open("/sys/class/dmi/id/sys_vendor", "r", errors="ignore") as f:
                sys_vendor = f.read().strip()
        except Exception:
            pass

    for p in ("/sys/class/dmi/id/product_version", "/sys/class/dmi/id/product_family", "/sys/class/dmi/id/product_name", "/sys/class/dmi/id/board_name"):
        if os.path.exists(p):
            try:
                with open(p, "r", errors="ignore") as f:
                    val = f.read().strip()
                    if val and val.lower() not in ("none", "system product name", "to be filled by o.e.m.", "default string", "type2 - board version"):
                        if sys_vendor and sys_vendor.lower() not in val.lower() and sys_vendor.lower() not in ("system manufacturer", "to be filled by o.e.m."):
                            return f"{sys_vendor} {val}"
                        return val
            except Exception:
                pass

    # 3. Fallback: OS-Name / Platform Machine
    return f"{platform.system()} {platform.machine()}"


def get_system_elbow_label(ram_total_gb=None):
    """Generiert die kurze, prägnante LCARS-Systembezeichnung für den linken Rahmen-Elbow (z.B. 'M720Q // 8G' oder 'PI-4B // 8G')."""
    model = get_hardware_model()
    if "raspberry pi" in model.lower():
        m = re.search(r"Raspberry Pi (\d+)(?:\s+Model\s+([A-Za-z0-9]+))?", model, re.I)
        if m:
            num = m.group(1)
            letter = m.group(2) or ""
            short = f"PI-{num}{letter}"
        else:
            short = "RASPBERRY-PI"
    elif "thinkcentre" in model.lower():
        m = re.search(r"M\d+[a-z]?", model, re.I)
        if m:
            short = m.group(0).upper()
        else:
            short = "THINKCENTRE"
    else:
        parts = model.split()
        if len(parts) >= 2 and len(parts[0]) + len(parts[1]) <= 12:
            short = f"{parts[0]} {parts[1]}".upper()
        else:
            short = (parts[-1] if parts else platform.machine()).upper()

    ram_str = f" // {round(ram_total_gb)}G" if (ram_total_gb and ram_total_gb > 0) else ""
    return f"{short}{ram_str}"


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


def sync_service_registry_to_user_service():
    if user_service and hasattr(user_service, "register_service"):
        for reg_k, reg_v in SERVICE_REGISTRY.items():
            port = reg_v.get("port")
            s_key = reg_k
            if port == 3000:
                s_key = "pulsecast"
            elif port == 8000:
                s_key = "telemetryvault"
            elif port == 5580:
                s_key = "matter"
            elif port == 8787:
                s_key = "headroom"
            elif port == 631:
                s_key = "cups"
            sub = STATIC_PORT_SUBDOMAINS.get(port) if isinstance(port, int) else None
            user_service.register_service(
                key=s_key,
                name=reg_v.get("title") or reg_v.get("name") or reg_k,
                subdomain=sub,
                port=port,
                desc=reg_v.get("description", ""),
            )


sync_service_registry_to_user_service()


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

            if user_service and hasattr(user_service, "register_service"):
                svc_key = None
                for reg_k, reg_v in SERVICE_REGISTRY.items():
                    if reg_v.get("port") == port:
                        svc_key = reg_k
                        break
                if port == 3000:
                    svc_key = "pulsecast"
                elif port == 8000:
                    svc_key = "telemetryvault"
                elif port == 5580:
                    svc_key = "matter"
                elif port == 8787:
                    svc_key = "headroom"
                elif port == 631:
                    svc_key = "cups"
                elif not svc_key:
                    svc_key = STATIC_PORT_SUBDOMAINS.get(port) or pname.lower().replace(" ", "_")

                sub_cand = STATIC_PORT_SUBDOMAINS.get(port)
                if not sub_cand and cf_url:
                    m_sub = re.search(r"https?://([a-z0-9-]+)\.", cf_url)
                    if m_sub:
                        sub_cand = m_sub.group(1)

                user_service.register_service(
                    key=str(svc_key),
                    name=title,
                    subdomain=sub_cand,
                    port=port,
                    desc=f"{pname} (Port {port})",
                )

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
        "by_instance": [],
        "by_api_key": [],
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

            # 0. apiKeys: Mapping von API-Keys zu Namen & Status laden
            api_key_meta = {}
            try:
                c.execute("SELECT key, name, isActive FROM apiKeys")
                for row in c.fetchall():
                    k, name, is_act = row
                    api_key_meta[k] = {
                        "name": name or "Unbenannter Key",
                        "is_active": bool(is_act)
                    }
            except Exception:
                pass

            aggregated_by_instance = {}
            for k, meta in api_key_meta.items():
                aggregated_by_instance[k] = {
                    "key": k,
                    "name": meta["name"],
                    "is_active": meta["is_active"],
                    "requests": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cached_tokens": 0,
                    "cost": 0.0,
                }

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

                # byApiKey
                for ak_key, ak_info in day_data.get("byApiKey", {}).items():
                    raw_key = ak_info.get("apiKey") or (ak_key.split("|")[0] if "|" in ak_key else ak_key) or "unauthenticated"
                    if raw_key not in aggregated_by_instance:
                        meta = api_key_meta.get(raw_key, {})
                        aggregated_by_instance[raw_key] = {
                            "key": raw_key,
                            "name": meta.get("name") or (f"Unbekannt ({raw_key[:7]}...{raw_key[-4:]})" if len(raw_key) > 14 else raw_key),
                            "is_active": meta.get("is_active", True),
                            "requests": 0,
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "cached_tokens": 0,
                            "cost": 0.0,
                        }
                    aggregated_by_instance[raw_key]["requests"] += ak_info.get("requests", 0)
                    aggregated_by_instance[raw_key]["prompt_tokens"] += ak_info.get("promptTokens", 0)
                    aggregated_by_instance[raw_key]["completion_tokens"] += ak_info.get("completionTokens", 0)
                    aggregated_by_instance[raw_key]["cached_tokens"] += ak_info.get("cachedTokens", 0)
                    aggregated_by_instance[raw_key]["cost"] += ak_info.get("cost", 0.0)

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

            by_instance = []
            for k, stats_item in aggregated_by_instance.items():
                total_tok = stats_item["prompt_tokens"] + stats_item["completion_tokens"]
                pct = round((total_tok / total_tokens * 100), 1) if total_tokens > 0 else 0.0
                prefix = f"{k[:7]}...{k[-4:]}" if len(k) > 14 else k
                by_instance.append({
                    "key": k,
                    "key_prefix": prefix,
                    "name": stats_item["name"],
                    "is_active": stats_item.get("is_active", True),
                    "requests": stats_item["requests"],
                    "requests_formatted": f"{stats_item['requests']:,}",
                    "prompt_tokens": stats_item["prompt_tokens"],
                    "completion_tokens": stats_item["completion_tokens"],
                    "cached_tokens": stats_item["cached_tokens"],
                    "total_tokens": total_tok,
                    "total_formatted": fmt_num(total_tok),
                    "prompt_formatted": fmt_num(stats_item["prompt_tokens"]),
                    "completion_formatted": fmt_num(stats_item["completion_tokens"]),
                    "cached_formatted": fmt_num(stats_item["cached_tokens"]),
                    "cost": round(stats_item["cost"], 6),
                    "cost_formatted": f"${stats_item['cost']:.4f}",
                    "percent": pct,
                    "percent_formatted": f"{pct:.1f}%"
                })

            by_instance.sort(key=lambda x: (x["total_tokens"], x["cost"]), reverse=True)

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
                "by_instance": by_instance,
                "by_api_key": by_instance,
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
    ram_metrics = get_ram_metrics()
    total_ram_gb = ram_metrics.get("total_gb", 0)
    hw_model = get_hardware_model()
    elbow_label = get_system_elbow_label(total_ram_gb)

    stats: dict = {
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "system_model": hw_model,
        "system_label": elbow_label,
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
    stats["ram"] = ram_metrics
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
# DASHBOARD_HTML wird aus templates/index.html geladen
def _load_dashboard_html():
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")
    content = ""
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
    js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "js", "lcars_dashboard.js")
    if os.path.exists(js_path):
        with open(js_path, "r", encoding="utf-8") as f:
            content += "\n<script>\n" + f.read() + "\n</script>\n"
    return content

DASHBOARD_HTML = _load_dashboard_html()


# ---------------------------------------------------------------------------
# Fallback HTML Renderer für Basis-HTTP-Server
# ---------------------------------------------------------------------------
def render_html_fallback(stats):
    stats_json = json.dumps(stats)
    hostname = str(stats.get("hostname", "pi"))
    system_label = str(stats.get("system_label", ""))
    system_model = str(stats.get("system_model", ""))
    return DASHBOARD_HTML.replace("{{ stats.hostname }}", hostname) \
                         .replace("{{ stats.hostname.upper() }}", hostname.upper()) \
                         .replace("{{ stats_json | safe }}", stats_json) \
                         .replace("{{ stats.timestamp }}", str(stats.get("timestamp", ""))) \
                         .replace("{{ stats.platform }}", str(stats.get("platform", ""))) \
                         .replace("{{ stats.system_label }}", system_label) \
                         .replace("{{ stats.system_model }}", system_model) \
                         .replace("{{ stats.system_model or stats.hostname }}", system_model or hostname)


# ---------------------------------------------------------------------------
# Server Initialisierung & Routes
# ---------------------------------------------------------------------------
if USE_FLASK:
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    @app.template_filter('number_format')
    def number_format_filter(val):
        try:
            return f"{int(val):,}"
        except Exception:
            return str(val)

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

    def _get_current_user():
        # 1. Check Session Cookie
        session_id = request.cookies.get("lcars_session")
        if session_id:
            if permissions_service and hasattr(permissions_service, "validate_session"):
                s = permissions_service.validate_session(session_id)
                if s:
                    return s
            elif user_service and user_service.validate_session(session_id):
                s = user_service.validate_session(session_id)
                if s:
                    return s

        # 2. Authorization Header (Bearer token / API key)
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr.split(" ", 1)[1].strip()
            if permissions_service and hasattr(permissions_service, "validate_session"):
                s = permissions_service.validate_session(token)
                if s:
                    return s
            elif user_service and user_service.validate_session(token):
                s = user_service.validate_session(token)
                if s:
                    return s
            if permissions_service and hasattr(permissions_service, "verify_api_key"):
                u = permissions_service.verify_api_key(token)
                if u:
                    return u
            elif user_service and hasattr(user_service, "verify_api_key"):
                u = user_service.verify_api_key(token)
                if u:
                    return u

        # 3. Custom Headers: X-Session-ID, X-API-Key
        x_session = request.headers.get("X-Session-ID")
        if x_session:
            if permissions_service and hasattr(permissions_service, "validate_session"):
                s = permissions_service.validate_session(x_session)
                if s:
                    return s
            elif user_service:
                s = user_service.validate_session(x_session)
                if s:
                    return s

        x_key = request.headers.get("X-API-Key")
        if x_key:
            if permissions_service and hasattr(permissions_service, "verify_api_key"):
                u = permissions_service.verify_api_key(x_key)
                if u:
                    return u
            elif user_service and hasattr(user_service, "verify_api_key"):
                u = user_service.verify_api_key(x_key)
                if u:
                    return u

        # 4. Query Parameters
        q_token = request.args.get("token")
        if q_token:
            if permissions_service and hasattr(permissions_service, "validate_session"):
                s = permissions_service.validate_session(q_token)
                if s:
                    return s
            elif user_service:
                s = user_service.validate_session(q_token)
                if s:
                    return s

        q_key = request.args.get("key") or request.args.get("api_key")
        if q_key:
            if permissions_service and hasattr(permissions_service, "verify_api_key"):
                u = permissions_service.verify_api_key(q_key)
                if u:
                    return u
            elif user_service and hasattr(user_service, "verify_api_key"):
                u = user_service.verify_api_key(q_key)
                if u:
                    return u

        return None

    def _is_dashboard_authenticated():
        return _get_current_user() is not None

    def _is_section_authorized(section):
        user = _get_current_user()
        if user:
            if permissions_service:
                if permissions_service.is_super_admin(user):
                    return True
                return permissions_service.check_permission(user, section)
            elif user_service:
                if user_service.is_super_admin(user):
                    return True
                return user_service.check_service_permission(user, section)
            return False

        return False

    @app.route("/")
    def index():
        if not _is_dashboard_authenticated():
            if LOGIN_HTML:
                return Response(LOGIN_HTML, mimetype="text/html")
            return redirect("/login")
        user = _get_current_user()
        allowed_sections = permissions_service.get_allowed_sections(user) if permissions_service else ["*"]
        user_json = json.dumps({
            "id": user.get("id") if user else None,
            "username": user.get("username", "") if user else "",
            "display_name": user.get("display_name", "") if user else "",
            "allowed_services": user.get("allowed_services", []) if user else [],
            "allowed_sections": allowed_sections,
            "is_super_admin": permissions_service.is_super_admin(user) if permissions_service else True,
        })
        stats = get_system_stats(include_history=True)
        stats_json = json.dumps(stats)
        return render_template('index.html', stats=stats, stats_json=stats_json, current_user_json=user_json)

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
        if not _is_section_authorized("services"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Services nicht berechtigt"}), 403
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
        if not _is_section_authorized("hermes"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Hermes nicht berechtigt"}), 403
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
        if not _is_section_authorized("hermes"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Hermes nicht berechtigt"}), 403
        data = request.get_json(silent=True) or {}
        profile = data.get("profile", "default")
        message = data.get("message", "")
        res = hermes_chat_prompt(profile, message)
        return jsonify(res), (200 if res.get("success") else 500)

    @app.route("/api/config/ide-url", methods=["GET", "POST"])
    def api_ide_url_route():
        if not _is_section_authorized("ide"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion IDE nicht berechtigt"}), 403
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
        if not _is_section_authorized("fantasy"):
            return jsonify({"error": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
        if espn_client:
            return jsonify(espn_client.fetch(force=False))
        return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503

    @app.route("/api/fantasy/refresh", methods=["GET", "POST"])
    def api_fantasy_refresh():
        if not _is_section_authorized("fantasy"):
            return jsonify({"error": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
        if espn_client:
            return jsonify(espn_client.fetch(force=True))
        return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503

    @app.route("/api/fantasy/test-flash", methods=["GET", "POST"])
    def api_fantasy_test_flash():
        if not _is_section_authorized("fantasy"):
            return jsonify({"error": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
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
        if not _is_section_authorized("fantasy"):
            return jsonify({"status": "error", "message": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
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
        if not _is_section_authorized("fantasy"):
            return jsonify({"status": "error", "message": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            mode = data.get("mode")
            risk_level = data.get("risk_level")
            flash_enabled = data.get("flash_enabled")
            interval_hours = data.get("interval_hours") or data.get("ai_check_interval_hours") or data.get("interval")
            try:
                res = espn_client.update_settings(mode=mode, risk_level=risk_level, flash_enabled=flash_enabled, interval_hours=interval_hours)
                return jsonify(res)
            except ValueError as ve:
                return jsonify({"status": "error", "message": str(ve)}), 400
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 500
        settings = espn_client.get_settings()
        return jsonify(settings)

    @app.route("/api/espn/ai-stats", methods=["GET"])
    def api_espn_ai_stats():
        if not _is_section_authorized("fantasy"):
            return jsonify({"status": "error", "message": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        stats = espn_client.get_ai_stats()
        return jsonify(stats)

    @app.route("/api/espn/proposals", methods=["GET"])
    def api_espn_proposals():
        if not _is_section_authorized("fantasy"):
            return jsonify({"status": "error", "message": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        only_pending = request.args.get("pending", "false").lower() in ("true", "1")
        proposals = espn_client.get_proposals(only_pending=only_pending)
        return jsonify({"status": "ok", "proposals": proposals})

    @app.route("/api/espn/proposals/<proposal_id>/apply", methods=["POST"])
    def api_espn_proposal_apply(proposal_id):
        if not _is_section_authorized("fantasy"):
            return jsonify({"status": "error", "message": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        res = espn_client.apply_proposal(proposal_id)
        status_code = 200 if res.get("success") else 400
        return jsonify(res), status_code

    @app.route("/api/espn/proposals/<proposal_id>/dismiss", methods=["POST"])
    def api_espn_proposal_dismiss(proposal_id):
        if not _is_section_authorized("fantasy"):
            return jsonify({"status": "error", "message": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        found = espn_client.dismiss_proposal(proposal_id)
        if found:
            return jsonify({"status": "ok", "success": True, "dismissed": proposal_id})
        return jsonify({"status": "error", "success": False, "message": f"Vorschlag '{proposal_id}' nicht gefunden oder bereits bearbeitet."}), 404

    @app.route("/api/espn/analyze", methods=["POST"])
    def api_espn_analyze():
        if not _is_section_authorized("fantasy"):
            return jsonify({"status": "error", "message": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        res = espn_client.analyze_roster_with_ai(force=True)
        return jsonify(res)

    @app.route("/api/espn/history", methods=["GET"])
    def api_espn_history():
        if not _is_section_authorized("fantasy"):
            return jsonify({"status": "error", "message": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
        if not espn_client:
            return jsonify({"status": "error", "message": "ESPN Service nicht verfügbar"}), 503
        try:
            limit = int(request.args.get("limit", 50))
        except (ValueError, TypeError):
            limit = 50
        history = espn_client.get_decision_log(limit=limit)
        bot_status = espn_client.get_bot_status() if hasattr(espn_client, "get_bot_status") else {}
        return jsonify({"status": "ok", "bot_status": bot_status, "history": history})

    @app.route("/api/espn/lineup/move", methods=["POST"])
    def api_espn_lineup_move():
        if not _is_section_authorized("fantasy"):
            return jsonify({"status": "error", "message": "LCARS Zugriff verweigert: Sektion Fantasy nicht berechtigt"}), 403
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
        if not _is_section_authorized("homeassistant"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Home Assistant nicht berechtigt"}), 403
        if ha_service:
            return jsonify(ha_service.get_config(safe=True))
        return jsonify({"configured": False, "error": "ha_service nicht verfügbar"}), 503

    @app.route("/api/config/homeassistant", methods=["POST"])
    def api_config_ha():
        if not _is_section_authorized("homeassistant"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Home Assistant nicht berechtigt"}), 403
        if not ha_service:
            return jsonify({"success": False, "error": "ha_service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        res = ha_service.save_config(data)
        return jsonify(res)

    @app.route("/api/homeassistant/test", methods=["POST"])
    def api_ha_test():
        if not _is_section_authorized("homeassistant"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Home Assistant nicht berechtigt"}), 403
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
        if not _is_section_authorized("homeassistant"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Home Assistant nicht berechtigt"}), 403
        if not ha_service:
            return jsonify({"success": False, "error": "ha_service nicht verfügbar"}), 503
        return jsonify(ha_service.get_rooms_and_entities())

    @app.route("/api/homeassistant/service", methods=["POST"])
    def api_ha_service():
        if not _is_section_authorized("homeassistant"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Home Assistant nicht berechtigt"}), 403
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
        if not _is_section_authorized("solar"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Solar nicht berechtigt"}), 403
        if not ha_service:
            return jsonify({"success": False, "error": "ha_service nicht verfügbar"}), 503
        return jsonify(ha_service.get_solar_data())

    @app.route("/api/permissions/status", methods=["GET"])
    def api_permissions_status():
        user = _get_current_user()
        allowed = permissions_service.get_allowed_sections(user) if permissions_service else ["*"]
        return jsonify({"allowed_sections": allowed, "has_code": False})

    @app.route("/api/permissions/verify", methods=["POST"])
    def api_permissions_verify():
        user = _get_current_user()
        if user:
            return jsonify({"valid": True, "user": user})
        return jsonify({"valid": False, "error": "Nicht authentifiziert"}), 401

    @app.route("/api/permissions/config", methods=["POST"])
    def api_permissions_config():
        if not _is_lcars_user_management_authorized():
            return jsonify({"success": False, "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)"}), 403
        return jsonify({"success": True})

    @app.route("/api/cycle/partners", methods=["GET", "POST"])
    def api_cycle_partners():
        if not _is_section_authorized("cycle"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Zyklus nicht berechtigt"}), 403
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
        if not _is_section_authorized("cycle"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Zyklus nicht berechtigt"}), 403
        if not cycle_service:
            return jsonify({"success": False, "error": "Service nicht verfügbar"}), 503
        if request.method == "DELETE":
            return jsonify(cycle_service.delete_partner(partner_id))
        data = request.get_json(silent=True) or {}
        return jsonify(cycle_service.update_partner(partner_id, data))

    @app.route("/api/cycle/partners/<partner_id>/start-cycle", methods=["POST"])
    def api_cycle_start_new(partner_id):
        if not _is_section_authorized("cycle"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Zyklus nicht berechtigt"}), 403
        if not cycle_service:
            return jsonify({"success": False, "error": "Service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        return jsonify(cycle_service.start_new_cycle(partner_id, data.get("start_date")))

    # -----------------------------------------------------------------------
    # PulseCast (Media & Download Hub) Proxy Endpoints
    # -----------------------------------------------------------------------
    PULSECAST_BASE_URL = "http://127.0.0.1:3000"

    def _pulsecast_authorized():
        return _is_section_authorized("pulsecast")

    def _pulsecast_proxy(method, endpoint, params=None, json_data=None, timeout=12):
        if not _pulsecast_authorized():
            return jsonify({"success": False, "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)", "locked": True}), 403
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

    @app.route("/api/pulsecast/sync", methods=["POST"])
    def api_pulsecast_sync():
        data = request.get_json(silent=True) or {}
        hours = data.get("xtreamSyncIntervalHours", 2)
        try:
            hours = int(hours)
            if hours <= 0:
                hours = 2
        except (ValueError, TypeError):
            hours = 2
        return _pulsecast_proxy("POST", "/api/settings", json_data={"xtreamSyncIntervalHours": hours}, timeout=15)

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
            return jsonify({"success": False, "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)", "locked": True}), 403

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

        scheme = request.headers.get("X-Forwarded-Proto") or request.scheme
        host_url = f"{scheme}://{request.host}".rstrip("/")
        if not request.headers.get("X-Forwarded-Proto") and request.host_url:
            host_url = request.host_url.rstrip("/")

        safe_encoded_fn = urllib.parse.quote(clean_filename, safe="/")
        full_stream_url = f"{host_url}/api/pulsecast/media/stream/{safe_encoded_fn}"

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
            return jsonify({"success": False, "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)", "locked": True}), 403

        safe_filename = urllib.parse.quote(filename, safe="/")
        target_url = f"{PULSECAST_BASE_URL}/api/media/stream/{safe_filename}"
        return _pulsecast_stream_proxy(target_url)

    @app.route("/api/pulsecast/media/transcode/<path:filename>", methods=["GET", "HEAD"])
    def api_pulsecast_media_transcode(filename):
        if not _pulsecast_authorized():
            return jsonify({"success": False, "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)", "locked": True}), 403

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
        # Check active session user or super admin: only cb / super admin can manage users
        user = _get_current_user()
        if user:
            if user.get("is_super_admin") or user.get("username", "").lower() in ("cb", "admin"):
                return True
            if permissions_service and permissions_service.is_super_admin(user):
                return True
        return False

    @app.route("/login", methods=["GET"])
    def lcars_login():
        return_to = request.args.get("return_to", "/")
        if _is_dashboard_authenticated():
            return redirect(return_to)
        if LOGIN_HTML:
            return Response(LOGIN_HTML, mimetype="text/html")
        return "LCARS Login nicht verfügbar", 500

    @app.route("/api/auth/login", methods=["POST"])
    def api_auth_login():
        if not user_service and not permissions_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503

        data = request.get_json(silent=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        api_key = (data.get("api_key") or data.get("key") or request.headers.get("X-API-Key") or "").strip()
        remember = bool(data.get("remember", False))
        return_to = data.get("return_to") or "/"

        ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For") or request.remote_addr or ""
        ua = request.headers.get("User-Agent", "")

        user_info = None

        if api_key:
            if permissions_service and hasattr(permissions_service, "verify_api_key"):
                user_info = permissions_service.verify_api_key(api_key)
            elif user_service and hasattr(user_service, "verify_api_key"):
                user_info = user_service.verify_api_key(api_key)

            if not user_info:
                return jsonify({"success": False, "error": "Ungültiger API-Key"}), 401
        else:
            auth_res = None
            if permissions_service and hasattr(permissions_service, "authenticate"):
                auth_res = permissions_service.authenticate(username, password, ip=ip, user_agent=ua)
            elif user_service:
                auth_res = user_service.authenticate(username, password, ip=ip, user_agent=ua)

            if not auth_res or not auth_res.get("success"):
                err_msg = auth_res.get("error", "Zugriff verweigert") if auth_res else "Zugriff verweigert"
                return jsonify({"success": False, "error": err_msg}), 401

            user_info = auth_res["user"]

        duration = getattr(user_service, "REMEMBER_SESSION_DURATION", 2592000) if remember else getattr(user_service, "DEFAULT_SESSION_DURATION", 86400)
        session_id = None
        if user_service and user_info.get("username"):
            db_u = user_service.get_user_by_username(user_info["username"])
            uid = int(db_u["id"]) if db_u else int(user_info.get("id") or 1)
            session_id = user_service.create_session(uid, duration_seconds=duration, ip=ip, user_agent=ua)
        elif user_service and user_info.get("id"):
            session_id = user_service.create_session(int(user_info["id"]), duration_seconds=duration, ip=ip, user_agent=ua)
        else:
            session_id = secrets.token_urlsafe(32)

        resp = jsonify({
            "success": True,
            "session_id": session_id,
            "token": session_id,
            "redirect_url": return_to,
            "user": user_info
        })
        resp.headers["X-Session-ID"] = session_id
        resp.headers["Authorization"] = f"Bearer {session_id}"

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
        if not session_id:
            auth_hdr = request.headers.get("Authorization", "")
            if auth_hdr.startswith("Bearer "):
                session_id = auth_hdr.split(" ", 1)[1].strip()
            if not session_id:
                session_id = request.headers.get("X-Session-ID")
            if not session_id and request.is_json:
                session_id = (request.get_json(silent=True) or {}).get("session_id")

        if session_id and user_service:
            user_service.delete_session(session_id)

        resp = jsonify({"success": True, "message": "Erfolgreich abgemeldet"})
        resp.delete_cookie("lcars_session", path="/")
        cookie_domain = _get_lcars_cookie_domain()
        if cookie_domain:
            resp.delete_cookie("lcars_session", domain=cookie_domain, path="/")
        return resp

    @app.route("/api/auth/me", methods=["GET"])
    def api_auth_me():
        user = _get_current_user()
        if user:
            is_sa = permissions_service.is_super_admin(user) if permissions_service else False
            allowed_secs = permissions_service.get_allowed_sections(user) if permissions_service else ["*"]
            user_data = dict(user)
            user_data["is_super_admin"] = is_sa
            user_data["allowed_sections"] = allowed_secs
            return jsonify({"authenticated": True, "user": user_data})
        return jsonify({"authenticated": False, "user": None})

    @app.route("/api/user-services", methods=["GET"])
    def api_user_services():
        if not user_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503
        services = user_service.list_services() if hasattr(user_service, "list_services") else []
        categories = permissions_service.list_categories() if permissions_service else []
        return jsonify({"success": True, "services": services, "categories": categories})

    @app.route("/api/users", methods=["GET", "POST"])
    def api_users():
        if not user_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503
        if not _is_lcars_user_management_authorized():
            return jsonify({"success": False, "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)"}), 403

        if request.method == "GET":
            users = user_service.list_users()
            services = user_service.list_services() if hasattr(user_service, "list_services") else []
            categories = permissions_service.list_categories() if permissions_service else []
            return jsonify({"success": True, "users": users, "services": services, "categories": categories})

        # POST: Neuer Benutzer anlegen
        data = request.get_json(silent=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        display_name = (data.get("display_name") or "").strip()
        allowed_services = data.get("allowed_services", [])
        api_key = (data.get("api_key") or "").strip() or None
        notes = (data.get("notes") or "").strip()

        res = user_service.create_user(
            username=username,
            password=password,
            display_name=display_name,
            allowed_services=allowed_services,
            notes=notes,
            api_key=api_key,
        )
        if res.get("success"):
            return jsonify(res), 201
        return jsonify(res), 400

    @app.route("/api/users/<int:user_id>", methods=["PUT", "DELETE"])
    def api_user_detail(user_id):
        if not user_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503
        if not _is_lcars_user_management_authorized():
            return jsonify({"success": False, "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)"}), 403

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

        update_kwargs = dict(
            display_name=display_name,
            is_active=is_active,
            allowed_services=allowed_services,
            notes=notes,
        )
        if "api_key" in data:
            update_kwargs["api_key"] = data.get("api_key")

        res = user_service.update_user(
            user_id=user_id,
            **update_kwargs
        )
        if res.get("success"):
            return jsonify(res)
        return jsonify(res), 400

    @app.route("/api/users/<int:user_id>/key", methods=["POST", "DELETE"])
    def api_user_key(user_id):
        if not user_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503
        if not _is_lcars_user_management_authorized():
            return jsonify({"success": False, "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)"}), 403

        if request.method == "DELETE":
            res = user_service.update_user(user_id=user_id, api_key="")
            return jsonify(res)

        data = request.get_json(silent=True) or {}
        new_key = (data.get("api_key") or "").strip()
        if not new_key:
            new_key = f"lcars_{secrets.token_hex(16)}"

        res = user_service.update_user(user_id=user_id, api_key=new_key)
        if res.get("success"):
            res["api_key"] = new_key
            return jsonify(res)
        return jsonify(res), 400

    @app.route("/api/users/<int:user_id>/password", methods=["POST"])
    def api_user_password(user_id):
        if not user_service:
            return jsonify({"success": False, "error": "User Service nicht verfügbar"}), 503
        if not _is_lcars_user_management_authorized():
            return jsonify({"success": False, "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)"}), 403

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
            return jsonify({"success": False, "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)"}), 403

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
        return _is_section_authorized("gemini_live")

    CACTUS_AGENT = None
    def get_cactus_agent():
        global CACTUS_AGENT
        if CACTUS_AGENT is None:
            try:
                import sys
                cactus_dir = "/home/cb/Projects/cactus-ha-poc"
                venv_site = f"{cactus_dir}/.venv/lib/python3.13/site-packages"
                src_dir = f"{cactus_dir}/src"
                if venv_site not in sys.path:
                    sys.path.insert(0, venv_site)
                if src_dir not in sys.path:
                    sys.path.insert(0, src_dir)
                from cactus_ha_poc.agent import NeedleAgent
                from cactus_ha_poc.config import load_config
                cfg = load_config(f"{cactus_dir}/.env")
                if ha_service:
                    try:
                        ha_cfg = ha_service.get_config(safe=False)
                        if ha_cfg.get("token"):
                            cfg.hass_token = ha_cfg["token"]
                        if ha_cfg.get("url"):
                            cfg.hass_url = ha_cfg["url"]
                    except Exception:
                        pass
                CACTUS_AGENT = NeedleAgent(config=cfg)
                print("[CACTUS] Needle 3 Agent successfully initialized for Subraum Comm.", flush=True)
            except Exception as e:
                print(f"[CACTUS] Initialization failed: {e}", flush=True)
        return CACTUS_AGENT

    WHISPER_MODEL = None
    WHISPER_LOCK = threading.Lock()

    def get_whisper_model():
        global WHISPER_MODEL
        if WHISPER_MODEL is None:
            with WHISPER_LOCK:
                if WHISPER_MODEL is None:
                    from faster_whisper import WhisperModel
                    WHISPER_MODEL = WhisperModel("base", device="cpu", compute_type="int8")
                    print("[STT] Faster-Whisper base model (cpu, int8) initialized.", flush=True)
        return WHISPER_MODEL

    CACTUS_UI_TOOLS = [
        {
            "name": "navigate_section",
            "description": "Wechsle zu einer LCARS Dashboard Sektion oder Kategorie.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Zielkategorie (system, services, ai, 9router, hermes, ide, agents, ai-info, config, fantasy, personal, solar, homeassistant, cycle, pulsecast, gemini_live, devteam)"
                    }
                },
                "required": ["target"]
            }
        },
        {
            "name": "paginate",
            "description": "Blättere in Listen, Katalogen oder Tabellen seitenweise weiter oder zurück.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["next", "prev"],
                        "description": "Paginierungsrichtung ('next' = nächste Seite, 'prev' = vorherige Seite)"
                    }
                },
                "required": ["direction"]
            }
        },
        {
            "name": "ui_action",
            "description": "Führe globale UI-Aktionen im Dashboard aus (Verlauf vor/zurück, Ansicht aktualisieren, Vollbild).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["back", "forward", "refresh", "fullscreen"],
                        "description": "Aktion: 'back' (Zurück), 'forward' (Vor), 'refresh' (Aktualisieren), 'fullscreen' (Vollbild)"
                    }
                },
                "required": ["action"]
            }
        }
    ]

    CATEGORY_NAMES_MAP = {
        "system": "SYSTEM & SENSOR VERLAUF",
        "services": "SERVICES & PROZESS-SCANNER",
        "ai": "LCARS KÜNSTLICHE INTELLIGENZ // BEREICHSÜBERSICHT",
        "9router": "9ROUTER COMM-LINK // SUBRAUM CHAT",
        "hermes": "HERMES AUTONOMOUS RUNTIME // SYSTEM-AGENTEN",
        "ide": "GOOGLE ANTIGRAVITY // ENTWICKLUNGSUMGEBUNG",
        "agents": "LCARS SUBRAUM COMM-LINK // KI-AGENTEN",
        "ai-info": "KI-INFO // 9ROUTER & NEURAL TELEMETRIE",
        "config": "SYSTEM CONFIG & FARBMODI",
        "fantasy": "ESPN FANTASY FOOTBALL // INCOMPLETE PASS",
        "personal": "LCARS PERSÖNLICHER BEREICH // ÜBERSICHT",
        "solar": "LCARS ENERGIE-MANAGEMENT // BALKONSOLAR",
        "homeassistant": "LCARS HAUSSTEUERUNG // HOME ASSISTANT",
        "cycle": "LCARS BIO-TELEMETRIE // PARTNERINNEN-ZYKLUS",
        "pulsecast": "LCARS PULSECAST // MEDIA & DOWNLOAD HUB",
        "gemini_live": "LCARS SUBRAUM COMM // CACTUS NEEDLE 3",
        "devteam": "DEV-TEAM // KANBAN WORKFLOW ENGINE"
    }

    SECTION_SYNONYMS = {
        "system": ["system", "systeme", "sensor", "sensoren", "sensor verlauf", "hardware", "cpu", "ram", "terminal 47", "terminal", "agy-pi", "agypi", "pi"],
        "services": ["services", "service", "prozesse", "prozess", "scanner", "prozess-scanner", "dienste", "ports", "server", "webserver"],
        "ai": ["ai", "ki", "ki bereich", "ki-bereich", "künstliche intelligenz", "kuenstliche intelligenz", "ki übersicht", "ki uebersicht", "ai themen", "ki themen", "ai bereich", "ki gruppe"],
        "9router": ["9router", "9 router", "router comm", "9router comm", "9router chat", "chat", "router chat", "9router comm-link"],
        "hermes": ["hermes", "hermes agenten", "hermes runtime", "system-agenten", "system agenten", "hermes profile"],
        "ide": ["ide", "antigravity", "antigravity ide", "entwicklerumgebung", "entwicklungsumgebung", "cloud ide", "coding ide", "google antigravity"],
        "agents": ["agents", "agenten", "ki agenten", "ki-agenten", "subraum comm-link", "assistent", "bot", "ai agents"],
        "ai-info": ["ai-info", "ai_info", "ai info", "ki-info", "ki_info", "ki info", "neural telemetrie", "telemetrie", "benchmarks", "neural", "router telemetrie"],
        "config": ["config", "konfiguration", "einstellungen", "farbmodi", "farbmodus", "settings", "theme", "farben", "lcars farben"],
        "fantasy": ["fantasy", "espn", "espn fantasy", "football", "incomplete pass", "fantasy football", "liga"],
        "personal": ["personal", "persönlich", "persoenlich", "persönlicher bereich", "persoenlicher bereich", "persönlichen bereich", "persoenlichen bereich", "persönlichen", "persönliche", "persoenliche", "privat", "home", "eigene", "persönliche themen"],
        "solar": ["solar", "balkonsolar", "energie", "energie-management", "photovoltaik", "pv", "akku", "batterie", "strom", "hausverbrauch", "stromverbrauch", "solaranlage"],
        "homeassistant": ["homeassistant", "home assistant", "ha", "haussteuerung", "smart home", "smarthome"],
        "cycle": ["cycle", "zyklus", "bio-telemetrie", "bio telemetrie", "partnerin"],
        "pulsecast": ["pulsecast", "mediathek", "media", "download hub", "downloads", "katalog", "filme", "serien"],
        "gemini_live": ["gemini_live", "gemini-live", "gemini live", "subraum comm", "subraum", "cactus", "needle", "sprachsteuerung", "needle 3"],
        "devteam": ["devteam", "dev-team", "dev team", "kanban", "workflow engine", "tasks", "board", "entwickler", "dev team kanban"]
    }

    def resolve_ui_command(prompt, context=None, mode="test"):
        p = (prompt or "").strip().lower()
        p_clean = re.sub(r"[^\w\s\-\_]", " ", p)
        p_clean = re.sub(r"\s+", " ", p_clean).strip()
        if not p_clean:
            return None

        # Exclusions: Home Assistant light or weather
        if any(p_clean.startswith(w) for w in ("schalte ", "schalt ", "mache ", "mach ", "dimme ", "dimm ", "stelle ", "stell ")) and any(p_clean.endswith(w) for w in (" an", " aus", " ein", " ab", "%")):
            return None
        if any(w in p_clean for w in ("wetter", "temperatur", "regen", "regenschirm", "wind", "grad")):
            return None

        # 1. Paginierung
        next_patterns = [
            "nächste seite", "naechste seite", "seite vor", "seite weiter", "seite vorwärts",
            "eine seite vor", "eine seite weiter", "blättere vor", "blättere weiter", "vorblättern",
            "weiterblättern", "next page", "page forward", "forward page", "seite danach"
        ]
        prev_patterns = [
            "vorherige seite", "vorige seite", "seite zurück", "seite zurueck", "eine seite zurück",
            "eine seite zurueck", "blättere zurück", "blättere zurueck", "zurückblättern", "zurueckblaettern",
            "previous page", "prev page", "page back", "back page", "seite davor"
        ]

        is_next = any(pattern in p_clean for pattern in next_patterns) or p_clean in ("weiter", "nächste", "naechste", "next")
        is_prev = any(pattern in p_clean for pattern in prev_patterns) or p_clean in ("vorherige", "vorige", "prev")

        if is_next or is_prev:
            direction = "next" if is_next else "prev"
            delta = 1 if is_next else -1
            msg = "Blättere zur nächsten Seite." if is_next else "Blättere zur vorherigen Seite."
            if context and isinstance(context.get("pagination"), dict) and context["pagination"].get("has_pagination"):
                pg = context["pagination"]
                cur = pg.get("current_page", 1)
                tot = pg.get("total_pages", 1)
                if is_next and not pg.get("has_next", True):
                    msg = f"Bereits auf der letzten Seite (Seite {cur} von {tot})."
                elif is_prev and not pg.get("has_prev", True):
                    msg = f"Bereits auf der ersten Seite (Seite {cur})."
                else:
                    next_p = cur + delta
                    msg = f"Blättere zu Seite {next_p} von {tot}."

            return {
                "success": True,
                "prompt": prompt,
                "message": msg,
                "tool_call": {"name": "paginate", "arguments": {"direction": direction}},
                "action": "paginate",
                "entity_id": f"pagination.{direction}",
                "ui_action": {"type": "paginate", "direction": direction, "delta": delta},
                "confidence": 100.0,
                "error": None,
                "mode": mode
            }

        # 2. UI Actions (History, Refresh, Fullscreen)
        back_patterns = ["zurück", "zurueck", "gehe zurück", "geh zurück", "navigiere zurück", "back", "go back", "vorherige ansicht"]
        fwd_patterns = ["vor", "vorwärts", "vorwaerts", "gehe vor", "geh vor", "navigiere vor", "forward", "go forward", "nächste ansicht"]
        refresh_patterns = ["aktualisieren", "aktualisiere", "aktualisiere ansicht", "dashboard aktualisieren", "neu laden", "refresh", "reload", "ansicht aktualisieren", "aktualisiere die ansicht", "daten aktualisieren"]
        fs_patterns = ["vollbild", "fullscreen", "vollbildmodus", "ganzer bildschirm"]

        if p_clean in back_patterns:
            return {
                "success": True,
                "prompt": prompt,
                "message": "Navigiere zurück zur vorherigen Ansicht.",
                "tool_call": {"name": "ui_action", "arguments": {"action": "back"}},
                "action": "ui_back",
                "entity_id": "ui.back",
                "ui_action": {"type": "ui_action", "action": "back"},
                "confidence": 100.0,
                "error": None,
                "mode": mode
            }
        if p_clean in fwd_patterns:
            return {
                "success": True,
                "prompt": prompt,
                "message": "Navigiere vorwärts zur nächsten Ansicht.",
                "tool_call": {"name": "ui_action", "arguments": {"action": "forward"}},
                "action": "ui_forward",
                "entity_id": "ui.forward",
                "ui_action": {"type": "ui_action", "action": "forward"},
                "confidence": 100.0,
                "error": None,
                "mode": mode
            }
        if p_clean in refresh_patterns:
            sec_title = context.get("active_section_title") if context else None
            msg = f"Aktualisiere Ansicht '{sec_title}'." if sec_title else "Aktualisiere Dashboard-Ansicht."
            return {
                "success": True,
                "prompt": prompt,
                "message": msg,
                "tool_call": {"name": "ui_action", "arguments": {"action": "refresh"}},
                "action": "ui_refresh",
                "entity_id": "ui.refresh",
                "ui_action": {"type": "ui_action", "action": "refresh"},
                "confidence": 100.0,
                "error": None,
                "mode": mode
            }
        if p_clean in fs_patterns:
            return {
                "success": True,
                "prompt": prompt,
                "message": "Schalte Vollbildmodus um.",
                "tool_call": {"name": "ui_action", "arguments": {"action": "fullscreen"}},
                "action": "ui_fullscreen",
                "entity_id": "ui.fullscreen",
                "ui_action": {"type": "ui_action", "action": "fullscreen"},
                "confidence": 100.0,
                "error": None,
                "mode": mode
            }

        # 3. Section Navigation
        nav_prefix_regex = r"^(?:geh(?:e)?(?:\s+(?:zu|zum|zur|in|ins|in\s+die|in\s+das|in\s+den|auf|auf\s+die|auf\s+das|nach))?|öffne(?:n)?|zeig(?:e)?(?:n)?(?:\s+mir)?|wechsel(?:n|e)?(?:\s+(?:zu|zum|zur|in|ins|in\s+die|in\s+das|in\s+den|auf|nach))?|wechsle(?:\s+(?:zu|zum|zur|in|ins|in\s+die|in\s+das|in\s+den|auf|nach))?|navigier(?:e|en)?(?:\s+(?:zu|zum|zur|in|ins|auf|nach))?|schalte(?:\s+(?:auf|zu|in))|spring(?:e|en)?(?:\s+(?:zu|zum|zur|in|ins|auf|nach))?|open|go\s+to|show(?:\s+me)?|switch\s+to|navigate\s+to|sektion|kategorie|ansicht)\s+"
        has_nav_prefix = bool(re.search(nav_prefix_regex, p_clean))
        candidate = re.sub(nav_prefix_regex, "", p_clean).strip()
        candidate = re.sub(r"^(?:das|die|der|dem|den|mir|uns)\s+", "", candidate).strip()
        candidate = re.sub(r"\s+(?:anzeigen|öffnen|oeffnen|sektion|ansicht|kategorie|dashboard|menü|menu|seite|bereich)$", "", candidate).strip()

        target_sec = None
        for sec_id, synonyms in SECTION_SYNONYMS.items():
            if candidate == sec_id or candidate in synonyms or p_clean == sec_id or p_clean in synonyms:
                target_sec = sec_id
                break

        if not target_sec and context and isinstance(context.get("visible_navigation"), list):
            for nav in context["visible_navigation"]:
                nid = str(nav.get("id", "")).lower()
                lbl = str(nav.get("label", "")).lower()
                if candidate and (candidate == nid or candidate in lbl or nid in candidate):
                    target_sec = nid
                    break

        if target_sec:
            title = CATEGORY_NAMES_MAP.get(target_sec, target_sec.upper())
            return {
                "success": True,
                "prompt": prompt,
                "message": f"Navigiere zur Sektion '{title}'.",
                "tool_call": {"name": "navigate_section", "arguments": {"target": target_sec}},
                "action": "navigate_section",
                "entity_id": f"section.{target_sec}",
                "ui_action": {"type": "navigate", "target": target_sec, "section": target_sec, "title": title},
                "confidence": 100.0,
                "error": None,
                "mode": mode
            }

        # Falls explizites Navigations-Präfix vorhanden war, aber kein Ziel passte:
        # Nicht an NeedleAgent/Home Assistant durchreichen, sondern deterministisch ablehnen
        if has_nav_prefix and candidate:
            return {
                "success": False,
                "prompt": prompt,
                "message": f"Sektion '{candidate}' nicht im LCARS Dashboard gefunden.",
                "tool_call": None,
                "action": "navigate_failed",
                "entity_id": None,
                "ui_action": None,
                "confidence": 0.0,
                "error": f"unknown_section: {candidate}",
                "mode": mode
            }

        return None

    def _process_cactus_prompt(prompt, mode="test", context=None):
        dry_run = (mode != "live")
        start_time = time.perf_counter()

        ui_res = resolve_ui_command(prompt, context=context, mode=mode)
        if ui_res:
            ui_res["latency_ms"] = round((time.perf_counter() - start_time) * 1000.0, 1)
            return ui_res

        agent = get_cactus_agent()
        if not agent:
            return {"error": "Cactus NeedleAgent konnte nicht geladen werden", "success": False, "mode": mode}
        try:
            res = agent.process_prompt(prompt, dry_run=dry_run)
            if not res.success and not prompt.lower().startswith("schalte ") and any(prompt.lower().endswith(w) for w in (" an", " aus", " ein", " ab")):
                res_retry = agent.process_prompt(f"Schalte {prompt}", dry_run=dry_run)
                if res_retry.success:
                    res = res_retry
            return {
                "success": res.success,
                "prompt": res.prompt,
                "message": res.message,
                "tool_call": res.tool_call,
                "entity_id": res.entity_id,
                "action": res.action,
                "confidence": round(res.confidence * 100, 1),
                "latency_ms": round(res.latency_ms, 1),
                "error": res.error,
                "mode": mode
            }
        except Exception as e:
            return {"error": str(e), "success": False, "mode": mode}

    @app.route("/api/voice/transcribe", methods=["POST"])
    @app.route("/api/cactus/transcribe", methods=["POST"])
    def api_voice_transcribe():
        if not _gemini_live_authorized():
            return jsonify({"error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)", "locked": True}), 403

        audio_bytes = None
        if "audio" in request.files:
            audio_bytes = request.files["audio"].read()
        elif "file" in request.files:
            audio_bytes = request.files["file"].read()
        else:
            audio_bytes = request.get_data()

        if not audio_bytes:
            return jsonify({"error": "Keine Audiodaten übermittelt", "success": False}), 400

        mode = request.form.get("mode") or request.args.get("mode") or "test"
        mode = mode.strip().lower()
        if mode not in ("test", "live"):
            mode = "test"

        process_param = request.form.get("process") or request.args.get("process") or "true"
        should_process = process_param.lower() in ("true", "1", "yes")

        lang = request.form.get("lang") or request.args.get("lang") or "de"
        prompt_hint = request.form.get("prompt_hint") or request.args.get("prompt_hint") or "LCARS Sprachsteuerung: Gehe zu Solar, System, Services, PulseCast, Einstellungen, Home Assistant, Nächste Seite, Vorherige Seite, Zurück, Vor, Aktualisieren, Vollbild."

        try:
            model = get_whisper_model()
            text = ""
            with WHISPER_LOCK:
                try:
                    import io
                    buf = io.BytesIO(audio_bytes)
                    segments, info = model.transcribe(buf, language=lang, beam_size=1, initial_prompt=prompt_hint)
                    text = " ".join([s.text for s in segments]).strip()
                except Exception:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".webm", delete=True) as tf:
                        tf.write(audio_bytes)
                        tf.flush()
                        segments, info = model.transcribe(tf.name, language=lang, beam_size=1, initial_prompt=prompt_hint)
                        text = " ".join([s.text for s in segments]).strip()

            if not text:
                return jsonify({
                    "success": True,
                    "text": "",
                    "recognized": False,
                    "message": "Keine Sprache erkannt.",
                    "mode": mode
                })

            if not should_process:
                return jsonify({
                    "success": True,
                    "text": text,
                    "recognized": True,
                    "mode": mode
                })

            context = None
            raw_ctx = request.form.get("context") or request.args.get("context")
            if raw_ctx:
                try:
                    context = json.loads(raw_ctx)
                except Exception:
                    context = None

            result = _process_cactus_prompt(text, mode=mode, context=context)
            result["text"] = text
            result["recognized"] = True
            return jsonify(result)

        except Exception as e:
            return jsonify({"error": f"Transkriptionsfehler: {str(e)}", "success": False, "mode": mode}), 500

    @app.route("/api/cactus/process", methods=["POST"])
    def api_cactus_process():
        if not _gemini_live_authorized():
            return jsonify({"error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)", "locked": True}), 403
        data = request.get_json(silent=True) or {}
        prompt = data.get("prompt", "").strip()
        if not prompt:
            return jsonify({"error": "Kein Prompt angegeben"}), 400
        mode = data.get("mode", "test").strip().lower()
        if mode not in ("test", "live"):
            mode = "test"
        context = data.get("context")
        result = _process_cactus_prompt(prompt, mode=mode, context=context)
        if not result.get("success") and "nicht geladen" in result.get("error", ""):
            return jsonify(result), 500
        return jsonify(result)

    @app.route("/api/cactus/tools", methods=["GET"])
    def api_cactus_tools():
        return jsonify({
            "tools": CACTUS_UI_TOOLS + [
                {
                    "name": "control_light",
                    "description": "Steuert Lichter und Lampen im Home Assistant.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Name der Lampe oder des Raums"},
                            "action": {"type": "string", "enum": ["on", "off", "dim"], "description": "Schaltaktion"},
                            "brightness": {"type": "integer", "description": "Helligkeit in Prozent (0-100)"}
                        },
                        "required": ["name", "action"]
                    }
                },
                {
                    "name": "get_weather",
                    "description": "Ruft Wetter- und Temperaturinformationen ab.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "Standort oder Stadt"},
                            "query_type": {"type": "string", "enum": ["all", "temperature", "rain", "wind"], "description": "Art der Wetterabfrage"}
                        }
                    }
                }
            ]
        })

    @app.route("/api/cactus/lights", methods=["GET"])
    def api_cactus_lights():
        lights = [
            {"entity_id": "light.decke1", "friendly_name": "Decke 1"},
            {"entity_id": "light.decke2", "friendly_name": "Decke 2"},
            {"entity_id": "light.decke3", "friendly_name": "Decke 3"},
            {"entity_id": "light.bodenlampe", "friendly_name": "Bodenlampe"},
            {"entity_id": "light.schlafzimmer", "friendly_name": "Schlafzimmer"},
            {"entity_id": "light.schlafzimmer_decke", "friendly_name": "Schlafzimmer Decke"},
            {"entity_id": "light.kuche", "friendly_name": "Küche"},
            {"entity_id": "light.flur_oben", "friendly_name": "Flur oben"},
            {"entity_id": "light.schranklampe", "friendly_name": "Schranklampe"},
            {"entity_id": "light.esstisch", "friendly_name": "Esstisch"},
            {"entity_id": "light.leto_bett", "friendly_name": "Leto Bett"},
            {"entity_id": "light.letos_led_leiste", "friendly_name": "Letos LED Leiste"},
            {"entity_id": "light.licht_1", "friendly_name": "Licht 1"},
            {"entity_id": "light.licht_10", "friendly_name": "Licht 10"},
            {"entity_id": "light.licht_11", "friendly_name": "Licht 11"},
            {"entity_id": "light.all", "friendly_name": "Alle Lichter"},
        ]
        return jsonify(lights)

    @app.route("/api/gemini-live/status", methods=["GET"])
    @app.route("/api/cactus/status", methods=["GET"])
    def api_gemini_live_status():
        is_locked = False
        if permissions_service:
            with permissions_service.lock:
                is_locked = "gemini_live" in permissions_service.locked_sections
        return jsonify({
            "configured": True,
            "model": "Cactus Needle 3 (On-Device)",
            "stt": "Faster-Whisper base (CPU int8)",
            "tools": [
                "control_light",
                "get_weather",
                "navigate_section",
                "paginate",
                "ui_action"
            ],
            "locked": is_locked
        })

    @app.route("/api/voice/stream", methods=["GET"])
    @app.route("/api/audio/live", methods=["GET"])
    def api_voice_stream():
        if not _gemini_live_authorized():
            return jsonify({"error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)", "locked": True}), 403

        source = request.args.get("source", "default").strip() or "default"
        fmt = request.args.get("format", "mp3").strip().lower()

        # Validate pulse source if specific source requested
        if source != "default":
            try:
                res = subprocess.run(["pactl", "list", "sources", "short"], capture_output=True, text=True, timeout=2)
                available_sources = [line.split()[1] for line in res.stdout.strip().splitlines() if len(line.split()) >= 2]
                if source not in available_sources:
                    return jsonify({"error": f"Audio-Quelle '{source}' nicht gefunden", "success": False}), 503
            except Exception as e:
                return jsonify({"error": f"Audio-Subsystem Prüffehler: {e}", "success": False}), 503

        if fmt in ("ogg", "opus"):
            content_type = "audio/ogg"
            ffmpeg_fmt = ["-c:a", "libopus", "-b:a", "64k", "-page_duration", "200000", "-f", "ogg"]
        else:
            content_type = "audio/mpeg"
            ffmpeg_fmt = ["-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"]

        resp_headers = {
            "Content-Type": content_type,
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "close"
        }
        if request.method == "HEAD":
            return Response(b"", headers=resp_headers)

        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-f", "pulse",
            "-i", source,
            "-vn",
        ] + ffmpeg_fmt + ["-flush_packets", "1", "pipe:1"]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=10 * 1024
            )
        except Exception as e:
            return jsonify({"error": f"FFmpeg Startfehler: {e}", "success": False}), 500

        # Wait briefly to detect immediate exit
        time.sleep(0.1)
        if proc.poll() is not None:
            err = proc.stderr.read().decode("utf-8", errors="ignore").strip() if proc.stderr else ""
            if proc.stdout:
                try: proc.stdout.close()
                except Exception: pass
            if proc.stderr:
                try: proc.stderr.close()
                except Exception: pass
            return jsonify({"error": f"Audio-Quelle nicht verfügbar: {err or 'Unbekannter Fehler'}", "success": False}), 503

        class AudioStreamer:
            def __init__(self, p):
                self.proc = p
                self._closed = False
            def __iter__(self):
                try:
                    while True:
                        if not self.proc.stdout:
                            break
                        read_fn = getattr(self.proc.stdout, "read1", self.proc.stdout.read)
                        chunk = read_fn(4096)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    self.close()
            def close(self):
                if self._closed:
                    return
                self._closed = True
                try:
                    if self.proc.poll() is None:
                        self.proc.terminate()
                        try:
                            self.proc.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            self.proc.kill()
                            self.proc.wait(timeout=1.0)
                except Exception:
                    pass
                if self.proc.stdout:
                    try: self.proc.stdout.close()
                    except Exception: pass
                if self.proc.stderr:
                    try: self.proc.stderr.close()
                    except Exception: pass
            def __del__(self):
                self.close()

        return Response(stream_with_context(iter(AudioStreamer(proc))), headers=resp_headers)

    if USE_SOCK and sock:
        @sock.route("/api/gemini-live/ws")
        def api_gemini_live_ws(ws):
            if not _gemini_live_authorized():
                try:
                    ws.send(json.dumps({
                        "type": "error",
                        "error": "Zugriff verweigert (Keine Berechtigung für diesen Bereich)",
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

    @app.route("/devteam/report", methods=["GET"])
    def devteam_report_form():
        if not _is_dashboard_authenticated():
            return redirect("/login")
        return render_template("devteam-report.html")

    @app.route("/api/devteam/report", methods=["POST"])
    def api_devteam_report_issue():
        if not _is_section_authorized("devteam"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Dev-Team nicht berechtigt"}), 403
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"success": False, "error": "Issue-Titel ist erforderlich"}), 400
        body = data.get("body") or ""
        component = (data.get("component") or "general").strip().lower()
        urgency = (data.get("urgency") or "medium").strip().lower()
        
        # Prefix title for bug reports
        prefixed_title = f"[BUG] {title}"
        
        # Construct detailed body with metadata
        detailed_body = f"""{body}

---
**Komponente:** {component}
**Dringlichkeit:** {urgency}
**Gemeldet von:** {request.headers.get('User-Agent', 'Unknown')}
**Zeitstempel:** {datetime.datetime.now().isoformat()}
"""
        
        assignee = "coder"
        
        hermes_bin = shutil.which("hermes") or "/home/cb/.local/share/mise/installs/pipx-hermes-agent/0.19.0/hermes-agent/bin/hermes"
        cmd = [hermes_bin, "kanban", "--board", "dev-team", "create", prefixed_title,
               "--body", detailed_body, "--assignee", assignee, "--priority", "1" if urgency == "high" else "0"]
        
        try:
            env = os.environ.copy()
            env["HERMES_KANBAN_BOARD"] = "dev-team"
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
            if res.returncode == 0:
                return jsonify({"success": True, "output": res.stdout.strip()})
            else:
                return jsonify({"success": False, "error": res.stderr.strip() or res.stdout.strip() or "Fehler beim Erstellen des Issues"}), 500
        except Exception as e:
            print(f"[WARN] api_devteam_report_issue Fehler: {e}", file=sys.stderr)
            return jsonify({"success": False, "error": str(e)}), 500

    # -----------------------------------------------------------------------
    # DEV-TEAM Kanban Board Endpoints
    # -----------------------------------------------------------------------
    DEVTEAM_DB_PATH = "/home/cb/.hermes/kanban/boards/dev-team/kanban.db"

    @app.route("/api/devteam/tasks", methods=["GET"])
    def api_devteam_tasks():
        if not _is_section_authorized("devteam"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Dev-Team nicht berechtigt"}), 403
        if not os.path.exists(DEVTEAM_DB_PATH):
            return jsonify([])
        try:
            conn = sqlite3.connect(f"file:{DEVTEAM_DB_PATH}?mode=ro", uri=True, timeout=2)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, title, status, assignee, "
                "COALESCE(workspace_path, workspace_kind, '') AS workspace, "
                "created_at, started_at, completed_at, body "
                "FROM tasks WHERE status != 'archived' "
                "ORDER BY created_at DESC"
            ).fetchall()
            tasks = []
            for r in rows:
                b = r["body"] or ""
                tasks.append({
                    "id": r["id"],
                    "title": r["title"],
                    "status": r["status"],
                    "assignee": r["assignee"] or "",
                    "workspace": r["workspace"] or "",
                    "created_at": r["created_at"],
                    "started_at": r["started_at"],
                    "completed_at": r["completed_at"],
                    "body": b[:200]
                })
            conn.close()
            return jsonify(tasks)
        except Exception as e:
            print(f"[WARN] api_devteam_tasks Fehler: {e}", file=sys.stderr)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/devteam/stats", methods=["GET"])
    def api_devteam_stats():
        if not _is_section_authorized("devteam"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Dev-Team nicht berechtigt"}), 403
        if not os.path.exists(DEVTEAM_DB_PATH):
            return jsonify({
                "by_status": {},
                "by_assignee": {},
                "total": 0,
                "running": 0,
                "blocked": 0,
                "done": 0,
                "ready": 0,
                "triage": 0
            })
        try:
            conn = sqlite3.connect(f"file:{DEVTEAM_DB_PATH}?mode=ro", uri=True, timeout=2)
            conn.row_factory = sqlite3.Row

            by_status = {}
            for r in conn.execute("SELECT status, COUNT(*) AS n FROM tasks WHERE status != 'archived' GROUP BY status"):
                by_status[r["status"]] = int(r["n"])

            by_assignee = {}
            for r in conn.execute("SELECT assignee, status, COUNT(*) AS n FROM tasks WHERE status != 'archived' AND assignee IS NOT NULL GROUP BY assignee, status"):
                by_assignee.setdefault(r["assignee"], {})[r["status"]] = int(r["n"])

            conn.close()
            total = sum(by_status.values())
            return jsonify({
                "by_status": by_status,
                "by_assignee": by_assignee,
                "total": total,
                "running": by_status.get("running", 0),
                "blocked": by_status.get("blocked", 0),
                "done": by_status.get("done", 0),
                "ready": by_status.get("ready", 0),
                "triage": by_status.get("triage", 0)
            })
        except Exception as e:
            print(f"[WARN] api_devteam_stats Fehler: {e}", file=sys.stderr)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/devteam/task/<task_id>", methods=["GET"])
    def api_devteam_task_detail(task_id):
        if not _is_section_authorized("devteam"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Dev-Team nicht berechtigt"}), 403
        if not os.path.exists(DEVTEAM_DB_PATH):
            return jsonify({"error": "Datenbank nicht gefunden"}), 404
        try:
            conn = sqlite3.connect(f"file:{DEVTEAM_DB_PATH}?mode=ro", uri=True, timeout=2)
            conn.row_factory = sqlite3.Row

            t_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not t_row:
                conn.close()
                return jsonify({"error": "Task nicht gefunden"}), 404

            comments = [dict(c) for c in conn.execute(
                "SELECT id, author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,)
            ).fetchall()]

            events = [dict(e) for e in conn.execute(
                "SELECT id, run_id, kind, payload, created_at FROM task_events WHERE task_id = ? ORDER BY created_at DESC LIMIT 10",
                (task_id,)
            ).fetchall()]

            conn.close()
            task_data = dict(t_row)
            task_data["workspace"] = task_data.get("workspace_path") or task_data.get("workspace_kind") or ""
            task_data["comments"] = comments
            task_data["events"] = events
            return jsonify(task_data)
        except Exception as e:
            print(f"[WARN] api_devteam_task_detail Fehler: {e}", file=sys.stderr)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/devteam/task", methods=["POST"])
    def api_devteam_create_task():
        if not _is_section_authorized("devteam"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Dev-Team nicht berechtigt"}), 403
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"success": False, "error": "Task-Titel ist erforderlich"}), 400
        body = data.get("body") or ""
        assignee = (data.get("assignee") or "").strip()

        hermes_bin = shutil.which("hermes") or "/home/cb/.local/share/mise/installs/pipx-hermes-agent/0.19.0/hermes-agent/bin/hermes"
        cmd = [hermes_bin, "kanban", "--board", "dev-team", "create", title]
        if body:
            cmd.extend(["--body", body])
        if assignee:
            cmd.extend(["--assignee", assignee])

        try:
            env = os.environ.copy()
            env["HERMES_KANBAN_BOARD"] = "dev-team"
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
            if res.returncode == 0:
                return jsonify({"success": True, "output": res.stdout.strip()})
            else:
                return jsonify({"success": False, "error": res.stderr.strip() or res.stdout.strip() or "Fehler beim Erstellen des Tasks"}), 500
        except Exception as e:
            print(f"[WARN] api_devteam_create_task Fehler: {e}", file=sys.stderr)
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/devteam/dispatch", methods=["POST"])
    def api_devteam_dispatch():
        if not _is_section_authorized("devteam"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Dev-Team nicht berechtigt"}), 403
        hermes_bin = shutil.which("hermes") or "/home/cb/.local/share/mise/installs/pipx-hermes-agent/0.19.0/hermes-agent/bin/hermes"
        cmd = [hermes_bin, "kanban", "--board", "dev-team", "dispatch"]
        try:
            env = os.environ.copy()
            env["HERMES_KANBAN_BOARD"] = "dev-team"
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=45, env=env)
            if res.returncode == 0:
                return jsonify({"success": True, "output": res.stdout.strip()})
            else:
                return jsonify({"success": False, "error": res.stderr.strip() or res.stdout.strip() or "Fehler beim Ausführen von Dispatch"}), 500
        except Exception as e:
            print(f"[WARN] api_devteam_dispatch Fehler: {e}", file=sys.stderr)
            return jsonify({"success": False, "error": str(e)}), 500

    # -----------------------------------------------------------------------
    # LCARS Knowledge Base Endpoints
    # -----------------------------------------------------------------------
    @app.route("/api/knowledge", methods=["GET"])
    def api_knowledge_list():
        if not _is_section_authorized("knowledge"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Wissen nicht berechtigt"}), 403
        if not knowledge_service:
            return jsonify([])
        category = request.args.get("category")
        tag = request.args.get("tag")
        query = request.args.get("q") or request.args.get("query")
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        articles = knowledge_service.list_articles(category=category, tag=tag, query=query, limit=limit, offset=offset)
        return jsonify(articles)

    @app.route("/api/knowledge/stats", methods=["GET"])
    def api_knowledge_stats():
        if not _is_section_authorized("knowledge"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Wissen nicht berechtigt"}), 403
        if not knowledge_service:
            return jsonify({"total": 0, "by_category": {}})
        return jsonify(knowledge_service.get_stats())

    @app.route("/api/knowledge/<article_id>", methods=["GET"])
    def api_knowledge_detail(article_id):
        if not _is_section_authorized("knowledge"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Wissen nicht berechtigt"}), 403
        if not knowledge_service:
            return jsonify({"error": "Knowledge Service nicht verfügbar"}), 503
        article = knowledge_service.get_article(article_id)
        if not article:
            return jsonify({"error": "Wissensartikel nicht gefunden"}), 404
        return jsonify(article)

    @app.route("/api/knowledge", methods=["POST"])
    def api_knowledge_create():
        if not _is_section_authorized("knowledge"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Wissen nicht berechtigt"}), 403
        if not knowledge_service:
            return jsonify({"success": False, "error": "Knowledge Service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"success": False, "error": "Titel ist erforderlich"}), 400
        content = (data.get("content") or "").strip()
        if not content:
            return jsonify({"success": False, "error": "Inhalt ist erforderlich"}), 400
        category = (data.get("category") or "allgemein").strip().lower()
        tags = data.get("tags") or []
        user = _get_current_user()
        author = data.get("author") or (user.get("username") if user else "bot") or "bot"
        summary = (data.get("summary") or "").strip()
        metadata = data.get("metadata") or {}
        article_id = data.get("id")

        try:
            art = knowledge_service.create_article(
                title=title,
                content=content,
                category=category,
                tags=tags,
                author=author,
                summary=summary,
                metadata=metadata,
                article_id=article_id,
            )
            return jsonify({"success": True, "article": art}), 201
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/knowledge/<article_id>", methods=["PUT"])
    def api_knowledge_update(article_id):
        if not _is_section_authorized("knowledge"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Wissen nicht berechtigt"}), 403
        if not knowledge_service:
            return jsonify({"success": False, "error": "Knowledge Service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        art = knowledge_service.update_article(
            article_id=article_id,
            title=data.get("title"),
            content=data.get("content"),
            category=data.get("category"),
            tags=data.get("tags"),
            author=data.get("author"),
            summary=data.get("summary"),
            metadata=data.get("metadata"),
        )
        if not art:
            return jsonify({"success": False, "error": "Wissensartikel nicht gefunden"}), 404
        return jsonify({"success": True, "article": art})

    @app.route("/api/knowledge/<article_id>", methods=["DELETE"])
    def api_knowledge_delete(article_id):
        if not _is_section_authorized("knowledge"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Wissen nicht berechtigt"}), 403
        if not knowledge_service:
            return jsonify({"success": False, "error": "Knowledge Service nicht verfügbar"}), 503
        deleted = knowledge_service.delete_article(article_id)
        if not deleted:
            return jsonify({"success": False, "error": "Wissensartikel nicht gefunden"}), 404
        return jsonify({"success": True, "deleted": article_id})

    # -----------------------------------------------------------------------
    # LCARS Research Endpoints
    # -----------------------------------------------------------------------
    @app.route("/api/research", methods=["GET"])
    def api_research_list():
        if not _is_section_authorized("research"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Research nicht berechtigt"}), 403
        if not research_service:
            return jsonify([])
        status = request.args.get("status")
        tag = request.args.get("tag")
        query = request.args.get("q") or request.args.get("query")
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        reports = research_service.list_reports(status=status, tag=tag, query=query, limit=limit, offset=offset)
        return jsonify(reports)

    @app.route("/api/research/stats", methods=["GET"])
    def api_research_stats():
        if not _is_section_authorized("research"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Research nicht berechtigt"}), 403
        if not research_service:
            return jsonify({"total": 0, "by_status": {}})
        return jsonify(research_service.get_stats())

    @app.route("/api/research/<report_id>", methods=["GET"])
    def api_research_detail(report_id):
        if not _is_section_authorized("research"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Research nicht berechtigt"}), 403
        if not research_service:
            return jsonify({"error": "Research Service nicht verfügbar"}), 503
        report = research_service.get_report(report_id)
        if not report:
            return jsonify({"error": "Recherche-Bericht nicht gefunden"}), 404
        return jsonify(report)

    @app.route("/api/research", methods=["POST"])
    def api_research_create():
        if not _is_section_authorized("research"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Research nicht berechtigt"}), 403
        if not research_service:
            return jsonify({"success": False, "error": "Research Service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"success": False, "error": "Titel ist erforderlich"}), 400

        action = (data.get("action") or "").strip().lower()
        if action == "trigger":
            # Start new assignment to researcher bot & kanban
            topic = (data.get("topic") or "").strip()
            tags = data.get("tags") or []
            notes = (data.get("notes") or "").strip()
            rep = research_service.trigger_research_task(title=title, topic=topic, tags=tags, notes=notes)
            return jsonify({"success": True, "report": rep}), 201

        # Regular creation or automated report push by researcher bot
        content = (data.get("content") or "").strip()
        status = (data.get("status") or "completed").strip().lower()
        topic = (data.get("topic") or "").strip()
        tags = data.get("tags") or []
        user = _get_current_user()
        author = data.get("author") or (user.get("username") if user else "researcher") or "researcher"
        summary = (data.get("summary") or "").strip()
        key_takeaways = data.get("key_takeaways") or []
        sources = data.get("sources") or []
        structured_data = data.get("structured_data") or {}
        report_id = data.get("id")

        try:
            rep = research_service.create_report(
                title=title,
                content=content,
                status=status,
                topic=topic,
                tags=tags,
                author=author,
                summary=summary,
                key_takeaways=key_takeaways,
                sources=sources,
                structured_data=structured_data,
                report_id=report_id,
            )
            return jsonify({"success": True, "report": rep}), 201
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/research/<report_id>", methods=["PUT"])
    def api_research_update(report_id):
        if not _is_section_authorized("research"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Research nicht berechtigt"}), 403
        if not research_service:
            return jsonify({"success": False, "error": "Research Service nicht verfügbar"}), 503
        data = request.get_json(silent=True) or {}
        rep = research_service.update_report(
            report_id=report_id,
            title=data.get("title"),
            content=data.get("content"),
            status=data.get("status"),
            topic=data.get("topic"),
            tags=data.get("tags"),
            author=data.get("author"),
            summary=data.get("summary"),
            key_takeaways=data.get("key_takeaways"),
            sources=data.get("sources"),
            structured_data=data.get("structured_data"),
        )
        if not rep:
            return jsonify({"success": False, "error": "Recherche-Bericht nicht gefunden"}), 404
        return jsonify({"success": True, "report": rep})

    @app.route("/api/research/<report_id>", methods=["DELETE"])
    def api_research_delete(report_id):
        if not _is_section_authorized("research"):
            return jsonify({"success": False, "error": "LCARS Zugriff verweigert: Sektion Research nicht berechtigt"}), 403
        if not research_service:
            return jsonify({"success": False, "error": "Research Service nicht verfügbar"}), 503
        deleted = research_service.delete_report(report_id)
        if not deleted:
            return jsonify({"success": False, "error": "Recherche-Bericht nicht gefunden"}), 404
        return jsonify({"success": True, "deleted": report_id})

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
                data = json.dumps({"configured": True, "model": "Cactus Needle 3 (On-Device)", "locked": is_locked}).encode("utf-8")
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
                            flash_enabled=data.get("flash_enabled"),
                            interval_hours=data.get("interval_hours") or data.get("ai_check_interval_hours") or data.get("interval")
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
