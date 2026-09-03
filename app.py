#!/usr/bin/env python3
"""
System Info Web-Dashboard
Lauscht auf Port 5000 (bind 0.0.0.0) und zeigt Systemmetriken an:
- CPU-Auslastung
- RAM-Verbrauch
- Temperatur
- Uptime
- Festplattenbelegung
- 24h In-Memory-Verlaufsgraph (Temperatur & CPU-Auslastung) mit Throttling-Markierungen
Mit automatischem Refresh alle 5 Sekunden (Verlauf alle 10s).
"""

import datetime
import glob
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
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


def format_uptime(seconds):
    """Formatiert Sekunden in lesbaren Uptime-String (Tage, Std, Min, Sek)."""
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


def get_system_stats():
    """Sammelt alle Systemmetriken."""
    stats = {
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # CPU Metriken
    if psutil:
        try:
            stats["cpu"] = {
                "percent": psutil.cpu_percent(interval=None),
                "cores": psutil.cpu_count(logical=True),
                "cores_physical": psutil.cpu_count(logical=False),
            }
        except Exception:
            stats["cpu"] = {"percent": 0.0, "cores": os.cpu_count() or 1}
    else:
        # Fallback via os.getloadavg
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
    if psutil:
        try:
            vm = psutil.virtual_memory()
            stats["ram"] = {
                "percent": vm.percent,
                "used_gb": round(vm.used / (1024**3), 2),
                "total_gb": round(vm.total / (1024**3), 2),
                "free_gb": round(vm.available / (1024**3), 2),
            }
        except Exception:
            stats["ram"] = {"percent": 0, "used_gb": 0, "total_gb": 0, "free_gb": 0}
    else:
        # Fallback via /proc/meminfo
        try:
            meminfo = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        meminfo[parts[0].strip()] = int(parts[1].split()[0])
            total = meminfo.get("MemTotal", 0) * 1024
            avail = meminfo.get("MemAvailable", 0) * 1024
            used = total - avail
            percent = round((used / total) * 100, 1) if total else 0
            stats["ram"] = {
                "percent": percent,
                "used_gb": round(used / (1024**3), 2),
                "total_gb": round(total / (1024**3), 2),
                "free_gb": round(avail / (1024**3), 2),
            }
        except Exception:
            stats["ram"] = {"percent": 0, "used_gb": 0, "total_gb": 0, "free_gb": 0}

    # Disk Metriken (Root /)
    if psutil:
        try:
            du = psutil.disk_usage("/")
            stats["disk"] = {
                "percent": du.percent,
                "used_gb": round(du.used / (1024**3), 2),
                "total_gb": round(du.total / (1024**3), 2),
                "free_gb": round(du.free / (1024**3), 2),
            }
        except Exception:
            stats["disk"] = {"percent": 0, "used_gb": 0, "total_gb": 0, "free_gb": 0}
    else:
        # Fallback via os.statvfs
        try:
            st = os.statvfs("/")
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
            percent = round((used / total) * 100, 1) if total else 0
            stats["disk"] = {
                "percent": percent,
                "used_gb": round(used / (1024**3), 2),
                "total_gb": round(total / (1024**3), 2),
                "free_gb": round(free / (1024**3), 2),
            }
        except Exception:
            stats["disk"] = {"percent": 0, "used_gb": 0, "total_gb": 0, "free_gb": 0}

    # Temperatur
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

    # Throttling-Status ermitteln (Pi vcgencmd)
    is_throttled, throttled_raw = get_throttled_status()
    stats["throttled"] = {
        "active": is_throttled,
        "raw": throttled_raw,
    }

    return stats


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
    import re
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
    Erfasst kontinuierlich Metriken (Temperatur, CPU-Last, Throttling).
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
                cpu_val = psutil.cpu_percent(interval=None)
            except Exception:
                cpu_val = 0.0
        else:
            try:
                cores = os.cpu_count() or 1
                cpu_val = round(min(100.0, (os.getloadavg()[0] / cores) * 100), 1)
            except Exception:
                cpu_val = 0.0

        is_throttled, throttled_raw = get_throttled_status()
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


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>System Dashboard - {{ stats.hostname }}</title>
  <noscript><meta http-equiv="refresh" content="5"></noscript>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --card-border: #334155;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --bar-bg: #334155;
    }
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    body {
      background: var(--bg);
      color: var(--text-main);
      min-height: 100vh;
      padding: 2rem 1rem;
      display: flex;
      flex-direction: column;
      align-items: center;
    }
    .container {
      width: 100%;
      max-width: 960px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 2rem;
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
      font-size: 0.9rem;
      margin-top: 0.25rem;
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
      font-size: 0.85rem;
      font-weight: 600;
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
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1.25rem;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 1.5rem;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }
    .card-title {
      font-size: 0.95rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      font-weight: 600;
    }
    .card-icon {
      font-size: 1.3rem;
    }
    .card-value {
      font-size: 2.2rem;
      font-weight: 700;
      margin-bottom: 0.5rem;
      color: #fff;
    }
    .card-subtitle {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-bottom: 1rem;
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
    .fill-ram { background: #818cf8; }
    .fill-disk { background: #a855f7; }
    .fill-temp { background: #f97316; }

    .badge-temp {
      display: inline-block;
      padding: 0.2rem 0.6rem;
      border-radius: 6px;
      font-size: 0.8rem;
      font-weight: 600;
      margin-top: 0.5rem;
    }

    /* History Graph Styles */
    .history-card {
      grid-column: 1 / -1;
    }
    .history-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-bottom: 0.75rem;
    }
    .range-btn-group {
      display: inline-flex;
      background: var(--bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 3px;
      gap: 3px;
    }
    .btn-range {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 0.35rem 0.75rem;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .btn-range:hover {
      color: var(--text-main);
      background: rgba(255, 255, 255, 0.06);
    }
    .btn-range.active {
      background: var(--primary);
      color: #0f172a;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
    }
    .chart-container {
      position: relative;
      height: 320px;
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
      gap: 0.5rem;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
      padding-top: 0.65rem;
    }
    .history-legend-badges {
      display: flex;
      gap: 1.25rem;
      align-items: center;
      flex-wrap: wrap;
    }
    .legend-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
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

    footer {
      margin-top: 2rem;
      color: var(--text-muted);
      font-size: 0.85rem;
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 0.5rem;
      border-top: 1px solid var(--card-border);
      padding-top: 1rem;
      width: 100%;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="title-group">
        <h1>📊 System Dashboard</h1>
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

    <div class="grid">
      <!-- CPU -->
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

      <!-- RAM -->
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

      <!-- DISK -->
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

      <!-- TEMPERATUR -->
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

      <!-- VERLAUFSGRAPH -->
      <div class="card history-card">
        <div>
          <div class="history-header">
            <div>
              <div class="card-title" style="display: flex; align-items: center; gap: 0.5rem;">
                <span>📈</span>
                <span>System-Verlauf (Temperatur & CPU)</span>
              </div>
              <div class="card-subtitle" style="margin-bottom: 0; margin-top: 0.25rem;">
                Kombinierter Verlauf mit dualer Achse &bull; Rote Markierungen = Throttling aktiv
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
            <div class="history-legend-badges">
              <span class="legend-badge">
                <span class="legend-line" style="background: #f97316;"></span>
                <span>Temperatur (°C, links)</span>
              </span>
              <span class="legend-badge">
                <span class="legend-line" style="background: #38bdf8;"></span>
                <span>CPU (%) (rechts)</span>
              </span>
              <span class="legend-badge">
                <span class="legend-dot"></span>
                <span>Throttling aktiv (Bit 0x1)</span>
              </span>
            </div>
            <div id="historyStatus">Lade Verlauf...</div>
          </div>
        </div>
      </div>

      <!-- UPTIME -->
      <div class="card" style="grid-column: 1 / -1;">
        <div>
          <div class="card-header">
            <span class="card-title">System Uptime</span>
            <span class="card-icon">⏱️</span>
          </div>
          <div class="card-value" style="font-size: 1.8rem;" id="uptimeDisplay">{{ stats.uptime.display }}</div>
          <div class="card-subtitle" id="bootTime">Systemstart: {{ stats.uptime.boot_time }}</div>
        </div>
      </div>
    </div>

    <footer>
      <span>Port: 5000 (bind 0.0.0.0)</span>
      <span id="lastUpdated">Stand: {{ stats.timestamp }}</span>
    </footer>
  </div>

  <script>
    function updateTempBadge(temp) {
      const badge = document.getElementById('tempBadge');
      const bar = document.getElementById('tempBar');
      if (!temp) {
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

    // Initial badge setup
    updateTempBadge({{ stats.temperature.value or 'null' }});

    async function fetchStats() {
      try {
        const response = await fetch('/api/stats');
        if (!response.ok) return;
        const data = await response.json();

        // CPU
        document.getElementById('cpuPercent').textContent = data.cpu.percent + '%';
        document.getElementById('cpuBar').style.width = data.cpu.percent + '%';
        document.getElementById('cpuCores').textContent = data.cpu.cores + ' Kerne';

        // RAM
        document.getElementById('ramPercent').textContent = data.ram.percent + '%';
        document.getElementById('ramBar').style.width = data.ram.percent + '%';
        document.getElementById('ramDetails').textContent = data.ram.used_gb + ' GB / ' + data.ram.total_gb + ' GB verwendet';

        // Disk
        document.getElementById('diskPercent').textContent = data.disk.percent + '%';
        document.getElementById('diskBar').style.width = data.disk.percent + '%';
        document.getElementById('diskDetails').textContent = data.disk.used_gb + ' GB / ' + data.disk.total_gb + ' GB (' + data.disk.free_gb + ' GB frei)';

        // Temp
        document.getElementById('tempValue').textContent = data.temperature.display;
        updateTempBadge(data.temperature.value);

        // Uptime
        document.getElementById('uptimeDisplay').textContent = data.uptime.display;
        if (data.uptime.boot_time) {
          document.getElementById('bootTime').textContent = 'Systemstart: ' + data.uptime.boot_time;
        }

        // Hostname / Platform
        document.getElementById('hostname').textContent = data.hostname;
        document.getElementById('platform').textContent = data.platform;
        document.getElementById('lastUpdated').textContent = 'Stand: ' + data.timestamp;
      } catch (err) {
        console.error('Fehler beim Abrufen der Systemstatistiken:', err);
      }
    }

    // --- Verlaufsgraph (Chart.js) Logik ---
    let currentRange = '1h';
    let currentHistorySamples = [];
    let historyChart = null;

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
              spanGaps: true
            },
            {
              label: 'CPU-Auslastung (%)',
              data: [],
              borderColor: '#38bdf8',
              backgroundColor: 'rgba(56, 189, 248, 0.08)',
              yAxisID: 'yCpu',
              tension: 0.25,
              borderWidth: 2,
              pointRadius: 2,
              pointHoverRadius: 5,
              pointBackgroundColor: '#38bdf8',
              fill: false,
              spanGaps: true
            },
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
              pointStyle: 'circle'
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
                font: { size: 12 },
                usePointStyle: true,
                boxWidth: 8,
                boxHeight: 8
              }
            },
            tooltip: {
              backgroundColor: '#1e293b',
              titleColor: '#f8fafc',
              bodyColor: '#cbd5e1',
              borderColor: '#475569',
              borderWidth: 1,
              padding: 10,
              callbacks: {
                label: function(context) {
                  if (context.datasetIndex === 2) return null;
                  const label = context.dataset.label || '';
                  const unit = context.datasetIndex === 0 ? ' °C' : ' %';
                  const val = context.parsed.y !== null ? context.parsed.y + unit : 'N/A';
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
                maxTicksLimit: 10,
                maxRotation: 0
              }
            },
            yTemp: {
              type: 'linear',
              position: 'left',
              title: {
                display: true,
                text: 'Temperatur (°C)',
                color: '#f97316',
                font: { size: 12, weight: '600' }
              },
              grid: {
                color: 'rgba(255, 255, 255, 0.06)'
              },
              ticks: {
                color: '#f97316',
                callback: function(val) { return val + ' °C'; }
              },
              suggestedMin: 25,
              suggestedMax: 80
            },
            yCpu: {
              type: 'linear',
              position: 'right',
              title: {
                display: true,
                text: 'CPU (%)',
                color: '#38bdf8',
                font: { size: 12, weight: '600' }
              },
              grid: {
                drawOnChartArea: false
              },
              ticks: {
                color: '#38bdf8',
                callback: function(val) { return val + ' %'; }
              },
              min: 0,
              max: 100
            }
          }
        }
      });

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
      historyChart.data.datasets[0].data = temps;
      historyChart.data.datasets[0].pointRadius = tempPointRadius;
      historyChart.data.datasets[0].pointBackgroundColor = tempPointBg;
      historyChart.data.datasets[0].pointBorderColor = tempPointBorder;
      historyChart.data.datasets[0].pointBorderWidth = tempPointWidth;

      historyChart.data.datasets[1].data = cpus;
      historyChart.data.datasets[1].pointRadius = isMany ? 0 : 2;

      historyChart.data.datasets[2].data = throttledData;

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
  </script>
</body>
</html>
"""


def render_html_fallback(stats):
    """Einfacher HTML-Renderer für Standardbibliothek ohne Jinja2."""
    temp_val = stats["temperature"]["value"] or 0
    temp_min_100 = min(temp_val, 100)
    html = DASHBOARD_HTML
    replacements = {
        "{{ stats.hostname }}": str(stats["hostname"]),
        "{{ stats.platform }}": str(stats["platform"]),
        "{{ stats.cpu.percent }}": str(stats["cpu"]["percent"]),
        "{{ stats.cpu.cores }}": str(stats["cpu"]["cores"]),
        "{{ stats.ram.percent }}": str(stats["ram"]["percent"]),
        "{{ stats.ram.used_gb }}": str(stats["ram"]["used_gb"]),
        "{{ stats.ram.total_gb }}": str(stats["ram"]["total_gb"]),
        "{{ stats.disk.percent }}": str(stats["disk"]["percent"]),
        "{{ stats.disk.used_gb }}": str(stats["disk"]["used_gb"]),
        "{{ stats.disk.total_gb }}": str(stats["disk"]["total_gb"]),
        "{{ stats.disk.free_gb }}": str(stats["disk"]["free_gb"]),
        "{{ stats.temperature.display }}": str(stats["temperature"]["display"]),
        "{{ stats.temperature.value or 'null' }}": str(stats["temperature"]["value"] if stats["temperature"]["value"] is not None else "null"),
        "{% if stats.temperature.value %}{{ [stats.temperature.value, 100]|min }}{% else %}0{% endif %}": str(temp_min_100),
        "{{ stats.uptime.display }}": str(stats["uptime"]["display"]),
        "{{ stats.uptime.boot_time }}": str(stats["uptime"]["boot_time"]),
        "{{ stats.timestamp }}": str(stats["timestamp"]),
    }
    for key, val in replacements.items():
        html = html.replace(key, val)
    return html


if USE_FLASK:
    app = Flask(__name__)

    @app.route("/")
    def index():
        stats = get_system_stats()
        return render_template_string(DASHBOARD_HTML, stats=stats)

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
