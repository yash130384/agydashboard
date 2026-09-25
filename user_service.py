#!/usr/bin/env python3
"""
LCARS Central User & Session Service for agydashboard.
Manages user accounts, credentials with PBKDF2-HMAC-SHA256, sessions, permissions,
and security audit logs in SQLite (users.db).
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")
DEFAULT_SESSION_DURATION = 86400  # 24 Stunden
REMEMBER_SESSION_DURATION = 2592000  # 30 Tage
PBKDF2_ITERATIONS = 600000

# Registrierte geschützte Webdienste
KNOWN_SERVICES = {
    "pulsecast": {"name": "PulseCast", "subdomain": "cast", "port": 3000, "desc": "Media & Download Hub"},
    "telemetryvault": {"name": "TelemetryVault", "subdomain": "tele", "port": 8000, "desc": "ACC Telemetriedienst"},
    "matter": {"name": "Matter Server", "subdomain": "mat", "port": 5580, "desc": "Smart Home Matter Gateway"},
    "headroom": {"name": "Headroom AI", "subdomain": "head", "port": 8787, "desc": "AI Context Compression Engine"},
    "cups": {"name": "CUPS Druckerdienst", "subdomain": "port", "port": 631, "desc": "Netzwerkdrucker Verwaltung"},
    "9router": {"name": "9Router", "subdomain": "ai", "port": 20128, "desc": "FREE AI Router & Token Saver"},
    "homeassistant": {"name": "Home Assistant", "subdomain": "ha", "port": 8123, "desc": "Open Source Home Automation"},
    "dashboard": {"name": "System Dashboard", "subdomain": "dash", "port": 5000, "desc": "LCARS System Dashboard"},
}


class UserService:
    DEFAULT_SESSION_DURATION = DEFAULT_SESSION_DURATION
    REMEMBER_SESSION_DURATION = REMEMBER_SESSION_DURATION

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self):
        with self._lock:
            with self._get_connection() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                        password_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        hash_algo TEXT NOT NULL DEFAULT 'pbkdf2_sha256',
                        display_name TEXT,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        allowed_services TEXT NOT NULL DEFAULT '[]',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_login_at TIMESTAMP,
                        notes TEXT
                    );

                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at INTEGER NOT NULL,
                        last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        ip_address TEXT,
                        user_agent TEXT
                    );

                    CREATE TABLE IF NOT EXISTS auth_audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        username TEXT,
                        event TEXT NOT NULL,
                        target_service TEXT,
                        ip TEXT,
                        user_agent TEXT,
                        details TEXT
                    );

                    CREATE TABLE IF NOT EXISTS registered_services (
                        key TEXT PRIMARY KEY COLLATE NOCASE,
                        name TEXT NOT NULL,
                        subdomain TEXT,
                        port INTEGER,
                        desc TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
                    CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
                    CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON auth_audit_log(timestamp);
                    """
                )
                for k, v in KNOWN_SERVICES.items():
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO registered_services (key, name, subdomain, port, desc)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (k, v.get("name", k), v.get("subdomain"), v.get("port"), v.get("desc", "")),
                    )
                conn.commit()
        self.ensure_default_admins()

    @staticmethod
    def hash_password(password: str, salt: bytes = None) -> tuple[str, str]:
        if not salt:
            salt = secrets.token_bytes(32)
        pwd_bytes = password.encode("utf-8")
        hash_bytes = hashlib.pbkdf2_hmac("sha256", pwd_bytes, salt, PBKDF2_ITERATIONS)
        return hash_bytes.hex(), salt.hex()

    @staticmethod
    def verify_password(password: str, hash_hex: str, salt_hex: str, algo: str = "pbkdf2_sha256") -> bool:
        try:
            salt = bytes.fromhex(salt_hex)
            expected_hash = bytes.fromhex(hash_hex)
            if algo == "pbkdf2_sha256":
                calc_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
                return hmac.compare_digest(calc_hash, expected_hash)
            return False
        except Exception:
            return False

    def log_audit(self, username: str, event: str, target_service: str = "", ip: str = "", user_agent: str = "", details: str = ""):
        try:
            with self._lock, self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO auth_audit_log (username, event, target_service, ip, user_agent, details)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (username, event, target_service, ip, user_agent, details),
                )
                conn.commit()
        except Exception as e:
            print(f"[WARN] UserService.log_audit Fehler: {e}", file=sys.stderr)

    def create_user(self, username: str, password: str, display_name: str = "", allowed_services: list = None, notes: str = "") -> dict:
        username = (username or "").strip().lower()
        if not username or len(username) < 2:
            return {"success": False, "error": "Benutzername muss mindestens 2 Zeichen lang sein."}
        if not password or len(password) < 6:
            return {"success": False, "error": "Passwort muss mindestens 6 Zeichen lang sein."}

        services = allowed_services if isinstance(allowed_services, list) else []
        pwd_hash, salt = self.hash_password(password)

        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO users (username, password_hash, salt, display_name, allowed_services, notes)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (username, pwd_hash, salt, display_name.strip(), json.dumps(services), notes.strip()),
                    )
                    user_id = cursor.lastrowid
                    conn.commit()
                self.log_audit(username, "USER_CREATED", details=f"User ID: {user_id}, Services: {services}")
                return {"success": True, "user_id": user_id, "username": username}
            except sqlite3.IntegrityError:
                return {"success": False, "error": f"Benutzername '{username}' existiert bereits."}
            except Exception as e:
                return {"success": False, "error": f"Datenbankfehler beim Erstellen: {e}"}

    def update_user(self, user_id: int, display_name: str = None, is_active: bool = None, allowed_services: list = None, notes: str = None) -> dict:
        with self._lock:
            try:
                with self._get_connection() as conn:
                    row = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
                    if not row:
                        return {"success": False, "error": "Benutzer nicht gefunden."}
                    username = row["username"]

                    updates = []
                    params = []
                    if display_name is not None:
                        updates.append("display_name = ?")
                        params.append(display_name.strip())
                    if is_active is not None:
                        updates.append("is_active = ?")
                        params.append(1 if is_active else 0)
                    if allowed_services is not None:
                        updates.append("allowed_services = ?")
                        params.append(json.dumps(allowed_services))
                    if notes is not None:
                        updates.append("notes = ?")
                        params.append(notes.strip())

                    if not updates:
                        return {"success": True, "message": "Keine Änderungen"}

                    params.append(user_id)
                    conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
                    conn.commit()

                self.log_audit(username, "USER_UPDATED", details=f"Updates: {updates}")
                return {"success": True, "message": "Benutzer erfolgreich aktualisiert"}
            except Exception as e:
                return {"success": False, "error": f"Fehler beim Aktualisieren: {e}"}

    def update_password(self, user_id: int, new_password: str) -> dict:
        if not new_password or len(new_password) < 6:
            return {"success": False, "error": "Neues Passwort muss mindestens 6 Zeichen lang sein."}

        pwd_hash, salt = self.hash_password(new_password)
        with self._lock:
            try:
                with self._get_connection() as conn:
                    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
                    if not row:
                        return {"success": False, "error": "Benutzer nicht gefunden."}
                    username = row["username"]

                    conn.execute(
                        "UPDATE users SET password_hash = ?, salt = ?, hash_algo = 'pbkdf2_sha256' WHERE id = ?",
                        (pwd_hash, salt, user_id),
                    )
                    # Invalidiere bestehende Sessions nach Passwortänderung
                    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                    conn.commit()

                self.log_audit(username, "PASSWORD_CHANGED")
                return {"success": True, "message": "Passwort erfolgreich aktualisiert"}
            except Exception as e:
                return {"success": False, "error": f"Fehler beim Ändern des Passworts: {e}"}

    def delete_user(self, user_id: int) -> dict:
        with self._lock:
            try:
                with self._get_connection() as conn:
                    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
                    if not row:
                        return {"success": False, "error": "Benutzer nicht gefunden."}
                    username = row["username"]
                    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
                    conn.commit()

                self.log_audit(username, "USER_DELETED", details=f"User ID: {user_id}")
                return {"success": True, "message": f"Benutzer '{username}' gelöscht"}
            except Exception as e:
                return {"success": False, "error": f"Fehler beim Löschen: {e}"}

    def get_user(self, user_id: int) -> dict | None:
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return None
            return self._row_to_user_dict(row)

    def get_user_by_username(self, username: str) -> dict | None:
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", ((username or "").strip().lower(),)).fetchone()
            if not row:
                return None
            return self._row_to_user_dict(row)

    def list_users(self) -> list[dict]:
        with self._lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, username, display_name, is_active, allowed_services, created_at, last_login_at, notes FROM users ORDER BY id ASC"
            ).fetchall()
            res = []
            for r in rows:
                try:
                    services = json.loads(r["allowed_services"] or "[]")
                except Exception:
                    services = []
                res.append({
                    "id": r["id"],
                    "username": r["username"],
                    "display_name": r["display_name"] or "",
                    "is_active": bool(r["is_active"]),
                    "allowed_services": services,
                    "created_at": r["created_at"],
                    "last_login_at": r["last_login_at"],
                    "notes": r["notes"] or "",
                })
            return res

    def _row_to_user_dict(self, r) -> dict:
        try:
            services = json.loads(r["allowed_services"] or "[]")
        except Exception:
            services = []
        return {
            "id": r["id"],
            "username": r["username"],
            "password_hash": r["password_hash"],
            "salt": r["salt"],
            "hash_algo": r["hash_algo"],
            "display_name": r["display_name"] or "",
            "is_active": bool(r["is_active"]),
            "allowed_services": services,
            "created_at": r["created_at"],
            "last_login_at": r["last_login_at"],
            "notes": r["notes"] or "",
        }

    def authenticate(self, username: str, password: str, ip: str = "", user_agent: str = "") -> dict:
        user = self.get_user_by_username(username)
        if not user:
            self.log_audit(username, "LOGIN_FAILED", ip=ip, user_agent=user_agent, details="Unbekannter Benutzername")
            return {"success": False, "error": "Ungültiger Benutzername oder Passwort"}

        if not user["is_active"]:
            self.log_audit(username, "LOGIN_FAILED", ip=ip, user_agent=user_agent, details="Konto deaktiviert")
            return {"success": False, "error": "Benutzerkonto ist deaktiviert. Wenden Sie sich an die Administration."}

        valid = self.verify_password(password, user["password_hash"], user["salt"], user["hash_algo"])
        if not valid:
            self.log_audit(username, "LOGIN_FAILED", ip=ip, user_agent=user_agent, details="Falsches Passwort")
            return {"success": False, "error": "Ungültiger Benutzername oder Passwort"}

        # Login erfolgreich
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._get_connection() as conn:
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_str, user["id"]))
            conn.commit()

        self.log_audit(username, "LOGIN_SUCCESS", ip=ip, user_agent=user_agent)
        return {
            "success": True,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "display_name": user["display_name"],
                "allowed_services": user["allowed_services"],
            },
        }

    def create_session(self, user_id: int, duration_seconds: int = DEFAULT_SESSION_DURATION, ip: str = "", user_agent: str = "") -> str:
        session_id = secrets.token_urlsafe(32)
        now_ts = int(time.time())
        expires_at = now_ts + duration_seconds

        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, user_id, expires_at, ip_address, user_agent)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, user_id, expires_at, ip, user_agent),
            )
            conn.commit()

        return session_id

    def validate_session(self, session_id: str) -> dict | None:
        if not session_id or len(session_id) < 16:
            return None

        now_ts = int(time.time())
        with self._lock, self._get_connection() as conn:
            query = """
                SELECT s.session_id, s.expires_at, u.id, u.username, u.display_name,
                       u.is_active, u.allowed_services
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.session_id = ?
            """
            row = conn.execute(query, (session_id,)).fetchone()
            if not row:
                return None

            if row["expires_at"] < now_ts:
                # Abgelaufen: entfernen
                conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                conn.commit()
                return None

            if not row["is_active"]:
                return None

            try:
                services = json.loads(row["allowed_services"] or "[]")
            except Exception:
                services = []

            return {
                "session_id": row["session_id"],
                "user_id": row["id"],
                "username": row["username"],
                "display_name": row["display_name"] or "",
                "allowed_services": services,
                "expires_at": row["expires_at"],
            }

    def delete_session(self, session_id: str) -> bool:
        if not session_id:
            return False
        with self._lock, self._get_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return True

    def cleanup_expired_sessions(self) -> int:
        now_ts = int(time.time())
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_ts,))
            conn.commit()
            return cur.rowcount

    def ensure_default_admins(self):
        """Stellt sicher, dass 'admin' und 'cb' existieren und als Super Admin voll berechtigt sind."""
        for uname in ("admin", "cb"):
            u = self.get_user_by_username(uname)
            if not u:
                self.create_user(
                    username=uname,
                    password="09010901",
                    display_name="cb (Super Admin)" if uname == "cb" else "Master Administrator",
                    allowed_services=["*"],
                    notes="Super Admin" if uname == "cb" else "Master Admin",
                )
            else:
                svcs = u.get("allowed_services", [])
                if "*" not in svcs and "all" not in svcs:
                    svcs.append("*")
                    self.update_user(u["id"], allowed_services=svcs)

    def register_service(self, key: str, name: str | None = None, subdomain: str | None = None, port: int | None = None, desc: str = "") -> bool:
        """Registriert einen Dienst dynamisch in users.db."""
        if not key:
            return False
        key = key.strip().lower()
        name = (name or key).strip()
        with self._lock:
            try:
                with self._get_connection() as conn:
                    conn.execute(
                        """
                        INSERT INTO registered_services (key, name, subdomain, port, desc)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            name=excluded.name,
                            subdomain=COALESCE(excluded.subdomain, registered_services.subdomain),
                            port=COALESCE(excluded.port, registered_services.port),
                            desc=CASE WHEN excluded.desc != '' THEN excluded.desc ELSE registered_services.desc END
                        """,
                        (key, name, subdomain, port, desc),
                    )
                    conn.commit()
                return True
            except Exception as e:
                print(f"[WARN] UserService.register_service Fehler: {e}", file=sys.stderr)
                return False

    def list_services(self) -> list[dict]:
        """Gibt alle registrierten Dienste sortiert zurück."""
        with self._lock:
            try:
                with self._get_connection() as conn:
                    rows = conn.execute(
                        "SELECT key, name, subdomain, port, desc FROM registered_services ORDER BY name ASC"
                    ).fetchall()
                    return [
                        {
                            "key": r["key"],
                            "name": r["name"],
                            "subdomain": r["subdomain"] or "",
                            "port": r["port"],
                            "desc": r["desc"] or "",
                        }
                        for r in rows
                    ]
            except Exception as e:
                print(f"[WARN] UserService.list_services Fehler: {e}", file=sys.stderr)
                return [
                    {"key": k, "name": v["name"], "subdomain": v.get("subdomain", ""), "port": v.get("port"), "desc": v.get("desc", "")}
                    for k, v in KNOWN_SERVICES.items()
                ]

    def get_services(self) -> dict[str, dict]:
        """Gibt registrierte Dienste als Dictionary gemappt auf Service-Key zurück."""
        return {s["key"]: s for s in self.list_services()}

    @staticmethod
    def is_super_admin(user_info: dict | None) -> bool:
        """Prüft, ob der Benutzer Super Admin oder cb ist."""
        if not user_info:
            return False
        uname = (user_info.get("username") or "").strip().lower()
        if uname in ("admin", "cb", "superadmin", "super_admin"):
            return True
        dname = (user_info.get("display_name") or "").lower()
        notes = (user_info.get("notes") or "").lower()
        if "super admin" in dname or "master admin" in dname or "super admin" in notes or "master admin" in notes:
            return True
        return False

    def check_service_permission(self, user_info: dict | None, service_key: str) -> bool:
        """Prüft, ob der Benutzer Zugriff auf den gegebenen Service hat.
        cb und Super Admin sind automatisch für alle Bereiche berechtigt.
        """
        if not user_info:
            return False
        if self.is_super_admin(user_info):
            return True
        services = user_info.get("allowed_services", [])
        if "*" in services or "all" in services:
            return True
        return service_key in services

    def get_audit_log(self, limit: int = 50) -> list[dict]:
        with self._lock, self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, username, event, target_service, ip, details
                FROM auth_audit_log
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "username": r["username"] or "—",
                    "event": r["event"],
                    "target_service": r["target_service"] or "",
                    "ip": r["ip"] or "",
                    "details": r["details"] or "",
                }
                for r in rows
            ]


user_service = UserService()
