#!/usr/bin/env python3
"""
Knowledge Base Service for LCARS System Dashboard.
Stores articles, QA test reports, Architecture Decision Records (ADRs),
and code review findings in a SQLite database.
DB default location: ~/.hermes/knowledge/knowledge.db (with fallback to repo dir).
"""

import json
import os
import sqlite3
import sys
import threading
import time
import uuid

DEFAULT_DB_DIR = os.path.expanduser("~/.hermes/knowledge")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "knowledge.db")


class KnowledgeService:
    def __init__(self, db_path=None):
        if not db_path:
            db_path = os.environ.get("HERMES_KNOWLEDGE_DB") or DEFAULT_DB_PATH
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
                print(f"[WARN] KnowledgeService: konnte Verzeichnis {db_dir} nicht erstellen: {e}", file=sys.stderr)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self.lock:
            try:
                conn = self._get_connection()
                with conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS articles (
                            id TEXT PRIMARY KEY,
                            title TEXT NOT NULL,
                            category TEXT NOT NULL,
                            tags TEXT DEFAULT '[]',
                            author TEXT DEFAULT 'system',
                            summary TEXT DEFAULT '',
                            content TEXT NOT NULL,
                            metadata TEXT DEFAULT '{}',
                            created_at INTEGER NOT NULL,
                            updated_at INTEGER NOT NULL
                        )
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_created_at ON articles(created_at)")
                conn.close()
            except Exception as e:
                print(f"[ERROR] KnowledgeService: Fehler bei DB-Initialisierung ({self.db_path}): {e}", file=sys.stderr)

    def _row_to_dict(self, row):
        if not row:
            return None
        d = dict(row)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = [t.strip() for t in (d.get("tags") or "").split(",") if t.strip()]
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except Exception:
            d["metadata"] = {}
        return d

    def list_articles(self, category=None, tag=None, query=None, limit=100, offset=0):
        with self.lock:
            try:
                conn = self._get_connection()
                clauses = []
                params = []

                if category and category.lower() != "all":
                    clauses.append("LOWER(category) = ?")
                    params.append(category.lower().strip())

                if tag:
                    clauses.append("LOWER(tags) LIKE ?")
                    params.append(f"%{tag.lower().strip()}%")

                if query:
                    q = f"%{query.lower().strip()}%"
                    clauses.append("(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(summary) LIKE ?)")
                    params.extend([q, q, q])

                where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                sql = f"""
                    SELECT id, title, category, tags, author, summary, metadata, created_at, updated_at,
                           substr(content, 1, 300) AS snippet
                    FROM articles
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
                print(f"[WARN] KnowledgeService.list_articles Fehler: {e}", file=sys.stderr)
                return []

    def get_article(self, article_id):
        with self.lock:
            try:
                conn = self._get_connection()
                row = conn.execute("SELECT * FROM articles WHERE id = ?", (str(article_id).strip(),)).fetchone()
                conn.close()
                return self._row_to_dict(row)
            except Exception as e:
                print(f"[WARN] KnowledgeService.get_article Fehler: {e}", file=sys.stderr)
                return None

    def create_article(self, title, content, category="allgemein", tags=None, author="bot", summary="", metadata=None, article_id=None):
        title = (title or "").strip()
        if not title:
            raise ValueError("Titel darf nicht leer sein.")
        content = (content or "").strip()
        category = (category or "allgemein").strip().lower()

        if tags is None:
            tags = []
        elif isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        if metadata is None:
            metadata = {}

        art_id = str(article_id).strip() if article_id else f"kb_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        now = int(time.time())

        # ponytail: auto-summarize first 200 chars if not provided; upgrade to LLM summarizer if needed
        if not summary and content:
            summary = content[:200].replace("\n", " ").strip()

        with self.lock:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    INSERT INTO articles (id, title, category, tags, author, summary, content, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    art_id,
                    title,
                    category,
                    json.dumps(tags),
                    author or "bot",
                    summary,
                    content,
                    json.dumps(metadata),
                    now,
                    now,
                ))
            conn.close()
            return self.get_article(art_id)

    def update_article(self, article_id, title=None, content=None, category=None, tags=None, author=None, summary=None, metadata=None):
        existing = self.get_article(article_id)
        if not existing:
            return None

        now = int(time.time())
        new_title = existing["title"] if title is None else str(title).strip()
        new_content = existing["content"] if content is None else str(content).strip()
        new_category = existing["category"] if category is None else str(category).strip().lower()
        new_author = existing["author"] if author is None else str(author).strip()
        new_summary = existing["summary"] if summary is None else str(summary).strip()

        if tags is None:
            new_tags = existing["tags"]
        elif isinstance(tags, str):
            new_tags = [t.strip() for t in tags.split(",") if t.strip()]
        else:
            new_tags = list(tags)

        if metadata is None:
            new_meta = existing["metadata"]
        else:
            new_meta = metadata

        with self.lock:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    UPDATE articles
                    SET title = ?, content = ?, category = ?, tags = ?, author = ?, summary = ?, metadata = ?, updated_at = ?
                    WHERE id = ?
                """, (
                    new_title,
                    new_content,
                    new_category,
                    json.dumps(new_tags),
                    new_author,
                    new_summary,
                    json.dumps(new_meta),
                    now,
                    str(article_id).strip(),
                ))
            conn.close()
            return self.get_article(article_id)

    def delete_article(self, article_id):
        with self.lock:
            try:
                conn = self._get_connection()
                with conn:
                    cur = conn.execute("DELETE FROM articles WHERE id = ?", (str(article_id).strip(),))
                    deleted = cur.rowcount > 0
                conn.close()
                return deleted
            except Exception as e:
                print(f"[WARN] KnowledgeService.delete_article Fehler: {e}", file=sys.stderr)
                return False

    def get_stats(self):
        with self.lock:
            try:
                conn = self._get_connection()
                total = conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
                by_cat = {}
                for r in conn.execute("SELECT category, COUNT(*) AS n FROM articles GROUP BY category"):
                    by_cat[r["category"]] = int(r["n"])
                conn.close()
                return {"total": int(total), "by_category": by_cat}
            except Exception as e:
                print(f"[WARN] KnowledgeService.get_stats Fehler: {e}", file=sys.stderr)
                return {"total": 0, "by_category": {}}


# Global singleton instance
knowledge_service = KnowledgeService()
