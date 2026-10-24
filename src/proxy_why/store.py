"""Local SQLite store: sources, decision cards, file links and reviewed answers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,            -- commit | pr | doc | session
    ref TEXT NOT NULL,             -- sha, PR number, doc path#section, session id
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE(kind, ref)
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    decision TEXT NOT NULL,
    why TEXT NOT NULL DEFAULT '',
    alternatives TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '[]',
    files TEXT NOT NULL DEFAULT '[]',
    date TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    method TEXT NOT NULL DEFAULT 'rules',
    UNIQUE(source_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
    title, decision, why, alternatives, files,
    content='cards', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS cards_ai AFTER INSERT ON cards BEGIN
    INSERT INTO cards_fts(rowid, title, decision, why, alternatives, files)
    VALUES (new.id, new.title, new.decision, new.why, new.alternatives, new.files);
END;
CREATE TRIGGER IF NOT EXISTS cards_ad AFTER DELETE ON cards BEGIN
    INSERT INTO cards_fts(cards_fts, rowid, title, decision, why, alternatives, files)
    VALUES ('delete', old.id, old.title, old.decision, old.why, old.alternatives, old.files);
END;
CREATE TRIGGER IF NOT EXISTS cards_au AFTER UPDATE ON cards BEGIN
    INSERT INTO cards_fts(cards_fts, rowid, title, decision, why, alternatives, files)
    VALUES ('delete', old.id, old.title, old.decision, old.why, old.alternatives, old.files);
    INSERT INTO cards_fts(rowid, title, decision, why, alternatives, files)
    VALUES (new.id, new.title, new.decision, new.why, new.alternatives, new.files);
END;
CREATE TABLE IF NOT EXISTS file_links (
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    PRIMARY KEY (card_id, path)
);
CREATE INDEX IF NOT EXISTS file_links_path ON file_links(path);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,            -- why | ask
    target TEXT NOT NULL,          -- normalised question or path:line
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    citations TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    final_answer TEXT,
    asked_by TEXT NOT NULL DEFAULT 'cli',
    created TEXT NOT NULL,
    reviewed TEXT
);
CREATE INDEX IF NOT EXISTS answers_target ON answers(kind, target, status);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Source:
    kind: str
    ref: str
    title: str
    body: str = ""
    author: str = ""
    date: str = ""
    url: str = ""
    files: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    id: int | None = None


@dataclass
class Card:
    source_id: int
    title: str
    decision: str
    why: str = ""
    alternatives: str = ""
    evidence: list[dict] = field(default_factory=list)  # [{"label": ..., "url": ...}]
    files: list[str] = field(default_factory=list)
    date: str = ""
    author: str = ""
    confidence: float = 0.5
    method: str = "rules"
    id: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "decision": self.decision,
            "why": self.why,
            "alternatives": self.alternatives,
            "evidence": self.evidence,
            "files": self.files,
            "date": self.date,
            "author": self.author,
            "confidence": round(self.confidence, 2),
            "method": self.method,
        }


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # meta -----------------------------------------------------------------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.db.commit()

    # sources --------------------------------------------------------------
    def upsert_source(self, s: Source) -> int:
        cur = self.db.execute(
            """INSERT INTO sources(kind, ref, title, body, author, date, url, files, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(kind, ref) DO UPDATE SET title = excluded.title, body = excluded.body,
                 author = excluded.author, date = excluded.date, url = excluded.url,
                 files = excluded.files, meta = excluded.meta
               RETURNING id""",
            (s.kind, s.ref, s.title, s.body, s.author, s.date, s.url,
             json.dumps(s.files), json.dumps(s.meta)),
        )
        s.id = cur.fetchone()["id"]
        return s.id

    def get_source(self, kind: str, ref: str) -> Source | None:
        row = self.db.execute("SELECT * FROM sources WHERE kind = ? AND ref = ?", (kind, ref)).fetchone()
        return _row_to_source(row) if row else None

    def sources(self, kind: str | None = None) -> list[Source]:
        if kind:
            rows = self.db.execute("SELECT * FROM sources WHERE kind = ? ORDER BY date DESC", (kind,))
        else:
            rows = self.db.execute("SELECT * FROM sources ORDER BY date DESC")
        return [_row_to_source(r) for r in rows]

    # cards ----------------------------------------------------------------
    def upsert_card(self, c: Card) -> int:
        cur = self.db.execute(
            """INSERT INTO cards(source_id, title, decision, why, alternatives, evidence, files,
                                 date, author, confidence, method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_id) DO UPDATE SET title = excluded.title, decision = excluded.decision,
                 why = excluded.why, alternatives = excluded.alternatives, evidence = excluded.evidence,
                 files = excluded.files, date = excluded.date, author = excluded.author,
                 confidence = excluded.confidence, method = excluded.method
               RETURNING id""",
            (c.source_id, c.title, c.decision, c.why, c.alternatives, json.dumps(c.evidence),
             json.dumps(c.files), c.date, c.author, c.confidence, c.method),
        )
        c.id = cur.fetchone()["id"]
        self.db.execute("DELETE FROM file_links WHERE card_id = ?", (c.id,))
        self.db.executemany(
            "INSERT OR IGNORE INTO file_links(card_id, path) VALUES (?, ?)",
            [(c.id, f) for f in c.files],
        )
        return c.id

    def card(self, card_id: int) -> Card | None:
        row = self.db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return _row_to_card(row) if row else None

    def cards_for_source(self, source_id: int) -> list[Card]:
        rows = self.db.execute("SELECT * FROM cards WHERE source_id = ?", (source_id,))
        return [_row_to_card(r) for r in rows]

    def cards_for_path(self, path: str, limit: int = 20) -> list[Card]:
        rows = self.db.execute(
            """SELECT cards.* FROM cards JOIN file_links ON file_links.card_id = cards.id
               WHERE file_links.path = ? ORDER BY cards.date DESC LIMIT ?""",
            (path, limit),
        )
        return [_row_to_card(r) for r in rows]

    def search_cards(self, fts_query: str, limit: int = 10) -> list[tuple[Card, float]]:
        if not fts_query:
            return []
        rows = self.db.execute(
            """SELECT cards.*, bm25(cards_fts, 4.0, 3.0, 3.0, 1.5, 1.0) AS score
               FROM cards_fts JOIN cards ON cards.id = cards_fts.rowid
               WHERE cards_fts MATCH ? ORDER BY score LIMIT ?""",
            (fts_query, limit),
        )
        # bm25() is lower-is-better and negative; flip it so higher is better.
        return [(_row_to_card(r), -r["score"]) for r in rows]

    def count(self, table: str) -> int:
        assert table in {"sources", "cards", "answers", "file_links"}
        return self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # answers --------------------------------------------------------------
    def add_answer(self, kind: str, target: str, question: str, answer: str,
                   citations: list[dict], asked_by: str = "cli") -> int:
        cur = self.db.execute(
            """INSERT INTO answers(kind, target, question, answer, citations, asked_by, created)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (kind, target, question, answer, json.dumps(citations), asked_by, now()),
        )
        self.db.commit()
        return cur.lastrowid

    def approved_answer(self, kind: str, target: str) -> sqlite3.Row | None:
        return self.db.execute(
            """SELECT * FROM answers WHERE kind = ? AND target = ? AND status = 'approved'
               ORDER BY reviewed DESC LIMIT 1""",
            (kind, target),
        ).fetchone()

    def answers(self, status: str | None = "pending") -> list[sqlite3.Row]:
        if status:
            return list(self.db.execute(
                "SELECT * FROM answers WHERE status = ? ORDER BY created DESC, id DESC", (status,)))
        return list(self.db.execute("SELECT * FROM answers ORDER BY created DESC, id DESC"))

    def answer(self, answer_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM answers WHERE id = ?", (answer_id,)).fetchone()

    def review(self, answer_id: int, status: str, final_answer: str | None = None) -> bool:
        assert status in {"approved", "rejected"}
        row = self.answer(answer_id)
        if row is None:
            return False
        text = final_answer if final_answer is not None else row["answer"]
        self.db.execute(
            "UPDATE answers SET status = ?, final_answer = ?, reviewed = ? WHERE id = ?",
            (status, text if status == "approved" else None, now(), answer_id),
        )
        self.db.commit()
        return True

    def commit(self) -> None:
        self.db.commit()


def _row_to_source(row: sqlite3.Row) -> Source:
    return Source(
        id=row["id"], kind=row["kind"], ref=row["ref"], title=row["title"], body=row["body"],
        author=row["author"], date=row["date"], url=row["url"],
        files=json.loads(row["files"]), meta=json.loads(row["meta"]),
    )


def _row_to_card(row: sqlite3.Row) -> Card:
    return Card(
        id=row["id"], source_id=row["source_id"], title=row["title"], decision=row["decision"],
        why=row["why"], alternatives=row["alternatives"], evidence=json.loads(row["evidence"]),
        files=json.loads(row["files"]), date=row["date"], author=row["author"],
        confidence=row["confidence"], method=row["method"],
    )
