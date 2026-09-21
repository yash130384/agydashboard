#!/usr/bin/env python3
"""
Pimmel Service for LCARS System Dashboard.
Manages telemetry, metrics and 9Router data from remote node 'PiMMEL' (100.88.215.98):
- SSH-based Host Vitals polling (CPU, RAM, Disk, Temp, Uptime, Load, Services)
- In-memory rolling 24h history ringbuffer
- Rsync SQLite sync for 9Router usage & telemetry
- PM2 live console logs streaming & parsing
"""

import collections
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time

REMOTE_ONESHOT_SCRIPT = """
import datetime, json, os, subprocess, sys

load1, load5, load15 = os.getloadavg()
cores = os.cpu_count() or 1
cpu_pct = round(min(100.0, (load1 / cores) * 100), 1)

uptime_sec = 0.0
with open("/proc/uptime") as f:
    uptime_sec = float(f.read().split()[0])

mem_total, mem_avail = 0, 0
with open("/proc/meminfo") as f:
    for line in f:
        if line.startswith("MemTotal:"):
            mem_total = int(line.split()[1]) * 1024
        elif line.startswith("MemAvailable:"):
            mem_avail = int(line.split()[1]) * 1024
mem_used = max(0, mem_total - mem_avail)
mem_pct = round((mem_used / mem_total) * 100, 1) if mem_total else 0.0

st = os.statvfs("/")
disk_total = st.f_blocks * st.f_frsize
disk_free = st.f_bavail * st.f_frsize
disk_used = disk_total - disk_free
disk_pct = round((disk_used / disk_total) * 100, 1) if disk_total else 0.0

temp_c = 0.0
try:
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        temp_c = round(float(f.read().strip()) / 1000.0, 1)
except Exception:
    pass

try:
    hermes_active = subprocess.check_output(
        ["systemctl", "--user", "is-active", "hermes-gateway"], text=True
    ).strip()
except Exception:
    hermes_active = "inactive"

pm2_9router = {"status": "offline"}
try:
    raw = subprocess.check_output(["pm2", "jlist"], text=True)
    for p in json.loads(raw):
        if p.get("name") == "9router":
            monit = p.get("monit", {})
            env = p.get("pm2_env", {})
            pm2_9router = {
                "status": env.get("status", "unknown"),
                "pid": p.get("pid"),
                "pm_id": p.get("pm_id"),
                "uptime_ms": env.get("pm_uptime"),
                "restarts": env.get("restart_time", 0),
                "cpu": monit.get("cpu", 0),
                "memory": monit.get("memory", 0),
                "version": env.get("version", ""),
            }
            break
except Exception as e:
    pm2_9router = {"status": "error", "error": str(e)}

logs = ""
try:
    logs = subprocess.check_output(
        ["tail", "-n", "60", "/home/yash/.pm2/logs/9router-out.log"], text=True
    )
except Exception as e:
    logs = f"[WARN] Fehler beim Lesen der Logs: {e}"

print("__JSON_START__")
print(json.dumps({
    "hostname": "PiMMEL",
    "ip": "100.88.215.98",
    "timestamp": datetime.datetime.now().isoformat(),
    "load": {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2), "cores": cores},
    "cpu": {"percent": cpu_pct, "cores": cores},
    "ram": {"total": mem_total, "used": mem_used, "free": mem_avail, "percent": mem_pct},
    "disk": {"total": disk_total, "used": disk_used, "free": disk_free, "percent": disk_pct},
    "temp": temp_c,
    "uptime_sec": int(uptime_sec),
    "hermes": {"status": hermes_active},
    "pm2_9router": pm2_9router,
    "logs": logs
}))
"""


def fmt_num(n):
    """Formatiert Token-Zahlen benutzerfreundlich (z. B. 34.8M, 12.4k)."""
    try:
        n = float(n)
    except (ValueError, TypeError):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def fmt_bytes_gb(b):
    """Formatiert Byte-Werte in GB mit 2 Nachkommastellen."""
    try:
        gb = float(b) / (1024 ** 3)
        return f"{gb:.2f} GB"
    except Exception:
        return "0.00 GB"


