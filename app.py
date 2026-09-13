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
    from flask import Flask, jsonify, render_template_string, request, send_from_directory
    USE_FLASK = True
except ImportError:
    print("[INFO] Flask nicht installiert, verwende Python Standardbibliothek (http.server).")
    USE_FLASK = False
    from http.server import HTTPServer, BaseHTTPRequestHandler

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
        "tailscale_url": "http://pimmel.tail3a782b.ts.net:8123",
        "lan_url": "http://192.168.31.210:8123",
    },
    "postgres": {
        "name": "PostgreSQL 17",
        "title": "PostgreSQL 17",
        "icon": "🗄️",
        "port": 5432,
        "description": "PostgreSQL Datenbank (nur localhost)",
        "allow_external": False,
        "cf_key": None,
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


def get_throttled_status():
    try:
        out = subprocess.check_output(["vcgencmd", "get_throttled"], stderr=subprocess.DEVNULL, timeout=1).decode("utf-8")
        m = re.search(r"throttled=(0x[0-9a-fA-F]+)", out)
        if m:
            val_hex = m.group(1)
            val = int(val_hex, 16)
            is_active = bool(val & 0x1)
            return is_active, val_hex
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


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True, timeout=1).strip()
        for ip_part in out.split():
            if ip_part.startswith("192.168.") or ip_part.startswith("10.") or ip_part.startswith("172."):
                return ip_part
    except Exception:
        pass
    return "192.168.31.210"



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


def get_cloudflared_urls():
    urls = {"dashboard": None, "xdcc": None}
    dash_file = "/home/yash/.cloudflared-urls/dash.url"
    xdcc_file = "/home/yash/.cloudflared-urls/xdcc.url"
    try:
        if os.path.exists(dash_file):
            with open(dash_file, "r") as f:
                val = f.read().strip()
                if val.startswith("https://"):
                    urls["dashboard"] = val
    except Exception:
        pass
    try:
        if os.path.exists(xdcc_file):
            with open(xdcc_file, "r") as f:
                val = f.read().strip()
                if val.startswith("https://"):
                    urls["xdcc"] = val
    except Exception:
        pass
    return urls


_service_status_cache = {}
_service_status_lock = threading.Lock()


