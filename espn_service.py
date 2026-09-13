#!/usr/bin/env python3
"""
ESPN Fantasy Football Data Service for LCARS Dashboard
Fetches league, team, live matchup, roster, and standings from ESPN Fantasy API.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
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

    def __init__(self, config_path=None):
        if not config_path:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        self.config_path = config_path
        self.tracker_path = os.path.join(os.path.dirname(self.config_path), "espn_score_tracker.json")
        self._cached_data = None
        self._cache_timestamp = 0
        self._cache_ttl = 30  # 30 Sekunden Cache für optimale Live-Aktualität

    def _load_tracker(self):
        try:
            if os.path.exists(self.tracker_path):
                with open(self.tracker_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_tracker(self, tracker):
        try:
            with open(self.tracker_path, "w", encoding="utf-8") as f:
                json.dump(tracker, f, indent=2)
        except Exception:
            pass

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
                    return cfg
        except Exception as e:
            print(f"[WARN] EspnFantasyClient: Konnte config.json nicht lesen: {e}", file=sys.stderr)
        return {}

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
        my_roster = self._parse_roster(my_entries, current_week)

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
            "my_rank": my_rank,
            "total_teams": len(teams_raw),
            "matchup": matchup_info,
            "roster": my_roster,
            "opponent_roster": opponent_roster,
            "standings": standings,
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
                "slot": slot_label,
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
                "gain_minutes_ago": minutes_ago
            })

        self._save_tracker(tracker)

        slot_order = {
            'QB': 1, 'RB': 2, 'RB/WR': 3, 'WR': 4, 'TE': 5,
            'FLEX': 6, 'D/ST': 7, 'K': 8, 'BENCH': 9, 'IR': 10
        }
        roster.sort(key=lambda x: (0 if x['is_starter'] else 1, slot_order.get(x['slot'], 50)))
        return roster

# Singleton instance
espn_client = EspnFantasyClient()

if __name__ == "__main__":
    result = espn_client.fetch(force=True)
    print(json.dumps(result, indent=2))
