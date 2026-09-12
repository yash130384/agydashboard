#!/usr/bin/env python3
"""
LCARS System Dashboard & Webserver Discovery Engine (Star Trek LCARS Interface)
Lauscht auf Port 5000 (bind 0.0.0.0) und bietet:
- Star Trek LCARS Fullscreen Benutzeroberfläche (Vorlage: https://www.thelcars.com/)
- Einstellungsmenü mit 5 umschaltbaren Farbmodi (Classic, Nemesis Blue, Lower Decks, Red Alert, Voyager Bio-Neural)
- Linke LCARS Steuerungsbuttons zur Einbettung aller Kategorien (System, 24h Verlauf, Services, KI-Agenten, Scanner, Config)
- Automatischer 5-Minuten Webserver-Scanner (ermittelt lokale LAN-, Tailscale- und Cloudflared-Adressen aller laufenden Prozesse)
- On-Demand Webserver-Scan & REST APIs (/api/stats, /api/history, /api/discovered-servers, /api/scan-webservers)
- 24h In-Memory System-Sensor-Historie & interaktive Chart.js Graphen
"""

import datetime
import glob
import json
import os
import platform
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque

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
    from flask import Flask, jsonify, render_template_string, request
    USE_FLASK = True
except ImportError:
    print("[INFO] Flask nicht installiert, verwende Python Standardbibliothek (http.server).")
    USE_FLASK = False
    from http.server import HTTPServer, BaseHTTPRequestHandler