def check_service_status(port=8000, host="127.0.0.1", timeout=0.25, max_age=4.0):
    now = time.time()
    cache_key = (host, port)
    with _service_status_lock:
        cached = _service_status_cache.get(cache_key)
        if cached and (now - cached["timestamp"] < max_age):
            return cached["online"]

    online = False
    try:
        req = urllib.request.Request(f"http://{host}:{port}/", method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            online = resp.status < 500
    except urllib.error.HTTPError:
        online = True
    except Exception:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                online = True
        except Exception:
            online = False

    with _service_status_lock:
        _service_status_cache[cache_key] = {"online": online, "timestamp": now}
    return online


# ---------------------------------------------------------------------------
# Automatischer Cloudflared-Quick-Tunnel-Manager
# ---------------------------------------------------------------------------
class CloudflaredTunnelManager:
    """Verwaltet Cloudflared-Quick-Tunnels für entdeckte Webdienste.
    Legt automatisch einen trycloudflare.com Tunnel an, falls ein Dienst noch keinen hat.
    """
    def __init__(self, binary_path="/home/yash/bin/cloudflared", url_dir="/home/yash/.cloudflared-urls"):
        self.binary_path = binary_path if (os.path.isfile(binary_path) and os.access(binary_path, os.X_OK)) else (shutil.which("cloudflared") or "cloudflared")
        self.url_dir = url_dir
        os.makedirs(self.url_dir, exist_ok=True)
        self._tunnels = {}  # port -> {"proc": Popen, "log": str, "url": str, "created_at": float}
        self._lock = threading.Lock()
        self._running = True
        self._watcher_thread = threading.Thread(target=self._watcher_loop, daemon=True)
        self._watcher_thread.start()

    def is_external_process_running(self, port: int) -> bool:
        if not psutil:
            return False
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                cmd = proc.info.get("cmdline") or []
                cmd_str = " ".join(cmd)
                if "cloudflared" in cmd_str and "--url" in cmd_str:
                    m_port = re.search(r"--url\s+https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d+)", cmd_str)
                    if m_port and int(m_port.group(1)) == port:
                        return True
        except Exception:
            pass
        return False

    def is_tunnel_alive(self, port: int) -> bool:
        with self._lock:
            t = self._tunnels.get(port)
            if t and t.get("proc") and t["proc"].poll() is None:
                return True
        return self.is_external_process_running(port)

    def get_url(self, port: int) -> str | None:
        with self._lock:
            t = self._tunnels.get(port)
            if t and t.get("url"):
                return t["url"]

        # Nur wenn nachweislich ein Prozess läuft, URL-Dateien oder Logs auswerten
        if self.is_tunnel_alive(port):
            patterns = [f"port_{port}.url", f"{port}.url"]
            if port == 5000:
                patterns.extend(["dash.url", "dashboard.url"])
            elif port == 3000:
                patterns.append("xdcc.url")
            elif port == 8000:
                patterns.append("telemetry.url")

            for pattern in patterns:
                p_file = os.path.join(self.url_dir, pattern)
                if os.path.isfile(p_file):
                    try:
                        with open(p_file, "r", encoding="utf-8") as f:
                            u = f.read().strip()
                            if u.startswith("https://"):
                                with self._lock:
                                    if port not in self._tunnels:
                                        self._tunnels[port] = {"proc": None, "log": f"/tmp/cloudflared_{port}.log", "url": u, "created_at": time.time()}
                                return u
                    except Exception:
                        pass

            log_candidates = [f"/tmp/cloudflared_{port}.log"]
            if port == 5000:
                log_candidates.append("/tmp/cloudflared_dash.log")
            elif port == 3000:
                log_candidates.append("/tmp/cloudflared_xdcc.log")

            for log_file in log_candidates:
                if os.path.isfile(log_file):
                    try:
                        with open(log_file, "r", errors="ignore") as f:
                            urls = re.findall(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com", f.read())
                            if urls:
                                u = urls[-1]
                                with self._lock:
                                    if port in self._tunnels:
                                        self._tunnels[port]["url"] = u
                                    else:
                                        self._tunnels[port] = {"proc": None, "log": log_file, "url": u, "created_at": time.time()}
                                return u
                    except Exception:
                        pass
        return None

    def get_all_urls(self) -> dict:
        result = {}
        with self._lock:
            for p, t in self._tunnels.items():
                if t.get("url"):
                    result[p] = t["url"]
        return result

    def ensure_tunnel(self, port: int):
        if port in (22, 111, 5432) or port >= 32768:
            return None

        # 1. Haben wir einen eigenen aktiven Prozess?
        with self._lock:
            if port in self._tunnels:
                t = self._tunnels[port]
                proc = t.get("proc")
                if proc and proc.poll() is None:
                    return t.get("url")
                else:
                    # Prozess gestorben, verwerfen
                    del self._tunnels[port]

        # 2. Läuft ein externer Prozess (z.B. port 5000, 3000)?
        if self.is_external_process_running(port):
            return self.get_url(port)

        # 3. Keine laufenden Prozesse: Stale URL-Dateien aufräumen
        for pattern in [f"port_{port}.url", f"{port}.url"]:
            p_file = os.path.join(self.url_dir, pattern)
            if os.path.isfile(p_file):
                try:
                    os.remove(p_file)
                except Exception:
                    pass

        log_file = f"/tmp/cloudflared_{port}.log"
        if os.path.isfile(log_file):
            try:
                os.remove(log_file)
            except Exception:
                pass

        # 4. Neuen Quick-Tunnel starten
        try:
            cmd = [
                self.binary_path,
                "tunnel",
                "--url", f"http://127.0.0.1:{port}",
                "--logfile", log_file,
                "--no-autoupdate",
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with self._lock:
                self._tunnels[port] = {
                    "proc": proc,
                    "log": log_file,
                    "url": None,
                    "created_at": time.time(),
                }
            print(f"[CLOUDFLARED AUTO] Neuer Quick-Tunnel für Port {port} gestartet (PID {proc.pid}).", flush=True)
        except Exception as e:
            print(f"[CLOUDFLARED AUTO] Fehler beim Starten für Port {port}: {e}", file=sys.stderr)

        return None

    def stop_tunnel(self, port: int):
        with self._lock:
            t = self._tunnels.pop(port, None)
        if t:
            proc = t.get("proc")
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        for pattern in [f"port_{port}.url", f"{port}.url"]:
            p_file = os.path.join(self.url_dir, pattern)
            if os.path.isfile(p_file):
                try:
                    os.remove(p_file)
                except Exception:
                    pass
        log_file = f"/tmp/cloudflared_{port}.log"
        if os.path.isfile(log_file):
            try:
                os.remove(log_file)
            except Exception:
                pass

    def _watcher_loop(self):
        while self._running:
            try:
                with self._lock:
                    items = list(self._tunnels.items())

                for p, t in items:
                    proc = t.get("proc")
                    if proc and proc.poll() is not None:
                        # Prozess unerwartet beendet
                        print(f"[CLOUDFLARED AUTO] Tunnel für Port {p} beendet (Exit-Code: {proc.returncode}).", file=sys.stderr)
                        with self._lock:
                            if p in self._tunnels and self._tunnels[p] is t:
                                del self._tunnels[p]
                        continue

                    if not t.get("url"):
                        log_file = t.get("log", f"/tmp/cloudflared_{p}.log")
                        if os.path.exists(log_file):
                            try:
                                with open(log_file, "r", errors="ignore") as f:
                                    matches = re.findall(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com", f.read())
                                    if matches:
                                        found_url = matches[-1]
                                        t["url"] = found_url
                                        print(f"[CLOUDFLARED AUTO] Port {p} Tunnel aktiv: {found_url}", flush=True)
                                        try:
                                            with open(os.path.join(self.url_dir, f"port_{p}.url"), "w", encoding="utf-8") as uf:
                                                uf.write(found_url + "\n")
                                        except Exception:
                                            pass
                            except Exception:
                                pass
            except Exception:
                pass
            time.sleep(1.0)

    def stop_all(self):
        self._running = False
        with self._lock:
            for p, t in self._tunnels.items():
                proc = t.get("proc")
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=1)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass


cf_tunnel_manager = CloudflaredTunnelManager()
atexit.register(cf_tunnel_manager.stop_all)


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
        cf_map = {}
        url_dir = "/home/yash/.cloudflared-urls"
        port_file_map = {
            "dash.url": 5000,
            "dashboard.url": 5000,
            "xdcc.url": 3000,
            "telemetry.url": 8000,
        }
        if os.path.exists(url_dir):
            try:
                for fname in os.listdir(url_dir):
                    fpath = os.path.join(url_dir, fname)
                    if os.path.isfile(fpath):
                        with open(fpath, "r", encoding="utf-8") as f:
                            u = f.read().strip()
                            if u.startswith("https://"):
                                p = port_file_map.get(fname.lower())
                                if not p:
                                    m_p = re.search(r"(\d+)", fname)
                                    if m_p:
                                        p = int(m_p.group(1))
                                if p:
                                    cf_map[p] = u
            except Exception:
                pass

        # URLs aus dem TunnelManager
        for p, u in cf_tunnel_manager.get_all_urls().items():
            if u:
                cf_map[p] = u

        for log_path, p in [("/tmp/cloudflared_dash.log", 5000), ("/tmp/cloudflared_xdcc.log", 3000)]:
            if p not in cf_map and os.path.exists(log_path):
                try:
                    with open(log_path, "r", errors="ignore") as f:
                        urls = re.findall(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com", f.read())
                        if urls:
                            cf_map[p] = urls[-1]
                except Exception:
                    pass

        if psutil:
            try:
                for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                    cmd = proc.info.get("cmdline") or []
                    cmd_str = " ".join(cmd)
                    if "cloudflared" in cmd_str and "--url" in cmd_str:
                        m_port = re.search(r"--url\s+https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d+)", cmd_str)
                        if m_port:
                            port = int(m_port.group(1))
                            m_log = re.search(r"--logfile\s+(\S+)", cmd_str)
                            if m_log and os.path.exists(m_log.group(1)) and port not in cf_map:
                                with open(m_log.group(1), "r", errors="ignore") as f:
                                    urls = re.findall(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com", f.read())
                                    if urls:
                                        cf_map[port] = urls[-1]
            except Exception:
                pass

        return cf_map

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

            # GRUNDSATZ: NIEMALS 127.0.0.1 anzeigen - immer netzwerkfähige Adressen
            lan_url = f"http://{lan_ip}:{port}"
            ts_url = f"http://{ts_host}:{port}"
            cf_url = cf_map.get(port)

            # Automatisch Cloudflared Tunnel anlegen, falls noch keiner existiert
            if not cf_url and port < 32768:
                cf_tunnel_manager.ensure_tunnel(port)
                cf_url = cf_tunnel_manager.get_url(port)

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
            t_end = time.time() + 6.0
            while time.time() < t_end:
                time.sleep(0.5)
                resolved = True
                for d in pending:
                    if not d.get("cloudflared_url"):
                        u = cf_tunnel_manager.get_url(d["port"])
                        if u:
                            d["cloudflared_url"] = u
                            for reg_key, reg_val in SERVICE_REGISTRY.items():
                                if reg_val["port"] == d["port"]:
                                    reg_val["cf_url"] = u
                        else:
                            resolved = False
                if resolved:
                    break

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
            cf_map = self.get_cloudflared_map()
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
        db_paths = glob.glob("/home/yash/.hermes/state.db") + glob.glob("/home/yash/.hermes/profiles/*/state.db")
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
        env_path = "/home/yash/.hermes/.env"
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
def get_antigravity_status():
    oauth_path = "/home/yash/.gemini/antigravity-cli/antigravity-oauth-token"
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
    for p in glob.glob("/home/yash/.gemini/antigravity-cli/conversations/*.db"):
        fname = os.path.basename(p)
        if not fname.endswith("-shm") and not fname.endswith("-wal"):
            session_ids.add(fname[:-3])
            try:
                mtimes.append(os.path.getmtime(p))
            except Exception:
                pass

    for p in glob.glob("/home/yash/.gemini/antigravity-cli/brain/*"):
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
    return {
        "status": "active" if token_present else "inactive",
        "badge": badge,
        "auth_method": auth_method,
        "account_label": "Google Consumer / Free Quota" if auth_method == "consumer" else auth_method.capitalize(),
        "sessions_count": len(session_ids),
        "latest_activity": latest_str,
        "quota": "Unbegrenzte Chat-/Agenten-Quota (Gemini Flash & Pro)",
        "token_valid": token_present,
    }


# ---------------------------------------------------------------------------
# 9Router SQLite Telemetrie & Verbrauchs-Statistiken
# ---------------------------------------------------------------------------
def get_9router_stats():
    """Liest Nutzungs-, Telemetrie- und Verbindungsdaten aus ~/.9router/db/data.sqlite."""
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

            # Ersparnis durch Context/Prompt Caching ermitteln
            uncached_cost = 0.0
            try:
                c.execute("SELECT model, promptTokens, completionTokens, cost, tokens FROM usageHistory;")
                history_all = c.fetchall()
                for u_model, u_pt, u_ct, u_c, u_tok in history_all:
                    u_pt = u_pt or 0
                    u_ct = u_ct or 0
                    rate = 0.0000005
                    if "high" in (u_model or "").lower():
                        rate = 0.0000025
                    comp_rate = 0.000003
                    if "high" in (u_model or "").lower():
                        comp_rate = 0.000010
                    uncached_cost += (u_pt * rate + u_ct * comp_rate)
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

            return {
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
def get_system_stats():
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
    stats["history_samples"] = history_store.get_samples(range_seconds=3600) if "history_store" in globals() else []
    stats["stardate"] = calculate_stardate()
    stats["alerts"] = alert_monitor.get_status() if "alert_monitor" in globals() else {"active": False, "reasons": []}

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
            time.sleep(3)

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

    hermes_bin = "/home/yash/.local/bin/hermes"
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

    python_bin = "/home/yash/.hermes/hermes-agent/venv/bin/python"
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
    .pill-ha { background-color: var(--c-secondary); }

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
        <div class="banner-stardate">
          <span>STARDATE:</span>
          <span id="stardateValue" style="font-weight:700;">{{ stats.stardate or '--------.-' }}</span>
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
        <button class="lcars-pill-btn pill-ha" onclick="switchCategory('homeassistant')" id="btn-cat-homeassistant" style="display: none;">
          HOME ASSISTANT
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

              <!-- Eingabebereich mit LCARS Send-Button -->
              <form id="lcarsChatForm" onsubmit="handleChatSubmit(event)" style="margin-top: 0.75rem; width: 100%; min-width: 0;">
                <div class="lcars-chat-input-row">
                  <input
                    type="text"
                    id="lcarsChatInput"
                    class="lcars-chat-input"
                    placeholder="BEFEHL AN 9ROUTER AGENTEN EINGEBEN..."
                    autocomplete="off"
                    required
                  />
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
                  <div class="ide-param-val">/home/yash/Projects/agydashboard</div>
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
                  <input type="text" id="cfgHaName" value="Home Assistant" class="lcars-input" placeholder="Home Assistant" required>
                  <div style="font-size:0.75rem; color:#888; margin-top:0.25rem;">z.B. Home Assistant, Smart Home, Quartier</div>
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
            <div style="margin-left:auto; display:flex; align-items:center;">
              <span id="fantasyCountdownBadge" style="font-size:0.8rem; color:#44dd88; background:rgba(68,221,136,0.15); border:1px solid rgba(68,221,136,0.4); padding:0.25rem 0.75rem; border-radius:12px; font-family:var(--mono-family); letter-spacing:0.04em;">
                ● REFRESH IN 30S
              </span>
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

  // Sound Engine (Web Audio API Synthesizer)
  let audioContext = null;
  let soundEnabled = (localStorage.getItem('lcars-sound') !== 'false');

  function getAudioCtx() {
    if (!audioContext) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) audioContext = new AudioCtx();
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

  // 7 KATEGORIEN NAVIGATION (OHNE ZAHLEN)
  const CATEGORY_NAMES = {
    'system': 'SYSTEM & SENSOR VERLAUF',
    'services': 'SERVICES & PROZESS-SCANNER',
    'agents': 'LCARS SUBRAUM COMM-LINK // KI-AGENTEN',
    'ai-info': 'KI-INFO // 9ROUTER & NEURAL TELEMETRIE',
    'config': 'SYSTEM CONFIG & FARBMODI',
    'fantasy': 'ESPN FANTASY FOOTBALL // INCOMPLETE PASS',
    'homeassistant': 'LCARS HAUSSTEUERUNG // HOME ASSISTANT'
  };

  function switchCategory(catId) {
    playLcarsBeep(980, 1400);

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
    if (catId === 'homeassistant') {
      setTimeout(() => {
        haCountdownSeconds = 15;
        updateHaCountdownUI();
        loadHomeAssistantData(false);
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
      const cfgName = (data.name || 'HOME ASSISTANT').toUpperCase();

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
    const name = document.getElementById('cfgHaName')?.value?.trim() || 'Home Assistant';
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
    const isOn = ['on', 'open', 'playing', 'cleaning'].includes(e.state);
    const isActiveState = (isOn || (e.domain === 'vacuum' && e.state !== 'docked') || (e.domain === 'climate' && e.state !== 'off'));
    
    let displayState = e.state;
    if (e.state === 'on') displayState = 'AN';
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
          ${e.controls?.can_toggle ? `
            <button class="ha-tile-toggle-btn ${isOn ? 'btn-active' : ''}" 
                    onclick="toggleHaEntity(event, '${escapeHtml(e.entity_id)}', '${escapeHtml(e.domain)}')">
              ${isOn ? 'AUS' : 'AN'}
            </button>
          ` : (e.controllable ? `
            <span style="font-size:0.75rem; color:var(--c-primary); font-family:var(--mono-family); font-weight:700;">
              STEUERN ▶
            </span>
          ` : '')}
        </div>
      </div>
    `;
  }

  async function toggleHaEntity(event, entityId, domain) {
    if (event) event.stopPropagation();
    playLcarsBeep(1200, 1600);

    const srv = (domain === 'light' || domain === 'switch' || domain === 'input_boolean') ? 'toggle' : 'toggle';
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

    // 8. BUTTON / SCENE / SCRIPT
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

    // Scanner Meta & Countdown
    if (data.scanner_meta) {
      document.getElementById('scanLastTime').textContent = data.scanner_meta.last_scan_time || '--';
      const sec = data.scanner_meta.next_scan_seconds || 0;
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      document.getElementById('scanCountdown').textContent = `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
    }
    if (data.discovered_servers) {
      document.getElementById('scanFoundCount').textContent = data.discovered_servers.length;
      updateServicesCards(data.discovered_servers);
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
    const grid = document.getElementById('servicesGrid');
    if (!grid || !servers) return;
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

  async function fetchLiveStats(playSound = false) {
    if (playSound) playLcarsBeep(1400, 900);
    try {
      const resp = await fetch('/api/stats');
      if (resp.ok) {
        const data = await resp.json();
        renderStats(data);
      }
    } catch (e) {
      console.warn("Telemetrie Fetch Fehler:", e);
    }
  }

  function setRefreshInterval(ms) {
    refreshIntervalMs = ms;
    document.getElementById('refreshRateDisplay').textContent = (ms / 1000) + ' SEKUNDEN';
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => fetchLiveStats(false), refreshIntervalMs);
  }
  refreshTimer = setInterval(() => fetchLiveStats(false), refreshIntervalMs);

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

    if (historyChart) {
      try { historyChart.destroy(); } catch (e) {}
      historyChart = null;
    }

    const samples = currentHistorySamples;
    const labels = samples.map(s => s.time || '');
    const temps = samples.map(s => s.temp);
    const cpus = samples.map(s => s.cpu);
    const throttles = samples.map(s => s.throttled ? s.temp : null);
    const rams = samples.map(s => s.ram);

    if (typeof Chart !== 'undefined') {
      try {
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

    // Provider Connections List Rendering
    const connList = document.getElementById('nineRouterConnectionsList');
    if (connList && nrData.connections) {
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

    // Recent History Table Rendering
    const tbody = document.getElementById('nineRouterHistoryTableBody');
    if (tbody && nrData.recent_history) {
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

    // Charts aktualisieren falls bereits gezeichnet
    if (nineRouterModelChart || nineRouterTimelineChart) {
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

    if (nineRouterModelChart) {
      try { nineRouterModelChart.destroy(); } catch (e) {}
      nineRouterModelChart = null;
    }

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

    if (nineRouterTimelineChart) {
      try { nineRouterTimelineChart.destroy(); } catch (e) {}
      nineRouterTimelineChart = null;
    }

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

    if (hermesChart) {
      try { hermesChart.destroy(); } catch (e) {}
      hermesChart = null;
    }

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

  async function handleChatSubmit(event) {
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

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: currentChatModel,
          messages: chatHistory
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
        playLcarsBeep(980, 1400);

        if (meta) {
          const toks = data.usage ? ` [Tokens: ${data.usage.total_tokens || 0}]` : '';
          meta.textContent = `TRANSMISSION EMPFANGEN // MODELL: ${data.model || currentChatModel}${toks}`;
        }
      } else {
        const errorMsg = data.error || (data.message && data.message.content) || 'Unbekannter Fehler bei Kommunikation mit KI-Proxy.';
        appendChatMessage('assistant', errorMsg, currentChatModel, true);
        playLcarsBeep(440, 220);
        if (meta) meta.textContent = `TRANSMISSIONSFEHLER (${resp.status})`;
      }
    } catch (e) {
      appendChatMessage('assistant', `Netzwerkfehler: Verbindung zum Backend fehlgeschlagen (${e.message})`, currentChatModel, true);
      playLcarsBeep(440, 220);
      if (meta) meta.textContent = 'NETZWERKFEHLER BEI TRANSMISSION';
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

  async function handleHermesChatSubmit(e) {
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
        playLcarsBeep(980, 1400);
        if (meta) meta.textContent = `ANTWORT EMPFANGEN // AGENT: ${currentHermesProfile.toUpperCase()}`;
      } else {
        const err = data.error || data.reply || 'Fehler bei Hermes Ausführung.';
        appendHermesMessage('assistant', err, currentHermesProfile, true);
        playLcarsBeep(440, 220);
        if (meta) meta.textContent = 'HERMES AUSFÜHRUNGSFEHLER';
      }
    } catch (err) {
      appendHermesMessage('assistant', `Verbindungsfehler zu Hermes: ${err.message}`, currentHermesProfile, true);
      playLcarsBeep(440, 220);
      if (meta) meta.textContent = 'NETZWERKFEHLER';
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
  });

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

    @app.route("/static/<path:filename>")
    def serve_static(filename):
        return send_from_directory("static", filename)

    @app.route("/chart.umd.min.js")
    def serve_chart_root():
        return send_from_directory("static", "chart.umd.min.js")

    @app.route("/")
    def index():
        stats = get_system_stats()
        stats_json = json.dumps(stats)
        return render_template_string(DASHBOARD_HTML, stats=stats, stats_json=stats_json)

    @app.route("/api/stats")
    def api_stats():
        return jsonify(get_system_stats())

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
        service_data = data.get("service_data", {})
        if not domain or not service:
            return jsonify({"success": False, "error": "Domain und Service erforderlich"}), 400
        res = ha_service.call_service(domain, service, service_data)
        return jsonify(res)


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
                service_data = data.get("service_data", {})
                res = ha_service.call_service(domain, service, service_data) if ha_service else {"success": False}
                resp = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            sys.stdout.write(f"[{self.log_date_time_string()}] {args[0]} {args[1]} {args[2]}\n")
            sys.stdout.flush()

    def run_server():
        server_address = ("0.0.0.0", 5000)
        httpd = HTTPServer(server_address, DashboardHTTPHandler)
        print("[START] Starte Standard-HTTP System Server auf http://0.0.0.0:5000 ...", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    run_server()
