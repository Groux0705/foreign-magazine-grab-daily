"""SQLite layer for the offline reading platform.

Tables:
  articles      — one cleaned article + local assets pointer
  annotations   — highlights / notes with DOM-range anchors
  vocabulary    — collected English words with lemma dedup
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "app.db"

# One write-serializing lock per process. SQLite itself is fine,
# but we do checkins with WAL so readers aren't blocked.
_LOCK = threading.Lock()


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url      TEXT UNIQUE NOT NULL,
    category        TEXT NOT NULL,
    title           TEXT NOT NULL,
    author          TEXT,
    published_at    TEXT,
    fetched_at      TEXT NOT NULL,
    fetch_date      TEXT NOT NULL,
    description     TEXT,
    cover_image     TEXT,
    content_html    TEXT,
    plain_text      TEXT,
    word_count      INTEGER DEFAULT 0,
    is_favorite     INTEGER NOT NULL DEFAULT 0,
    in_library      INTEGER NOT NULL DEFAULT 0,
    is_read         INTEGER NOT NULL DEFAULT 0,
    read_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_fetch_date ON articles(fetch_date);
CREATE INDEX IF NOT EXISTS idx_articles_category   ON articles(category);
CREATE INDEX IF NOT EXISTS idx_articles_favorite   ON articles(is_favorite);
CREATE INDEX IF NOT EXISTS idx_articles_library    ON articles(in_library);

CREATE TABLE IF NOT EXISTS annotations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id    INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK(kind IN ('highlight','note')),
    color         TEXT,
    quote         TEXT NOT NULL,
    prefix        TEXT,
    suffix        TEXT,
    start_xpath   TEXT NOT NULL,
    start_offset  INTEGER NOT NULL,
    end_xpath     TEXT NOT NULL,
    end_offset    INTEGER NOT NULL,
    comment       TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_article ON annotations(article_id);

CREATE TABLE IF NOT EXISTS vocabulary (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    word          TEXT NOT NULL,
    lemma         TEXT NOT NULL UNIQUE,
    context       TEXT,
    article_id    INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    note          TEXT,
    mastery       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    reviewed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_vocab_lemma   ON vocabulary(lemma);
CREATE INDEX IF NOT EXISTS idx_vocab_mastery ON vocabulary(mastery);
"""


def _dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_write_conn() -> Iterator[sqlite3.Connection]:
    """Serialize writers in this process."""
    with _LOCK:
        conn = connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with get_conn() as c:
        c.executescript(SCHEMA)


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ---------- articles ----------

def upsert_article(data: dict) -> int:
    """Insert or update by source_url. Returns article id."""
    cols = [
        "source_url", "category", "title", "author", "published_at",
        "fetched_at", "fetch_date", "description", "cover_image",
        "content_html", "plain_text", "word_count",
    ]
    values = [data.get(c) for c in cols]
    placeholders = ",".join("?" * len(cols))
    update_set = ",".join(f"{c}=excluded.{c}" for c in cols if c != "source_url")
    sql = (
        f"INSERT INTO articles ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(source_url) DO UPDATE SET {update_set}"
    )
    with get_write_conn() as c:
        c.execute(sql, values)
        row = c.execute(
            "SELECT id FROM articles WHERE source_url=?",
            (data["source_url"],),
        ).fetchone()
    return row["id"]


def get_article(article_id: int) -> Optional[dict]:
    with get_conn() as c:
        return c.execute(
            "SELECT * FROM articles WHERE id=?", (article_id,)
        ).fetchone()


def list_articles(
    *,
    date: Optional[str] = None,
    category: Optional[str] = None,
    favorite: Optional[bool] = None,
    in_library: Optional[bool] = None,
    is_read: Optional[bool] = None,
    query: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    where = []
    params: list = []
    if date:
        where.append("fetch_date = ?")
        params.append(date)
    if category:
        where.append("category = ?")
        params.append(category)
    if favorite is not None:
        where.append("is_favorite = ?")
        params.append(1 if favorite else 0)
    if in_library is not None:
        where.append("in_library = ?")
        params.append(1 if in_library else 0)
    if is_read is not None:
        where.append("is_read = ?")
        params.append(1 if is_read else 0)
    if query:
        where.append("(title LIKE ? OR description LIKE ? OR plain_text LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT id, source_url, category, title, author, published_at, "
        "fetched_at, fetch_date, description, cover_image, word_count, "
        "is_favorite, in_library, is_read, read_at "
        f"FROM articles {clause} "
        "ORDER BY fetch_date DESC, id ASC LIMIT ?"
    )
    params.append(limit)
    with get_conn() as c:
        return c.execute(sql, params).fetchall()


def distinct_fetch_dates() -> list[str]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT DISTINCT fetch_date FROM articles ORDER BY fetch_date DESC"
        ).fetchall()
    return [r["fetch_date"] for r in rows]


def set_article_flag(article_id: int, field: str, value: bool) -> dict:
    if field not in ("is_favorite", "in_library", "is_read"):
        raise ValueError(f"invalid field {field}")
    extra = ""
    params: list = [1 if value else 0]
    if field == "is_read":
        extra = ", read_at = ?"
        params.append(now_iso() if value else None)
    params.append(article_id)
    sql = f"UPDATE articles SET {field}=?{extra} WHERE id=?"
    with get_write_conn() as c:
        c.execute(sql, params)
    return get_article(article_id)


