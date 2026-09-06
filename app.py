#!/usr/bin/env python3
"""
System Info Web-Dashboard für Raspberry Pi & KI-Agenten
Lauscht auf Port 5000 (bind 0.0.0.0) und zeigt:
- Systemmetriken: CPU, RAM, Temperatur, Festplatte, Uptime
- 24h In-Memory-Systemverlauf (Temperatur, CPU, Throttling, RAM, Festplatte)
- KI-Agenten Budgets & Spendings: OpenRouter (Limit, Daily/Weekly/Monthly) & Antigravity (Consumer Auth, Sessions)
- Hermes Agent Modell-Nutzung: Token-Anteile (Donut Chart) & Sessions/Kosten-Tabelle aller Profile
- Web-Services & Schnellzugriff (Tailscale, LAN, Cloudflare Quick-Tunnels)
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

# Flask Import mit Fallback zu http.server falls nicht installiert
try:
    from flask import Flask, jsonify, render_template_string, request
    USE_FLASK = True
except ImportError:
    print("[INFO] Flask nicht installiert, verwende Python Standardbibliothek (http.server).")
    USE_FLASK = False
    from http.server import HTTPServer, BaseHTTPRequestHandler


# ---------------------------------------------------------------------------
# Formatierungs-Helper
# ---------------------------------------------------------------------------
def format_tokens(n):
    """Formatiert Token-Zahlen lesbar (z.B. 11.65M, 174.7k)."""
    if not n:
        return "0"
    try:
        n = int(n)
        if n >= 1_000_000:
            return f"{n / 1_000_000:.2f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)
    except Exception:
        return str(n)


def format_usd(n):
    """Formatiert US-Dollar-Beträge sauber mit $."""
    if n is None:
        return "$0.00"
    try:
        val = float(n)
        if val == 0.0:
            return "$0.00"
        if val < 0.01:
            return f"${val:.4f}"
        return f"${val:.2f}"
    except Exception:
        return "$0.00"


def format_uptime(seconds):
    """Formatiert Sekunden in lesbaren Uptime-String (Tage, Std, Min, Sek)."""
    try:
        days, rem = divmod(int(seconds), 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0 or days > 0:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        parts.append(f"{secs}s")
        return " ".join(parts)
    except Exception:
        return "N/A"


# ---------------------------------------------------------------------------
# Systemmetriken Ausleser
# ---------------------------------------------------------------------------
def get_temperature():
    """Liest die CPU-Temperatur aus psutil, sysfs oder vcgencmd aus."""
    # 1. psutil sensors_temperatures
    if psutil and hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    for entry in entries:
                        if entry.current is not None and entry.current > 0:
                            return round(entry.current, 1), f"{entry.current:.1f} °C"
        except Exception:
            pass

    # 2. Linux Thermal Zones (/sys/class/thermal/thermal_zone*/temp)
    for path in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
        try:
            with open(path, "r") as f:
                val = float(f.read().strip())
                if val > 1000:
                    val = val / 1000.0
                return round(val, 1), f"{val:.1f} °C"
        except Exception:
            continue

    # 3. Raspberry Pi vcgencmd
    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"], text=True, timeout=2)
        val = float(out.replace("temp=", "").replace("'C", "").strip())
        return round(val, 1), f"{val:.1f} °C"
    except Exception:
        pass

    return None, "N/A"


def get_throttled_status():
    """Liest den Throttling-Status via 'vcgencmd get_throttled' auf dem Raspberry Pi aus.
    Bit 0x1 = Under-voltage / aktives Throttling.
    Gibt ein Tupel (is_throttled: bool, raw_hex: str) zurück.
    """
    try:
        out = subprocess.check_output(["vcgencmd", "get_throttled"], text=True, timeout=2).strip()
        if "=" in out:
            val = int(out.split("=")[1], 16)
            is_throttled = bool(val & 0x1)
            return is_throttled, hex(val)
    except Exception:
        pass
    return False, "0x0"


def get_ram_metrics():
    """Liest RAM-Auslastung und GB-Werte aus psutil oder /proc/meminfo."""
    if psutil:
        try:
            vm = psutil.virtual_memory()
            return {
                "percent": round(vm.percent, 1),
                "used_gb": round(vm.used / (1024**3), 2),
                "total_gb": round(vm.total / (1024**3), 2),
                "free_gb": round(vm.available / (1024**3), 2),
            }
        except Exception:
            pass

    # Fallback via /proc/meminfo
    try:
        meminfo = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = int(parts[1].split()[0])
        total = meminfo.get("MemTotal", 0) * 1024
        avail = meminfo.get("MemAvailable", 0) * 1024
        used = max(0, total - avail)
        percent = round((used / total) * 100, 1) if total else 0.0
        return {
            "percent": percent,
            "used_gb": round(used / (1024**3), 2),
            "total_gb": round(total / (1024**3), 2),
            "free_gb": round(avail / (1024**3), 2),
        }
    except Exception:
        return {"percent": 0.0, "used_gb": 0.0, "total_gb": 0.0, "free_gb": 0.0}


def get_disk_metrics():
    """Liest Festplattenbelegung für Root / aus psutil oder statvfs."""
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

    # Fallback via os.statvfs
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


def check_service_status(port=8000, host="127.0.0.1", timeout=0.3, max_age=4.0):
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
    """Fragt OpenRouter Key Info & Credits ab (~/.hermes/.env).
    Mit Thread-sicherem Background-Caching (TTL 60s), um 0ms Dashboard-Ladezeiten zu garantieren.
    """

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
                "error": "Kein OPENROUTER_API_KEY in ~/.hermes/.env gefunden",
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
            # 1. /api/v1/auth/key
            req_key = urllib.request.Request("https://openrouter.ai/api/v1/auth/key", headers=headers)
            with urllib.request.urlopen(req_key, timeout=4) as resp:
                key_json = json.loads(resp.read().decode("utf-8"))
            kdata = key_json.get("data", {})

            # 2. /api/v1/credits
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

            if limit and float(limit) > 0:
                pct = round(min(100.0, (usage / float(limit)) * 100), 1)
            else:
                pct = 0.0

            if pct >= 85:
                color = "danger"
            elif pct >= 65:
                color = "warning"
            else:
                color = "success"

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
            print(f"[WARN] OpenRouter API Fehler: {e}", file=sys.stderr)
            if self._cached_data and self._cached_data.get("available"):
                return self._cached_data
            return {
                "available": False,
                "error": str(e),
                "label": "OpenRouter Key (Offline/Timeout)",
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
                t = threading.Thread(target=self._async_fetch, name="OpenRouterFetcher", daemon=True)
                t.start()
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

    badge = "Aktiv (Google Consumer / Unbegrenzt/Free Quota)" if token_present else "Inaktiv / Nicht angemeldet"
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
    """Sammelt alle Systemmetriken inklusive Hermes- und Budget-Daten."""
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

    # RAM Metriken
    stats["ram"] = get_ram_metrics()

    # Disk Metriken (Root /)
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
        try:
            with open("/proc/uptime", "r") as f:
                uptime_sec = float(f.read().split()[0])
            stats["uptime"] = {
                "seconds": int(uptime_sec),
                "display": format_uptime(uptime_sec),
                "boot_time": "N/A",
            }
        except Exception:
            stats["uptime"] = {"seconds": 0, "display": "N/A", "boot_time": "N/A"}

    # Throttling-Status
    is_throttled, throttled_raw = get_throttled_status()
    stats["throttled"] = {
        "active": is_throttled,
        "raw": throttled_raw,
    }

    # Cloudflare Quick-Tunnel URLs
    stats["cloudflared"] = get_cloudflared_urls()

    # Web-Services Status
    stats["services"] = {
        "telemetry": {
            "online": check_service_status(8000),
            "port": 8000,
        }
    }

    # KI-Agenten: Budgets & Spendings
    stats["openrouter"] = openrouter_manager.get_budget()
    stats["antigravity"] = get_antigravity_status()

    # Hermes Agent Modell-Nutzung
    stats["hermes"] = hermes_manager.get_stats()

    return stats


# ---------------------------------------------------------------------------
# Range-Parsing & History-Store
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
    """Parst den range-Parameter und liefert (range_label, seconds)."""
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
            return val_clean, num * 60
        elif unit == "h":
            return val_clean, num * 3600
        elif unit == "d":
            return val_clean, num * 86400
    return "1h", 3600


class MetricsHistory:
    """In-Memory-History-Store mit 24h Retention und Thread-Safety.
    Erfasst kontinuierlich Metriken: Temperatur, CPU-Last, Throttling, RAM und Festplatte.
    """

    def __init__(self, retention_seconds=86400, sample_interval=10):
        self.retention_seconds = retention_seconds
        self.sample_interval = sample_interval
        self.lock = threading.RLock()
        self.samples = deque()
        self._running = False
        self._thread = None

    def record_current(self):
        temp_val, _ = get_temperature()
        if psutil:
            try:
                cpu_val = round(psutil.cpu_percent(interval=None), 1)
            except Exception:
                cpu_val = 0.0
        else:
            try:
                cores = os.cpu_count() or 1
                cpu_val = round(min(100.0, (os.getloadavg()[0] / cores) * 100), 1)
            except Exception:
                cpu_val = 0.0

        is_throttled, throttled_raw = get_throttled_status()
        ram_info = get_ram_metrics()
        disk_info = get_disk_metrics()

        now_dt = datetime.datetime.now()
        now_ts = time.time()

        sample = {
            "timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "time": now_dt.strftime("%H:%M:%S"),
            "time_short": now_dt.strftime("%H:%M"),
            "epoch": now_ts,
            "cpu": cpu_val,
            "temperature": temp_val,
            "throttled": is_throttled,
            "throttled_raw": throttled_raw,
            "ram_percent": ram_info.get("percent", 0.0),
            "ram_used_gb": ram_info.get("used_gb", 0.0),
            "disk_percent": disk_info.get("percent", 0.0),
            "disk_used_gb": disk_info.get("used_gb", 0.0),
        }
        with self.lock:
            self.samples.append(sample)
            cutoff = now_ts - self.retention_seconds
            while self.samples and self.samples[0]["epoch"] < cutoff:
                self.samples.popleft()
        return sample

    def get_samples(self, range_seconds=3600):
        cutoff = time.time() - range_seconds
        with self.lock:
            return [dict(s) for s in self.samples if s["epoch"] >= cutoff]

    def _worker(self):
        while self._running:
            for _ in range(self.sample_interval * 2):
                if not self._running:
                    return
                time.sleep(0.5)
            if not self._running:
                return
            try:
                self.record_current()
            except Exception as e:
                print(f"[WARN] Fehler bei History-Sampling: {e}", file=sys.stderr)

    def start(self):
        with self.lock:
            if not self._running:
                self._running = True
                try:
                    self.record_current()
                except Exception as e:
                    print(f"[WARN] Initiales Sampling fehlgeschlagen: {e}", file=sys.stderr)
                self._thread = threading.Thread(target=self._worker, name="MetricsHistoryWorker", daemon=True)
                self._thread.start()

    def stop(self):
        self._running = False


# Globaler In-Memory History Store
history_store = MetricsHistory(retention_seconds=86400, sample_interval=10)
history_store.start()


# ---------------------------------------------------------------------------
# HTML, CSS & JavaScript UI
# ---------------------------------------------------------------------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
  <title>System Dashboard - {{ stats.hostname }}</title>
  <noscript><meta http-equiv="refresh" content="5"></noscript>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg: #0b1120;
      --card-bg: rgba(30, 41, 59, 0.75);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-border-glow: rgba(56, 189, 248, 0.2);
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --primary-hover: #0284c7;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --bar-bg: #1e293b;
      --purple: #c084fc;
      --emerald: #10b981;
      --amber: #f59e0b;
    }
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      -webkit-tap-highlight-color: transparent;
    }
    html, body {
      max-width: 100%;
      overflow-x: hidden;
    }
    body {
      background: var(--bg);
      color: var(--text-main);
      min-height: 100vh;
      padding: 1.5rem 1rem;
      display: flex;
      flex-direction: column;
      align-items: center;
      -webkit-font-smoothing: antialiased;
    }
    .container {
      width: 100%;
      max-width: 1040px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.25rem;
      flex-wrap: wrap;
      gap: 1rem;
      border-bottom: 1px solid var(--card-border);
      padding-bottom: 1.25rem;
    }
    .title-group h1 {
      font-size: 1.75rem;
      font-weight: 700;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .title-group p {
      color: var(--text-muted);
      font-size: 0.88rem;
      margin-top: 0.25rem;
      word-break: break-word;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #34d399;
      padding: 0.4rem 0.85rem;
      border-radius: 9999px;
      font-size: 0.82rem;
      font-weight: 600;
      white-space: nowrap;
    }
    .dot {
      width: 8px;
      height: 8px;
      background-color: #10b981;
      border-radius: 50%;
      box-shadow: 0 0 8px #10b981;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(1.2); }
    }
    .refresh-bar-container {
      width: 100%;
      height: 3px;
      background: var(--card-border);
      border-radius: 2px;
      overflow: hidden;
      margin-bottom: 1.5rem;
    }
    .refresh-bar {
      height: 100%;
      background: var(--primary);
      width: 0%;
      transition: width 0.1s linear;
    }

    /* Cards Base (Glassmorphism) */
    .card {
      background: var(--card-bg);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 1.35rem;
      box-shadow: 0 4px 16px -2px rgba(0, 0, 0, 0.4);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: relative;
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.85rem;
    }
    .card-title {
      font-size: 0.88rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 0.45rem;
    }
    .card-icon {
      font-size: 1.3rem;
    }
    .card-value {
      font-size: 2.1rem;
      font-weight: 700;
      margin-bottom: 0.45rem;
      color: #fff;
      line-height: 1.2;
    }
    .card-value-sm {
      font-size: 1.65rem;
    }
    .card-subtitle {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-bottom: 0.9rem;
    }
    .progress-bar-bg {
      background: var(--bar-bg);
      height: 10px;
      border-radius: 6px;
      overflow: hidden;
      position: relative;
    }
    .progress-bar-fill {
      height: 100%;
      border-radius: 6px;
      transition: width 0.4s ease, background-color 0.4s ease;
    }
    .fill-cpu { background: #38bdf8; }
    .fill-ram { background: #c084fc; }
    .fill-disk { background: #10b981; }
    .fill-temp { background: #f97316; }

    .badge-temp {
      display: inline-block;
      padding: 0.2rem 0.6rem;
      border-radius: 6px;
      font-size: 0.8rem;
      font-weight: 600;
      margin-top: 0.25rem;
    }

    /* 1. History Graph (Ganz oben prominent) */
    .history-card {
      margin-bottom: 1.5rem;
      padding: 1.35rem;
    }
    .history-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.85rem;
      margin-bottom: 0.85rem;
    }
    .history-title-group {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
    }
    .history-title {
      font-size: 1.1rem;
      font-weight: 700;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .history-subtitle {
      font-size: 0.82rem;
      color: var(--text-muted);
    }
    .range-btn-group {
      display: inline-flex;
      background: rgba(15, 23, 42, 0.7);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 3px;
      gap: 3px;
    }
    .btn-range {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 0.45rem 0.85rem;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      min-height: 42px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      touch-action: manipulation;
    }
    .btn-range:hover {
      color: var(--text-main);
      background: rgba(255, 255, 255, 0.08);
    }
    .btn-range.active {
      background: var(--primary);
      color: #0b1120;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
    }
    .chart-container {
      position: relative;
      height: 330px;
      width: 100%;
    }
    .history-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 0.85rem;
      font-size: 0.8rem;
      color: var(--text-muted);
      flex-wrap: wrap;
      gap: 0.65rem;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
      padding-top: 0.85rem;
    }
    .history-legend-badges {
      display: flex;
      gap: 0.5rem;
      align-items: center;
      flex-wrap: wrap;
    }
    .legend-badge {
      background: rgba(15, 23, 42, 0.5);
      border: 1px solid rgba(255, 255, 255, 0.08);
      color: var(--text-muted);
      border-radius: 6px;
      padding: 0.4rem 0.65rem;
      font-size: 0.78rem;
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      cursor: pointer;
      transition: all 0.2s ease;
      min-height: 42px;
      touch-action: manipulation;
      user-select: none;
    }
    .legend-badge:hover {
      background: rgba(255, 255, 255, 0.06);
      color: #fff;
    }
    .legend-badge.active {
      background: rgba(255, 255, 255, 0.08);
      border-color: rgba(255, 255, 255, 0.25);
      color: var(--text-main);
      font-weight: 600;
    }
    .legend-line {
      width: 14px;
      height: 3px;
      border-radius: 2px;
      display: inline-block;
    }
    .legend-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      display: inline-block;
      border: 1.5px solid #ffffff;
      background: #ef4444;
      box-shadow: 0 0 6px rgba(239, 68, 68, 0.6);
    }

    /* 2. Status Grid (Darunter) */
    .status-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 1.25rem;
      margin-bottom: 2rem;
    }
    .card-uptime {
      grid-column: 1 / -1;
    }

    /* 3. KI-Agenten & Budgets Section */
    .ai-section {
      margin-bottom: 2rem;
    }
    .section-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-bottom: 1rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      padding-bottom: 0.75rem;
    }
    .section-title-group {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .section-icon {
      font-size: 1.25rem;
    }
    .section-title {
      font-size: 1.15rem;
      font-weight: 700;
      color: #fff;
    }
    .ai-header-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      background: rgba(168, 85, 247, 0.1);
      border: 1px solid rgba(168, 85, 247, 0.25);
      color: #c084fc;
      padding: 0.35rem 0.75rem;
      border-radius: 9999px;
      font-size: 0.78rem;
      font-weight: 600;
    }

    /* Budgets Grid */
    .budgets-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 1.25rem;
      margin-bottom: 1.25rem;
    }
    .budget-card {
      padding: 1.35rem;
    }
    .budget-subgrid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 0.65rem;
      margin-top: 1rem;
    }
    .budget-subitem {
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 8px;
      padding: 0.65rem 0.75rem;
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
    }
    .budget-sublabel {
      font-size: 0.72rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      font-weight: 600;
    }
    .budget-subval {
      font-size: 0.95rem;
      font-weight: 700;
      color: #fff;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .pill-tag {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.2rem 0.55rem;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 600;
      white-space: nowrap;
    }
    .pill-tag-active {
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .pill-tag-consumer {
      background: rgba(56, 189, 248, 0.15);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.3);
    }
    .fill-budget-success { background: #10b981; }
    .fill-budget-warning { background: #f59e0b; }
    .fill-budget-danger  { background: #ef4444; }

    /* Hermes Card */
    .hermes-card {
      padding: 1.35rem;
    }
    .hermes-stats-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      margin-bottom: 1.25rem;
    }
    .hermes-pill {
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.08);
      padding: 0.35rem 0.75rem;
      border-radius: 8px;
      font-size: 0.8rem;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      color: #cbd5e1;
    }
    .hermes-pill strong {
      color: #fff;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .hermes-body-grid {
      display: grid;
      grid-template-columns: 280px 1fr;
      gap: 1.5rem;
      align-items: center;
    }
    .donut-container {
      position: relative;
      height: 250px;
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .donut-center-text {
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      text-align: center;
      pointer-events: none;
    }
    .donut-center-val {
      font-size: 1.25rem;
      font-weight: 700;
      color: #fff;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .donut-center-label {
      font-size: 0.7rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .table-responsive {
      width: 100%;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }
    .hermes-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.82rem;
      text-align: left;
    }
    .hermes-table th {
      padding: 0.6rem 0.75rem;
      color: var(--text-muted);
      font-weight: 600;
      border-bottom: 1px solid var(--card-border);
      font-size: 0.74rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      white-space: nowrap;
    }
    .hermes-table td {
      padding: 0.65rem 0.75rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      vertical-align: middle;
      white-space: nowrap;
    }
    .hermes-table tr:last-child td {
      border-bottom: none;
    }
    .hermes-table tr:hover td {
      background: rgba(255, 255, 255, 0.02);
    }
    .model-cell {
      display: flex;
      align-items: center;
      gap: 0.55rem;
    }
    .model-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .model-name {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-weight: 600;
      color: #f1f5f9;
      font-size: 0.8rem;
    }
    .mono-num {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .cost-badge {
      display: inline-block;
      padding: 0.2rem 0.5rem;
      border-radius: 5px;
      font-size: 0.76rem;
      font-weight: 600;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .cost-paid {
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.3);
    }
    .cost-free {
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }

    /* 4. Web-Services & Schnellzugriff-Links */
    .services-section {
      margin-bottom: 2rem;
    }
    .services-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-bottom: 1rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      padding-bottom: 0.75rem;
    }
    .services-title-group {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .services-title {
      font-size: 1.15rem;
      font-weight: 700;
      color: #fff;
    }
    .services-network-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      background: rgba(56, 189, 248, 0.1);
      border: 1px solid rgba(56, 189, 248, 0.25);
      color: var(--primary);
      padding: 0.35rem 0.75rem;
      border-radius: 9999px;
      font-size: 0.78rem;
    }
    .services-network-badge code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      color: #e0f2fe;
      font-weight: 600;
      font-size: 0.8rem;
    }
    .ts-dot {
      width: 7px;
      height: 7px;
      background-color: var(--primary);
      border-radius: 50%;
      box-shadow: 0 0 6px var(--primary);
    }
    .services-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1rem;
    }
    .service-tile {
      background: var(--card-bg);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 1.1rem 1.15rem;
      text-decoration: none;
      color: inherit;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 0.75rem;
      min-height: 94px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
      transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease;
      touch-action: manipulation;
    }
    .service-tile:hover {
      border-color: var(--primary);
      background: rgba(36, 50, 72, 0.85);
      transform: translateY(-2px);
      box-shadow: 0 8px 16px -2px rgba(56, 189, 248, 0.18);
    }
    .service-tile:active {
      transform: scale(0.98);
      background: rgba(26, 35, 51, 0.95);
    }
    .service-tile-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.5rem;
    }
    .service-tile-brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      min-width: 0;
    }
    .service-tile-icon {
      font-size: 1.35rem;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 40px;
      height: 40px;
      background: rgba(255, 255, 255, 0.05);
      border-radius: 10px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      flex-shrink: 0;
    }
    .icon-xdcc {
      background: rgba(192, 132, 252, 0.12);
      border-color: rgba(192, 132, 252, 0.3);
    }
    .icon-dash {
      background: rgba(56, 189, 248, 0.12);
      border-color: rgba(56, 189, 248, 0.3);
    }
    .icon-lan {
      background: rgba(16, 185, 129, 0.12);
      border-color: rgba(16, 185, 129, 0.3);
    }
    .icon-cf {
      background: rgba(249, 115, 22, 0.15);
      border-color: rgba(249, 115, 22, 0.35);
    }
    .icon-telemetry {
      background: rgba(244, 63, 94, 0.12);
      border-color: rgba(244, 63, 94, 0.3);
    }
    .service-tile-name {
      font-weight: 700;
      font-size: 1rem;
      color: #fff;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .service-tile-route {
      font-size: 0.78rem;
      color: var(--text-muted);
      margin-top: 2px;
    }
    .badge-port {
      font-size: 0.75rem;
      font-weight: 700;
      padding: 0.25rem 0.55rem;
      border-radius: 6px;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .badge-port-3000 {
      background: rgba(192, 132, 252, 0.18);
      color: #c084fc;
      border: 1px solid rgba(192, 132, 252, 0.35);
    }
    .badge-port-5000 {
      background: rgba(56, 189, 248, 0.18);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.35);
    }
    .badge-port-lan {
      background: rgba(16, 185, 129, 0.18);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.35);
    }
    .badge-port-cf {
      background: rgba(249, 115, 22, 0.18);
      color: #fb923c;
      border: 1px solid rgba(249, 115, 22, 0.35);
    }
    .badge-port-8000 {
      background: rgba(244, 63, 94, 0.18);
      color: #fb7185;
      border: 1px solid rgba(244, 63, 94, 0.35);
    }
    .service-tile-badges {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      flex-shrink: 0;
    }
    .service-status-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      font-size: 0.72rem;
      font-weight: 600;
      padding: 0.22rem 0.5rem;
      border-radius: 6px;
      white-space: nowrap;
      transition: background-color 0.2s ease, color 0.2s ease, border-color 0.2s ease;
    }
    .status-badge-online {
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.35);
    }
    .status-badge-offline {
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.35);
    }
    .status-dot-sm {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: currentColor;
    }
    .status-badge-online .status-dot-sm {
      background: #10b981;
      box-shadow: 0 0 6px #10b981;
    }
    .status-badge-offline .status-dot-sm {
      background: #ef4444;
    }
    .service-tile-bottom {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.5rem;
      background: rgba(15, 23, 42, 0.65);
      border: 1px solid rgba(255, 255, 255, 0.05);
      padding: 0.45rem 0.75rem;
      border-radius: 8px;
    }
    .service-tile-url {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.78rem;
      color: #cbd5e1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .service-tile-arrow {
      font-size: 0.95rem;
      font-weight: 700;
      color: var(--primary);
      flex-shrink: 0;
      transition: transform 0.15s ease;
    }
    .service-tile:hover .service-tile-arrow {
      transform: translate(2px, -2px);
    }

    footer {
      margin-top: 1.5rem;
      color: var(--text-muted);
      font-size: 0.82rem;
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 0.5rem;
      border-top: 1px solid var(--card-border);
      padding-top: 1rem;
      width: 100%;
    }

    /* Responsive Breakpoints (< 768px) */
    @media (max-width: 768px) {
      .budgets-grid {
        grid-template-columns: 1fr;
      }
      .hermes-body-grid {
        grid-template-columns: 1fr;
        gap: 1.25rem;
      }
      .donut-container {
        height: 220px;
      }
    }

    /* Mobile Breakpoints (< 640px) */
    @media (max-width: 640px) {
      body {
        padding: 0.75rem 0.5rem;
      }
      header {
        flex-direction: column;
        align-items: flex-start;
        gap: 0.6rem;
        margin-bottom: 1rem;
        padding-bottom: 0.75rem;
      }
      .title-group h1 {
        font-size: 1.35rem;
      }
      .title-group p {
        font-size: 0.78rem;
      }
      .status-badge {
        font-size: 0.75rem;
        padding: 0.3rem 0.65rem;
      }
      .refresh-bar-container {
        margin-bottom: 1rem;
      }
      .history-card {
        padding: 0.85rem;
        margin-bottom: 1rem;
        border-radius: 10px;
      }
      .history-header {
        flex-direction: column;
        align-items: stretch;
        gap: 0.65rem;
      }
      .history-title {
        font-size: 0.95rem;
      }
      .history-subtitle {
        font-size: 0.75rem;
      }
      .range-btn-group {
        width: 100%;
        display: flex;
        justify-content: space-between;
      }
      .btn-range {
        flex: 1;
        min-height: 42px;
        padding: 0.4rem 0.15rem;
        font-size: 0.78rem;
      }
      .chart-container {
        height: 240px;
      }
      .history-footer {
        flex-direction: column;
        align-items: flex-start;
        gap: 0.55rem;
        font-size: 0.74rem;
        padding-top: 0.6rem;
      }
      .history-legend-badges {
        gap: 0.35rem;
        width: 100%;
      }
      .legend-badge {
        flex: 1 1 45%;
        min-height: 42px;
        justify-content: center;
        font-size: 0.74rem;
        padding: 0.35rem 0.4rem;
      }
      .status-grid {
        grid-template-columns: 1fr;
        gap: 0.75rem;
        margin-bottom: 1.25rem;
      }
      .card {
        padding: 1rem;
        border-radius: 10px;
      }
      .card-title {
        font-size: 0.82rem;
      }
      .card-value {
        font-size: 1.8rem;
        margin-bottom: 0.3rem;
      }
      .card-value-sm {
        font-size: 1.45rem;
      }
      .card-subtitle {
        font-size: 0.78rem;
        margin-bottom: 0.7rem;
      }
      .budget-card, .hermes-card {
        padding: 1rem;
      }
      .budget-subgrid {
        grid-template-columns: 1fr 1fr;
        gap: 0.5rem;
      }
      .services-section {
        margin-bottom: 1.25rem;
      }
      .services-header {
        flex-direction: column;
        align-items: flex-start;
        gap: 0.5rem;
      }
      .services-title {
        font-size: 1.05rem;
      }
      .services-network-badge {
        width: 100%;
        box-sizing: border-box;
        font-size: 0.72rem;
        padding: 0.35rem 0.55rem;
      }
      .services-network-badge code {
        font-size: 0.72rem;
      }
      .services-grid {
        grid-template-columns: 1fr;
        gap: 0.75rem;
      }
      .service-tile {
        padding: 0.9rem 1rem;
        min-height: 86px;
        border-radius: 10px;
      }
      footer {
        font-size: 0.75rem;
        margin-top: 1rem;
      }
    }

    /* Small Mobile Screen (< 400px) */
    @media (max-width: 400px) {
      body {
        padding: 0.5rem 0.35rem;
      }
      .chart-container {
        height: 200px;
      }
      .btn-range {
        font-size: 0.72rem;
        min-height: 42px;
      }
      .card-value {
        font-size: 1.55rem;
      }
      .budget-subgrid {
        grid-template-columns: 1fr;
      }
      .legend-badge {
        flex: 1 1 100%;
      }
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="title-group">
        <h1>📊 System &amp; AI Dashboard</h1>
        <p><span id="hostname">{{ stats.hostname }}</span> &bull; <span id="platform">{{ stats.platform }}</span></p>
      </div>
      <div class="status-badge">
        <span class="dot"></span>
        <span>Auto-Refresh (5s)</span>
      </div>
    </header>

    <div class="refresh-bar-container">
      <div class="refresh-bar" id="refreshBar"></div>
    </div>

    <!-- 1. GANZ OBEN: VERLAUFSGRAPH FÜR SYSTEMMETRIKEN (KOMBINIERTER CHART) -->
    <div class="card history-card">
      <div>
        <div class="history-header">
          <div class="history-title-group">
            <div class="history-title">
              <span>📈</span>
              <span>System-Verlauf (Metriken &amp; Stabilität)</span>
            </div>
            <div class="history-subtitle">
              24h-Verlauf &bull; Duale Achse &bull; Klickbare Datasets für RAM &amp; Disk
            </div>
          </div>
          <div class="range-btn-group" role="group" aria-label="Zeitraum auswählen">
            <button type="button" class="btn-range" data-range="10min">10min</button>
            <button type="button" class="btn-range" data-range="30min">30min</button>
            <button type="button" class="btn-range active" data-range="1h">1h</button>
            <button type="button" class="btn-range" data-range="12h">12h</button>
            <button type="button" class="btn-range" data-range="24h">24h</button>
          </div>
        </div>
        <div class="chart-container">
          <canvas id="historyChart"></canvas>
        </div>
        <div class="history-footer">
          <div class="history-legend-badges" id="historyLegendBadges">
            <button type="button" class="legend-badge active" data-ds="0" onclick="toggleDataset(0)" title="Klicken zum Ein-/Ausblenden">
              <span class="legend-line" style="background: #f97316;"></span>
              <span>Temperatur (°C)</span>
            </button>
            <button type="button" class="legend-badge active" data-ds="1" onclick="toggleDataset(1)" title="Klicken zum Ein-/Ausblenden">
              <span class="legend-line" style="background: #38bdf8;"></span>
              <span>CPU (%)</span>
            </button>
            <button type="button" class="legend-badge active" data-ds="2" onclick="toggleDataset(2)" title="Klicken zum Ein-/Ausblenden">
              <span class="legend-dot"></span>
              <span>Throttling</span>
            </button>
            <button type="button" class="legend-badge" data-ds="3" onclick="toggleDataset(3)" title="Klicken zum Einblenden von RAM">
              <span class="legend-line" style="background: #c084fc;"></span>
              <span>RAM (%)</span>
            </button>
            <button type="button" class="legend-badge" data-ds="4" onclick="toggleDataset(4)" title="Klicken zum Einblenden von Disk">
              <span class="legend-line" style="background: #10b981;"></span>
              <span>Festplatte (%)</span>
            </button>
          </div>
          <div id="historyStatus">Lade Verlauf...</div>
        </div>
      </div>
    </div>

    <!-- 2. STATUS-KARTEN GRID (CPU, TEMPERATUR, RAM, FESTPLATTE, UPTIME) -->
    <div class="status-grid">
      <!-- 1. CPU-Auslastung -->
      <div class="card">
        <div>
          <div class="card-header">
            <span class="card-title">CPU-Auslastung</span>
            <span class="card-icon">⚡</span>
          </div>
          <div class="card-value" id="cpuPercent">{{ stats.cpu.percent }}%</div>
          <div class="card-subtitle" id="cpuCores">{{ stats.cpu.cores }} Kerne</div>
        </div>
        <div class="progress-bar-bg">
          <div class="progress-bar-fill fill-cpu" id="cpuBar" style="width: {{ stats.cpu.percent }}%;"></div>
        </div>
      </div>

      <!-- 2. CPU-Temperatur -->
      <div class="card">
        <div>
          <div class="card-header">
            <span class="card-title">CPU-Temperatur</span>
            <span class="card-icon">🌡️</span>
          </div>
          <div class="card-value" id="tempValue">{{ stats.temperature.display }}</div>
          <div class="card-subtitle">
            <span class="badge-temp" id="tempBadge" style="background: rgba(249, 115, 22, 0.2); color: #fb923c;">Status</span>
          </div>
        </div>
        <div class="progress-bar-bg">
          <div class="progress-bar-fill fill-temp" id="tempBar" style="width: {% if stats.temperature.value %}{{ [stats.temperature.value, 100]|min }}{% else %}0{% endif %}%;"></div>
        </div>
      </div>

      <!-- 3. Arbeitsspeicher (RAM) -->
      <div class="card">
        <div>
          <div class="card-header">
            <span class="card-title">Arbeitsspeicher (RAM)</span>
            <span class="card-icon">🧠</span>
          </div>
          <div class="card-value" id="ramPercent">{{ stats.ram.percent }}%</div>
          <div class="card-subtitle" id="ramDetails">{{ stats.ram.used_gb }} GB / {{ stats.ram.total_gb }} GB verwendet</div>
        </div>
        <div class="progress-bar-bg">
          <div class="progress-bar-fill fill-ram" id="ramBar" style="width: {{ stats.ram.percent }}%;"></div>
        </div>
      </div>

      <!-- 4. Festplatte (/) -->
      <div class="card">
        <div>
          <div class="card-header">
            <span class="card-title">Festplatte (/)</span>
            <span class="card-icon">💾</span>
          </div>
          <div class="card-value" id="diskPercent">{{ stats.disk.percent }}%</div>
          <div class="card-subtitle" id="diskDetails">{{ stats.disk.used_gb }} GB / {{ stats.disk.total_gb }} GB ({{ stats.disk.free_gb }} GB frei)</div>
        </div>
        <div class="progress-bar-bg">
          <div class="progress-bar-fill fill-disk" id="diskBar" style="width: {{ stats.disk.percent }}%;"></div>
        </div>
      </div>

      <!-- 5. System Uptime -->
      <div class="card card-uptime">
        <div>
          <div class="card-header">
            <span class="card-title">System Uptime</span>
            <span class="card-icon">⏱️</span>
          </div>
          <div class="card-value card-value-sm" id="uptimeDisplay">{{ stats.uptime.display }}</div>
          <div class="card-subtitle" id="bootTime">Systemstart: {{ stats.uptime.boot_time }}</div>
        </div>
      </div>
    </div>

    <!-- 3. KI-AGENTEN: BUDGETS & MODELL-NUTZUNG -->
    <section class="ai-section" aria-label="KI-Agenten und Budgets">
      <div class="section-header">
        <div class="section-title-group">
          <span class="section-icon">🤖</span>
          <h2 class="section-title">KI-Agenten &amp; API-Budgets</h2>
        </div>
        <div class="ai-header-badge">
          <span class="ts-dot" style="background:#c084fc;box-shadow:0 0 6px #c084fc;"></span>
          <span>OpenRouter &bull; Antigravity &bull; Hermes</span>
        </div>
      </div>

      <!-- 3A. BUDGETS GRID (OpenRouter + Antigravity) -->
      <div class="budgets-grid">
        <!-- OpenRouter Card -->
        <div class="card budget-card">
          <div>
            <div class="card-header">
              <span class="card-title">
                <span>🌐</span>
                <span>OpenRouter API Budget</span>
              </span>
              <span class="pill-tag pill-tag-active" id="orKeyLabel">Key aktiv</span>
            </div>
            <div class="card-value card-value-sm" id="orSpentValue">$0.00 verbraucht</div>
            <div class="card-subtitle" id="orRemainingValue">Lade Budget...</div>
            <div class="progress-bar-bg" style="margin-bottom: 0.5rem;">
              <div class="progress-bar-fill fill-budget-success" id="orProgressBar" style="width: 0%;"></div>
            </div>
          </div>
          <div class="budget-subgrid">
            <div class="budget-subitem">
              <span class="budget-sublabel">📅 Heute</span>
              <span class="budget-subval" id="orUsageDaily">$0.00</span>
            </div>
            <div class="budget-subitem">
              <span class="budget-sublabel">📆 7 Tage</span>
              <span class="budget-subval" id="orUsageWeekly">$0.00</span>
            </div>
            <div class="budget-subitem">
              <span class="budget-sublabel">🗓️ 30 Tage</span>
              <span class="budget-subval" id="orUsageMonthly">$0.00</span>
            </div>
            <div class="budget-subitem">
              <span class="budget-sublabel">💳 Account Credits</span>
              <span class="budget-subval" id="orTotalCredits">$0.00</span>
            </div>
          </div>
        </div>

        <!-- Antigravity Card -->
        <div class="card budget-card">
          <div>
            <div class="card-header">
              <span class="card-title">
                <span>⚡</span>
                <span>Google Antigravity Agent</span>
              </span>
              <span class="pill-tag pill-tag-consumer" id="agyStatusBadge">Google Consumer</span>
            </div>
            <div class="card-value card-value-sm" id="agySessionsVal">0 Sessions</div>
            <div class="card-subtitle" id="agySubtitle">Lokale agy-Sessions gespeichert</div>
            <div class="progress-bar-bg" style="margin-bottom: 0.5rem;">
              <div class="progress-bar-fill" style="width: 100%; background: #38bdf8;"></div>
            </div>
          </div>
          <div class="budget-subgrid">
            <div class="budget-subitem">
              <span class="budget-sublabel">🔑 Auth-Status</span>
              <span class="budget-subval" id="agyAuthMethod" style="color: #38bdf8;">Consumer</span>
            </div>
            <div class="budget-subitem">
              <span class="budget-sublabel">🔄 Letzte Aktivität</span>
              <span class="budget-subval" id="agyLatestAct">Heute</span>
            </div>
            <div class="budget-subitem" style="grid-column: 1 / -1;">
              <span class="budget-sublabel">📊 Kontingent / Quota</span>
              <span class="budget-subval" style="color: #34d399; font-size: 0.85rem;" id="agyQuotaDesc">Unbegrenzt (Free Gemini Flash &amp; Pro Quota)</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 3B. HERMES AGENT MODELL-NUTZUNG CARD -->
      <div class="card hermes-card">
        <div class="card-header">
          <div>
            <div class="card-title">
              <span>🤖</span>
              <span>Hermes Agent Modell-Nutzung</span>
            </div>
            <div class="card-subtitle" style="margin-bottom: 0; margin-top: 0.2rem;">
              Aggregierte Session- &amp; Token-Statistiken aller Profile aus SQLite
            </div>
          </div>
        </div>

        <!-- Stats Chips Bar -->
        <div class="hermes-stats-bar">
          <div class="hermes-pill">
            <span>🎯 Sessions:</span>
            <strong id="hermesTotalSessions">0</strong>
          </div>
          <div class="hermes-pill">
            <span>🔢 Token gesamt:</span>
            <strong id="hermesTotalTokens">0</strong>
          </div>
          <div class="hermes-pill">
            <span>💵 Geschätzte Kosten:</span>
            <strong id="hermesTotalCost">$0.00</strong>
          </div>
          <div class="hermes-pill">
            <span>📁 Profile:</span>
            <strong id="hermesProfilesCount">default</strong>
          </div>
        </div>

        <!-- Chart & Table Grid -->
        <div class="hermes-body-grid">
          <!-- Donut Chart -->
          <div class="donut-container">
            <canvas id="hermesChart"></canvas>
            <div class="donut-center-text">
              <div class="donut-center-val" id="donutTotalTokens">0</div>
              <div class="donut-center-label">Token</div>
            </div>
          </div>

          <!-- Table -->
          <div class="table-responsive">
            <table class="hermes-table">
              <thead>
                <tr>
                  <th>Modell</th>
                  <th>Sessions</th>
                  <th>In / Out Token</th>
                  <th>Gesamt</th>
                  <th>Kosten (USD)</th>
                </tr>
              </thead>
              <tbody id="hermesTableBody">
                <tr><td colspan="5" style="text-align:center; color: var(--text-muted); padding: 1.5rem;">Lade Hermes-Modelle...</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

    <!-- 4. WEB-SERVICES & SCHNELLZUGRIFF-LINKS -->
    <section class="services-section" aria-label="Web-Services und Schnellzugriff">
      <div class="services-header">
        <div class="services-title-group">
          <span style="font-size: 1.25rem;">🌐</span>
          <h2 class="services-title">Web-Services &amp; Schnellzugriff</h2>
        </div>
        <div class="services-network-badge">
          <span class="ts-dot"></span>
          <span>Tailscale: <code>100.88.215.98</code> &bull; <code>pimmel.tail3a782b.ts.net</code></span>
        </div>
      </div>

      <div class="services-grid">
        <!-- Service 1: xdcc-load-cast Domain -->
        <a href="http://pimmel.tail3a782b.ts.net:3000" target="_blank" rel="noopener noreferrer" class="service-tile">
          <div class="service-tile-top">
            <div class="service-tile-brand">
              <span class="service-tile-icon icon-xdcc">🚀</span>
              <div>
                <div class="service-tile-name">xdcc-load-cast</div>
                <div class="service-tile-route">Tailscale Domain</div>
              </div>
            </div>
            <span class="badge-port badge-port-3000">Port 3000</span>
          </div>
          <div class="service-tile-bottom">
            <span class="service-tile-url">pimmel.tail3a782b.ts.net:3000</span>
            <span class="service-tile-arrow">↗</span>
          </div>
        </a>

        <!-- Service 2: TelemetryVault -->
        <a href="http://pimmel.tail3a782b.ts.net:8000" target="_blank" rel="noopener noreferrer" class="service-tile">
          <div class="service-tile-top">
            <div class="service-tile-brand">
              <span class="service-tile-icon icon-telemetry">🏎️</span>
              <div>
                <div class="service-tile-name">TelemetryVault</div>
                <div class="service-tile-route">ACC Telemetry Dashboard</div>
              </div>
            </div>
            <div class="service-tile-badges">
              <span id="telemetryStatus" class="service-status-badge {% if stats.services and stats.services.telemetry and stats.services.telemetry.online %}status-badge-online{% else %}status-badge-offline{% endif %}" title="Dienst-Status">
                <span class="status-dot-sm"></span>{% if stats.services and stats.services.telemetry and stats.services.telemetry.online %}Online{% else %}Offline{% endif %}
              </span>
              <span class="badge-port badge-port-8000">Port 8000</span>
            </div>
          </div>
          <div class="service-tile-bottom">
            <span class="service-tile-url">pimmel.tail3a782b.ts.net:8000</span>
            <span class="service-tile-arrow">↗</span>
          </div>
        </a>

        <!-- Service 3: xdcc-load-cast Tailscale IP -->
        <a href="http://100.88.215.98:3000" target="_blank" rel="noopener noreferrer" class="service-tile">
          <div class="service-tile-top">
            <div class="service-tile-brand">
              <span class="service-tile-icon icon-xdcc">⚡</span>
              <div>
                <div class="service-tile-name">xdcc-load-cast</div>
                <div class="service-tile-route">Tailscale IP</div>
              </div>
            </div>
            <span class="badge-port badge-port-3000">Port 3000</span>
          </div>
          <div class="service-tile-bottom">
            <span class="service-tile-url">100.88.215.98:3000</span>
            <span class="service-tile-arrow">↗</span>
          </div>
        </a>

        <!-- Service 3: xdcc-load-cast Lokales LAN -->
        <a id="lanTileXdcc" href="http://localhost:3000" target="_blank" rel="noopener noreferrer" class="service-tile">
          <div class="service-tile-top">
            <div class="service-tile-brand">
              <span class="service-tile-icon icon-lan">🏠</span>
              <div>
                <div class="service-tile-name">xdcc-load-cast</div>
                <div class="service-tile-route">Lokales LAN / Host</div>
              </div>
            </div>
            <span class="badge-port badge-port-lan">Port 3000</span>
          </div>
          <div class="service-tile-bottom">
            <span class="service-tile-url" id="lanUrlXdcc">Aktueller Host :3000</span>
            <span class="service-tile-arrow">↗</span>
          </div>
        </a>

        <!-- Service 4: Dashboard Domain -->
        <a href="http://pimmel.tail3a782b.ts.net:5000" target="_blank" rel="noopener noreferrer" class="service-tile">
          <div class="service-tile-top">
            <div class="service-tile-brand">
              <span class="service-tile-icon icon-dash">📊</span>
              <div>
                <div class="service-tile-name">System Dashboard</div>
                <div class="service-tile-route">Tailscale Domain</div>
              </div>
            </div>
            <span class="badge-port badge-port-5000">Port 5000</span>
          </div>
          <div class="service-tile-bottom">
            <span class="service-tile-url">pimmel.tail3a782b.ts.net:5000</span>
            <span class="service-tile-arrow">↗</span>
          </div>
        </a>

        <!-- Service 5: Dashboard Tailscale IP -->
        <a href="http://100.88.215.98:5000" target="_blank" rel="noopener noreferrer" class="service-tile">
          <div class="service-tile-top">
            <div class="service-tile-brand">
              <span class="service-tile-icon icon-dash">📈</span>
              <div>
                <div class="service-tile-name">System Dashboard</div>
                <div class="service-tile-route">Tailscale IP</div>
              </div>
            </div>
            <span class="badge-port badge-port-5000">Port 5000</span>
          </div>
          <div class="service-tile-bottom">
            <span class="service-tile-url">100.88.215.98:5000</span>
            <span class="service-tile-arrow">↗</span>
          </div>
        </a>

        <!-- Service 6: Dashboard Cloudflare Tunnel -->
        <a id="cfTileDash" href="{% if stats.cloudflared and stats.cloudflared.dashboard %}{{ stats.cloudflared.dashboard }}{% else %}#{% endif %}" target="_blank" rel="noopener noreferrer" class="service-tile">
          <div class="service-tile-top">
            <div class="service-tile-brand">
              <span class="service-tile-icon icon-cf">☁️</span>
              <div>
                <div class="service-tile-name">Dashboard (Cloudflare)</div>
                <div class="service-tile-route">Global Public Tunnel</div>
              </div>
            </div>
            <span class="badge-port badge-port-cf">Quick Tunnel</span>
          </div>
          <div class="service-tile-bottom">
            <span class="service-tile-url" id="cfUrlDash">{% if stats.cloudflared and stats.cloudflared.dashboard %}{{ stats.cloudflared.dashboard }}{% else %}Verbinde Tunnel...{% endif %}</span>
            <span class="service-tile-arrow">↗</span>
          </div>
        </a>

        <!-- Service 7: xdcc Cloudflare Tunnel -->
        <a id="cfTileXdcc" href="{% if stats.cloudflared and stats.cloudflared.xdcc %}{{ stats.cloudflared.xdcc }}{% else %}#{% endif %}" target="_blank" rel="noopener noreferrer" class="service-tile">
          <div class="service-tile-top">
            <div class="service-tile-brand">
              <span class="service-tile-icon icon-cf">☁️</span>
              <div>
                <div class="service-tile-name">xdcc (Cloudflare)</div>
                <div class="service-tile-route">Global Public Tunnel</div>
              </div>
            </div>
            <span class="badge-port badge-port-cf">Quick Tunnel</span>
          </div>
          <div class="service-tile-bottom">
            <span class="service-tile-url" id="cfUrlXdcc">{% if stats.cloudflared and stats.cloudflared.xdcc %}{{ stats.cloudflared.xdcc }}{% else %}Verbinde Tunnel...{% endif %}</span>
            <span class="service-tile-arrow">↗</span>
          </div>
        </a>
      </div>
    </section>

    <footer>
      <span>Port: 5000 (bind 0.0.0.0)</span>
      <span id="lastUpdated">Stand: {{ stats.timestamp }}</span>
    </footer>
  </div>

  <script>
    // Initiale Daten aus Server-Rendering
    const initialStats = {{ stats_json | safe }};

    function updateTempBadge(temp) {
      const badge = document.getElementById('tempBadge');
      const bar = document.getElementById('tempBar');
      if (temp === null || temp === undefined) {
        badge.textContent = 'Unbekannt';
        badge.style.background = '#334155';
        badge.style.color = '#94a3b8';
        if (bar) bar.style.width = '0%';
        return;
      }
      if (bar) {
        bar.style.width = Math.min(temp, 100) + '%';
      }
      if (temp < 55) {
        badge.textContent = 'Kühl';
        badge.style.background = 'rgba(16, 185, 129, 0.2)';
        badge.style.color = '#34d399';
        if (bar) bar.style.background = '#10b981';
      } else if (temp < 75) {
        badge.textContent = 'Normal';
        badge.style.background = 'rgba(245, 158, 11, 0.2)';
        badge.style.color = '#fbbf24';
        if (bar) bar.style.background = '#f59e0b';
      } else {
        badge.textContent = 'Heiß';
        badge.style.background = 'rgba(239, 68, 68, 0.2)';
        badge.style.color = '#f87171';
        if (bar) bar.style.background = '#ef4444';
      }
    }

    // Palette für Modelle
    const MODEL_PALETTE = [
      '#38bdf8', '#a855f7', '#10b981', '#f59e0b',
      '#ec4899', '#06b6d4', '#f97316', '#6366f1',
      '#84cc16', '#e11d48', '#14b8a6', '#8b5cf6'
    ];

    let hermesChart = null;

    function renderOpenRouter(orData) {
      if (!orData) return;
      const spentEl = document.getElementById('orSpentValue');
      const remEl = document.getElementById('orRemainingValue');
      const barEl = document.getElementById('orProgressBar');
      const keyLabel = document.getElementById('orKeyLabel');

      if (!orData.available) {
        spentEl.textContent = 'Offline';
        remEl.textContent = orData.error || 'Nicht erreichbar';
        barEl.style.width = '0%';
        keyLabel.textContent = 'Key inaktiv';
        keyLabel.className = 'pill-tag';
        keyLabel.style.background = 'rgba(239, 68, 68, 0.15)';
        keyLabel.style.color = '#f87171';
        return;
      }

      keyLabel.textContent = orData.label || 'OpenRouter';
      keyLabel.className = 'pill-tag pill-tag-active';

      const usageStr = orData.usage_formatted || '$0.00';
      const limitStr = orData.limit_formatted || 'Unbegrenzt';
      const remStr = orData.remaining_formatted || 'N/A';

      if (orData.limit !== null && orData.limit !== undefined) {
        spentEl.textContent = usageStr + ' / ' + limitStr;
        remEl.textContent = remStr + ' verbleibend (' + orData.percent_used + '% genutzt)';
      } else {
        spentEl.textContent = usageStr + ' verbraucht';
        remEl.textContent = 'Kein Ausgabenlimit festgelegt';
      }

      const pct = Math.min(100, Math.max(0, orData.percent_used || 0));
      barEl.style.width = pct + '%';
      if (pct >= 85) {
        barEl.className = 'progress-bar-fill fill-budget-danger';
      } else if (pct >= 65) {
        barEl.className = 'progress-bar-fill fill-budget-warning';
      } else {
        barEl.className = 'progress-bar-fill fill-budget-success';
      }

      document.getElementById('orUsageDaily').textContent = orData.daily_formatted || '$0.00';
      document.getElementById('orUsageWeekly').textContent = orData.weekly_formatted || '$0.00';
      document.getElementById('orUsageMonthly').textContent = orData.monthly_formatted || '$0.00';
      document.getElementById('orTotalCredits').textContent = (orData.total_credits_formatted || '$0.00') + ' (Rest: ' + (orData.credits_remaining_formatted || '$0.00') + ')';
    }

    function renderAntigravity(agyData) {
      if (!agyData) return;
      document.getElementById('agySessionsVal').textContent = (agyData.sessions_count || 0) + ' Sessions';
      document.getElementById('agyStatusBadge').textContent = agyData.badge || 'Aktiv';
      document.getElementById('agyAuthMethod').textContent = agyData.account_label || 'Consumer';
      document.getElementById('agyLatestAct').textContent = agyData.latest_activity || 'N/A';
      if (agyData.quota) {
        document.getElementById('agyQuotaDesc').textContent = agyData.quota;
      }
    }

    function renderHermes(hermesData) {
      if (!hermesData) return;
      document.getElementById('hermesTotalSessions').textContent = hermesData.total_sessions || 0;
      document.getElementById('hermesTotalTokens').textContent = hermesData.total_tokens_formatted || '0';
      document.getElementById('hermesTotalCost').textContent = hermesData.total_cost_formatted || '$0.00';
      document.getElementById('donutTotalTokens').textContent = hermesData.total_tokens_formatted || '0';

      const profiles = (hermesData.profiles && hermesData.profiles.length) ? hermesData.profiles.join(', ') : 'default';
      document.getElementById('hermesProfilesCount').textContent = profiles;

      const tbody = document.getElementById('hermesTableBody');
      const models = hermesData.models || [];

      if (!models.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color: var(--text-muted); padding: 1rem;">Keine Hermes-Sessions in SQLite gefunden.</td></tr>';
      } else {
        let html = '';
        models.forEach(function(m, idx) {
          const color = MODEL_PALETTE[idx % MODEL_PALETTE.length];
          const isFree = (m.cost_usd === 0 || m.cost_formatted === '$0.00');
          const costClass = isFree ? 'cost-badge cost-free' : 'cost-badge cost-paid';
          const costText = isFree ? 'Free ($0.00)' : m.cost_formatted;

          html += '<tr>' +
            '<td><div class="model-cell"><span class="model-dot" style="background:' + color + ';"></span><span class="model-name">' + m.model + '</span></div></td>' +
            '<td><span class="pill-tag" style="background:rgba(255,255,255,0.06);color:#fff;">' + m.sessions + '</span></td>' +
            '<td class="mono-num" style="color:var(--text-muted);">' + m.input_formatted + ' in &bull; ' + m.output_formatted + ' out</td>' +
            '<td class="mono-num"><strong>' + m.total_formatted + '</strong> <span style="font-size:0.75rem;color:var(--text-muted);">(' + m.percent_tokens + '%)</span></td>' +
            '<td><span class="' + costClass + '">' + costText + '</span></td>' +
          '</tr>';
        });
        tbody.innerHTML = html;
      }

      // Donut Chart aktualisieren / initialisieren
      updateHermesChart(models);
    }

    function updateHermesChart(models) {
      const canvas = document.getElementById('hermesChart');
      if (!canvas) return;
      if (typeof Chart === 'undefined') {
        setTimeout(function() { updateHermesChart(models); }, 100);
        return;
      }

      const labels = models.map(function(m) { return m.model; });
      const data = models.map(function(m) { return m.total_tokens; });
      const bgColors = models.map(function(m, idx) { return MODEL_PALETTE[idx % MODEL_PALETTE.length]; });

      if (hermesChart) {
        hermesChart.data.labels = labels;
        hermesChart.data.datasets[0].data = data;
        hermesChart.data.datasets[0].backgroundColor = bgColors;
        hermesChart.update('none');
        return;
      }

      const ctx = canvas.getContext('2d');
      hermesChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
          labels: labels,
          datasets: [{
            data: data,
            backgroundColor: bgColors,
            borderColor: '#1e293b',
            borderWidth: 2,
            hoverOffset: 6
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: '70%',
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: '#1e293b',
              titleColor: '#f8fafc',
              bodyColor: '#cbd5e1',
              borderColor: '#475569',
              borderWidth: 1,
              padding: 10,
              callbacks: {
                label: function(context) {
                  const m = models[context.dataIndex];
                  if (!m) return '';
                  return [
                    ' Token: ' + m.total_formatted + ' (' + m.percent_tokens + '%)',
                    ' Sessions: ' + m.sessions,
                    ' Kosten: ' + m.cost_formatted
                  ];
                }
              }
            }
          }
        }
      });
    }

    // Rendert alle Daten auf einen Schlag
    function renderDashboard(data) {
      if (!data) return;

      // CPU
      if (data.cpu) {
        document.getElementById('cpuPercent').textContent = data.cpu.percent + '%';
        document.getElementById('cpuBar').style.width = data.cpu.percent + '%';
        document.getElementById('cpuCores').textContent = data.cpu.cores + ' Kerne';
      }

      // RAM
      if (data.ram) {
        document.getElementById('ramPercent').textContent = data.ram.percent + '%';
        document.getElementById('ramBar').style.width = data.ram.percent + '%';
        document.getElementById('ramDetails').textContent = data.ram.used_gb + ' GB / ' + data.ram.total_gb + ' GB verwendet';
      }

      // Disk
      if (data.disk) {
        document.getElementById('diskPercent').textContent = data.disk.percent + '%';
        document.getElementById('diskBar').style.width = data.disk.percent + '%';
        document.getElementById('diskDetails').textContent = data.disk.used_gb + ' GB / ' + data.disk.total_gb + ' GB (' + data.disk.free_gb + ' GB frei)';
      }

      // Temp
      if (data.temperature) {
        document.getElementById('tempValue').textContent = data.temperature.display;
        updateTempBadge(data.temperature.value);
      }

      // Uptime
      if (data.uptime) {
        document.getElementById('uptimeDisplay').textContent = data.uptime.display;
        if (data.uptime.boot_time) {
          document.getElementById('bootTime').textContent = 'Systemstart: ' + data.uptime.boot_time;
        }
      }

      // Hostname / Platform / Timestamp
      if (data.hostname) document.getElementById('hostname').textContent = data.hostname;
      if (data.platform) document.getElementById('platform').textContent = data.platform;
      if (data.timestamp) document.getElementById('lastUpdated').textContent = 'Stand: ' + data.timestamp;

      // Cloudflare Quick-Tunnel URLs
      if (data.cloudflared) {
        const dashTile = document.getElementById('cfTileDash');
        const dashUrl = document.getElementById('cfUrlDash');
        if (dashTile && dashUrl && data.cloudflared.dashboard) {
          dashTile.href = data.cloudflared.dashboard;
          dashUrl.textContent = data.cloudflared.dashboard.replace('https://', '');
        }
        const xdccTile = document.getElementById('cfTileXdcc');
        const xdccUrl = document.getElementById('cfUrlXdcc');
        if (xdccTile && xdccUrl && data.cloudflared.xdcc) {
          xdccTile.href = data.cloudflared.xdcc;
          xdccUrl.textContent = data.cloudflared.xdcc.replace('https://', '');
        }
      }

      // OpenRouter & Antigravity
      renderOpenRouter(data.openrouter);
      renderAntigravity(data.antigravity);

      // Hermes Agent
      renderHermes(data.hermes);

      // Web-Services Status
      if (data.services && data.services.telemetry) {
        const telBadge = document.getElementById('telemetryStatus');
        if (telBadge) {
          const isOnline = !!data.services.telemetry.online;
          telBadge.className = 'service-status-badge ' + (isOnline ? 'status-badge-online' : 'status-badge-offline');
          telBadge.innerHTML = '<span class="status-dot-sm"></span>' + (isOnline ? 'Online' : 'Offline');
        }
      }
    }

    // Initialer Render aus serverseitig übergebenem JSON
    if (initialStats) {
      renderDashboard(initialStats);
    }

    async function fetchStats() {
      try {
        const response = await fetch('/api/stats');
        if (!response.ok) return;
        const data = await response.json();
        renderDashboard(data);
      } catch (err) {
        console.error('Fehler beim Abrufen der Systemstatistiken:', err);
      }
    }

    // --- Verlaufsgraph (Chart.js) Logik ---
    let currentRange = '1h';
    let currentHistorySamples = [];
    let historyChart = null;

    function updateLegendBadges() {
      if (!historyChart) return;
      const badges = document.querySelectorAll('#historyLegendBadges .legend-badge');
      badges.forEach(function(btn) {
        const dsIdx = parseInt(btn.getAttribute('data-ds'), 10);
        const visible = historyChart.isDatasetVisible(dsIdx);
        if (visible) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      });
    }

    function toggleDataset(idx) {
      if (!historyChart) return;
      const isVisible = historyChart.isDatasetVisible(idx);
      historyChart.setDatasetVisibility(idx, !isVisible);
      historyChart.update();
      updateLegendBadges();
    }

    function initHistoryChart() {
      const canvas = document.getElementById('historyChart');
      if (!canvas) return;
      if (typeof Chart === 'undefined') {
        setTimeout(initHistoryChart, 100);
        return;
      }
      const ctx = canvas.getContext('2d');
      historyChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: [],
          datasets: [
            // Dataset 0: Temperatur (°C) -> Standardmäßig AKTIV (orange)
            {
              label: 'Temperatur (°C)',
              data: [],
              borderColor: '#f97316',
              backgroundColor: 'rgba(249, 115, 22, 0.08)',
              yAxisID: 'yTemp',
              tension: 0.25,
              borderWidth: 2,
              pointRadius: [],
              pointBackgroundColor: [],
              pointBorderColor: [],
              pointBorderWidth: [],
              fill: false,
              spanGaps: true,
              hidden: false
            },
            // Dataset 1: CPU-Auslastung (%) -> Standardmäßig AKTIV (sky blue)
            {
              label: 'CPU-Auslastung (%)',
              data: [],
              borderColor: '#38bdf8',
              backgroundColor: 'rgba(56, 189, 248, 0.08)',
              yAxisID: 'yPercent',
              tension: 0.25,
              borderWidth: 2,
              pointRadius: 2,
              pointHoverRadius: 5,
              pointBackgroundColor: '#38bdf8',
              fill: false,
              spanGaps: true,
              hidden: false
            },
            // Dataset 2: Throttling aktiv -> Standardmäßig AKTIV (rot)
            {
              label: 'Throttling aktiv (Bit 0x1)',
              data: [],
              yAxisID: 'yTemp',
              showLine: false,
              pointRadius: 7,
              pointHoverRadius: 9,
              pointBackgroundColor: '#ef4444',
              pointBorderColor: '#ffffff',
              pointBorderWidth: 2,
              pointStyle: 'circle',
              hidden: false
            },
            // Dataset 3: RAM-Auslastung (%) -> Standardmäßig DEAKTIVIERT (lila/violett)
            {
              label: 'RAM-Auslastung (%)',
              data: [],
              borderColor: '#c084fc',
              backgroundColor: 'rgba(192, 132, 252, 0.08)',
              yAxisID: 'yPercent',
              tension: 0.25,
              borderWidth: 2,
              pointRadius: 2,
              pointHoverRadius: 5,
              pointBackgroundColor: '#c084fc',
              fill: false,
              spanGaps: true,
              hidden: true
            },
            // Dataset 4: Festplatte (%) -> Standardmäßig DEAKTIVIERT (grün/smaragd)
            {
              label: 'Festplatte (%)',
              data: [],
              borderColor: '#10b981',
              backgroundColor: 'rgba(16, 185, 129, 0.08)',
              yAxisID: 'yPercent',
              tension: 0.25,
              borderWidth: 2,
              pointRadius: 2,
              pointHoverRadius: 5,
              pointBackgroundColor: '#10b981',
              fill: false,
              spanGaps: true,
              hidden: true
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: {
            duration: 250
          },
          interaction: {
            mode: 'index',
            intersect: false
          },
          plugins: {
            legend: {
              position: 'top',
              labels: {
                color: '#94a3b8',
                font: { size: window.innerWidth <= 640 ? 10 : 12 },
                usePointStyle: true,
                boxWidth: window.innerWidth <= 640 ? 6 : 8,
                boxHeight: window.innerWidth <= 640 ? 6 : 8,
                padding: window.innerWidth <= 640 ? 6 : 12
              },
              onClick: function(e, legendItem, legend) {
                const index = legendItem.datasetIndex;
                const ci = legend.chart;
                if (ci.isDatasetVisible(index)) {
                  ci.hide(index);
                  legendItem.hidden = true;
                } else {
                  ci.show(index);
                  legendItem.hidden = false;
                }
                updateLegendBadges();
              }
            },
            tooltip: {
              backgroundColor: '#1e293b',
              titleColor: '#f8fafc',
              bodyColor: '#cbd5e1',
              borderColor: '#475569',
              borderWidth: 1,
              padding: window.innerWidth <= 640 ? 8 : 10,
              callbacks: {
                label: function(context) {
                  if (context.datasetIndex === 2) return null;
                  const label = context.dataset.label || '';
                  const val = context.parsed.y;
                  if (val === null || val === undefined) return null;
                  const idx = context.dataIndex;
                  const s = currentHistorySamples[idx] || {};

                  if (context.datasetIndex === 0) {
                    return ' ' + label + ': ' + val + ' °C';
                  } else if (context.datasetIndex === 1) {
                    return ' ' + label + ': ' + val + ' %';
                  } else if (context.datasetIndex === 3) {
                    const gb = s.ram_used_gb !== undefined ? ' (' + s.ram_used_gb + ' GB)' : '';
                    return ' ' + label + ': ' + val + ' %' + gb;
                  } else if (context.datasetIndex === 4) {
                    const gb = s.disk_used_gb !== undefined ? ' (' + s.disk_used_gb + ' GB)' : '';
                    return ' ' + label + ': ' + val + ' %' + gb;
                  }
                  return ' ' + label + ': ' + val;
                },
                afterBody: function(tooltipItems) {
                  if (!tooltipItems || !tooltipItems.length) return '';
                  const idx = tooltipItems[0].dataIndex;
                  const s = currentHistorySamples[idx];
                  if (s && s.throttled) {
                    return '⚠️ THROTTLING AKTIV! (Bit 0x1 / under-voltage)';
                  }
                  return '';
                }
              }
            }
          },
          scales: {
            x: {
              grid: {
                color: 'rgba(255, 255, 255, 0.05)'
              },
              ticks: {
                color: '#94a3b8',
                maxTicksLimit: window.innerWidth <= 640 ? 5 : 10,
                maxRotation: 0,
                font: { size: window.innerWidth <= 640 ? 9 : 11 }
              }
            },
            yTemp: {
              type: 'linear',
              position: 'left',
              title: {
                display: window.innerWidth > 640,
                text: 'Temperatur (°C)',
                color: '#f97316',
                font: { size: 11, weight: '600' }
              },
              grid: {
                color: 'rgba(255, 255, 255, 0.06)'
              },
              ticks: {
                color: '#f97316',
                font: { size: window.innerWidth <= 640 ? 9 : 11 },
                callback: function(val) { return window.innerWidth <= 640 ? val + '°' : val + ' °C'; }
              },
              suggestedMin: 25,
              suggestedMax: 80
            },
            yPercent: {
              type: 'linear',
              position: 'right',
              title: {
                display: window.innerWidth > 640,
                text: 'Auslastung (%)',
                color: '#94a3b8',
                font: { size: 11, weight: '600' }
              },
              grid: {
                drawOnChartArea: false
              },
              ticks: {
                color: '#94a3b8',
                font: { size: window.innerWidth <= 640 ? 9 : 11 },
                callback: function(val) { return val + ' %'; }
              },
              min: 0,
              max: 100
            }
          }
        }
      });

      updateLegendBadges();
      fetchHistory(currentRange);
    }

    async function fetchHistory(range) {
      const r = range || currentRange;
      try {
        const response = await fetch('/api/history?range=' + encodeURIComponent(r));
        if (!response.ok) return;
        const data = await response.json();
        const samples = Array.isArray(data) ? data : (data.samples || []);
        currentHistorySamples = samples;
        updateChart(samples, r);
      } catch (err) {
        console.error('Fehler beim Abrufen der History-Daten:', err);
        const statusEl = document.getElementById('historyStatus');
        if (statusEl) statusEl.textContent = 'Fehler beim Laden';
      }
    }

    function updateChart(samples, range) {
      if (!historyChart) return;

      const labels = samples.map(function(s) {
        return s.time || (s.timestamp ? s.timestamp.split(' ')[1] : '');
      });
      const temps = samples.map(function(s) { return s.temperature; });
      const cpus = samples.map(function(s) { return s.cpu; });
      const throttledData = samples.map(function(s) {
        return s.throttled ? (s.temperature !== null ? s.temperature : s.cpu) : null;
      });
      const rams = samples.map(function(s) { return (s.ram_percent !== undefined && s.ram_percent !== null) ? s.ram_percent : null; });
      const disks = samples.map(function(s) { return (s.disk_percent !== undefined && s.disk_percent !== null) ? s.disk_percent : null; });

      const isMany = samples.length > 60;
      const tempPointRadius = samples.map(function(s) {
        return s.throttled ? 6 : (isMany ? 0 : 2);
      });
      const tempPointBg = samples.map(function(s) {
        return s.throttled ? '#ef4444' : '#f97316';
      });
      const tempPointBorder = samples.map(function(s) {
        return s.throttled ? '#ffffff' : '#f97316';
      });
      const tempPointWidth = samples.map(function(s) {
        return s.throttled ? 2 : 1;
      });

      historyChart.data.labels = labels;

      // Dataset 0: Temp
      historyChart.data.datasets[0].data = temps;
      historyChart.data.datasets[0].pointRadius = tempPointRadius;
      historyChart.data.datasets[0].pointBackgroundColor = tempPointBg;
      historyChart.data.datasets[0].pointBorderColor = tempPointBorder;
      historyChart.data.datasets[0].pointBorderWidth = tempPointWidth;

      // Dataset 1: CPU
      historyChart.data.datasets[1].data = cpus;
      historyChart.data.datasets[1].pointRadius = isMany ? 0 : 2;

      // Dataset 2: Throttled
      historyChart.data.datasets[2].data = throttledData;

      // Dataset 3: RAM
      historyChart.data.datasets[3].data = rams;
      historyChart.data.datasets[3].pointRadius = isMany ? 0 : 2;

      // Dataset 4: Festplatte
      historyChart.data.datasets[4].data = disks;
      historyChart.data.datasets[4].pointRadius = isMany ? 0 : 2;

      historyChart.update('none');

      const statusEl = document.getElementById('historyStatus');
      if (statusEl) {
        const throttledCount = samples.filter(function(s) { return s.throttled; }).length;
        let text = samples.length + ' Datenpunkte (' + range + ')';
        if (throttledCount > 0) {
          text += ' &bull; <span style="color:#ef4444;font-weight:700;">⚠️ ' + throttledCount + ' Throttling-Ereignis(se)</span>';
        } else {
          text += ' &bull; <span style="color:#10b981;">✓ Kein Throttling</span>';
        }
        statusEl.innerHTML = text;
      }
    }

    // Range-Buttons Listener
    document.querySelectorAll('.btn-range').forEach(function(btn) {
      btn.addEventListener('click', function() {
        document.querySelectorAll('.btn-range').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        currentRange = btn.getAttribute('data-range');
        fetchHistory(currentRange);
      });
    });

    // Refresh-Balken & periodisches Update
    let progress = 0;
    const intervalMs = 5000;
    const stepMs = 100;
    const bar = document.getElementById('refreshBar');
    let historyCycle = 0;

    setInterval(() => {
      progress += (stepMs / intervalMs) * 100;
      if (progress >= 100) {
        progress = 0;
        fetchStats();
        historyCycle++;
        if (historyCycle % 2 === 0) {
          fetchHistory(currentRange);
        }
      }
      bar.style.width = progress + '%';
    }, stepMs);

    // Initialisiere Verlaufschart
    initHistoryChart();

    // Responsives Verhalten bei Bildschirmdrehung & Größenänderung
    window.addEventListener('resize', () => {
      if (historyChart) {
        const isMobile = window.innerWidth <= 640;
        historyChart.options.scales.x.ticks.maxTicksLimit = isMobile ? 5 : 10;
        historyChart.options.scales.x.ticks.font = { size: isMobile ? 9 : 11 };
        historyChart.options.scales.yTemp.title.display = !isMobile;
        historyChart.options.scales.yTemp.ticks.font = { size: isMobile ? 9 : 11 };
        historyChart.options.scales.yPercent.title.display = !isMobile;
        historyChart.options.scales.yPercent.ticks.font = { size: isMobile ? 9 : 11 };
        historyChart.options.plugins.legend.labels.font = { size: isMobile ? 10 : 12 };
        historyChart.update('none');
      }
      if (hermesChart) {
        hermesChart.update('none');
      }
    });

    // Dynamischen lokalen LAN-Link für aktuellen Hostnamen setzen
    (function setupLanLinks() {
      try {
        const currentHost = window.location.hostname || 'localhost';
        const lanTile = document.getElementById('lanTileXdcc');
        const lanUrl = document.getElementById('lanUrlXdcc');
        if (lanTile && lanUrl) {
          lanTile.href = 'http://' + currentHost + ':3000';
          lanUrl.textContent = currentHost + ':3000';
        }
      } catch (err) {
        console.error('Fehler bei LAN-Link Setup:', err);
      }
    })();
  </script>
</body>
</html>
"""