def fmt_uptime(seconds):
    """Formatiert Uptime in Sekunden zu 'X Tage Y Std' oder 'X Std Y Min'."""
    try:
        sec = int(seconds)
        days = sec // 86400
        hours = (sec % 86400) // 3600
        mins = (sec % 3600) // 60
        if days > 0:
            return f"{days} Tage {hours} Std"
        return f"{hours} Std {mins} Min"
    except Exception:
        return "0 Std 0 Min"


def parse_9router_sqlite(db_path):
    """Parst eine 9Router SQLite-Datenbank (WAL-sicher im read-only Modus) und aggregiert Verbrauchsdaten."""
    default_res = {
        "status": "offline",
        "error": "Datenbank nicht gefunden",
        "totals": {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "saved_tokens": 0,
            "total_tokens": 0,
            "cost": 0.0,
            "cost_formatted": "$0.00",
            "tokens_formatted": "0",
            "prompt_formatted": "0",
            "completion_formatted": "0",
            "cached_formatted": "0",
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
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        try:
            c = conn.cursor()

            # 1. usageDaily: Aggregation über Tage
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

            # 2. usageHistory: Letzte 30 Anfragen
            c.execute("""
                SELECT id, timestamp, provider, model, promptTokens, completionTokens, cost, status, tokens, meta
                FROM usageHistory
                ORDER BY id DESC
                LIMIT 30
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

            # Prompt-Caching Ersparnisberechnung
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
        print(f"[WARN] 9Router SQLite Parsing Fehler: {e}", file=sys.stderr)
        err_res = dict(default_res)
        err_res["status"] = "error"
        err_res["error"] = str(e)
        return err_res


class PimmelService:
    def __init__(self, config_path=None):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        if not config_path:
            config_path = os.path.join(self.base_dir, "config.json")
        self.config_path = config_path
        self.lock = threading.RLock()

        # Konfigurationswerte
        self.enabled = True
        self.ssh_host = "pimmel"
        self.ip = "100.88.215.98"
        self.poll_interval = 10
        self.sync_interval = 30
        self.remote_db_path = "/home/yash/.9router/db/data.sqlite"
        self.remote_log_path = "/home/yash/.pm2/logs/9router-out.log"
        self.cache_db_path = os.path.join(self.base_dir, "data_cache", "pimmel_9router.sqlite")

        # Status & Metrik-Caches
        self.is_online = False
        self.last_sync = None
        self.last_error = None
        self.latest_logs = ""
        self.host_metrics = {
            "cpu": {"percent": 0.0, "cores": 4},
            "load": {"1m": 0.0, "5m": 0.0, "15m": 0.0, "cores": 4},
            "ram": {"total": 0, "used": 0, "free": 0, "percent": 0.0, "used_gb": "0.00 GB", "total_gb": "0.00 GB"},
            "disk": {"total": 0, "used": 0, "free": 0, "percent": 0.0, "used_gb": "0.00 GB", "total_gb": "0.00 GB"},
            "temp": {"value": 0.0, "display": "--.- °C"},
            "uptime": {"seconds": 0, "display": "-- Std -- Min", "boot_time": "--"},
            "services": {
                "hermes_gateway": {"status": "unknown", "display": "NICHT VERFÜGBAR", "type": "systemd-user"},
                "pm2_9router": {"status": "unknown", "pid": None, "pm_id": 0, "restarts": 0, "cpu": 0, "memory_mb": 0.0, "version": ""}
            }
        }
        self.nine_router_data = {
            "status": "offline",
            "totals": {
                "requests": 0, "tokens_formatted": "0", "prompt_formatted": "0",
                "completion_formatted": "0", "cached_formatted": "0",
                "cache_hit_rate_formatted": "0.0%", "cost_formatted": "$0.00",
                "saved_cost_formatted": "$0.00", "saved_cost_pct_formatted": "0.0%"
            },
            "by_provider": {},
            "by_model": {},
            "daily_timeline": [],
            "recent_history": [],
            "connections": []
        }

        # 24h Ringbuffer (alle 10s ein Snapshot -> 8760 Einträge = 24.3 Std)
        self.history_samples = collections.deque(maxlen=8760)

        self._load_config()

        # Vorhandenen lokalen SQLite-Cache sofort einlesen falls verfügbar
        if os.path.exists(self.cache_db_path):
            try:
                parsed = parse_9router_sqlite(self.cache_db_path)
                if parsed.get("status") == "online":
                    self.nine_router_data = parsed
            except Exception as e:
                print(f"[WARN] PimmelService: Initialer Cache-Read fehlgeschlagen: {e}", file=sys.stderr)

        # Background Worker starten
        self._running = True
        self._poller_thread = threading.Thread(target=self._poller_worker, name="PimmelPollerWorker", daemon=True)
        self._sync_thread = threading.Thread(target=self._sync_worker, name="PimmelSyncWorker", daemon=True)
        self._poller_thread.start()
        self._sync_thread.start()

    def _load_config(self):
        with self.lock:
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        p_cfg = cfg.get("pimmel_node", {})
                        if isinstance(p_cfg, dict):
                            self.enabled = p_cfg.get("enabled", True)
                            self.ssh_host = p_cfg.get("ssh_host", "pimmel")
                            self.ip = p_cfg.get("ip", "100.88.215.98")
                            self.poll_interval = p_cfg.get("poll_interval_seconds", 10)
                            self.sync_interval = p_cfg.get("sync_interval_seconds", 30)
                            self.remote_db_path = p_cfg.get("remote_db_path", "/home/yash/.9router/db/data.sqlite")
                            self.remote_log_path = p_cfg.get("remote_log_path", "/home/yash/.pm2/logs/9router-out.log")
                            rel_cache = p_cfg.get("cache_db_path", "data_cache/pimmel_9router.sqlite")
                            self.cache_db_path = os.path.normpath(os.path.join(self.base_dir, rel_cache))
                except Exception as e:
                    print(f"[WARN] PimmelService: Fehler beim Laden von config.json: {e}", file=sys.stderr)

    def _poll_host_telemetry(self):
        """Führt das One-Shot Python Skript auf PiMMEL per SSH aus und aktualisiert Vitals & Ringbuffer."""
        cmd = [
            "ssh",
            "-o", "ConnectTimeout=3",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            self.ssh_host,
            "python3 -"
        ]
        try:
            res = subprocess.run(
                cmd,
                input=REMOTE_ONESHOT_SCRIPT,
                text=True,
                capture_output=True,
                timeout=8
            )
            if res.returncode != 0:
                raise RuntimeError(f"SSH Rückgabewert {res.returncode}: {res.stderr.strip()}")

            stdout = res.stdout
            if "__JSON_START__" not in stdout:
                raise ValueError("Payload enthält kein __JSON_START__ Marker")

            raw_json = stdout.split("__JSON_START__", 1)[1].strip()
            data = json.loads(raw_json)

            now_ts = time.time()
            now_iso = datetime.datetime.now().astimezone().isoformat()
            uptime_sec = data.get("uptime_sec", 0)
            boot_time = datetime.datetime.fromtimestamp(now_ts - uptime_sec).strftime("%Y-%m-%d %H:%M:%S") if uptime_sec else "--"

            # Services
            hermes_status = data.get("hermes", {}).get("status", "inactive")
            hermes_display = "ONLINE // RUNNING" if hermes_status == "active" else "INACTIVE / FAILED"

            pm2_info = data.get("pm2_9router", {})
            pm2_mem_bytes = pm2_info.get("memory", 0) or 0
            pm2_mem_mb = round(pm2_mem_bytes / (1024 * 1024), 1)

            with self.lock:
                self.is_online = True
                self.last_sync = now_iso
                self.last_error = None
                self.latest_logs = data.get("logs", "")

                ram_data = data.get("ram", {})
                disk_data = data.get("disk", {})
                temp_val = data.get("temp", 0.0)

                self.host_metrics = {
                    "cpu": data.get("cpu", {"percent": 0.0, "cores": 4}),
                    "load": data.get("load", {"1m": 0.0, "5m": 0.0, "15m": 0.0, "cores": 4}),
                    "ram": {
                        "total": ram_data.get("total", 0),
                        "used": ram_data.get("used", 0),
                        "free": ram_data.get("free", 0),
                        "percent": ram_data.get("percent", 0.0),
                        "used_gb": fmt_bytes_gb(ram_data.get("used", 0)),
                        "total_gb": fmt_bytes_gb(ram_data.get("total", 0)),
                    },
                    "disk": {
                        "total": disk_data.get("total", 0),
                        "used": disk_data.get("used", 0),
                        "free": disk_data.get("free", 0),
                        "percent": disk_data.get("percent", 0.0),
                        "used_gb": fmt_bytes_gb(disk_data.get("used", 0)),
                        "total_gb": fmt_bytes_gb(disk_data.get("total", 0)),
                    },
                    "temp": {
                        "value": temp_val,
                        "display": f"{temp_val:.1f} °C" if temp_val > 0 else "--.- °C"
                    },
                    "uptime": {
                        "seconds": uptime_sec,
                        "display": fmt_uptime(uptime_sec),
                        "boot_time": boot_time
                    },
                    "services": {
                        "hermes_gateway": {
                            "status": hermes_status,
                            "display": hermes_display,
                            "type": "systemd-user"
                        },
                        "pm2_9router": {
                            "status": pm2_info.get("status", "offline"),
                            "pid": pm2_info.get("pid"),
                            "pm_id": pm2_info.get("pm_id", 0),
                            "restarts": pm2_info.get("restarts", 0),
                            "cpu": pm2_info.get("cpu", 0),
                            "memory_mb": pm2_mem_mb,
                            "version": pm2_info.get("version", "")
                        }
                    }
                }

                # Ringbuffer Snapshot speichern
                sample = {
                    "ts": int(now_ts),
                    "time": datetime.datetime.fromtimestamp(now_ts).strftime("%H:%M:%S"),
                    "cpu": self.host_metrics["cpu"].get("percent", 0.0),
                    "ram": self.host_metrics["ram"].get("percent", 0.0),
                    "temp": temp_val,
                    "disk": self.host_metrics["disk"].get("percent", 0.0),
                    "load1": self.host_metrics["load"].get("1m", 0.0)
                }
                self.history_samples.append(sample)

        except Exception as e:
            with self.lock:
                self.is_online = False
                self.last_error = str(e)
            print(f"[WARN] PimmelService Poller Fehler: {e}", file=sys.stderr)

    def _sync_sqlite_db(self):
        """Synchronisiert per rsync die SQLite-Datenbank von PiMMEL in den lokalen Cache."""
        os.makedirs(os.path.dirname(self.cache_db_path), exist_ok=True)
        cmd = [
            "rsync",
            "-az",
            "--timeout=10",
            f"{self.ssh_host}:{self.remote_db_path}",
            self.cache_db_path
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                parsed = parse_9router_sqlite(self.cache_db_path)
                with self.lock:
                    if parsed.get("status") in ("online", "ok"):
                        self.nine_router_data = parsed
            else:
                print(f"[WARN] PimmelService rsync Fehler ({res.returncode}): {res.stderr.strip()}", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] PimmelService rsync Ausnahme: {e}", file=sys.stderr)

    def _poller_worker(self):
        """Dauerhafter Polling-Thread für Host Vitals."""
        while self._running:
            if self.enabled:
                self._poll_host_telemetry()
            time.sleep(self.poll_interval)

    def _sync_worker(self):
        """Dauerhafter Sync-Thread für SQLite-Datenbank."""
        while self._running:
            if self.enabled:
                self._sync_sqlite_db()
            time.sleep(self.sync_interval)

    def get_status(self):
        """Liefert aggregierte Statusinformationen für /api/pimmel/status."""
        with self.lock:
            totals = self.nine_router_data.get("totals", {})
            return {
                "node": {
                    "hostname": "PiMMEL",
                    "ip": self.ip,
                    "online": self.is_online,
                    "last_sync": self.last_sync,
                    "last_error": self.last_error
                },
                "host_metrics": self.host_metrics,
                "nine_router_summary": {
                    "total_requests": totals.get("requests", 0),
                    "total_tokens_formatted": totals.get("tokens_formatted", "0"),
                    "prompt_tokens_formatted": totals.get("prompt_formatted", "0"),
                    "completion_tokens_formatted": totals.get("completion_formatted", "0"),
                    "cached_tokens_formatted": totals.get("cached_formatted", "0"),
                    "cache_hit_rate_formatted": totals.get("cache_hit_rate_formatted", "0.0%"),
                    "cost_formatted": totals.get("cost_formatted", "$0.00"),
                    "saved_cost_formatted": totals.get("saved_cost_formatted", "$0.00"),
                    "saved_cost_pct_formatted": totals.get("saved_cost_pct_formatted", "0.0%")
                }
            }

    def get_history(self, range_param="1h"):
        """Liefert gefilterte Sensor-Historie für den ausgewählten Zeitraum."""
        range_map = {
            "10m": 600,
            "30m": 1800,
            "1h": 3600,
            "12h": 43200,
            "24h": 86400
        }
        sec = range_map.get(str(range_param).strip().lower(), 3600)
        cutoff = int(time.time()) - sec
        with self.lock:
            samples = [s for s in self.history_samples if s.get("ts", 0) >= cutoff]
            return {
                "range": range_param,
                "seconds": sec,
                "count": len(samples),
                "samples": samples
            }

    def get_9router_data(self):
        """Liefert vollständige 9Router-Statistiken aus der synchronisierten SQLite-Datenbank."""
        with self.lock:
            return dict(self.nine_router_data)

    def get_logs(self, lines=50):
        """Liefert die letzten Zeilen der PM2 9Router-Logs."""
        try:
            lines = max(10, min(200, int(lines)))
        except (ValueError, TypeError):
            lines = 50

        # Wenn die angeforderten Zeilen im schnellen Cache vorliegen und nicht höher sind als gecacht
        with self.lock:
            cached_logs = self.latest_logs

        # Optional: Wenn mehr Zeilen explizit gefordert werden oder Cache leer ist, direkt via SSH abfragen
        if not cached_logs or lines > 60:
            cmd = [
                "ssh",
                "-o", "ConnectTimeout=3",
                "-o", "BatchMode=yes",
                self.ssh_host,
                f"tail -n {lines} {self.remote_log_path}"
            ]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    cached_logs = r.stdout
            except Exception:
                pass

        lines_list = cached_logs.strip().split("\n") if cached_logs else []
        return {
            "lines": lines,
            "count": len(lines_list),
            "logs": cached_logs,
            "timestamp": datetime.datetime.now().astimezone().isoformat()
        }

    def trigger_sync(self):
        """Triggert sofortige manuelle Synchronisation von DB und Telemetrie."""
        def _run():
            self._sync_sqlite_db()
            self._poll_host_telemetry()

        t = threading.Thread(target=_run, name="PimmelManualSync", daemon=True)
        t.start()
        return {
            "success": True,
            "message": "Synchronisation initiiert (DB & Telemetrie)"
        }


pimmel_service = PimmelService()