# ---------- annotations ----------

def list_annotations(article_id: int) -> list[dict]:
    with get_conn() as c:
        return c.execute(
            "SELECT * FROM annotations WHERE article_id=? ORDER BY id ASC",
            (article_id,),
        ).fetchall()


def create_annotation(data: dict) -> dict:
    now = now_iso()
    cols = [
        "article_id", "kind", "color", "quote", "prefix", "suffix",
        "start_xpath", "start_offset", "end_xpath", "end_offset", "comment",
        "created_at", "updated_at",
    ]
    values = [
        data.get("article_id"),
        data.get("kind", "highlight"),
        data.get("color"),
        data.get("quote", ""),
        data.get("prefix"),
        data.get("suffix"),
        data.get("start_xpath"),
        data.get("start_offset", 0),
        data.get("end_xpath"),
        data.get("end_offset", 0),
        data.get("comment"),
        now,
        now,
    ]
    sql = f"INSERT INTO annotations ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})"
    with get_write_conn() as c:
        cur = c.execute(sql, values)
        new_id = cur.lastrowid
    with get_conn() as c:
        return c.execute("SELECT * FROM annotations WHERE id=?", (new_id,)).fetchone()


def update_annotation(anno_id: int, fields: dict) -> Optional[dict]:
    allowed = {"kind", "color", "comment"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return None
    updates["updated_at"] = now_iso()
    sets = ",".join(f"{k}=?" for k in updates)
    params = list(updates.values()) + [anno_id]
    with get_write_conn() as c:
        c.execute(f"UPDATE annotations SET {sets} WHERE id=?", params)
    with get_conn() as c:
        return c.execute("SELECT * FROM annotations WHERE id=?", (anno_id,)).fetchone()


def delete_annotation(anno_id: int) -> None:
    with get_write_conn() as c:
        c.execute("DELETE FROM annotations WHERE id=?", (anno_id,))


# ---------- vocabulary ----------

def list_vocabulary(
    *, query: Optional[str] = None, mastery: Optional[int] = None, sort: str = "recent"
) -> list[dict]:
    where = []
    params: list = []
    if query:
        where.append("(word LIKE ? OR lemma LIKE ? OR note LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    if mastery is not None:
        where.append("mastery = ?")
        params.append(mastery)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = {
        "recent": "v.created_at DESC",
        "alpha": "v.lemma ASC",
        "mastery": "v.mastery ASC, v.created_at DESC",
    }.get(sort, "v.created_at DESC")
    sql = (
        "SELECT v.*, a.title AS article_title "
        "FROM vocabulary v LEFT JOIN articles a ON a.id = v.article_id "
        f"{clause} ORDER BY {order}"
    )
    with get_conn() as c:
        return c.execute(sql, params).fetchall()


def upsert_vocabulary(word: str, lemma: str, *, context: str = None,
                      article_id: int = None, note: str = None) -> dict:
    now = now_iso()
    with get_write_conn() as c:
        row = c.execute(
            "SELECT id, context FROM vocabulary WHERE lemma=?", (lemma,)
        ).fetchone()
        if row:
            # append new context if different, keep most recent first
            new_context = row["context"] or ""
            if context and context not in new_context:
                new_context = (context + "\n---\n" + new_context).strip("\n-")
            c.execute(
                "UPDATE vocabulary SET context=?, article_id=COALESCE(?, article_id), "
                "note=COALESCE(?, note), reviewed_at=? WHERE id=?",
                (new_context, article_id, note, now, row["id"]),
            )
            vid = row["id"]
        else:
            cur = c.execute(
                "INSERT INTO vocabulary (word, lemma, context, article_id, note, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (word, lemma, context, article_id, note, now),
            )
            vid = cur.lastrowid
    with get_conn() as c:
        return c.execute(
            "SELECT v.*, a.title AS article_title FROM vocabulary v "
            "LEFT JOIN articles a ON a.id = v.article_id WHERE v.id=?",
            (vid,),
        ).fetchone()


def update_vocabulary(vid: int, fields: dict) -> Optional[dict]:
    allowed = {"note", "mastery"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return None
    updates["reviewed_at"] = now_iso()
    sets = ",".join(f"{k}=?" for k in updates)
    params = list(updates.values()) + [vid]
    with get_write_conn() as c:
        c.execute(f"UPDATE vocabulary SET {sets} WHERE id=?", params)
    with get_conn() as c:
        return c.execute(
            "SELECT v.*, a.title AS article_title FROM vocabulary v "
            "LEFT JOIN articles a ON a.id = v.article_id WHERE v.id=?",
            (vid,),
        ).fetchone()


def delete_vocabulary(vid: int) -> None:
    with get_write_conn() as c:
        c.execute("DELETE FROM vocabulary WHERE id=?", (vid,))


def iter_vocabulary() -> Iterable[dict]:
    with get_conn() as c:
        return c.execute(
            "SELECT v.*, a.title AS article_title FROM vocabulary v "
            "LEFT JOIN articles a ON a.id = v.article_id "
            "ORDER BY v.lemma ASC"
        ).fetchall()
