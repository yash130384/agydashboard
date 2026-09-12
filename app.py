#!/usr/bin/env python3
"""
LCARS System Dashboard & Webserver Discovery Engine (Star Trek LCARS Interface)
Lauscht auf Port 5000 (bind 0.0.0.0) und bietet:
- Star Trek LCARS Fullscreen Benutzeroberfläche (Vorlage: https://www.thelcars.com/)
- 4 Hauptkategorien ohne Nummern: SYSTEM, SERVICES, KI-AGENTEN, CONFIG
- System & 24h Sensor-Verlauf in einer gemeinsamen Kategorie (SYSTEM)
- Web-Services & 5-Minuten Webserver-Scanner in einer gemeinsamen Kategorie (SERVICES)
- 5 umschaltbare LCARS Farbmodi (Classic, Nemesis Blue, Lower Decks, Red Alert, Voyager Bio-Neural)
- Keine horizontalen Scrollbalken (alle Inhalte responsive und bildschirmgerecht aufbereitet)
- Zuverlässig initialisierte Chart.js Diagramme für Systemverlauf und KI-Modell-Verbrauch
- 5-Minuten Hintergrund-Scanner für alle laufenden Prozesse mit LAN-, Tailscale- und Cloudflared-Adressen
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

    def get_url(self, port: int) -> str | None:
        with self._lock:
            t = self._tunnels.get(port)
            if t and t.get("url"):
                return t["url"]

        for pattern in [f"port_{port}.url", f"{port}.url"]:
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

        log_file = f"/tmp/cloudflared_{port}.log"
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
        existing_url = self.get_url(port)
        if existing_url:
            return existing_url

        with self._lock:
            if port in self._tunnels:
                t = self._tunnels[port]
                if t.get("proc") and t["proc"].poll() is None:
                    return t.get("url")

            log_file = f"/tmp/cloudflared_{port}.log"
            try:
                if os.path.exists(log_file):
                    os.remove(log_file)
            except Exception:
                pass

            try:
                cmd = [
                    self.binary_path,
                    "tunnel",
                    "--url", f"http://127.0.0.1:{port}",
                    "--logfile", log_file,
                    "--no-autoupdate",
                ]
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

    def _watcher_loop(self):
        while self._running:
            try:
                with self._lock:
                    ports = list(self._tunnels.keys())

                for p in ports:
                    with self._lock:
                        t = self._tunnels.get(p)
                    if not t:
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
            time.sleep(2)

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
            t_end = time.time() + 3.0
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
    .data-cascade {
      display: flex;
      gap: 0.5rem;
      font-family: var(--mono-family);
      font-size: 0.75rem;
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
    .pill-cfg   { background-color: var(--c-gold); }

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
    .btn-scan-trigger:active { transform: scale(0.98); }

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
      .data-cascade { display: none; }
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
      main { max-height: none; overflow: visible; }
    }
  </style>
</head>
<body>

<section class="wrap-standard">
  <!-- OBERER RAHMEN -->
  <div class="wrap">
    <div class="left-frame-top">
      <button onclick="playLcarsBeep(880, 1760); switchCategory('system')">LCARS 47<br><span style="font-size:0.8rem; opacity:0.85;">AGY-PI</span></button>
      <div style="font-size: 0.8rem; font-family: var(--mono-family);">ONLINE</div>
    </div>
    <div class="right-frame-top">
      <div class="banner-container">
        <div class="banner-title" id="bannerSectionTitle">LCARS / SYSTEM & SENSOR VERLAUF</div>
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

  <!-- MITTLERER RAHMEN: 4 KATEGORIEN OHNE ZAHLEN -->
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
        <button class="lcars-pill-btn pill-cfg" onclick="switchCategory('config')" id="btn-cat-config">
          CONFIG
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

        <!-- KATEGORIE 1: SYSTEM & 24H SENSOR VERLAUF (KOMBINIERT) -->
        <section class="lcars-section active-section" id="section-system">
          <div class="lcars-header-bar">
            <h2>SYSTEM STATUS & 24H VERLAUF</h2>
            <span class="lcars-pill-tag">LIVE ODN METRIKEN</span>
          </div>

          <!-- Telemetrie Readout Cards -->
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

          <!-- 24H SENSOR VERLAUFS-CHART -->
          <div class="lcars-card" style="margin-top: 1.25rem; padding: 1.1rem; width: 100%; min-width: 0;">
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
                <span style="font-size:0.75rem; color:rgba(255,255,255,0.4); padding-left:0.2rem;">☁️ Kein Cloudflare-Tunnel</span>
                {% endif %}
              </div>
            </div>
            {% endfor %}
          </div>
        </section>

        <!-- KATEGORIE 3: KI-AGENTEN & HERMES -->
        <section class="lcars-section" id="section-agents">
          <div class="lcars-header-bar">
            <h2>KI-AGENTEN & HERMES NUTZUNG</h2>
            <span class="lcars-pill-tag">NEURAL ARCHIV</span>
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
          <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap:1rem; margin-top:1.25rem; width:100%; min-width:0;">
            <!-- Donut Canvas Card -->
            <div class="lcars-card" style="min-height:320px; display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; min-width:0;">
              <div class="card-head-title" style="align-self:flex-start; margin-bottom:0.75rem;">MODELL TOKEN-VERTEILUNG</div>
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

        <!-- KATEGORIE 4: CONFIG & FARBMODI -->
        <section class="lcars-section" id="section-config">
          <div class="lcars-header-bar">
            <h2>SYSTEM CONFIG & FARBMODI</h2>
            <span class="lcars-pill-tag">TERMINAL OPTIONEN</span>
          </div>

          <p style="margin-bottom:1.1rem; color:var(--c-gold);">
            WÄHLEN SIE DEN LCARS-FARBMODUS FÜR DAS GESAMTE DASHBOARD AUS. EINSTELLUNGEN WERDEN AUTOMATISCH GESPEICHERT.
          </p>

          <div class="theme-selector-grid">
            <!-- Classic TNG -->
            <button class="theme-btn active-theme" onclick="setLcarsTheme('classic')" id="theme-btn-classic">
              <span style="font-weight:700; font-size:1.15rem;">CLASSIC 24TH C.</span>
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

            <!-- Red Alert -->
            <button class="theme-btn" onclick="setLcarsTheme('redalert')" id="theme-btn-redalert">
              <span style="font-weight:700; font-size:1.15rem;">RED ALERT</span>
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
              <span style="font-weight:700; font-size:1.15rem;">BIO-NEURAL</span>
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

          <!-- Weitere Optionen -->
          <div class="readout-grid">
            <div class="lcars-card">
              <div class="card-head-title">AUDIO & SOUND-EFFEKTE</div>
              <p style="font-size:0.88rem; color:var(--c-gold); margin:0.6rem 0;">
                AUTHENTISCHE LCARS-BEEPS VIA WEB AUDIO API SYNTHESIZER.
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
    <div>LCARS TERMINAL 47 // HOST: {{ stats.hostname }} // STAND: <span id="footerTimestamp">{{ stats.timestamp }}</span></div>
    <div>DESIGN: <a href="https://www.thelcars.com/" target="_blank">THELCARS.COM</a> // AGY DASHBOARD</div>
  </footer>
</section>

<!-- ==========================================================================
     JAVASCRIPT: LCARS CONTROLLER, CHARTS, THEMES & APIS
     ========================================================================== -->
<script>
  const initialStats = {{ stats_json | safe }};
  var historyChart = null;
  var hermesChart = null;
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
    } catch (e) {}
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

  // Stardate Berechnung
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

  // 4 KATEGORIEN NAVIGATION (OHNE ZAHLEN)
  const CATEGORY_NAMES = {
    'system': 'LCARS / SYSTEM & SENSOR VERLAUF',
    'services': 'LCARS / SERVICES & PROZESS-SCANNER',
    'agents': 'LCARS / KI-AGENTEN & HERMES ARCHIV',
    'config': 'LCARS / SYSTEM CONFIG & FARBMODI'
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
        initHermesChart();
      }, 60);
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
      updateServicesCards(data.discovered_servers);
    }

    // LAN IP
    if (data.lan_ip) {
      const el = document.getElementById('sysLanIp');
      if (el) el.textContent = data.lan_ip;
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
        </div>
      `;
    });
    grid.innerHTML = html;
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

    // Zentrierte LCARS Beschriftung
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
  });

  // Initialer Boot-Ablauf
  function bootDashboard() {
    renderStats(initialStats);
    ensureChart(() => {
      initHistoryChart();
    });
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
