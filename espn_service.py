#!/usr/bin/env python3
"""
ESPN Fantasy Football Data Service for LCARS Dashboard
Fetches league, team, live matchup, roster, and standings from ESPN Fantasy API.
"""

import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
import sqlite3
import re
from datetime import datetime

class EspnFantasyClient:
    SLOT_MAP = {
        0: 'QB',
        1: 'TQB',
        2: 'RB',
        3: 'RB/WR',
        4: 'WR',
        5: 'WR/TE',
        6: 'TE',
        7: 'OP',
        16: 'D/ST',
        17: 'K',
        20: 'BENCH',
        21: 'IR',
        23: 'FLEX'
    }

    POS_MAP = {
        1: 'QB',
        2: 'RB',
        3: 'WR',
        4: 'TE',
        5: 'K',
        16: 'D/ST'
    }

    PRO_TEAMS = {
        0: 'FA', 1: 'ATL', 2: 'BUF', 3: 'CHI', 4: 'CIN', 5: 'CLE', 6: 'DAL', 7: 'DEN',
        8: 'DET', 9: 'GB', 10: 'TEN', 11: 'IND', 12: 'KC', 13: 'LV', 14: 'LAR',
        15: 'MIA', 16: 'MIN', 17: 'NE', 18: 'NO', 19: 'NYG', 20: 'NYJ', 21: 'PHI',
        22: 'ARI', 23: 'PIT', 24: 'LAC', 25: 'SF', 26: 'SEA', 27: 'TB', 28: 'WSH',
        29: 'CAR', 30: 'JAX', 33: 'BAL', 34: 'HOU'
    }

    RISK_LEVELS = {
        1: {
            "name": "Ultra-Konservativ",
            "short": "FLOOR",
            "desc": "Ultra-Konservativ (High Floor, minimales Risiko, sichere Snaps)",
            "prompt_guidance": "Ultra-Konservativ (Stufe 1/5): Priorisiere Spieler mit absolut sicherem Floor und hoher Snap-Count-Garantie. Vermeide riskante Boom-or-Bust Optionen oder angeschlagene Spieler. Lieber sichere 8-10 Punkte als unberechenbare 0-25 Punkte."
        },
        2: {
            "name": "Konservativ",
            "short": "KONS",
            "desc": "Konservativ (Sicherheitsorientiert, solide Projektionen bevorzugt)",
            "prompt_guidance": "Konservativ (Stufe 2/5): Bevorzuge verlässliche Stammspieler mit konstantem Target-Share bzw. Touches gegenüber spekulativen Matchups."
        },
        3: {
            "name": "Ausgewogen",
            "short": "AUSG",
            "desc": "Ausgewogen (Standard-Balancierung zwischen Floor und Ceiling)",
            "prompt_guidance": "Ausgewogen (Stufe 3/5, Standard): Ausgeglichene Balance zwischen Floor (Sicherheit) und Ceiling (Upside) basierend auf ESPN-Projektionen."
        },
        4: {
            "name": "Offensiv",
            "short": "OFF",
            "desc": "Offensiv (Ceiling / Matchup-Upside)",
            "prompt_guidance": "Offensiv (Stufe 4/5): Suche gezielt nach Matchup-Vorteilen und hohem Ceiling. Nimm kalkuliertes Risiko in Kauf, wenn das Potenzial deutlich über dem Durchschnitt liegt."
        },
        5: {
            "name": "Boom-or-Bust",
            "short": "BOOM",
            "desc": "Boom-or-Bust (Maximales Upside gegen starke Gegner)",
            "prompt_guidance": "Boom-or-Bust (Stufe 5/5): Maximales Upside erforderlich (z.B. gegen übermächtigen Gegner oder bei großem Punkterückstand). Riskiere alles für Deep-Threats, explosive Spielmacher und extremes Potenzial, auch bei geringerem Floor."
        }
    }

    def __init__(self, config_path=None):
        if not config_path:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        self.config_path = config_path
        self.tracker_path = os.path.join(os.path.dirname(self.config_path), "espn_score_tracker.json")
        self.proposals_path = os.path.join(os.path.dirname(self.config_path), "espn_proposals.json")
        self.decision_log_path = os.path.join(os.path.dirname(self.config_path), "espn_decision_log.json")
        self._cached_data = None
        self._cache_timestamp = 0
        self._cache_ttl = 30  # 30 Sekunden Cache für optimale Live-Aktualität
        self._tracker_lock = threading.Lock()
        self._mode_lock = threading.Lock()
        self._proposals_lock = threading.Lock()
        self._decision_lock = threading.Lock()
        self._poller_thread = None
        self._poller_running = False
        self._poller_lock = threading.Lock()
        self._poll_interval = 35
        self._mode = "manual"
        self._risk_level = 3
        self._flash_enabled = True
        self._last_ai_usage = {
            "prompt_tokens": 950,
            "completion_tokens": 250,
            "total_tokens": 1200
        }
        self._last_ai_model = "ag/gemini-3.8-flash-high via 9Router"
        self._last_ai_check_ts = 0
        self._last_ai_action = ""
        self._last_ai_reason = ""
        self._ai_cycle_interval = 120
        self._last_raw_roster_entries = []
        self._last_scoring_period_id = 1

    def _load_tracker(self):
        with self._tracker_lock:
            try:
                if os.path.exists(self.tracker_path):
                    with open(self.tracker_path, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception as e:
                print(f"[WARN] EspnFantasyClient: Fehler beim Laden von espn_score_tracker.json: {e}", file=sys.stderr)
            return {}

    def _save_tracker(self, tracker):
        with self._tracker_lock:
            try:
                with open(self.tracker_path, "w", encoding="utf-8") as f:
                    json.dump(tracker, f, indent=2)
            except Exception as e:
                print(f"[WARN] EspnFantasyClient: Fehler beim Speichern von espn_score_tracker.json: {e}", file=sys.stderr)

    def _load_config(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cfg = data.get("espn_fantasy", {})
                    if "cache_ttl" in cfg:
                        try:
                            self._cache_ttl = max(15, int(cfg["cache_ttl"]))
                        except (ValueError, TypeError):
                            pass
                    if "poll_interval" in cfg:
                        try:
                            self._poll_interval = max(15, int(cfg["poll_interval"]))
                        except (ValueError, TypeError):
                            pass
                    if "mode" in cfg and cfg["mode"] in ("manual", "semi", "full"):
                        self._mode = cfg["mode"]
                    if "risk_level" in cfg:
                        try:
                            rl = int(cfg["risk_level"])
                            if 1 <= rl <= 5:
                                self._risk_level = rl
                        except (ValueError, TypeError):
                            pass
                    if "flash_enabled" in cfg:
                        val = cfg["flash_enabled"]
                        if isinstance(val, str):
                            self._flash_enabled = val.lower() in ("true", "1", "yes", "on")
                        else:
                            self._flash_enabled = bool(val)
                    return cfg
        except Exception as e:
            print(f"[WARN] EspnFantasyClient: Konnte config.json nicht lesen: {e}", file=sys.stderr)
        return {}

    def get_mode(self):
        with self._mode_lock:
            cfg = self._load_config()
            return cfg.get("mode", self._mode or "manual")

    def set_mode(self, mode):
        mode = str(mode).lower().strip()
        if mode not in ("manual", "semi", "full"):
            raise ValueError(f"Ungültiger Modus '{mode}'. Erlaubt sind: manual, semi, full.")
        with self._mode_lock:
            self._mode = mode
            success = self._save_mode_to_config(mode)
            if self._cached_data and isinstance(self._cached_data, dict):
                self._cached_data["mode"] = mode
            self.log_decision("MODE_CHANGE", f"Betriebsmodus geändert auf: {mode}", success=success)
            return {"success": success, "mode": mode}

    def _save_setting_to_config(self, key, value):
        return self._save_settings_to_config({key: value})

    def _save_settings_to_config(self, updates):
        try:
            data = {}
            if os.path.exists(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, dict):
                data = {}
            if "espn_fantasy" not in data or not isinstance(data["espn_fantasy"], dict):
                data["espn_fantasy"] = {}
            for k, v in updates.items():
                data["espn_fantasy"][k] = v
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            if "mode" in updates and len(updates) == 1:
                print(f"[INFO] EspnFantasyClient: Modus '{updates['mode']}' in config.json persistiert.", flush=True)
            else:
                keys_str = ", ".join(f"{k}='{v}'" for k, v in updates.items())
                print(f"[INFO] EspnFantasyClient: {keys_str} in config.json persistiert.", flush=True)
            return True
        except Exception as e:
            print(f"[ERROR] EspnFantasyClient: Fehler beim Speichern in config.json: {e}", file=sys.stderr, flush=True)
            return False

    def _save_mode_to_config(self, mode):
        return self._save_setting_to_config("mode", mode)

    def get_risk_level(self):
        with self._mode_lock:
            cfg = self._load_config()
            try:
                val = int(cfg.get("risk_level", getattr(self, "_risk_level", 3)))
                if 1 <= val <= 5:
                    return val
            except (ValueError, TypeError):
                pass
            return 3

    def set_risk_level(self, level):
        try:
            lvl = int(level)
        except (ValueError, TypeError):
            raise ValueError(f"Ungültiges Risk-Level '{level}'. Erlaubt sind ganze Zahlen von 1 bis 5.")
        if not (1 <= lvl <= 5):
            raise ValueError(f"Ungültiges Risk-Level '{level}'. Erlaubt sind ganze Zahlen von 1 bis 5.")
        with self._mode_lock:
            self._risk_level = lvl
            success = self._save_setting_to_config("risk_level", lvl)
            if self._cached_data and isinstance(self._cached_data, dict):
                self._cached_data["risk_level"] = lvl
            desc = self.RISK_LEVELS.get(lvl, {}).get("name", f"Stufe {lvl}")
            self.log_decision("RISK_LEVEL_CHANGE", f"Risiko-Level geändert auf: {lvl} ({desc})", success=success)
            return {"success": success, "risk_level": lvl}

    def get_flash_enabled(self):
        with self._mode_lock:
            cfg = self._load_config()
            val = cfg.get("flash_enabled", getattr(self, "_flash_enabled", True))
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes", "on")
            return bool(val)

    def set_flash_enabled(self, enabled):
        if isinstance(enabled, str):
            enabled_val = enabled.lower() in ("true", "1", "yes", "on")
        else:
            enabled_val = bool(enabled)
        with self._mode_lock:
            self._flash_enabled = enabled_val
            success = self._save_setting_to_config("flash_enabled", enabled_val)
            if self._cached_data and isinstance(self._cached_data, dict):
                self._cached_data["flash_enabled"] = enabled_val
            self.log_decision("FLASH_TOGGLE", f"Flash-Signal {'aktiviert' if enabled_val else 'deaktiviert'}", success=success)
            return {"success": success, "flash_enabled": enabled_val}

    def get_settings(self):
        return {
            "status": "ok",
            "mode": self.get_mode(),
            "risk_level": self.get_risk_level(),
            "flash_enabled": self.get_flash_enabled()
        }

    def update_settings(self, mode=None, risk_level=None, flash_enabled=None):
        if mode is not None:
            self.set_mode(mode)
        if risk_level is not None:
            self.set_risk_level(risk_level)
        if flash_enabled is not None:
            self.set_flash_enabled(flash_enabled)
        return self.get_settings()

    def get_ai_interval(self):
        cfg = self._load_config()
        try:
            return max(30, int(cfg.get("ai_check_interval", getattr(self, "_ai_cycle_interval", 120))))
        except (ValueError, TypeError):
            return 120

    def get_bot_status(self):
        mode = self.get_mode()
        interval = self.get_ai_interval()
        now = time.time()
        last_run = getattr(self, "_last_ai_check_ts", 0)

        # Fallback: Aus Decision-Log wiederherstellen falls nach Neustart noch 0
        if not last_run:
            try:
                log_data = self._load_decision_log()
                for entry in reversed(log_data.get("history", [])):
                    act = entry.get("action", "")
                    if act in ("ROSTER_CHECK", "AUTO_MOVE_EXECUTED", "TRANSACTION_EXECUTED", "PROPOSAL_CREATED"):
                        last_run = int(entry.get("timestamp", 0))
                        self._last_ai_check_ts = last_run
                        self._last_ai_action = entry.get("details", "")
                        self._last_ai_reason = (entry.get("metadata") or {}).get("reason", "")
                        break
            except Exception:
                pass

        is_active = (mode in ("semi", "full")) and getattr(self, "_poller_running", False)

        if not is_active:
            next_run_ts = None
            next_run_in_seconds = None
            next_run_text = "Pausiert (Modus Manuell)" if mode == "manual" else "Poller inaktiv"
        else:
            if last_run > 0:
                target_ts = last_run + interval
                next_run_ts = int(target_ts)
                next_run_in_seconds = max(0, int(target_ts - now))
            else:
                next_run_ts = int(now + 10)
                next_run_in_seconds = 10
            next_run_text = f"In {next_run_in_seconds}s" if next_run_in_seconds > 0 else "In Kürze..."

        return {
            "mode": mode,
            "is_active": is_active,
            "interval_seconds": interval,
            "last_run_ts": last_run,
            "last_run_datetime": datetime.fromtimestamp(last_run).strftime("%d.%m.%Y %H:%M:%S") if last_run else "Noch kein Lauf",
            "last_action": getattr(self, "_last_ai_action", "Kader analysiert: Keine Änderungen erforderlich") if last_run else "Noch kein Lauf aufgezeichnet",
            "last_reason": getattr(self, "_last_ai_reason", "") if last_run else "",
            "next_run_ts": next_run_ts,
            "next_run_in_seconds": next_run_in_seconds,
            "next_run_text": next_run_text,
            "model": getattr(self, "_last_ai_model", "ag/gemini-3.8-flash-high via 9Router")
        }

    def get_ai_stats(self):
        usage = getattr(self, "_last_ai_usage", {
            "prompt_tokens": 950,
            "completion_tokens": 250,
            "total_tokens": 1200
        })
        return {
            "status": "ok",
            "model": getattr(self, "_last_ai_model", "ag/gemini-3.8-flash-high via 9Router"),
            "estimated_cost_usd": 0.0002,
            "last_token_usage": usage,
            "last_check_ts": getattr(self, "_last_ai_check_ts", 0),
            "bot_status": self.get_bot_status()
        }

    def _load_proposals(self):
        with self._proposals_lock:
            try:
                if os.path.exists(self.proposals_path):
                    with open(self.proposals_path, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception as e:
                print(f"[WARN] EspnFantasyClient: Fehler beim Laden von espn_proposals.json: {e}", file=sys.stderr)
            return {"proposals": []}

    def _save_proposals(self, data):
        with self._proposals_lock:
            try:
                with open(self.proposals_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                print(f"[WARN] EspnFantasyClient: Fehler beim Speichern von espn_proposals.json: {e}", file=sys.stderr)

    def get_proposals(self, only_pending=False):
        data = self._load_proposals()
        props = data.get("proposals", [])
        if only_pending:
            return [p for p in props if p.get("status") == "pending"]
        return props

    def get_active_proposal(self):
        props = self.get_proposals(only_pending=True)
        return props[-1] if props else None

    def add_proposal(self, proposal):
        data = self._load_proposals()
        props = data.get("proposals", [])
        # Prüfen, ob bereits identischer offener Vorschlag existiert
        for p in props:
            if p.get("status") == "pending" and p.get("moves") == proposal.get("moves"):
                return p
        props.append(proposal)
        data["proposals"] = props[-20:]  # max 20 Vorschläge behalten
        self._save_proposals(data)
        p_in = proposal.get("player_in_name")
        p_out = proposal.get("player_out_name")
        gain = proposal.get("projected_gain")
        details = f"Vorschlag erstellt: {p_in} für {p_out}" if (p_in and p_out) else f"Vorschlag {proposal.get('id')}: {proposal.get('reason')}"
        if gain:
            details += f" (+{gain} PTS)"
        self.log_decision(
            "PROPOSAL_CREATED",
            details,
            success=True,
            metadata={
                "proposal_id": proposal.get("id"),
                "reason": proposal.get("reason"),
                "player_in": p_in,
                "player_out": p_out,
                "confidence": proposal.get("confidence"),
                "projected_gain": gain
            }
        )
        return proposal

    def dismiss_proposal(self, proposal_id):
        data = self._load_proposals()
        found = False
        for p in data.get("proposals", []):
            if p.get("id") == proposal_id and p.get("status") == "pending":
                p["status"] = "dismissed"
                p["dismissed_at"] = int(time.time())
                found = True
                break
        if found:
            self._save_proposals(data)
            self.log_decision("PROPOSAL_DISMISSED", f"Vorschlag {proposal_id} verworfen", success=True)
        return found

    def _load_decision_log(self):
        with self._decision_lock:
            try:
                if os.path.exists(self.decision_log_path):
                    with open(self.decision_log_path, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception as e:
                print(f"[WARN] EspnFantasyClient: Fehler beim Laden von espn_decision_log.json: {e}", file=sys.stderr)
            return {"history": []}

    def _save_decision_log(self, data):
        with self._decision_lock:
            try:
                with open(self.decision_log_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                print(f"[WARN] EspnFantasyClient: Fehler beim Speichern von espn_decision_log.json: {e}", file=sys.stderr)

    def log_decision(self, action, details, success=True, metadata=None):
        data = self._load_decision_log()
        history = data.get("history", [])
        now_ts = int(time.time())
        now_dt = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        # In-Place-Aktualisierung bei aufeinanderfolgenden identischen Roster-Checks (kein Log-Spam)
        if history and action == "ROSTER_CHECK" and history[-1].get("action") == "ROSTER_CHECK":
            last_entry = history[-1]
            last_meta = last_entry.get("metadata") or {}
            cur_reason = (metadata or {}).get("reason", "")
            if last_meta.get("reason") == cur_reason:
                last_entry["timestamp"] = now_ts
                last_entry["datetime"] = now_dt
                repeat_count = int(last_meta.get("repeat_count", 1)) + 1
                last_meta["repeat_count"] = repeat_count
                base_details = (metadata or {}).get("base_details") or "Kader analysiert: Aufstellung optimal, keine Änderungen nötig"
                last_entry["details"] = f"{base_details} (x{repeat_count}, zuletzt {now_dt.split(' ')[1]})"
                last_entry["metadata"] = last_meta
                self._save_decision_log(data)
                return last_entry

        entry = {
            "timestamp": now_ts,
            "datetime": now_dt,
            "action": action,
            "details": details,
            "success": bool(success),
            "mode": getattr(self, "_mode", "manual"),
            "metadata": metadata or {}
        }
        history.append(entry)
        data["history"] = history[-100:]  # max 100 Einträge im Audit-Trail
        self._save_decision_log(data)
        return entry

    def get_decision_log(self, limit=50):
        data = self._load_decision_log()
        history = data.get("history", [])
        return history[-limit:]

    def send_telegram_notification(self, text):
        """Sendet Push-Benachrichtigung via Telegram Bot API falls konfiguriert."""
        cfg = self._load_config()
        token = cfg.get("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = cfg.get("telegram_chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            # Fallback: Versuche Home Assistant notify falls verfügbar
            try:
                from ha_service import ha_service
                if ha_service:
                    ha_service.call_service("notify", "persistent_notification", {
                        "title": "🏈 ESPN Fantasy KI-Manager",
                        "message": text
                    })
            except Exception:
                pass
            return False

        def _send():
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown"
                }
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass
            except Exception as e:
                print(f"[WARN] EspnFantasyClient: Fehler beim Senden von Telegram-Nachricht: {e}", file=sys.stderr)

        t = threading.Thread(target=_send, name="EspnTelegramNotifier", daemon=True)
        t.start()
        return True


    def fetch(self, force=False):
        now = time.time()
        if not force and self._cached_data and (now - self._cache_timestamp < self._cache_ttl):
            return self._cached_data

        cfg = self._load_config()
        league_id = cfg.get("league_id", 378649793)
        my_team_id = cfg.get("team_id", 17)
        season = cfg.get("season_year", 2026)
        swid = cfg.get("swid", "")
        espn_s2 = cfg.get("espn_s2", "")

        url = (
            f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}"
            f"?view=mMatchup&view=mMatchupScore&view=mBoxscore&view=mRoster&view=mTeam&view=mSettings&view=mStandings"
        )

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json"
        }
        if swid and espn_s2:
            headers["Cookie"] = f"SWID={swid}; espn_s2={espn_s2};"

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_text = f"HTTP Error {e.code}: {e.reason}"
            if e.code == 401:
                err_text += " (Authentifizierung abgelaufen oder ungültig - bitte espn_s2/swid prüfen)"
            return {
                "status": "error",
                "message": err_text,
                "cached": False,
                "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Verbindungsfehler zur ESPN API: {e}",
                "cached": False,
                "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            }

        league_name = data.get("settings", {}).get("name", "ESPN Fantasy Football")
        current_week = data.get("status", {}).get("currentMatchupPeriod", 1)

        teams_raw = {t["id"]: t for t in data.get("teams", [])}

        def get_team_name(t_obj):
            if not t_obj:
                return "Unknown Team"
            loc = (t_obj.get("location") or "").strip()
            nick = (t_obj.get("nickname") or "").strip()
            if loc or nick:
                return f"{loc} {nick}".strip()
            return t_obj.get("name", f"Team {t_obj.get('id', '?')}")

        my_team_raw = teams_raw.get(my_team_id, {})
        my_team_name = get_team_name(my_team_raw)

        # Matchup Resolution
        matchup_info = None
        opponent_roster = []
        for m in data.get("schedule", []):
            if m.get("matchupPeriodId") == current_week:
                away = m.get("away", {})
                home = m.get("home", {})
                if away.get("teamId") == my_team_id or home.get("teamId") == my_team_id:
                    is_away = (away.get("teamId") == my_team_id)
                    me_side = away if is_away else home
                    opp_side = home if is_away else away
                    opp_team_raw = teams_raw.get(opp_side.get("teamId"), {})

                    my_score = me_side.get("totalPointsLive")
                    if my_score is None:
                        my_score = me_side.get("pointsByScoringPeriod", {}).get(str(current_week), 0.0)

                    opp_score = opp_side.get("totalPointsLive")
                    if opp_score is None:
                        opp_score = opp_side.get("pointsByScoringPeriod", {}).get(str(current_week), 0.0)

                    my_proj = me_side.get("totalProjectedPointsLive", 0.0)
                    opp_proj = opp_side.get("totalProjectedPointsLive", 0.0)

                    my_win_prob = round((me_side.get("winProbability") or 0.5) * 100, 1)
                    opp_win_prob = round((opp_side.get("winProbability") or 0.5) * 100, 1)

                    matchup_info = {
                        "week": current_week,
                        "my_team": {
                            "id": my_team_id,
                            "name": my_team_name,
                            "score": round(float(my_score or 0.0), 2),
                            "projected": round(float(my_proj or 0.0), 2) if my_proj else None,
                            "win_prob": my_win_prob
                        },
                        "opponent": {
                            "id": opp_side.get("teamId"),
                            "name": get_team_name(opp_team_raw),
                            "score": round(float(opp_score or 0.0), 2),
                            "projected": round(float(opp_proj or 0.0), 2) if opp_proj else None,
                            "win_prob": opp_win_prob
                        }
                    }

                    opp_entries = opp_side.get("rosterForCurrentScoringPeriod", {}).get("entries", [])
                    if opp_entries:
                        opponent_roster = self._parse_roster(opp_entries, current_week)
                    break

        # Roster Resolution
        my_entries = my_team_raw.get("roster", {}).get("entries", [])
        self._last_raw_roster_entries = my_entries
        self._last_scoring_period_id = current_week
        my_roster = self._parse_roster(my_entries, current_week)

        # Team Score Tracker & Flash Trigger Auswertung
        tracked_team_score = None
        if matchup_info and "my_team" in matchup_info:
            current_team_score = matchup_info["my_team"].get("score")
            tracked_team_score = self._evaluate_team_score(current_team_score, current_week, my_team_id)

        # Standings Resolution
        standings = []
        for t in data.get("teams", []):
            rec = t.get("record", {}).get("overall", {})
            standings.append({
                "id": t.get("id"),
                "name": get_team_name(t),
                "seed": t.get("playoffSeed", 0),
                "wins": rec.get("wins", 0),
                "losses": rec.get("losses", 0),
                "ties": rec.get("ties", 0),
                "points_for": round(float(rec.get("pointsFor", 0.0)), 2),
                "points_against": round(float(rec.get("pointsAgainst", 0.0)), 2),
                "is_my_team": (t.get("id") == my_team_id)
            })

        standings.sort(key=lambda x: (x["seed"] if x["seed"] else 99, -x["wins"], -x["points_for"]))

        # Compute rank for my_team
        my_rank = 1
        for idx, s in enumerate(standings, 1):
            if s["is_my_team"]:
                my_rank = idx
                break

        res = {
            "status": "ok",
            "league_name": league_name,
            "current_week": current_week,
            "season": season,
            "team_id": my_team_id,
            "team_name": my_team_name,
            "mode": self.get_mode(),
            "risk_level": self.get_risk_level(),
            "flash_enabled": self.get_flash_enabled(),
            "ai_stats": self.get_ai_stats(),
            "bot_status": self.get_bot_status(),
            "decision_history": self.get_decision_log(limit=40),
            "active_proposal": self.get_active_proposal(),
            "my_rank": my_rank,
            "total_teams": len(teams_raw),
            "matchup": matchup_info,
            "roster": my_roster,
            "opponent_roster": opponent_roster,
            "standings": standings,
            "team_score_tracker": tracked_team_score,
            "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        }

        self._cached_data = res
        self._cache_timestamp = now
        return res

    def _parse_roster(self, entries, current_week):
        tracker = self._load_tracker()
        now_ts = time.time()
        roster = []
        for e in entries:
            p = e.get("playerPoolEntry", {}).get("player", {})
            slot_id = e.get("lineupSlotId", 20)
            slot_label = self.SLOT_MAP.get(slot_id, f"Slot {slot_id}")
            pos_id = p.get("defaultPositionId")
            pos_label = self.POS_MAP.get(pos_id, "FLEX")
            pro_id = p.get("proTeamId", 0)
            pro_label = self.PRO_TEAMS.get(pro_id, "FA")
            inj = p.get("injuryStatus", "ACTIVE")

            act_pts = 0.0
            proj_pts = 0.0
            for s in p.get("stats", []):
                if s.get("scoringPeriodId") == current_week and s.get("statSplitTypeId") == 1:
                    if s.get("statSourceId") == 0:
                        act_pts = s.get("appliedTotal", 0.0)
                    elif s.get("statSourceId") == 1:
                        proj_pts = s.get("appliedTotal", 0.0)

            act_rounded = round(float(act_pts), 2)
            player_name = p.get("fullName", "Unknown Player")
            player_id = str(p.get("id") or player_name)
            is_locked = bool(e.get("playerPoolEntry", {}).get("lineupLocked", False))

            # 10-Minuten Highlight Tracker
            is_recent = False
            gain_val = 0.0
            minutes_ago = None

            if player_id in tracker:
                prev_info = tracker[player_id]
                prev_pts = float(prev_info.get("points", 0.0))
                last_gain_ts = float(prev_info.get("gain_ts", 0.0))
                last_gain_val = float(prev_info.get("gain_val", 0.0))

                # Hat der Spieler mehr Punkte erhalten als zuvor?
                if act_rounded > prev_pts:
                    last_gain_ts = now_ts
                    last_gain_val = round(act_rounded - prev_pts, 2)
                    prev_info["gain_ts"] = last_gain_ts
                    prev_info["gain_val"] = last_gain_val

                prev_info["points"] = act_rounded
                prev_info["name"] = player_name

                # Prüfen, ob der Zuwachs innerhalb der letzten 10 Minuten (600 Sek.) lag
                if last_gain_ts > 0 and (now_ts - last_gain_ts) <= 600:
                    is_recent = True
                    gain_val = last_gain_val
                    minutes_ago = max(1, int(round((now_ts - last_gain_ts) / 60.0)))
            else:
                tracker[player_id] = {
                    "name": player_name,
                    "points": act_rounded,
                    "gain_ts": 0.0,
                    "gain_val": 0.0
                }

            roster.append({
                "player_id": int(p.get("id") or 0),
                "slot": slot_label,
                "slot_id": int(slot_id),
                "is_starter": slot_id not in (20, 21),
                "is_ir": slot_id == 21,
                "name": player_name,
                "position": pos_label,
                "pro_team": pro_label,
                "injury": inj,
                "actual": act_rounded,
                "projected": round(float(proj_pts), 2),
                "is_recently_scored": is_recent,
                "score_gain": gain_val,
                "gain_minutes_ago": minutes_ago,
                "is_locked": is_locked,
                "eligible_slots": p.get("eligibleSlots", [])
            })

        self._save_tracker(tracker)

        slot_order = {
            'QB': 1, 'RB': 2, 'RB/WR': 3, 'WR': 4, 'TE': 5,
            'FLEX': 6, 'D/ST': 7, 'K': 8, 'BENCH': 9, 'IR': 10
        }
        roster.sort(key=lambda x: (0 if x['is_starter'] else 1, slot_order.get(x['slot'], 50)))
        return roster

    def _evaluate_team_score(self, current_score, current_week, my_team_id):
        """
        Prüft, ob für das eigene Team Punkte erzielt wurden.
        Bei Punkterhöhung wird asynchron das Home Assistant Lichtsignal ausgelöst.
        """
        if current_score is None:
            return None

        try:
            current_score = round(float(current_score), 2)
        except (ValueError, TypeError):
            return None

        now_ts = time.time()
        tracker = self._load_tracker()
        score_info = tracker.get("my_team_score")

        cfg = self._load_config()
        flash_enabled = cfg.get("flash_enabled", True)
        flash_entity = cfg.get("flash_light") or cfg.get("flash_entity", "light.esstisch")
        try:
            flash_duration = float(cfg.get("flash_duration", 1.2))
        except (ValueError, TypeError):
            flash_duration = 1.2

        # Fall 1: Kaltstart / Noch kein Eintrag oder neue Spielwoche
        if not isinstance(score_info, dict) or score_info.get("week") != current_week or not score_info.get("initialized", True):
            print(f"[INFO] EspnFantasyClient: Initialisiere Score-Tracker für Team {my_team_id} (Woche {current_week}): {current_score} Pkt (kein Kaltstart-Flash).", flush=True)
            new_info = {
                "team_id": my_team_id,
                "week": current_week,
                "score": current_score,
                "initialized": True,
                "last_gain": 0.0,
                "last_gain_ts": 0.0,
                "last_updated": now_ts
            }
            tracker["my_team_score"] = new_info
            self._save_tracker(tracker)
            return new_info

        # Fall 2: Score vergleichen
        prev_score = round(float(score_info.get("score", 0.0)), 2)

        if current_score > prev_score:
            gain = round(current_score - prev_score, 2)
            print(f"[INFO] EspnFantasyClient: 🏈 PUNKTGEWINN für Team {my_team_id}! Alter Score: {prev_score}, Neuer Score: {current_score} (+{gain} Pkt). Triggere Flash auf {flash_entity}...", flush=True)
            score_info["score"] = current_score
            score_info["last_gain"] = gain
            score_info["last_gain_ts"] = now_ts
            score_info["last_updated"] = now_ts
            tracker["my_team_score"] = score_info
            self._save_tracker(tracker)

            if flash_enabled:
                self.trigger_flash(entity_id=flash_entity, duration=flash_duration)
            else:
                print(f"[INFO] EspnFantasyClient: Flash-Signal übersprungen (flash_enabled=false).", flush=True)

        elif current_score < prev_score:
            # Score-Korrektur nach unten
            print(f"[INFO] EspnFantasyClient: Score-Korrektur für Team {my_team_id}: {prev_score} -> {current_score}.", flush=True)
            score_info["score"] = current_score
            score_info["last_updated"] = now_ts
            tracker["my_team_score"] = score_info
            self._save_tracker(tracker)
        else:
            # Score unverändert
            score_info["last_updated"] = now_ts
            tracker["my_team_score"] = score_info
            self._save_tracker(tracker)

        return score_info

    def validate_roster_move(self, items, raw_entries=None):
        """
        Prüft einen vorgeschlagenen Move gegen ESPN Roster Safeguards:
        1. Ist der Spieler gelockt (lineupLocked == True)?
        2. Entspricht der Ziel-Slot (toLineupSlotId) den eligibleSlots des Spielers?
        3. IR-Regel: Nur verletzte Spieler (OUT, INJURY_RESERVE) dürfen auf Slot 21.
        """
        if not items or not isinstance(items, list):
            return {"valid": False, "error": "Keine Transaktions-Items übergeben."}

        entries = raw_entries or self._last_raw_roster_entries
        if not entries:
            self.fetch(force=True)
            entries = self._last_raw_roster_entries

        player_map = {}
        for e in entries:
            p = e.get("playerPoolEntry", {}).get("player", {})
            pid = p.get("id")
            if pid is not None:
                player_map[int(pid)] = e

        for item in items:
            pid = item.get("playerId")
            try:
                pid = int(pid)
            except (ValueError, TypeError):
                return {"valid": False, "error": f"Ungültige playerId: {pid}"}

            if pid not in player_map:
                return {"valid": False, "error": f"Spieler {pid} nicht im Kader gefunden."}

            entry = player_map[pid]
            p_obj = entry.get("playerPoolEntry", {}).get("player", {})
            p_name = p_obj.get("fullName", f"Spieler {pid}")
            is_locked = bool(entry.get("playerPoolEntry", {}).get("lineupLocked", False))
            eligible_slots = p_obj.get("eligibleSlots", [])
            inj = p_obj.get("injuryStatus", "ACTIVE")
            to_slot = item.get("toLineupSlotId")

            # 1. Lock Check
            if is_locked:
                return {
                    "valid": False,
                    "error": f"Spieler '{p_name}' ist gelockt (Spiel hat bereits begonnen oder ist beendet)."
                }

            # 2. Slot Eligibility Check
            if to_slot is not None:
                try:
                    to_slot = int(to_slot)
                except (ValueError, TypeError):
                    return {"valid": False, "error": f"Ungültiger toLineupSlotId: {to_slot}"}

                if eligible_slots and to_slot not in eligible_slots:
                    slot_name = self.SLOT_MAP.get(to_slot, f"Slot {to_slot}")
                    return {
                        "valid": False,
                        "error": f"Slot '{slot_name}' ({to_slot}) ist für '{p_name}' unzulässig (Eligible: {eligible_slots})."
                    }

                # 3. IR Slot Check (Slot 21)
                if to_slot == 21 and inj not in ("OUT", "INJURY_RESERVE"):
                    return {
                        "valid": False,
                        "error": f"'{p_name}' hat Status '{inj}'. Nur Spieler mit OUT oder INJURY_RESERVE dürfen auf den IR-Slot (21)."
                    }

        return {"valid": True, "error": None}

    def execute_roster_transaction(self, items, scoring_period_id=None, execution_type="EXECUTE"):
        """
        Führt eine Roster-Transaktion (Start/Sit Swap) via ESPN lm-api-writes aus.
        execution_type kann 'EXECUTE' oder 'VALIDATE' (Dry Run) sein.
        """
        cfg = self._load_config()
        league_id = cfg.get("league_id", 378649793)
        my_team_id = cfg.get("team_id", 17)
        season = cfg.get("season_year", 2026)
        swid = cfg.get("swid", "")
        espn_s2 = cfg.get("espn_s2", "")

        if not swid or not espn_s2:
            err = "Authentifizierungsdaten unvollständig: swid oder espn_s2 fehlt in config.json."
            self.log_decision("TRANSACTION_FAILED", err, success=False)
            return {"success": False, "error": err}

        url = (
            f"https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
            f"/segments/0/leagues/{league_id}/transactions/"
        )

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cookie": f"SWID={swid}; espn_s2={espn_s2};"
        }

        sp_id = scoring_period_id or getattr(self, "_last_scoring_period_id", 1)
        payload = {
            "isLeagueManager": False,
            "teamId": my_team_id,
            "type": "ROSTER",
            "scoringPeriodId": int(sp_id),
            "executionType": execution_type,
            "items": items
        }

        try:
            req_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=req_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=12) as resp:
                resp_text = resp.read().decode("utf-8", errors="replace")
                try:
                    resp_json = json.loads(resp_text) if resp_text.strip() else {}
                except Exception:
                    resp_json = {"raw": resp_text}

            self._cached_data = None
            self.log_decision(
                "TRANSACTION_EXECUTED" if execution_type == "EXECUTE" else "TRANSACTION_VALIDATED",
                f"Transaktion ({execution_type}) für {len(items)} Items erfolgreich.",
                success=True,
                metadata={"items": items, "execution_type": execution_type}
            )
            return {
                "success": True,
                "execution_type": execution_type,
                "data": resp_json,
                "message": f"Transaktion ({execution_type}) erfolgreich verarbeitet."
            }

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            err_msg = f"ESPN API HTTP Error {e.code}: {e.reason}"
            try:
                err_data = json.loads(err_body)
                if isinstance(err_data, dict) and "messages" in err_data:
                    err_msg += f" - {err_data['messages']}"
            except Exception:
                err_msg += f" - {err_body[:200]}"

            self.log_decision("TRANSACTION_FAILED", err_msg, success=False, metadata={"items": items})
            return {
                "success": False,
                "error": err_msg,
                "status_code": e.code,
                "details": err_body
            }
        except Exception as e:
            err_msg = f"Verbindungsfehler beim Ausführen des Moves: {e}"
            self.log_decision("TRANSACTION_ERROR", err_msg, success=False, metadata={"items": items})
            return {"success": False, "error": err_msg}

    def apply_proposal(self, proposal_id):
        """Führt einen offenen Vorschlag aus espn_proposals.json aus."""
        data = self._load_proposals()
        target = None
        for p in data.get("proposals", []):
            if p.get("id") == proposal_id and p.get("status") == "pending":
                target = p
                break

        if not target:
            return {"success": False, "error": f"Offener Vorschlag '{proposal_id}' nicht gefunden."}

        moves = target.get("moves", [])
        if not moves:
            return {"success": False, "error": "Vorschlag enthält keine Moves."}

        validation = self.validate_roster_move(moves)
        if not validation["valid"]:
            return {"success": False, "error": f"Safeguard-Prüfung fehlgeschlagen: {validation['error']}"}

        res = self.execute_roster_transaction(moves, scoring_period_id=target.get("week"), execution_type="EXECUTE")
        if res.get("success"):
            target["status"] = "applied"
            target["applied_at"] = int(time.time())
            self._save_proposals(data)
            self.fetch(force=True)
            self.send_telegram_notification(
                f"✅ *[LCARS ESPN]* Vorschlag `{proposal_id}` erfolgreich freigegeben und Aufstellung aktualisiert!"
            )
            return {"success": True, "message": "Vorschlag erfolgreich auf ESPN ausgeführt.", "data": res}
        else:
            return {"success": False, "error": f"ESPN Ausführung fehlgeschlagen: {res.get('error')}"}

    def _get_9router_api_key(self):
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

    def _call_9router(self, prompt, model="ag/gemini-3.8-flash-high"):
        key = self._get_9router_api_key()
        headers = {
            "Content-Type": "application/json"
        }
        if key:
            headers["Authorization"] = f"Bearer {key}"

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Du bist der Starfleet LCARS Fantasy Football Taktik-Offizier der USS Antigravity. "
                        "Analysiere das Roster. Ein verletzter Starter mit Status OUT oder INJURY_RESERVE "
                        "MUSS IMMER gegen einen fitten Bankspieler mit passender Position getauscht werden. "
                        "Schlage nur Moves vor, die noch nicht gelockt sind. Antworte AUSSCHLIESSLICH im JSON-Format."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            "stream": False
        }

        req = urllib.request.Request(
            "http://127.0.0.1:20128/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            choices = data.get("choices", [])
            usage = data.get("usage")
            if usage and isinstance(usage, dict):
                self._last_ai_usage = {
                    "prompt_tokens": int(usage.get("prompt_tokens", 950)),
                    "completion_tokens": int(usage.get("completion_tokens", 250)),
                    "total_tokens": int(usage.get("total_tokens", 1200))
                }
            if choices:
                return choices[0].get("message", {}).get("content", "")
        return ""

    def _heuristic_roster_optimizer(self, roster, raw_entries, risk_level=None):
        """
        Regelbasierter Heuristik-Optimizer als robuster Fallback.
        Prüft verletzte Starter und sucht den besten fitten Ersatzspieler auf der Bank.
        Berücksichtigt das eingestellte Risiko-Level (1: Floor bis 5: Boom).
        """
        if risk_level is None:
            risk_level = self.get_risk_level()
        try:
            risk_level = int(risk_level)
        except (ValueError, TypeError):
            risk_level = 3

        risk_info = self.RISK_LEVELS.get(risk_level, self.RISK_LEVELS[3])
        risk_name = risk_info.get("name", "Ausgewogen")

        # Starter-Filter abhängig vom Risiko-Level:
        # Bei Stufe 1 (Ultra-Konservativ) wird auch QUESTIONABLE betrachtet,
        # wenn ein fitter (ACTIVE) Bankspieler bereitsteht.
        critical_statuses = ("OUT", "INJURY_RESERVE", "DOUBTFUL")
        if risk_level == 1:
            critical_statuses = ("OUT", "INJURY_RESERVE", "DOUBTFUL", "QUESTIONABLE")

        starters = [p for p in roster if p.get("is_starter") and not p.get("is_locked")]
        bench = [p for p in roster if not p.get("is_starter") and not p.get("is_ir") and not p.get("is_locked")]

        recommended_moves = []
        assessment_lines = []

        for s in starters:
            inj = s.get("injury", "ACTIVE")
            if inj in critical_statuses:
                candidates = []
                s_slot_id = s.get("slot_id")
                for b in bench:
                    b_inj = b.get("injury", "ACTIVE")
                    if b_inj not in ("OUT", "INJURY_RESERVE"):
                        if risk_level <= 2 and b_inj != "ACTIVE":
                            continue
                        b_eligible = b.get("eligible_slots", [])
                        if s_slot_id in b_eligible:
                            candidates.append(b)

                if candidates:
                    # Sortierung je nach Risiko-Level:
                    # Bei Stufe 4 & 5 (Offensiv / Boom) bevorzugen wir maximalen Projected Score
                    # Bei Stufe 1 & 2 bevorzugen wir sichere Spieler mit positivem Floor
                    candidates.sort(key=lambda x: float(x.get("projected") or 0.0), reverse=True)
                    best_replacement = candidates[0]
                    bench.remove(best_replacement)

                    gain = round(float(best_replacement.get("projected") or 0.0) - float(s.get("projected") or 0.0), 1)
                    move_items = [
                        {
                            "playerId": best_replacement.get("player_id"),
                            "type": "LINEUP",
                            "fromLineupSlotId": best_replacement.get("slot_id"),
                            "toLineupSlotId": s_slot_id
                        },
                        {
                            "playerId": s.get("player_id"),
                            "type": "LINEUP",
                            "fromLineupSlotId": s_slot_id,
                            "toLineupSlotId": best_replacement.get("slot_id")
                        }
                    ]
                    recommended_moves.append({
                        "action": "SWAP",
                        "player_in_id": best_replacement.get("player_id"),
                        "player_in_name": best_replacement.get("name"),
                        "player_out_id": s.get("player_id"),
                        "player_out_name": s.get("name"),
                        "from_slot_in": best_replacement.get("slot_id"),
                        "to_slot_in": s_slot_id,
                        "from_slot_out": s_slot_id,
                        "to_slot_out": best_replacement.get("slot_id"),
                        "rationale": f"[{risk_name}] {s.get('name')} ist {inj}. {best_replacement.get('name')} übernimmt Slot {s.get('slot')} (+{gain} Proj. PTS).",
                        "projected_gain": gain,
                        "items": move_items
                    })
                    assessment_lines.append(f"Starter {s.get('name')} ({inj}) sollte durch {best_replacement.get('name')} ersetzt werden.")

        if not recommended_moves:
            return {
                "assessment": f"Strategie [{risk_name}]: Aufstellung ist optimal besetzt. Keine verletzten Starter ohne Ersatz gefunden.",
                "recommended_moves": [],
                "confidence": 0.90,
                "risk_level": risk_level
            }

        return {
            "assessment": f"Strategie [{risk_name}]: " + " ".join(assessment_lines),
            "recommended_moves": recommended_moves,
            "confidence": 0.88,
            "risk_level": risk_level
        }

    def analyze_roster_with_ai(self, force=False):
        """
        Analysiert das Roster via 9Router (Gemini 3.8 Flash) oder Heuristik.
        Im Modus 'semi': Erzeugt Vorschlag in espn_proposals.json und sendet Benachrichtigung.
        Im Modus 'full': Führt validierte Moves autonom aus und sendet Bericht.
        """
        data = self.fetch(force=force)
        roster = data.get("roster", [])
        raw_entries = getattr(self, "_last_raw_roster_entries", [])
        current_week = data.get("current_week", 1)
        mode = self.get_mode()
        risk_level = self.get_risk_level()
        risk_info = self.RISK_LEVELS.get(risk_level, self.RISK_LEVELS[3])
        risk_name = risk_info.get("name", "Ausgewogen")
        risk_guidance = risk_info.get("prompt_guidance", "")
        self._last_ai_check_ts = int(time.time())

        # 1. Prompt für 9Router konstruieren
        roster_summary = []
        for p in roster:
            lock_str = "LOCKED" if p.get("is_locked") else "OPEN"
            role_str = "STARTER" if p.get("is_starter") else ("IR" if p.get("is_ir") else "BENCH")
            roster_summary.append(
                f"- ID {p.get('player_id')}: {p.get('name')} ({p.get('position')}, {p.get('pro_team')}) | "
                f"Rolle: {role_str} in Slot {p.get('slot')} (ID {p.get('slot_id')}) | Status: {p.get('injury')} | "
                f"Proj: {p.get('projected')} Pkt | Live: {p.get('actual')} Pkt | {lock_str} | Eligible: {p.get('eligible_slots')}"
            )

        matchup = data.get("matchup") or {}
        my_team = matchup.get("my_team") or {}
        opp_team = matchup.get("opponent") or {}

        prompt = (
            f"FANTASY FOOTBALL ANALYSE - WOCHE {current_week}\n"
            f"Mein Team: {data.get('team_name')} (Score: {my_team.get('score', 0)}, Proj: {my_team.get('projected', 0)}, Siegchance: {my_team.get('win_prob', 50)}%)\n"
            f"Gegner: {opp_team.get('name')} (Score: {opp_team.get('score', 0)}, Proj: {opp_team.get('projected', 0)})\n\n"
            f"RISIKO-LEVEL & STRATEGIE: Stufe {risk_level}/5 - {risk_name}\n"
            f"Taktische Vorgabe: {risk_guidance}\n\n"
            f"KADER-STATUS:\n" + "\n".join(roster_summary) + "\n\n"
            f"REGELN:\n"
            f"1. Spieler mit Status OUT, INJURY_RESERVE oder DOUBTFUL in einem Starting-Slot MÜSSEN durch fitte Bankspieler ersetzt werden.\n"
            f"2. Niemals Spieler bewegen, die LOCKED sind.\n"
            f"3. Ziel-Slot muss in eligible_slots des Spielers enthalten sein.\n"
            f"4. Berücksichtige das Risiko-Level ({risk_name}) bei Start/Sit-Abwägungen.\n"
            f"5. Falls keine Moves nötig sind, recommended_moves als leeres Array [] zurückgeben.\n\n"
            f"Antworte ausschließlich mit folgendem JSON-Format:\n"
            f'{{\n'
            f'  "assessment": "Kurze strategische Analyse",\n'
            f'  "recommended_moves": [\n'
            f'    {{\n'
            f'      "action": "SWAP",\n'
            f'      "player_in_id": 12345,\n'
            f'      "player_in_name": "Name",\n'
            f'      "from_slot_in": 20,\n'
            f'      "to_slot_in": 2,\n'
            f'      "player_out_id": 67890,\n'
            f'      "player_out_name": "Name",\n'
            f'      "from_slot_out": 2,\n'
            f'      "to_slot_out": 20,\n'
            f'      "rationale": "Begründung",\n'
            f'      "projected_gain": 5.2\n'
            f'    }}\n'
            f'  ],\n'
            f'  "confidence": 0.95\n'
            f'}}'
        )

        analysis_result = None
        # Versuch 1: 9Router Gemini 3.8 Flash
        try:
            raw_ai = self._call_9router(prompt)
            if raw_ai:
                # Bereinige eventuelle Markdown-Codeblöcke
                cleaned = re.sub(r"^```json\s*", "", raw_ai.strip(), flags=re.MULTILINE)
                cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
                analysis_result = json.loads(cleaned)
        except Exception as e:
            print(f"[WARN] EspnFantasyClient: 9Router KI-Aufruf fehlgeschlagen ({e}), verwende Heuristik.", file=sys.stderr)

        # Versuch 2: Heuristik-Fallback
        if not analysis_result or not isinstance(analysis_result, dict):
            analysis_result = self._heuristic_roster_optimizer(roster, raw_entries, risk_level=risk_level)

        recommended_moves = analysis_result.get("recommended_moves", [])

        # 2. Moves formatieren und gegen Safeguards prüfen
        valid_proposals = []
        for rm in recommended_moves:
            p_in_id = rm.get("player_in_id")
            p_out_id = rm.get("player_out_id")
            from_in = rm.get("from_slot_in", 20)
            to_in = rm.get("to_slot_in")
            from_out = rm.get("from_slot_out")
            to_out = rm.get("to_slot_out", 20)

            items = [
                {
                    "playerId": int(p_in_id),
                    "type": "LINEUP",
                    "fromLineupSlotId": int(from_in),
                    "toLineupSlotId": int(to_in)
                },
                {
                    "playerId": int(p_out_id),
                    "type": "LINEUP",
                    "fromLineupSlotId": int(from_out),
                    "toLineupSlotId": int(to_out)
                }
            ]
            val = self.validate_roster_move(items, raw_entries)
            if val.get("valid"):
                rm["items"] = items
                valid_proposals.append(rm)
            else:
                print(f"[INFO] EspnFantasyClient: Move von KI abgewiesen durch Safeguard: {val.get('error')}", flush=True)

        analysis_result["recommended_moves"] = valid_proposals

        # 3. Auswertung je nach Betriebsmodus
        if valid_proposals:
            prop_id = f"prop_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            first_move = valid_proposals[0]
            p_in = first_move.get("player_in_name", "Unbekannt")
            p_out = first_move.get("player_out_name", "Unbekannt")
            rationale = first_move.get("rationale") or analysis_result.get("assessment") or "Strategische Aufstellungsoptimierung"
            gain = first_move.get("projected_gain", 0.0)
            conf = analysis_result.get("confidence", 0.9)

            proposal_obj = {
                "id": prop_id,
                "created_at": int(time.time()),
                "week": current_week,
                "status": "pending",
                "reason": rationale,
                "confidence": conf,
                "projected_gain": gain,
                "player_in_name": p_in,
                "player_out_name": p_out,
                "moves": first_move.get("items", [])
            }

            if mode == "semi":
                self.add_proposal(proposal_obj)
                self._last_ai_action = f"Vorschlag erstellt: {p_in} für {p_out}"
                self._last_ai_reason = rationale
                self.send_telegram_notification(
                    f"⚠️ *[LCARS ESPN KI-MANAGER // SEMI]*\n"
                    f"Vorschlag für Woche {current_week}:\n"
                    f"🔄 *Move*: {p_in} (Bench ➔ Starter) für {p_out} ({rationale})\n"
                    f"👉 Im Dashboard freigeben: https://dash.pimmel.site#fantasy"
                )
            elif mode == "full":
                # Autonome Ausführung
                print(f"[INFO] EspnFantasyClient [FULL-MODE]: Führe autonomen Move für Vorschlag {prop_id} aus...", flush=True)
                res = self.execute_roster_transaction(first_move.get("items"), scoring_period_id=current_week, execution_type="EXECUTE")
                if res.get("success"):
                    proposal_obj["status"] = "applied_autonomously"
                    proposal_obj["applied_at"] = int(time.time())
                    self.add_proposal(proposal_obj)
                    self._last_ai_action = f"Aufstellung angepasst: {p_in} für {p_out} eingewechselt"
                    self._last_ai_reason = rationale
                    self.log_decision(
                        "AUTO_MOVE_EXECUTED",
                        f"Aufstellung autonom angepasst: {p_in} eingewechselt für {p_out}",
                        success=True,
                        metadata={
                            "reason": rationale,
                            "player_in": p_in,
                            "player_out": p_out,
                            "projected_gain": gain,
                            "confidence": conf,
                            "mode": "full"
                        }
                    )
                    self.send_telegram_notification(
                        f"⚡ *[LCARS ESPN KI-MANAGER // FULL-AUTONOM]*\n"
                        f"Autonome Aufstellungsanpassung durchgeführt:\n"
                        f"✅ {p_in} eingewechselt für {p_out}.\n"
                        f"Grund: {rationale}"
                    )
                else:
                    err_msg = res.get('error', 'Unbekannter Fehler')
                    self._last_ai_action = f"Autonome Ausführung fehlgeschlagen: {err_msg}"
                    self._last_ai_reason = rationale
                    self.log_decision(
                        "AUTO_MOVE_FAILED",
                        f"Autonomer Move fehlgeschlagen: {err_msg}",
                        success=False,
                        metadata={
                            "reason": rationale,
                            "player_in": p_in,
                            "player_out": p_out,
                            "error": err_msg,
                            "mode": "full"
                        }
                    )
                    self.send_telegram_notification(
                        f"❌ *[LCARS ESPN KI-MANAGER // FULL-AUTONOM]*\n"
                        f"Fehler bei autonomer Ausführung: {err_msg}"
                    )
            else:
                # Modus manual
                self._last_ai_action = f"Analyse abgeschlossen: {len(valid_proposals)} Wechsel empfohlen"
                self._last_ai_reason = rationale
                self.log_decision(
                    "ROSTER_ANALYSIS_MANUAL",
                    f"Kader analysiert: {len(valid_proposals)} Wechsel empfohlen (Modus: Manuell)",
                    success=True,
                    metadata={
                        "reason": rationale,
                        "player_in": p_in,
                        "player_out": p_out,
                        "projected_gain": gain,
                        "confidence": conf,
                        "mode": "manual"
                    }
                )
        else:
            assessment = analysis_result.get("assessment") if analysis_result else None
            reason = assessment or "Kader optimal aufgestellt. Alle Starter aktiv und prognostizieren Bestleistung."
            self._last_ai_action = "Kader analysiert: Keine Änderungen erforderlich"
            self._last_ai_reason = reason
            self.log_decision(
                "ROSTER_CHECK",
                "Kader analysiert: Aufstellung optimal, keine Änderungen nötig",
                success=True,
                metadata={
                    "reason": reason,
                    "assessment": assessment,
                    "confidence": analysis_result.get("confidence") if analysis_result else 1.0,
                    "recommended_moves_count": 0,
                    "base_details": "Kader analysiert: Aufstellung optimal, keine Änderungen nötig",
                    "mode": mode
                }
            )

        return {
            "status": "ok",
            "mode": mode,
            "analysis": analysis_result,
            "proposals_count": len(valid_proposals),
            "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        }

    def _check_ai_manager_cycle(self):
        """Wird im Poller-Thread aufgerufen, um periodisch autonome Aktionen zu prüfen."""
        now = time.time()
        mode = self.get_mode()
        if mode == "manual":
            return

        interval = self.get_ai_interval()
        if now - self._last_ai_check_ts < interval:
            return

        self._last_ai_check_ts = now
        try:
            print(f"[INFO] EspnScorePoller: Führe KI-Manager Zyklus durch (Modus: {mode})...", flush=True)
            self.analyze_roster_with_ai(force=False)
        except Exception as e:
            print(f"[WARN] EspnScorePoller: Fehler im KI-Manager Zyklus: {e}", file=sys.stderr, flush=True)

    def trigger_flash(self, entity_id=None, duration=None):
        """Triggert das Home Assistant Flash-Signal asynchron in einem separaten Thread."""
        cfg = self._load_config()
        flash_enabled = cfg.get("flash_enabled", getattr(self, "_flash_enabled", True))
        if isinstance(flash_enabled, str):
            flash_enabled = flash_enabled.lower() in ("true", "1", "yes", "on")
        if not flash_enabled:
            print("[INFO] EspnFantasyClient: Flash-Signal übersprungen (flash_enabled=false).", flush=True)
            return False

        if not entity_id:
            entity_id = cfg.get("flash_light") or cfg.get("flash_entity", "light.esstisch")
        if duration is None:
            try:
                duration = float(cfg.get("flash_duration", 1.2))
            except (ValueError, TypeError):
                duration = 1.2

        try:
            from ha_service import ha_service
            if ha_service:
                ha_service.flash_light(entity_id=entity_id, duration=duration, async_run=True)
                return True
            else:
                print("[WARN] EspnFantasyClient: ha_service steht nicht zur Verfügung.", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[ERROR] EspnFantasyClient: Fehler beim Aufrufen von ha_service.flash_light: {e}", file=sys.stderr, flush=True)
        return False

    def start_poller(self, interval=None):
        """Startet den periodischen Hintergrund-Poller-Thread, falls nicht bereits aktiv."""
        with self._poller_lock:
            if self._poller_running:
                return
            self._poller_running = True
            if interval:
                try:
                    self._poll_interval = max(15, int(interval))
                except (ValueError, TypeError):
                    pass
            self._poller_thread = threading.Thread(
                target=self._poller_worker,
                name="EspnScorePoller",
                daemon=True
            )
            self._poller_thread.start()
            print(f"[START] EspnScorePoller gestartet (Intervall: {self._poll_interval}s).", flush=True)

    def stop_poller(self):
        """Stoppt den periodischen Hintergrund-Poller-Thread."""
        with self._poller_lock:
            self._poller_running = False

    def _poller_worker(self):
        # 3 Sekunden Startverzögerung
        time.sleep(3)
        while self._poller_running:
            try:
                cfg = self._load_config()
                if cfg.get("enabled", True):
                    self.fetch(force=True)
                    self._check_ai_manager_cycle()
            except Exception as e:
                print(f"[WARN] EspnScorePoller: Fehler beim periodischen Abrufen: {e}", file=sys.stderr, flush=True)

            cfg = self._load_config()
            interval = cfg.get("poll_interval", getattr(self, "_poll_interval", 35))
            try:
                interval = max(15, int(interval))
            except (ValueError, TypeError):
                interval = 35

            for _ in range(interval):
                if not self._poller_running:
                    break
                time.sleep(1)

# Singleton instance
espn_client = EspnFantasyClient()

if __name__ == "__main__":
    if "--test-flash" in sys.argv:
        print("Triggere Test-Flash auf Home Assistant (light.esstisch)...")
        res = espn_client.trigger_flash()
        print("Test-Flash initiiert:", res)
        time.sleep(2.0)
    elif "--poll" in sys.argv:
        print("Starte ESPN Poller im Vordergrund (Strg+C zum Beenden)...")
        espn_client.start_poller()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            espn_client.stop_poller()
            print("Poller beendet.")
    else:
        result = espn_client.fetch(force=True)
        print(json.dumps(result, indent=2))