# ---------------------------------------------------------------------------
# Basis Service-Registry für Web-Services
# ---------------------------------------------------------------------------
SERVICE_REGISTRY = {
    "dashboard": {
        "name": "agydashboard",
        "title": "System Dashboard",
        "icon": "📟",
        "port": 5000,
        "description": "Flask LCARS Dashboard (Eigenes)",
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
    """Liest die CPU-Temperatur des Raspberry Pi aus."""
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


def get_throttled_status():
    """Prüft get_throttled beim Raspberry Pi auf Undervoltage / Throttling."""
    try:
        out = subprocess.check_output(["vcgencmd", "get_throttled"], stderr=subprocess.DEVNULL, timeout=1).decode("utf-8")
        m = re.search(r"throttled=(0x[0-9a-fA-F]+)", out)
        if m:
            val_hex = m.group(1)
            val = int(val_hex, 16)
            is_active = bool(val & 0x1)  # Bit 0: aktuell gethrottled
            return is_active, val_hex
    except Exception:
        pass
    return False, "0x0"


def get_ram_metrics():
    """Liest RAM-Auslastung via psutil oder /proc/meminfo."""
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
    """Liest Disk-Auslastung für Root /."""
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
    """Ermittelt die primäre lokale LAN IP des Hosts."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_tailscale_info():
    """Ermittelt Tailscale Hostname, FQDN und IP."""
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
    """Liest die aktuellen Quick-Tunnel URLs von cloudflared aus ~/.cloudflared-urls/."""
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
    """Prüft, ob ein Dienst via Port erreichbar ist (mit kurzem In-Memory Cache)."""
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
# 5-Minuten Automatischer Webserver-Erkennungs-Dienst
# ---------------------------------------------------------------------------
class WebserverDiscoveryScanner:
    """Hintergrund-Dienst zur periodischen Erkennung aller laufenden Webserver.
    Sucht alle 5 Minuten (300 Sekunden) nach allen lauschenden Prozessen und ermittelt
    für jeden Dienst:
    - Port, PID, Prozessname, Befehlszeile
    - Lokale LAN-Adresse (z.B. http://192.168.31.210:PORT)
    - Tailscale-Adresse (z.B. http://pimmel.tail3a782b.ts.net:PORT)
    - Cloudflared-Quick-Tunnel-Adresse (falls vorhanden)
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
        """Mappt Ports zu Cloudflare trycloudflare.com URLs."""
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
                                if p:
                                    cf_map[p] = u
            except Exception:
                pass

        # Logs scannen
        for log_path, p in [("/tmp/cloudflared_dash.log", 5000), ("/tmp/cloudflared_xdcc.log", 3000)]:
            if p not in cf_map and os.path.exists(log_path):
                try:
                    with open(log_path, "r", errors="ignore") as f:
                        urls = re.findall(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com", f.read())
                        if urls:
                            cf_map[p] = urls[-1]
                except Exception:
                    pass

        # Prozess-Argumente scannen
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
        """Führt einen Scan aller lauschenden TCP-Sockets und Webserver durch."""
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
        skip_ports = {22, 111, 5432}  # SSH, RPC, PostgreSQL

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

            # Interne cloudflared Proxy Ports überspringen
            if "cloudflared" in pname and port > 20000:
                continue

            # HTTP Probing
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
                    req = urllib.request.Request(f"http://{phost}:{port}/", headers={"User-Agent": "LCARS-Discovery/1.0"})
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

            web_procs = ["node", "python", "python3", "uvicorn", "gunicorn", "headroom", "agy", "caddy", "nginx", "apache2"]
            if not is_http and any(wp in pname.lower() for wp in web_procs):
                is_http = True
                http_status = 200

            if not is_http:
                continue

            is_loopback = info["ip"] in ["127.0.0.1", "::1"]
            lan_url = f"http://{lan_ip}:{port}" if not is_loopback else f"http://127.0.0.1:{port}"
            ts_url = f"http://{ts_host}:{port}" if not is_loopback else f"http://{ts_host}:{port} (Localhost)"
            cf_url = cf_map.get(port)

            # Titel ermitteln
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

            # Dynamische URL Aktualisierung in SERVICE_REGISTRY
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

        now_ts = time.time()
        with self._lock:
            self.discovered_servers = discovered
            self.last_scan_ts = now_ts
            self.last_scan_time = datetime.datetime.now().strftime("%H:%M:%S")
            self.next_scan_ts = now_ts + self.interval
            self.scan_count += 1
            self.is_scanning = False

        print(f"[LCARS DISCOVERY] Scan #{self.scan_count} abgeschlossen: {len(discovered)} Webserver aktiv.", flush=True)
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
            remaining = max(0, int(self.next_scan_ts - time.time())) if self.next_scan_ts else self.interval
            return {
                "discovered": list(self.discovered_servers),
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
    """Fragt OpenRouter Key Info & Credits ab (~/.hermes/.env)."""

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
    """Liest den Auth-Status und Session-Aktivität von Antigravity CLI aus."""
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
# Gesamt-System-Stats Sammler
# ---------------------------------------------------------------------------
def get_system_stats():
    """Sammelt alle Systemmetriken inklusive Discovered Webserver."""
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
# Star Trek LCARS Fullscreen Dashboard UI (HTML, CSS & JavaScript)
# Vorlage: https://www.thelcars.com/
# ---------------------------------------------------------------------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="de" data-theme="classic">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="format-detection" content="telephone=no, date=no">
  <title>LCARS 47 // SYSTEM DASHBOARD // {{ stats.hostname }}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Antonio:wght@400;600;700&family=Bebas+Neue&family=Share+Tech+Mono&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    /* ==========================================================================
       LCARS THEME PALETTES & CSS VARIABLES
       ========================================================================== */
    :root {
      --bg: #000000;
      --font-family: 'Antonio', 'Arial Narrow', sans-serif;
      --mono-family: 'Share Tech Mono', monospace;
      --lfw: 230px;
      --elbow-radius: 0 0 0 130px;
      --elbow-radius-bottom: 130px 0 0 0;
      --corner-cutout: 0 0 0 50px;
      --corner-cutout-bottom: 50px 0 0 0;
      --bar-height: 26px;
      --pill-height: 52px;

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

    /* Red Alert Combat Theme */
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
      --c-card-border: rgba(207, 48, 48, 0.5);
    }

    /* Voyager Bio-Neural Theme */
    [data-theme="voyager"] {
      --c-primary: #14b8a6;
      --c-secondary: #38bdf8;
      --c-blue: #10b981;
      --c-almond: #a78bfa;
      --c-butterscotch: #06b6d4;
      --c-red: #f43f5e;
      --c-gold: #f59e0b;
      --elbow-top: #14b8a6;
      --elbow-bottom: #38bdf8;
      --banner-color: #38bdf8;
      --data-cascade-color: #14b8a6;
      --c-card-border: rgba(20, 184, 166, 0.4);
    }

    /* ==========================================================================
       GLOBAL LAYOUT & TYPOGRAPHY
       ========================================================================== */
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-tap-highlight-color: transparent;
    }
    html, body {
      width: 100%;
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
      padding: 0.4rem 0.8rem;
    }

    /* LCARS Standard Wrap Container (Fullscreen) */
    .wrap-standard {
      width: 100%;
      min-height: 98vh;
      display: flex;
      flex-direction: column;
      position: relative;
    }
    .wrap {
      display: flex;
      width: 100%;
      position: relative;
    }

    /* ==========================================================================
       UPPER FRAME & ELBOW
       ========================================================================== */
    .left-frame-top {
      width: var(--lfw);
      display: flex;
      flex-direction: column;
      background-color: var(--elbow-top);
      border-radius: var(--elbow-radius);
      padding: 0.5rem;
      text-align: right;
      color: #000;
      font-weight: 700;
      justify-content: space-between;
      min-height: 140px;
    }
    .left-frame-top button {
      background: transparent;
      border: none;
      color: #000;
      font-family: var(--font-family);
      font-size: 1.25rem;
      font-weight: 700;
      text-transform: uppercase;
      text-align: right;
      cursor: pointer;
      line-height: 1.1;
    }

    .right-frame-top {
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      position: relative;
      padding-left: 0;
    }
    /* Konkave Okuda-Kurve am oberen Elbow */
    .right-frame-top::before {
      content: '';
      display: block;
      width: 50px;
      height: 50px;
      background-color: var(--elbow-top);
      position: absolute;
      left: 0;
      bottom: var(--bar-height);
      z-index: 1;
    }
    .right-frame-top::after {
      content: '';
      display: block;
      width: 50px;
      height: 50px;
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
      padding-bottom: 0.4rem;
      padding-left: 60px;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .banner-title {
      font-size: clamp(1.4rem, 2.8vw, 2.5rem);
      font-weight: 700;
      color: var(--banner-color);
      line-height: 1;
      letter-spacing: 0.08em;
    }
    .banner-stardate {
      font-family: var(--mono-family);
      font-size: 1.15rem;
      color: var(--c-gold);
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    /* Top Data Cascade Animation */
    .data-cascade-bar {
      display: flex;
      justify-content: flex-end;
      align-items: center;
      gap: 0.5rem;
      padding-bottom: 0.4rem;
      overflow: hidden;
    }
    .data-cascade {
      display: flex;
      gap: 0.6rem;
      font-family: var(--mono-family);
      font-size: 0.78rem;
      color: var(--data-cascade-color);
      user-select: none;
    }
    .dc-col {
      display: flex;
      flex-direction: column;
      line-height: 1.1;
      text-align: right;
    }
    .dc-flash-1 { animation: dc-blink 2.2s infinite; }
    .dc-flash-2 { animation: dc-blink 3.1s infinite 0.5s; }
    .dc-flash-3 { animation: dc-blink 1.8s infinite 1s; }
    @keyframes dc-blink {
      0%, 100% { opacity: 0.9; color: var(--c-primary); }
      50% { opacity: 0.3; color: var(--c-secondary); }
      75% { opacity: 1; color: #fff; }
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
    .bar-1 { width: 42%; background-color: var(--elbow-top); }
    .bar-2 { width: 5%; background-color: var(--c-primary); }
    .bar-3 { width: 18%; background-color: var(--c-secondary); }
    .bar-4 { flex: 1; background-color: var(--c-butterscotch); }
    .bar-5 { width: 6%; background-color: var(--c-red); }

    /* ==========================================================================
       MIDDLE FRAME: LEFT CONTROL PILLAR & MAIN EMBEDDED CATEGORIES
       ========================================================================== */
    .gap-wrap {
      margin-top: 4px;
      flex: 1;
      display: flex;
    }
    .left-frame {
      width: var(--lfw);
      background-color: var(--elbow-bottom);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: relative;
      padding-top: 4px;
    }

    /* Linke LCARS Steuerungs-Buttons */
    .nav-pillar {
      display: flex;
      flex-direction: column;
      gap: 4px;
      width: 100%;
    }
    .lcars-pill-btn {
      display: flex;
      justify-content: flex-end;
      align-items: flex-end;
      width: 100%;
      height: var(--pill-height);
      padding: 0.5rem 0.8rem;
      border: none;
      outline: none;
      background-color: var(--c-primary);
      color: #000;
      font-family: var(--font-family);
      font-size: 1.15rem;
      font-weight: 700;
      letter-spacing: 0.05em;
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
      margin-right: 0.35rem;
    }
    .pill-sys   { background-color: var(--c-primary); }
    .pill-hist  { background-color: var(--c-secondary); }
    .pill-srv   { background-color: var(--c-blue); }
    .pill-ai    { background-color: var(--c-butterscotch); }
    .pill-scan  { background-color: var(--c-red); }
    .pill-cfg   { background-color: var(--c-gold); }

    /* Unterer Teil des linken Pillars (Action Buttons & Elbow) */
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
      height: 42px;
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
    }
    .left-action-btn:hover {
      background-color: var(--elbow-bottom);
      color: #000;
    }
    .left-elbow-bottom {
      height: 90px;
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
       MAIN CONTENT CONTAINER
       ========================================================================== */
    .right-frame {
      flex: 1;
      display: flex;
      flex-direction: column;
      position: relative;
      padding-left: 0;
      background: transparent;
    }
    /* Konkave Okuda-Kurve am unteren Elbow */
    .right-frame::after {
      content: '';
      display: block;
      width: 50px;
      height: 50px;
      background-color: var(--elbow-bottom);
      position: absolute;
      left: 0;
      bottom: var(--bar-height);
      z-index: 1;
    }
    .right-frame-inner-corner {
      width: 50px;
      height: 50px;
      background-color: #000;
      border-radius: var(--corner-cutout-bottom);
      position: absolute;
      left: 0;
      bottom: var(--bar-height);
      z-index: 2;
    }

    main {
      flex: 1;
      padding: 1rem 1.25rem 2rem clamp(1rem, 2.5vw, 2.5rem);
      overflow-y: auto;
      max-height: calc(100vh - 180px);
    }

    /* Embedded Category Sections */
    .lcars-section {
      display: none;
      animation: lcars-fade 0.2s ease-in;
    }
    .lcars-section.active-section {
      display: block;
    }
    @keyframes lcars-fade {
      from { opacity: 0; transform: translateY(4px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* LCARS Header Bars */
    .lcars-header-bar {
      display: flex;
      align-items: center;
      gap: 0.8rem;
      margin-bottom: 1.2rem;
      border-bottom: 2px solid var(--c-primary);
      padding-bottom: 0.4rem;
    }
    .lcars-header-bar h2 {
      font-size: 1.5rem;
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

    /* ==========================================================================
       LCARS READOUT CARDS & GRIDS
       ========================================================================== */
    .readout-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .lcars-card {
      background-color: var(--c-card-bg);
      border-left: 6px solid var(--c-primary);
      border-top: 1px solid var(--c-card-border);
      border-right: 1px solid var(--c-card-border);
      border-bottom: 1px solid var(--c-card-border);
      border-radius: 0 14px 14px 0;
      padding: 1.1rem;
      position: relative;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .lcars-card.card-violet { border-left-color: var(--c-secondary); }
    .lcars-card.card-blue { border-left-color: var(--c-blue); }
    .lcars-card.card-almond { border-left-color: var(--c-almond); }
    .lcars-card.card-red { border-left-color: var(--c-red); }

    .card-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.6rem;
    }
    .card-head-title {
      font-size: 0.92rem;
      color: var(--c-secondary);
      font-weight: 700;
      letter-spacing: 0.06em;
    }
    .card-head-icon {
      font-size: 1.25rem;
    }
    .card-metric {
      font-size: 2.3rem;
      font-weight: 700;
      color: #fff;
      line-height: 1;
      margin-bottom: 0.4rem;
    }
    .card-metric-sub {
      font-size: 0.86rem;
      color: var(--c-gold);
      margin-bottom: 0.75rem;
      font-family: var(--mono-family);
    }

    /* LCARS Progress Bars */
    .lcars-bar-track {
      width: 100%;
      height: 14px;
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

    /* Badges */
    .badge-status {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.2rem 0.6rem;
      border-radius: 100vmax;
      font-size: 0.78rem;
      font-weight: 700;
      text-transform: uppercase;
    }
    .badge-online { background-color: #059669; color: #fff; }
    .badge-offline { background-color: #dc2626; color: #fff; }
    .badge-warn { background-color: #d97706; color: #fff; }

    /* ==========================================================================
       DISCOVERY SCANNER VIEW (Kategorie 05)
       ========================================================================== */
    .scanner-dashboard-box {
      background: rgba(20, 20, 30, 0.85);
      border: 2px solid var(--c-red);
      border-radius: 12px;
      padding: 1.25rem;
      margin-bottom: 1.5rem;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
    }
    .scanner-telemetry-col {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }
    .scanner-telemetry-col strong {
      color: var(--c-primary);
      font-size: 1.25rem;
    }
    .scanner-telemetry-col span {
      font-family: var(--mono-family);
      font-size: 0.95rem;
      color: #e2e8f0;
    }
    .btn-scan-trigger {
      background-color: var(--c-red);
      color: #000;
      font-family: var(--font-family);
      font-size: 1.15rem;
      font-weight: 700;
      padding: 0.65rem 1.5rem;
      border-radius: 100vmax;
      border: none;
      cursor: pointer;
      text-transform: uppercase;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      transition: filter 0.15s ease, transform 0.1s ease;
    }
    .btn-scan-trigger:hover { filter: brightness(1.25); }
    .btn-scan-trigger:active { transform: scale(0.98); }

    .scanner-table-wrapper {
      width: 100%;
      overflow-x: auto;
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 10px;
      margin-bottom: 1.5rem;
      background: rgba(10, 12, 18, 0.95);
    }
    .lcars-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
      text-align: left;
    }
    .lcars-table th {
      background-color: rgba(235, 148, 58, 0.15);
      color: var(--c-primary);
      padding: 0.75rem 0.9rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      border-bottom: 2px solid var(--c-primary);
      white-space: nowrap;
    }
    .lcars-table td {
      padding: 0.75rem 0.9rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      vertical-align: middle;
    }
    .lcars-table tr:hover td {
      background-color: rgba(255, 255, 255, 0.04);
    }
    .lcars-table .url-link {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      color: var(--c-blue);
      text-decoration: none;
      font-family: var(--mono-family);
      font-size: 0.85rem;
      padding: 0.2rem 0.5rem;
      background: rgba(136, 153, 255, 0.12);
      border-radius: 4px;
      transition: background 0.15s ease;
    }
    .lcars-table .url-link:hover {
      background: rgba(136, 153, 255, 0.25);
      color: #fff;
    }
    .lcars-table .url-cf {
      color: var(--c-primary);
      background: rgba(235, 148, 58, 0.12);
    }
    .lcars-table .url-cf:hover {
      background: rgba(235, 148, 58, 0.28);
      color: #fff;
    }

    /* ==========================================================================
       SETTINGS & CONFIG VIEW (Kategorie 06)
       ========================================================================== */
    .theme-selector-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }
    .theme-btn {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 0.6rem;
      padding: 1rem;
      background-color: #12141e;
      border: 2px solid rgba(255, 255, 255, 0.15);
      border-radius: 12px;
      color: #fff;
      cursor: pointer;
      font-family: var(--font-family);
      text-transform: uppercase;
      transition: all 0.15s ease;
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
      gap: 5px;
      width: 100%;
      height: 18px;
    }
    .theme-swatch {
      flex: 1;
      height: 100%;
      border-radius: 3px;
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
    .bar-6 { width: 42%; background-color: var(--c-red); }
    .bar-7 { width: 6%; background-color: var(--c-butterscotch); }
    .bar-8 { width: 18%; background-color: var(--c-red); }
    .bar-9 { flex: 1; background-color: var(--c-secondary); }
    .bar-10 { width: 5%; background-color: var(--c-butterscotch); }

    footer {
      padding: 0.5rem 1rem;
      font-size: 0.78rem;
      color: rgba(255, 255, 255, 0.5);
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 0.5rem;
      font-family: var(--mono-family);
    }
    footer a { color: var(--c-primary); text-decoration: none; }

    /* Responsive Anpassungen */
    @media (max-width: 860px) {
      :root { --lfw: 160px; --pill-height: 44px; }
      .data-cascade { display: none; }
      .banner-title { font-size: 1.25rem; }
      main { padding: 0.8rem; }
    }
    @media (max-width: 600px) {
      .wrap-standard { min-height: auto; }
      .wrap { flex-direction: column; }
      .left-frame-top, .left-frame { width: 100%; border-radius: 0; }
      .right-frame-top::before, .right-frame-top::after,
      .right-frame::after, .right-frame-inner-corner { display: none; }
      .banner-container { padding-left: 0.5rem; }
    }
  </style>
</head>
<body>

<section class="wrap-standard">
  <!-- OBERER RAHMEN -->
  <div class="wrap">
    <div class="left-frame-top">
      <button onclick="playLcarsBeep(880, 1760); switchCategory('system')">LCARS 47<br><span style="font-size:0.8rem; opacity:0.85;">AGY-PI</span></button>
      <div style="font-size: 0.8rem; font-family: var(--mono-family);">SYS // ONLINE</div>
    </div>
    <div class="right-frame-top">
      <div class="banner-container">
        <div class="banner-title" id="bannerSectionTitle">LCARS / SYSTEM MONITOR</div>
        <div class="banner-stardate">
          <span>STARDATE:</span>
          <span id="stardateValue" style="font-weight:700;">--------.-</span>
        </div>
      </div>
      <div class="data-cascade-bar">
        <div class="data-cascade">
          <div class="dc-col dc-flash-1"><span>4701</span><span>8824</span><span>1209</span><span>9941</span></div>
          <div class="dc-col dc-flash-2"><span>10482</span><span>33918</span><span>7402</span><span>18293</span></div>
          <div class="dc-col dc-flash-3"><span>2904</span><span>5518</span><span>8024</span><span>3102</span></div>
          <div class="dc-col dc-flash-1"><span>99104</span><span>44812</span><span>66301</span><span>77219</span></div>
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

  <!-- MITTLERER RAHMEN (LINKE STEUERUNGSBUTTONS & INHALT) -->
  <div class="wrap gap-wrap">
    <!-- Linke Steuerungssäule -->
    <div class="left-frame">
      <nav class="nav-pillar">
        <button class="lcars-pill-btn pill-sys active" onclick="switchCategory('system')" id="btn-cat-system">
          01 // SYSTEM
        </button>
        <button class="lcars-pill-btn pill-hist" onclick="switchCategory('history')" id="btn-cat-history">
          02 // VERLAUF
        </button>
        <button class="lcars-pill-btn pill-srv" onclick="switchCategory('services')" id="btn-cat-services">
          03 // SERVICES
        </button>
        <button class="lcars-pill-btn pill-ai" onclick="switchCategory('agents')" id="btn-cat-agents">
          04 // KI-AGENTEN
        </button>
        <button class="lcars-pill-btn pill-scan" onclick="switchCategory('scanner')" id="btn-cat-scanner">
          05 // SCANNER
        </button>
        <button class="lcars-pill-btn pill-cfg" onclick="switchCategory('config')" id="btn-cat-config">
          06 // CONFIG
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

    <!-- Haupt-Inhaltsbereich mit eingebetteten Kategorien -->
    <div class="right-frame">
      <div class="right-frame-inner-corner"></div>
      <main>

        <!-- KATEGORIE 01: SYSTEM OVERVIEW -->
        <section class="lcars-section active-section" id="section-system">
          <div class="lcars-header-bar">
            <h2>01 // SYSTEM STATUS & TELEMETRIE</h2>
            <span class="lcars-pill-tag">LIVE DIAGNOSTIK</span>
          </div>

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
              <div class="badge-status badge-online">SYS RUNNING</div>
            </div>

            <!-- HARDWARE SPEC -->
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">SPEZIFIKATION</span>
                <span class="card-head-icon">🎛️</span>
              </div>
              <div style="font-size: 1.15rem; color:#fff; margin-bottom: 0.3rem;">{{ stats.hostname }}</div>
              <div class="card-metric-sub">{{ stats.platform }}</div>
              <div class="card-metric-sub">LAN IP: <span id="sysLanIp">192.168.31.210</span></div>
            </div>
          </div>
        </section>

        <!-- KATEGORIE 02: 24H SENSOR VERLAUF -->
        <section class="lcars-section" id="section-history">
          <div class="lcars-header-bar">
            <h2>02 // 24H SENSOR VERLAUF & CHARTS</h2>
            <span class="lcars-pill-tag">ODN METRIKEN</span>
          </div>

          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.6rem; margin-bottom: 1rem;">
            <!-- Range Selector Pills -->
            <div style="display:flex; gap:0.4rem; flex-wrap:wrap;">
              <button class="left-action-btn range-btn" onclick="setChartRange('10m')">10 MIN</button>
              <button class="left-action-btn range-btn" onclick="setChartRange('30m')">30 MIN</button>
              <button class="left-action-btn range-btn active-range" onclick="setChartRange('1h')">1 STUNDE</button>
              <button class="left-action-btn range-btn" onclick="setChartRange('12h')">12 STUNDEN</button>
              <button class="left-action-btn range-btn" onclick="setChartRange('24h')">24 STUNDEN</button>
            </div>

            <!-- Dataset Toggles -->
            <div style="display:flex; gap:0.4rem; flex-wrap:wrap;">
              <button class="left-action-btn" onclick="toggleDataset(0)" id="dsBtn0" style="border-color:var(--c-primary); color:var(--c-primary);">TEMPERATUR</button>
              <button class="left-action-btn" onclick="toggleDataset(1)" id="dsBtn1" style="border-color:var(--c-blue); color:var(--c-blue);">CPU %</button>
              <button class="left-action-btn" onclick="toggleDataset(2)" id="dsBtn2" style="border-color:var(--c-red); color:var(--c-red);">THROTTLING</button>
              <button class="left-action-btn" onclick="toggleDataset(3)" id="dsBtn3" style="border-color:var(--c-secondary); color:var(--c-secondary);">RAM %</button>
            </div>
          </div>

          <div class="lcars-card" style="padding:1rem; min-height: 380px;">
            <canvas id="historyChart" style="width:100%; height:360px;"></canvas>
          </div>
        </section>

        <!-- KATEGORIE 03: WEB-SERVICES -->
        <section class="lcars-section" id="section-services">
          <div class="lcars-header-bar">
            <h2>03 // WEB-SERVICES & SCHNELLZUGRIFF</h2>
            <span class="lcars-pill-tag">ODN NETZWERK</span>
          </div>

          <div class="readout-grid" id="servicesGrid">
            {% for key, svc in stats.services.items() %}
            <div class="lcars-card">
              <div class="card-head">
                <span class="card-head-title">{{ svc.title }}</span>
                <span class="card-head-icon">{{ svc.icon }}</span>
              </div>
              <div style="margin-bottom: 0.6rem;">
                <span class="badge-status {% if svc.online %}badge-online{% else %}badge-offline{% endif %}">
                  PORT {{ svc.port }} // {% if svc.online %}ONLINE{% else %}OFFLINE{% endif %}
                </span>
              </div>
              <div class="card-metric-sub">{{ svc.description }}</div>
              <div style="display:flex; flex-direction:column; gap:0.4rem; margin-top:0.6rem;">
                {% if svc.lan_url %}
                <a href="{{ svc.lan_url }}" target="_blank" class="left-action-btn" style="text-decoration:none;">
                  <span>🏠</span> <span>LAN: {{ svc.lan_url }}</span>
                </a>
                {% endif %}
                {% if svc.tailscale_url %}
                <a href="{{ svc.tailscale_url }}" target="_blank" class="left-action-btn" style="text-decoration:none; border-color:var(--c-secondary); color:var(--c-secondary);">
                  <span>🌐</span> <span>TAILSCALE</span>
                </a>
                {% endif %}
                {% if svc.cf_url %}
                <a href="{{ svc.cf_url }}" target="_blank" class="left-action-btn" style="text-decoration:none; border-color:var(--c-primary); color:var(--c-primary);">
                  <span>☁️</span> <span>CLOUDFLARE TUNNEL</span>
                </a>
                {% endif %}
              </div>
            </div>
            {% endfor %}
          </div>
        </section>

        <!-- KATEGORIE 04: KI-AGENTEN & HERMES -->
        <section class="lcars-section" id="section-agents">
          <div class="lcars-header-bar">
            <h2>04 // KI-AGENTEN & HERMES NUTZUNG</h2>
            <span class="lcars-pill-tag">NEURAL PROZESSOREN</span>
          </div>

          <div class="readout-grid">
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

          <!-- Hermes Donut & Model Table -->
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap:1rem; margin-top:1.5rem;">
            <div class="lcars-card" style="min-height:300px; display:flex; flex-direction:column; align-items:center; justify-content:center;">
              <div class="card-head-title" style="align-self:flex-start; margin-bottom:1rem;">MODELL TOKEN-VERTEILUNG</div>
              <div style="width:100%; max-width:260px; height:240px; position:relative;">
                <canvas id="hermesChart"></canvas>
              </div>
            </div>

            <div class="lcars-card">
              <div class="card-head-title" style="margin-bottom:0.8rem;">HERMES MODELLE & KOSTEN</div>
              <div class="scanner-table-wrapper" style="margin-bottom:0;">
                <table class="lcars-table">
                  <thead>
                    <tr>
                      <th>MODELL</th>
                      <th>TOKENS</th>
                      <th>SESSIONS</th>
                      <th>KOSTEN</th>
                    </tr>
                  </thead>
                  <tbody id="hermesTableBody">
                    {% for m in stats.hermes.models %}
                    <tr>
                      <td><strong>{{ m.model }}</strong></td>
                      <td>{{ m.total_formatted }} ({{ m.percent_tokens }}%)</td>
                      <td>{{ m.sessions }}</td>
                      <td>{{ m.cost_formatted }}</td>
                    </tr>
                    {% endfor %}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </section>

        <!-- KATEGORIE 05: 5-MINUTEN WEBSERVER DISCOVERY SCANNER -->
        <section class="lcars-section" id="section-scanner">
          <div class="lcars-header-bar">
            <h2>05 // AUTOMATISCHE WEBSERVER-ERKENNUNG</h2>
            <span class="lcars-pill-tag">5-MIN SCHLEIFE</span>
          </div>

          <!-- Scanner Telemetrie Banner -->
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
              <span>Gefundene Webserver: <strong id="scanFoundCount" style="color:#fff;">{{ stats.discovered_servers|length }}</strong></span>
            </div>
            <div>
              <button class="btn-scan-trigger" onclick="triggerWebserverScan()" id="btnScanTrigger">
                <span>⚡</span> <span id="btnScanLabel">JETZT SCANNEN</span>
              </button>
            </div>
          </div>

          <!-- Tabelle aller erkannten Webserver -->
          <div class="scanner-table-wrapper">
            <table class="lcars-table">
              <thead>
                <tr>
                  <th>PORT / PROZESS</th>
                  <th>PID & BEFEHL</th>
                  <th>LOKALE LAN ADRESSE</th>
                  <th>TAILSCALE ADRESSE</th>
                  <th>CLOUDFLARED ADRESSE</th>
                  <th>STATUS</th>
                </tr>
              </thead>
              <tbody id="discoveredServersTableBody">
                {% for s in stats.discovered_servers %}
                <tr>
                  <td>
                    <strong>{{ s.title }}</strong><br>
                    <span style="font-size:0.8rem; color:var(--c-gold);">Port {{ s.port }} ({{ s.server_header }})</span>
                  </td>
                  <td>
                    <span style="font-family:var(--mono-family);">PID {{ s.pid }} [{{ s.process_name }}]</span><br>
                    <span style="font-size:0.75rem; color:rgba(255,255,255,0.6);">{{ s.cmdline }}</span>
                  </td>
                  <td>
                    <a href="{{ s.lan_url }}" target="_blank" class="url-link">🏠 {{ s.lan_url }}</a>
                  </td>
                  <td>
                    {% if s.tailscale_url %}
                    <a href="{{ s.tailscale_url }}" target="_blank" class="url-link">🌐 {{ s.tailscale_url }}</a>
                    {% else %}
                    <span style="color:rgba(255,255,255,0.4); font-size:0.8rem;">Localhost only</span>
                    {% endif %}
                  </td>
                  <td>
                    {% if s.cloudflared_url %}
                    <a href="{{ s.cloudflared_url }}" target="_blank" class="url-link url-cf">☁️ {{ s.cloudflared_url }}</a>
                    {% else %}
                    <span style="color:rgba(255,255,255,0.4); font-size:0.8rem;">Kein Tunnel</span>
                    {% endif %}
                  </td>
                  <td>
                    <span class="badge-status badge-online">{{ s.http_status or 200 }} OK</span>
                  </td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
        </section>

        <!-- KATEGORIE 06: LCARS CONFIG & FARBMODI -->
        <section class="lcars-section" id="section-config">
          <div class="lcars-header-bar">
            <h2>06 // LCARS CONFIG & FARBMODI</h2>
            <span class="lcars-pill-tag">BENUTZER-EINSTELLUNGEN</span>
          </div>

          <p style="margin-bottom:1.2rem; color:var(--c-gold);">
            WÄHLEN SIE DEN LCARS-FARBMODUS FÜR DAS GESAMTE DASHBOARD AUS. EINSTELLUNGEN WERDEN IM BROWSER GESPEICHERT.
          </p>

          <div class="theme-selector-grid">
            <!-- Classic TNG -->
            <button class="theme-btn active-theme" onclick="setLcarsTheme('classic')" id="theme-btn-classic">
              <span style="font-weight:700; font-size:1.15rem;">01 // CLASSIC 24TH C.</span>
              <span style="font-size:0.8rem; color:#aaa;">TNG / DS9 / VOYAGER OKUDA</span>
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
              <span style="font-weight:700; font-size:1.15rem;">02 // NEMESIS BLUE</span>
              <span style="font-size:0.8rem; color:#aaa;">FIRST CONTACT / TACTICAL BLUE</span>
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
              <span style="font-weight:700; font-size:1.15rem;">03 // LOWER DECKS</span>
              <span style="font-size:0.8rem; color:#aaa;">CALIFORNIA CLASS WARM GOLD</span>
              <div class="theme-swatches">
                <div class="theme-swatch" style="background-color:#ffaa44;"></div>
                <div class="theme-swatch" style="background-color:#ff7700;"></div>
                <div class="theme-swatch" style="background-color:#ffcc99;"></div>
                <div class="theme-swatch" style="background-color:#ffeecc;"></div>
                <div class="theme-swatch" style="background-color:#ff4400;"></div>
              </div>
            </button>

            <!-- Red Alert -->
            <button class="theme-btn" onclick="setLcarsTheme('redalert')" id="theme-btn-redalert">
              <span style="font-weight:700; font-size:1.15rem;">04 // RED ALERT</span>
              <span style="font-size:0.8rem; color:#aaa;">DEFIANT KAMPFSTATION</span>
              <div class="theme-swatches">
                <div class="theme-swatch" style="background-color:#cf3030;"></div>
                <div class="theme-swatch" style="background-color:#ff4444;"></div>
                <div class="theme-swatch" style="background-color:#ffaa00;"></div>
                <div class="theme-swatch" style="background-color:#881111;"></div>
                <div class="theme-swatch" style="background-color:#ff0000;"></div>
              </div>
            </button>

            <!-- Voyager Bio-Neural -->
            <button class="theme-btn" onclick="setLcarsTheme('voyager')" id="theme-btn-voyager">
              <span style="font-weight:700; font-size:1.15rem;">05 // BIO-NEURAL</span>
              <span style="font-size:0.8rem; color:#aaa;">WISSENSCHAFT & SMARAGD</span>
              <div class="theme-swatches">
                <div class="theme-swatch" style="background-color:#14b8a6;"></div>
                <div class="theme-swatch" style="background-color:#38bdf8;"></div>
                <div class="theme-swatch" style="background-color:#10b981;"></div>
                <div class="theme-swatch" style="background-color:#a78bfa;"></div>
                <div class="theme-swatch" style="background-color:#f59e0b;"></div>
              </div>
            </button>
          </div>

          <!-- Zusätzliche Optionen -->
          <div class="readout-grid">
            <div class="lcars-card">
              <div class="card-head-title">AUDIO & LCARS SOUND EFFEKTE</div>
              <p style="font-size:0.88rem; color:var(--c-gold); margin:0.6rem 0;">
                AUTHENTISCHE LCARS-BEEPS VIA WEB AUDIO API BEI TASTENDRUCK.
              </p>
              <button class="left-action-btn" onclick="toggleAudio(); playLcarsBeep(880, 1760);">
                <span>🔊</span> <span id="cfgAudioLabel">SOUND EFFEKTE UMSCHALTEN</span>
              </button>
            </div>

            <div class="lcars-card">
              <div class="card-head-title">VOLLBILD-MODUS (FULLSCREEN)</div>
              <p style="font-size:0.88rem; color:var(--c-gold); margin:0.6rem 0;">
                OPTIMIERT FÜR TERMINALS & MONITOR-WAND IM STAR TREK DESIGN.
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
              <div style="display:flex; gap:0.4rem;">
                <button class="left-action-btn" onclick="setRefreshInterval(2000)">2s</button>
                <button class="left-action-btn" onclick="setRefreshInterval(3000)">3s</button>
                <button class="left-action-btn" onclick="setRefreshInterval(5000)">5s</button>
                <button class="left-action-btn" onclick="setRefreshInterval(10000)">10s</button>
              </div>
            </div>
          </div>
        </section>

      </main>
    </div>
  </div>

  <!-- UNTERER RAHMEN -->
  <div class="wrap" style="margin-top: 4px;">
    <div style="width: var(--lfw);"></div>
    <div style="flex:1;">
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
    <div>LCARS SYSTEM TERMINAL 47 // HOST: {{ stats.hostname }} // STAND: <span id="footerTimestamp">{{ stats.timestamp }}</span></div>
    <div>DESIGN VORLAGE: <a href="https://www.thelcars.com/" target="_blank">THELCARS.COM</a> // AGY DASHBOARD</div>
  </footer>
</section>

<!-- ==========================================================================
     JAVASCRIPT: LCARS CONTROLLER, SOUNDS, THEMES & APIS
     ========================================================================== -->
<script>
  // Initiales Datenobjekt vom Backend
  const initialStats = {{ stats_json | safe }};

  // Sound Engine (Web Audio API Synthesizer)
  let audioContext = null;
  let soundEnabled = (localStorage.getItem('lcars-sound') !== 'false');

  function getAudioCtx() {
    if (!audioContext) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) audioContext = new AudioCtx();
    }
    if (audioContext && audioContext.state === 'suspended') {
      audioContext.resume();
    }
    return audioContext;
  }

  function playLcarsBeep(f1 = 880, f2 = 1760, duration = 0.04) {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(f1, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(f2, ctx.currentTime + duration);
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + duration);
    } catch (e) {
      // Audio Ignorieren falls Browser Blockiert
    }
  }

  function toggleAudio() {
    soundEnabled = !soundEnabled;
    localStorage.setItem('lcars-sound', soundEnabled ? 'true' : 'false');
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

  // Stardate Berechnung (Format: Jahr-1946 + TagDesJahres * 2.732 . Stunde)
  function updateStardate() {
    const now = new Date();
    const currentHour = now.getHours();
    let d = new Date(now);
    if (currentHour === 0) d.setDate(d.getDate() + 1);
    const firstNum = d.getFullYear() - 1946;
    const startOfYear = new Date(d.getFullYear(), 0, 1);
    const diffDays = Math.ceil(Math.abs(d - startOfYear) / (1000 * 60 * 60 * 24));
    const secondNum = String(Math.floor(diffDays * 2.732)).padStart(3, '0');
    let finalNum = (currentHour === 0) ? "0" : ((currentHour >= 12 && currentHour <= 21) ? String(currentHour % 12 || 12) : String(currentHour).padStart(2, '0'));
    const stardateStr = `${firstNum}${secondNum}.${finalNum}`;
    const el = document.getElementById('stardateValue');
    if (el) el.textContent = stardateStr;
  }
  setInterval(updateStardate, 5000);
  updateStardate();

  // Fullscreen Handler
  function toggleFullscreen() {
    playLcarsBeep(1200, 1600);
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(err => {
        console.warn("Fullscreen Request abgelehnt:", err);
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

  // Kategorie Navigation (Left Buttons)
  const CATEGORY_NAMES = {
    'system': 'LCARS / SYSTEM MONITOR',
    'history': 'LCARS / 24H ODN SENSOR VERLAUF',
    'services': 'LCARS / WEB-SERVICES & SCHNELLZUGRIFF',
    'agents': 'LCARS / KI-AGENTEN & HERMES ARCHIV',
    'scanner': 'LCARS / PROZESS- & WEBSERVER-ERKENNUNG',
    'config': 'LCARS / SYSTEM CONFIG & FARBMODI'
  };

  function switchCategory(catId) {
    playLcarsBeep(980, 1400);

    // Alle Buttons deaktiveren & gewählten aktivieren
    document.querySelectorAll('.lcars-pill-btn').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById('btn-cat-' + catId);
    if (activeBtn) activeBtn.classList.add('active');

    // Alle Sektionen ausblenden & gewählte anzeigen
    document.querySelectorAll('.lcars-section').forEach(sec => sec.classList.remove('active-section'));
    const activeSec = document.getElementById('section-' + catId);
    if (activeSec) activeSec.classList.add('active-section');

    // Banner Titel anpassen
    const banner = document.getElementById('bannerSectionTitle');
    if (banner && CATEGORY_NAMES[catId]) {
      banner.textContent = CATEGORY_NAMES[catId];
    }

    // Falls History geöffnet wird, Chart resize ausführen
    if (catId === 'history' && historyChart) {
      setTimeout(() => historyChart.resize(), 50);
    }
    if (catId === 'agents' && hermesChart) {
      setTimeout(() => hermesChart.resize(), 50);
    }
  }

  // Theme Switcher (Farbmodi)
  function setLcarsTheme(themeName) {
    playLcarsBeep(1100, 1800);
    document.documentElement.setAttribute('data-theme', themeName);
    localStorage.setItem('lcars-theme', themeName);

    document.querySelectorAll('.theme-btn').forEach(btn => btn.classList.remove('active-theme'));
    const btn = document.getElementById('theme-btn-' + themeName);
    if (btn) btn.classList.add('active-theme');

    if (historyChart) historyChart.update();
  }

  // Gespeichertes Theme laden
  const savedTheme = localStorage.getItem('lcars-theme') || 'classic';
  setLcarsTheme(savedTheme);
  updateAudioUI();

  // Telemetrie Aktualisierung & Rendering
  function renderStats(data) {
    if (!data) return;

    // CPU
    if (data.cpu) {
      document.getElementById('sysCpuVal').textContent = data.cpu.percent + '%';
      document.getElementById('sysCpuBar').style.width = data.cpu.percent + '%';
      document.getElementById('sysCpuCores').textContent = `Kerne: ${data.cpu.cores || 1}`;
    }

    // RAM
    if (data.ram) {
      document.getElementById('sysRamVal').textContent = data.ram.percent + '%';
      document.getElementById('sysRamBar').style.width = data.ram.percent + '%';
      document.getElementById('sysRamSub').textContent = `${data.ram.used_gb} GB / ${data.ram.total_gb} GB (${data.ram.available_gb} frei)`;
    }

    // Temp
    if (data.temperature) {
      document.getElementById('sysTempVal').textContent = data.temperature.display || '-- °C';
      if (data.temperature.value) {
        document.getElementById('sysTempBar').style.width = Math.min(100, (data.temperature.value / 85) * 100) + '%';
      }
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
      updateDiscoveredTable(data.discovered_servers);
    }

    // Timestamp
    if (data.timestamp) {
      document.getElementById('footerTimestamp').textContent = data.timestamp;
    }
  }

  // Tabelle der erkannten Webserver rendern
  function updateDiscoveredTable(servers) {
    const tbody = document.getElementById('discoveredServersTableBody');
    if (!tbody || !servers) return;
    let html = '';
    servers.forEach(s => {
      const tailscaleCell = s.tailscale_url 
        ? `<a href="${s.tailscale_url}" target="_blank" class="url-link">🌐 ${s.tailscale_url}</a>`
        : `<span style="color:rgba(255,255,255,0.4); font-size:0.8rem;">Localhost only</span>`;

      const cfCell = s.cloudflared_url
        ? `<a href="${s.cloudflared_url}" target="_blank" class="url-link url-cf">☁️ ${s.cloudflared_url}</a>`
        : `<span style="color:rgba(255,255,255,0.4); font-size:0.8rem;">Kein Tunnel</span>`;

      html += `
        <tr>
          <td>
            <strong>${s.title}</strong><br>
            <span style="font-size:0.8rem; color:var(--c-gold);">Port ${s.port} (${s.server_header || 'HTTP'})</span>
          </td>
          <td>
            <span style="font-family:var(--mono-family);">PID ${s.pid || 'N/A'} [${s.process_name || 'N/A'}]</span><br>
            <span style="font-size:0.75rem; color:rgba(255,255,255,0.6);">${s.cmdline || ''}</span>
          </td>
          <td>
            <a href="${s.lan_url}" target="_blank" class="url-link">🏠 ${s.lan_url}</a>
          </td>
          <td>${tailscaleCell}</td>
          <td>${cfCell}</td>
          <td>
            <span class="badge-status badge-online">${s.http_status || 200} OK</span>
          </td>
        </tr>
      `;
    });
    tbody.innerHTML = html;
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
        if (data.discovered) updateDiscoveredTable(data.discovered);
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
  // Chart.js: 24h Verlauf & Hermes Donut
  // ---------------------------------------------------------------------------
  let historyChart = null;
  let hermesChart = null;
  let activeRange = '1h';

  function initHistoryChart() {
    const canvas = document.getElementById('historyChart');
    if (!canvas || typeof Chart === 'undefined') return;
    const ctx = canvas.getContext('2d');

    historyChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: 'Temperatur (°C)',
            data: [],
            borderColor: '#eb943a',
            backgroundColor: 'rgba(235, 148, 58, 0.1)',
            borderWidth: 2,
            tension: 0.25,
            fill: false,
            yAxisID: 'yTemp'
          },
          {
            label: 'CPU-Auslastung (%)',
            data: [],
            borderColor: '#8899ff',
            backgroundColor: 'rgba(136, 153, 255, 0.1)',
            borderWidth: 2,
            tension: 0.25,
            fill: false,
            yAxisID: 'yPercent'
          },
          {
            label: 'Throttling Aktiv',
            data: [],
            borderColor: '#cf4f4f',
            backgroundColor: '#cf4f4f',
            showLine: false,
            pointRadius: 6,
            yAxisID: 'yTemp'
          },
          {
            label: 'RAM (%)',
            data: [],
            borderColor: '#baa4e5',
            backgroundColor: 'rgba(186, 164, 229, 0.1)',
            borderWidth: 2,
            tension: 0.25,
            fill: false,
            hidden: true,
            yAxisID: 'yPercent'
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
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
            ticks: { color: '#aaa', font: { family: 'Share Tech Mono' } }
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

    fetchHistoryData(activeRange);
  }

  async function fetchHistoryData(range) {
    try {
      const resp = await fetch(`/api/history?range=${range}`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (!historyChart || !data.samples) return;

      const labels = data.samples.map(s => s.time);
      const temps = data.samples.map(s => s.temp);
      const cpus = data.samples.map(s => s.cpu);
      const throttles = data.samples.map(s => s.throttled ? s.temp : null);
      const rams = data.samples.map(s => s.ram);

      historyChart.data.labels = labels;
      historyChart.data.datasets[0].data = temps;
      historyChart.data.datasets[1].data = cpus;
      historyChart.data.datasets[2].data = throttles;
      historyChart.data.datasets[3].data = rams;
      historyChart.update();
    } catch (e) {
      console.warn("History Fetch Fehler:", e);
    }
  }

  function setChartRange(range) {
    playLcarsBeep(1100, 1600);
    activeRange = range;
    document.querySelectorAll('.range-btn').forEach(btn => btn.classList.remove('active-range'));
    fetchHistoryData(range);
  }

  function toggleDataset(idx) {
    if (!historyChart) return;
    playLcarsBeep(880, 1320);
    const visible = historyChart.isDatasetVisible(idx);
    historyChart.setDatasetVisibility(idx, !visible);
    historyChart.update();
    const btn = document.getElementById('dsBtn' + idx);
    if (btn) {
      btn.style.opacity = !visible ? '1' : '0.4';
    }
  }

  // Hermes Donut Chart
  function initHermesChart() {
    const canvas = document.getElementById('hermesChart');
    if (!canvas || typeof Chart === 'undefined') return;
    const ctx = canvas.getContext('2d');

    const models = initialStats?.hermes?.models || [];
    const labels = models.map(m => m.model);
    const data = models.map(m => m.total_tokens);
    const palette = ['#eb943a', '#baa4e5', '#8899ff', '#ea9c72', '#edb378', '#cf4f4f', '#10b981'];

    hermesChart = new Chart(ctx, {
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
  }

  // Initialer Render nach Laden
  window.addEventListener('DOMContentLoaded', () => {
    renderStats(initialStats);
    initHistoryChart();
    initHermesChart();
  });
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
    app = Flask(__name__)

    @app.route("/")
    def index():
        stats = get_system_stats()
        stats_json = json.dumps(stats)
        return render_template_string(DASHBOARD_HTML, stats=stats, stats_json=stats_json)

    @app.route("/api/stats")
    def api_stats():
        return jsonify(get_system_stats())

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

    def run_server():
        print("[START] Starte Star Trek LCARS Dashboard Server auf http://0.0.0.0:5000 ...", flush=True)
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
            else:
                stats = get_system_stats()
                html = render_html_fallback(stats).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

        def log_message(self, format, *args):
            sys.stdout.write(f"[{self.log_date_time_string()}] {args[0]} {args[1]} {args[2]}\n")
            sys.stdout.flush()

    def run_server():
        server_address = ("0.0.0.0", 5000)
        httpd = HTTPServer(server_address, DashboardHTTPHandler)
        print("[START] Starte Standard-HTTP LCARS Server auf http://0.0.0.0:5000 ...", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    run_server()