def render_html_fallback(stats):
    """Einfacher HTML-Renderer für Standardbibliothek ohne Jinja2."""
    temp_val = stats["temperature"]["value"] or 0
    temp_min_100 = min(temp_val, 100)
    stats_json = json.dumps(stats)
    is_tel_online = bool(stats.get("services", {}).get("telemetry", {}).get("online", False))
    tel_class = "status-badge-online" if is_tel_online else "status-badge-offline"
    tel_text = "Online" if is_tel_online else "Offline"
    html = DASHBOARD_HTML
    replacements = {
        "{{ stats_json | safe }}": stats_json,
        "{{ stats.hostname }}": str(stats.get("hostname", "")),
        "{{ stats.platform }}": str(stats.get("platform", "")),
        "{{ stats.cpu.percent }}": str(stats.get("cpu", {}).get("percent", 0.0)),
        "{{ stats.cpu.cores }}": str(stats.get("cpu", {}).get("cores", 1)),
        "{{ stats.ram.percent }}": str(stats.get("ram", {}).get("percent", 0.0)),
        "{{ stats.ram.used_gb }}": str(stats.get("ram", {}).get("used_gb", 0.0)),
        "{{ stats.ram.total_gb }}": str(stats.get("ram", {}).get("total_gb", 0.0)),
        "{{ stats.disk.percent }}": str(stats.get("disk", {}).get("percent", 0.0)),
        "{{ stats.disk.used_gb }}": str(stats.get("disk", {}).get("used_gb", 0.0)),
        "{{ stats.disk.total_gb }}": str(stats.get("disk", {}).get("total_gb", 0.0)),
        "{{ stats.disk.free_gb }}": str(stats.get("disk", {}).get("free_gb", 0.0)),
        "{{ stats.temperature.display }}": str(stats.get("temperature", {}).get("display", "N/A")),
        "{{ stats.temperature.value or 'null' }}": str(stats.get("temperature", {}).get("value") if stats.get("temperature", {}).get("value") is not None else "null"),
        "{% if stats.temperature.value %}{{ [stats.temperature.value, 100]|min }}{% else %}0{% endif %}": str(temp_min_100),
        "{{ stats.uptime.display }}": str(stats.get("uptime", {}).get("display", "N/A")),
        "{{ stats.uptime.boot_time }}": str(stats.get("uptime", {}).get("boot_time", "N/A")),
        "{{ stats.timestamp }}": str(stats.get("timestamp", "")),
        "{% if stats.cloudflared and stats.cloudflared.dashboard %}{{ stats.cloudflared.dashboard }}{% else %}#{% endif %}": str(stats.get("cloudflared", {}).get("dashboard") or "#"),
        "{% if stats.cloudflared and stats.cloudflared.dashboard %}{{ stats.cloudflared.dashboard }}{% else %}Verbinde Tunnel...{% endif %}": str(stats.get("cloudflared", {}).get("dashboard") or "Verbinde Tunnel..."),
        "{% if stats.cloudflared and stats.cloudflared.xdcc %}{{ stats.cloudflared.xdcc }}{% else %}#{% endif %}": str(stats.get("cloudflared", {}).get("xdcc") or "#"),
        "{% if stats.cloudflared and stats.cloudflared.xdcc %}{{ stats.cloudflared.xdcc }}{% else %}Verbinde Tunnel...{% endif %}": str(stats.get("cloudflared", {}).get("xdcc") or "Verbinde Tunnel..."),
        "{% if stats.services and stats.services.telemetry and stats.services.telemetry.online %}status-badge-online{% else %}status-badge-offline{% endif %}": tel_class,
        "{% if stats.services and stats.services.telemetry and stats.services.telemetry.online %}Online{% else %}Offline{% endif %}": tel_text,
    }
    for key, val in replacements.items():
        html = html.replace(key, val)
    return html


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
        stats = get_system_stats()
        return jsonify(stats)

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

    def run_server():
        print("[START] Starte Flask Dashboard Server auf http://0.0.0.0:5000 ...", flush=True)
        app.run(host="0.0.0.0", port=5000, debug=False)

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
                range_label, seconds = parse_range_param(range_param)
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
        print("[START] Starte Standard-HTTP Dashboard Server auf http://0.0.0.0:5000 ...", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    run_server()
