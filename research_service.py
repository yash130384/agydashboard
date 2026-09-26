#!/usr/bin/env python3
"""
Research Service for LCARS System Dashboard.
Stores research requests and reports (sources, key takeaways, structured data)
in a SQLite database.
DB default location: ~/.hermes/research/research.db (with fallback to repo dir).
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

DEFAULT_DB_DIR = os.path.expanduser("~/.hermes/research")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "research.db")


class ResearchService:
    def __init__(self, db_path=None):
        if not db_path:
            db_path = os.environ.get("HERMES_RESEARCH_DB") or DEFAULT_DB_PATH
        self.db_path = db_path
        self.lock = threading.RLock()
        self._init_db()

    def _get_connection(self):
        # ponytail: standard sqlite connection with row_factory; upgrade to connection pool if high concurrency
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except Exception as e:
                print(f"[WARN] ResearchService: konnte Verzeichnis {db_dir} nicht erstellen: {e}", file=sys.stderr)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self.lock:
            try:
                conn = self._get_connection()
                with conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS research_reports (
                            id TEXT PRIMARY KEY,
                            title TEXT NOT NULL,
                            status TEXT NOT NULL DEFAULT 'completed',
                            topic TEXT DEFAULT '',
                            tags TEXT DEFAULT '[]',
                            author TEXT DEFAULT 'researcher',
                            summary TEXT DEFAULT '',
                            key_takeaways TEXT DEFAULT '[]',
                            sources TEXT DEFAULT '[]',
                            structured_data TEXT DEFAULT '{}',
                            content TEXT NOT NULL,
                            created_at INTEGER NOT NULL,
                            updated_at INTEGER NOT NULL
                        )
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_status ON research_reports(status)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_research_created_at ON research_reports(created_at)")
                conn.close()
            except Exception as e:
                print(f"[ERROR] ResearchService: Fehler bei DB-Initialisierung ({self.db_path}): {e}", file=sys.stderr)

    def _row_to_dict(self, row):
        if not row:
            return None
        d = dict(row)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = [t.strip() for t in (d.get("tags") or "").split(",") if t.strip()]
        try:
            d["key_takeaways"] = json.loads(d.get("key_takeaways") or "[]")
        except Exception:
            d["key_takeaways"] = []
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except Exception:
            d["sources"] = []
        try:
            d["structured_data"] = json.loads(d.get("structured_data") or "{}")
        except Exception:
            d["structured_data"] = {}
        return d

    def list_reports(self, status=None, tag=None, query=None, limit=100, offset=0):
        with self.lock:
            try:
                conn = self._get_connection()
                clauses = []
                params = []

                if status and status.lower() != "all":
                    clauses.append("LOWER(status) = ?")
                    params.append(status.lower().strip())

                if tag:
                    clauses.append("LOWER(tags) LIKE ?")
                    params.append(f"%{tag.lower().strip()}%")

                if query:
                    q = f"%{query.lower().strip()}%"
                    clauses.append("(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(summary) LIKE ? OR LOWER(topic) LIKE ?)")
                    params.extend([q, q, q, q])

                where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                sql = f"""
                    SELECT id, title, status, topic, tags, author, summary, key_takeaways, sources, structured_data, created_at, updated_at,
                           substr(content, 1, 300) AS snippet
                    FROM research_reports
                    {where_sql}
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                """
                params.extend([int(limit), int(offset)])
                rows = conn.execute(sql, params).fetchall()
                result = [self._row_to_dict(r) for r in rows]
                conn.close()
                return result
            except Exception as e:
                print(f"[WARN] ResearchService.list_reports Fehler: {e}", file=sys.stderr)
                return []

    def get_report(self, report_id):
        with self.lock:
            try:
                conn = self._get_connection()
                row = conn.execute("SELECT * FROM research_reports WHERE id = ?", (str(report_id).strip(),)).fetchone()
                conn.close()
                return self._row_to_dict(row)
            except Exception as e:
                print(f"[WARN] ResearchService.get_report Fehler: {e}", file=sys.stderr)
                return None

    def create_report(self, title, content="", status="completed", topic="", tags=None,
                      author="researcher", summary="", key_takeaways=None, sources=None,
                      structured_data=None, report_id=None):
        title = (title or "").strip()
        if not title:
            raise ValueError("Titel darf nicht leer sein.")
        content = (content or "").strip()
        status = (status or "completed").strip().lower()

        if tags is None:
            tags = []
        elif isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        if key_takeaways is None:
            key_takeaways = []
        elif isinstance(key_takeaways, str):
            key_takeaways = [k.strip() for k in key_takeaways.split("\n") if k.strip()]

        if sources is None:
            sources = []
        elif isinstance(sources, str):
            sources = [s.strip() for s in sources.split("\n") if s.strip()]

        if structured_data is None:
            structured_data = {}

        rep_id = str(report_id).strip() if report_id else f"res_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        now = int(time.time())

        # ponytail: auto-summarize first 200 chars if not provided; upgrade to LLM summarizer if needed
        if not summary and content:
            summary = content[:200].replace("\n", " ").strip()

        with self.lock:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    INSERT INTO research_reports (
                        id, title, status, topic, tags, author, summary, key_takeaways, sources, structured_data, content, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rep_id,
                    title,
                    status,
                    topic,
                    json.dumps(tags),
                    author,
                    summary,
                    json.dumps(key_takeaways),
                    json.dumps(sources),
                    json.dumps(structured_data),
                    content,
                    now,
                    now
                ))
            conn.close()

        return self.get_report(rep_id)

    def update_report(self, report_id, title=None, content=None, status=None, topic=None,
                      tags=None, author=None, summary=None, key_takeaways=None, sources=None,
                      structured_data=None):
        report = self.get_report(report_id)
        if not report:
            return None

        updates = []
        params = []

        if title is not None:
            updates.append("title = ?")
            params.append(str(title).strip())
        if content is not None:
            updates.append("content = ?")
            params.append(str(content).strip())
        if status is not None:
            updates.append("status = ?")
            params.append(str(status).strip().lower())
        if topic is not None:
            updates.append("topic = ?")
            params.append(str(topic).strip())
        if tags is not None:
            updates.append("tags = ?")
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            params.append(json.dumps(tags))
        if author is not None:
            updates.append("author = ?")
            params.append(str(author).strip())
        if summary is not None:
            updates.append("summary = ?")
            params.append(str(summary).strip())
        if key_takeaways is not None:
            updates.append("key_takeaways = ?")
            if isinstance(key_takeaways, str):
                key_takeaways = [k.strip() for k in key_takeaways.split("\n") if k.strip()]
            params.append(json.dumps(key_takeaways))
        if sources is not None:
            updates.append("sources = ?")
            if isinstance(sources, str):
                sources = [s.strip() for s in sources.split("\n") if s.strip()]
            params.append(json.dumps(sources))
        if structured_data is not None:
            updates.append("structured_data = ?")
            params.append(json.dumps(structured_data))

        now = int(time.time())
        updates.append("updated_at = ?")
        params.append(now)

        params.append(str(report_id).strip())

        with self.lock:
            conn = self._get_connection()
            with conn:
                conn.execute(f"UPDATE research_reports SET {', '.join(updates)} WHERE id = ?", params)
            conn.close()

        return self.get_report(report_id)

    def delete_report(self, report_id):
        with self.lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("DELETE FROM research_reports WHERE id = ?", (str(report_id).strip(),))
                affected = cursor.rowcount
            conn.close()
            return affected > 0

    def get_stats(self):
        with self.lock:
            try:
                conn = self._get_connection()
                total = conn.execute("SELECT COUNT(*) FROM research_reports").fetchone()[0]
                rows = conn.execute("SELECT status, COUNT(*) FROM research_reports GROUP BY status").fetchall()
                by_status = {r[0]: r[1] for r in rows}
                conn.close()
                return {
                    "total": total,
                    "by_status": by_status,
                }
            except Exception as e:
                print(f"[WARN] ResearchService.get_stats Fehler: {e}", file=sys.stderr)
                return {"total": 0, "by_status": {}}

    def trigger_research_task(self, title, topic="", tags=None, notes=""):
        """
        Creates a new research assignment in research_reports as 'running' or 'planned'
        AND pushes a corresponding task to Hermes Kanban (assignee='researcher') if Hermes is available.
        """
        tags_list = tags if isinstance(tags, list) else ([t.strip() for t in tags.split(",") if t.strip()] if tags else [])
        report = self.create_report(
            title=title,
            content=f"Rechercheauftrag gestartet: {topic or title}\nNotizen: {notes}",
            status="running",
            topic=topic or title,
            tags=tags_list,
            author="researcher",
            summary=f"Laufende Recherche zu: {title}",
            structured_data={"notes": notes, "initiated_by": "lcars_ui"}
        )
        if not report:
            return None

        # Dispatch task to Hermes kanban board (dev-team or default) with assignee 'researcher'
        kanban_created = False
        hermes_bin = os.environ.get("HERMES_BIN") or "/home/cb/.local/share/mise/installs/pipx-hermes-agent/0.19.0/hermes-agent/bin/hermes"
        if os.path.exists(hermes_bin):
            body_text = f"""Recherche-Auftrag aus LCARS Dashboard:
ID: {report['id']}
Thema: {topic or title}
Tags: {', '.join(tags_list)}
Notizen: {notes}

Bitte führe die Recherche durch und speichere den finalen Bericht direkt via POST /api/research
oder aktualisiere den Datensatz {report['id']} mit Quellen, Key Takeaways und Markdown-Content.
"""
            cmd = [
                hermes_bin, "kanban", "--board", "dev-team", "create",
                f"Recherche: {title}",
                "--assignee", "researcher",
                "--body", body_text
            ]
            try:
                env = os.environ.copy()
                env["HERMES_KANBAN_BOARD"] = "dev-team"
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
                if res.returncode == 0:
                    kanban_created = True
                    out = res.stdout.strip()
                    report["kanban_output"] = out
            except Exception as e:
                print(f"[WARN] ResearchService.trigger_research_task Hermes Kanban dispatch error: {e}", file=sys.stderr)

        report["kanban_dispatched"] = kanban_created
        return report


# Global Singleton Instance
research_service = ResearchService()
