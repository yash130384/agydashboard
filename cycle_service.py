#!/usr/bin/env python3
"""
Partner Cycle Tracking Service for LCARS System Dashboard.
Tracks menstrual cycles for multiple partners:
- Name, cycle duration (in days), period start date, period duration, notes.
- Calculates current cycle day, phase, fertility, next period forecast.
- Generates 1-30 day graph data highlighting the current day.
"""

import datetime
import json
import os
import sys
import threading
import uuid


class CycleService:
    def __init__(self, data_path=None):
        if not data_path:
            data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cycle_tracker.json")
        self.data_path = data_path
        self.lock = threading.Lock()
        self.partners = []
        self._load_data()

    def _load_data(self):
        with self.lock:
            if os.path.exists(self.data_path):
                try:
                    with open(self.data_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self.partners = data.get("partners", [])
                        elif isinstance(data, list):
                            self.partners = data
                except Exception as e:
                    print(f"[WARN] CycleService: Fehler beim Laden von cycle_tracker.json: {e}", file=sys.stderr)
            else:
                self.partners = []

    def _save_data(self):
        try:
            payload = {"partners": self.partners}
            with open(self.data_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[WARN] CycleService: Fehler beim Speichern von cycle_tracker.json: {e}", file=sys.stderr)
            return False

    def _calculate_phase(self, day_num, cycle_dur, period_dur):
        ovulation_day = max(cycle_dur - 14, period_dur + 2)

        if 1 <= day_num <= period_dur:
            return {
                "key": "menstruation",
                "name": "Menstruation (Regelblutung)",
                "fertility": "Niedrig",
                "color": "#cf4f4f",
                "curve_value": 20,
                "badge": "🔴 MENSTRUATION",
                "desc": "Regelblutung. Hormonspiegel (Östrogen/Progesteron) niedrig. Regeneration & Ruhe.",
            }
        elif period_dur < day_num < ovulation_day - 3:
            denom = max(1, (ovulation_day - 3 - period_dur))
            val = 40 + int(35 * ((day_num - period_dur) / denom))
            return {
                "key": "follicular",
                "name": "Follikelphase (Aufbau)",
                "fertility": "Mittel",
                "color": "#eb943a",
                "curve_value": val,
                "badge": "🟠 FOLLIKELPHASE",
                "desc": "Östrogen steigt kontinuierlich. Zunehmende Energie, Produktivität & gute Laune.",
            }
        elif ovulation_day - 3 <= day_num <= ovulation_day + 1:
            val = 100 if day_num == ovulation_day else (90 if day_num == ovulation_day - 1 else 80)
            return {
                "key": "ovulation",
                "name": "Eisprung / Fruchtbares Fenster",
                "fertility": "Sehr hoch (Maximum)",
                "color": "#baa4e5",
                "curve_value": val,
                "badge": "🟣 OVULATION / PEAK",
                "desc": "LH- & Östrogen-Peak! Maximale Fruchtbarkeit, hohe Energie, gesteigerte Libido & Ausstrahlung.",
            }
        elif ovulation_day + 1 < day_num <= cycle_dur:
            denom = max(1, (cycle_dur - ovulation_day))
            val = max(25, 75 - int(45 * ((day_num - ovulation_day) / denom)))
            return {
                "key": "luteal",
                "name": "Lutealphase (Gelbkörperphase)",
                "fertility": "Niedrig",
                "color": "#8899ff",
                "curve_value": val,
                "badge": "🔵 LUTEALPHASE",
                "desc": "Progesteron dominant. Körper bereitet sich auf Zyklusende vor. Eventuell PMS-sensibel.",
            }
        else:
            return {
                "key": "extended",
                "name": "Verzögerung / Folgezyklus",
                "fertility": "Niedrig",
                "color": "#d29b7f",
                "curve_value": 20,
                "badge": "🟡 ÜBERHILFE / FOLGETAGE",
                "desc": "Tage über die reguläre Zyklusdauer hinaus. Neuer Zyklus steht bevor.",
            }

    def enrich_partner(self, p):
        try:
            cycle_dur = int(p.get("cycle_duration", 28))
        except (ValueError, TypeError):
            cycle_dur = 28
        if cycle_dur < 15:
            cycle_dur = 15
        if cycle_dur > 50:
            cycle_dur = 50

        try:
            period_dur = int(p.get("period_duration", 5))
        except (ValueError, TypeError):
            period_dur = 5
        if period_dur < 1:
            period_dur = 1
        if period_dur > 10:
            period_dur = 10

        start_date_str = p.get("start_date")
        today = datetime.date.today()

        if not start_date_str:
            start_date = today
            start_date_str = today.isoformat()
        else:
            try:
                start_date = datetime.date.fromisoformat(start_date_str)
            except Exception:
                start_date = today
                start_date_str = today.isoformat()

        diff_days = (today - start_date).days
        if diff_days >= 0:
            current_day = (diff_days % cycle_dur) + 1
            cycle_num = (diff_days // cycle_dur) + 1
        else:
            current_day = 1
            cycle_num = 1

        days_until_next = max(0, cycle_dur - current_day + 1)
        next_period_dt = start_date + datetime.timedelta(days=(diff_days // cycle_dur + (1 if diff_days >= 0 else 0)) * cycle_dur)
        next_period_str = next_period_dt.strftime("%d.%m.%Y")
        start_date_formatted = start_date.strftime("%d.%m.%Y")

        current_phase = self._calculate_phase(current_day, cycle_dur, period_dur)
        current_phase["is_current"] = True
        current_phase["day"] = current_day

        days_graph = []
        for d in range(1, 31):
            phase_info = self._calculate_phase(d, cycle_dur, period_dur)
            is_cur = (d == current_day)
            days_graph.append({
                "day": d,
                "label": f"Tag {d}",
                "is_current": is_cur,
                "phase_key": phase_info["key"],
                "phase_name": phase_info["name"],
                "fertility": phase_info["fertility"],
                "curve_value": phase_info["curve_value"],
                "color": phase_info["color"],
                "badge": phase_info["badge"],
                "desc": phase_info["desc"] + (" ★ HEUTE" if is_cur else ""),
            })

        ovulation_day = max(cycle_dur - 14, period_dur + 2)

        return {
            "id": p.get("id"),
            "name": p.get("name", "Partnerin"),
            "cycle_duration": cycle_dur,
            "period_duration": period_dur,
            "start_date": start_date_str,
            "start_date_formatted": start_date_formatted,
            "next_period_date": next_period_str,
            "notes": p.get("notes", ""),
            "current_day": current_day,
            "cycle_number": cycle_num,
            "days_until_next_period": days_until_next,
            "ovulation_day": ovulation_day,
            "current_phase": current_phase,
            "progress_percent": round((current_day / cycle_dur) * 100, 1) if cycle_dur else 0,
            "days_graph": days_graph,
        }

    def get_all_partners(self):
        with self.lock:
            return [self.enrich_partner(p) for p in self.partners]

    def add_partner(self, name, cycle_duration=28, start_date=None, period_duration=5, notes=""):
        name_str = str(name).strip()
        if not name_str:
            return {"success": False, "error": "Name der Partnerin ist erforderlich"}

        try:
            c_dur = int(cycle_duration)
            if c_dur < 15 or c_dur > 60:
                c_dur = 28
        except Exception:
            c_dur = 28

        try:
            p_dur = int(period_duration)
            if p_dur < 1 or p_dur > 15:
                p_dur = 5
        except Exception:
            p_dur = 5

        if not start_date:
            s_date = datetime.date.today().isoformat()
        else:
            try:
                datetime.date.fromisoformat(str(start_date).strip())
                s_date = str(start_date).strip()
            except Exception:
                s_date = datetime.date.today().isoformat()

        new_partner = {
            "id": f"p_{int(datetime.datetime.now().timestamp())}_{uuid.uuid4().hex[:6]}",
            "name": name_str,
            "cycle_duration": c_dur,
            "period_duration": p_dur,
            "start_date": s_date,
            "notes": str(notes or "").strip(),
            "created_at": datetime.datetime.now().isoformat(),
        }

        with self.lock:
            self.partners.append(new_partner)
            self._save_data()

        return {"success": True, "partner": self.enrich_partner(new_partner)}

    def update_partner(self, partner_id, updates):
        with self.lock:
            for p in self.partners:
                if p.get("id") == partner_id:
                    if "name" in updates and str(updates["name"]).strip():
                        p["name"] = str(updates["name"]).strip()
                    if "cycle_duration" in updates:
                        try:
                            val = int(updates["cycle_duration"])
                            if 15 <= val <= 60:
                                p["cycle_duration"] = val
                        except Exception:
                            pass
                    if "period_duration" in updates:
                        try:
                            val = int(updates["period_duration"])
                            if 1 <= val <= 15:
                                p["period_duration"] = val
                        except Exception:
                            pass
                    if "start_date" in updates and updates["start_date"]:
                        try:
                            datetime.date.fromisoformat(str(updates["start_date"]).strip())
                            p["start_date"] = str(updates["start_date"]).strip()
                        except Exception:
                            pass
                    if "notes" in updates:
                        p["notes"] = str(updates["notes"] or "").strip()

                    self._save_data()
                    return {"success": True, "partner": self.enrich_partner(p)}
            return {"success": False, "error": "Partnerin nicht gefunden"}

    def delete_partner(self, partner_id):
        with self.lock:
            orig_len = len(self.partners)
            self.partners = [p for p in self.partners if p.get("id") != partner_id]
            if len(self.partners) < orig_len:
                self._save_data()
                return {"success": True}
            return {"success": False, "error": "Partnerin nicht gefunden"}

    def start_new_cycle(self, partner_id, start_date=None):
        if not start_date:
            date_str = datetime.date.today().isoformat()
        else:
            date_str = str(start_date).strip()
        return self.update_partner(partner_id, {"start_date": date_str})


cycle_service = CycleService()
