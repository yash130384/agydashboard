#!/usr/bin/env python3
"""
System Info Web-Dashboard
Lauscht auf Port 5000 (bind 0.0.0.0) und zeigt Systemmetriken an:
- CPU-Auslastung
- RAM-Verbrauch
- Temperatur
- Uptime
- Festplattenbelegung
Mit automatischem Refresh alle 5 Sekunden.
"""

import sys
import os
import time
import glob
import socket
import platform
import datetime
import subprocess
import json

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

# Flask Import mit Fallback zu http.server falls nicht installiert
try:
    from flask import Flask, jsonify, render_template_string
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
        out = subprocess.check_output(["vcgencmd", "measure_temp"], text=True)
        val = float(out.replace("temp=", "").replace("'C", "").strip())
        return round(val, 1), f"{val:.1f} °C"
    except Exception:
        pass

    return None, "N/A"


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

    return stats


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>System Dashboard - {{ stats.hostname }}</title>
  <noscript><meta http-equiv="refresh" content="5"></noscript>
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

    // Progress bar animation for 5 second interval
    let progress = 0;
    const intervalMs = 5000;
    const stepMs = 100;
    const bar = document.getElementById('refreshBar');

    setInterval(() => {
      progress += (stepMs / intervalMs) * 100;
      if (progress >= 100) {
        progress = 0;
        fetchStats();
      }
      bar.style.width = progress + '%';
    }, stepMs);
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

    def run_server():
        print("[START] Starte Flask Dashboard Server auf http://0.0.0.0:5000 ...")
        app.run(host="0.0.0.0", port=5000, debug=False)

else:
    class DashboardHTTPHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/stats":
                stats = get_system_stats()
                data = json.dumps(stats).encode("utf-8")
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
        print("[START] Starte Standard-HTTP Dashboard Server auf http://0.0.0.0:5000 ...")
        httpd.serve_forever()


if __name__ == "__main__":
    run_server()
