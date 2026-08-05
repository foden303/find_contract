"""Run history: every scan is kept so results can be revisited and compared."""

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict

from core.models import Result

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id       TEXT PRIMARY KEY,
    created  REAL NOT NULL,
    label    TEXT,
    source   TEXT,
    status   TEXT NOT NULL,
    total    INTEGER NOT NULL DEFAULT 0,
    done     INTEGER NOT NULL DEFAULT 0,
    elapsed  REAL NOT NULL DEFAULT 0,
    settings TEXT,
    error    TEXT
);
CREATE TABLE IF NOT EXISTS run_results (
    run_id  TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs (created DESC);

-- One row per company, keyed by its normalised name. Re-running a file, or
-- running a different file containing the same importer, becomes a SELECT
-- instead of a fresh round of searching and scraping.
CREATE TABLE IF NOT EXISTS companies (
    key        TEXT PRIMARY KEY,
    name       TEXT,
    country    TEXT,
    website    TEXT,
    confidence REAL,
    payload    TEXT NOT NULL,
    updated    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_updated ON companies (updated DESC);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def create_run(self, label: str, source: str, total: int, settings: dict) -> str:
        run_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (id, created, label, source, status, total, done, settings)"
                " VALUES (?, ?, ?, ?, 'running', ?, 0, ?)",
                (run_id, time.time(), label, source, total,
                 json.dumps(settings, ensure_ascii=False)),
            )
            self._conn.commit()
        return run_id

    def add_result(self, run_id: str, seq: int, result: Result) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO run_results (run_id, seq, payload) VALUES (?, ?, ?)",
                (run_id, seq, json.dumps(asdict(result), ensure_ascii=False)),
            )
            self._conn.execute(
                "UPDATE runs SET done = done + 1 WHERE id = ?", (run_id,)
            )
            self._conn.commit()

    def finish_run(self, run_id: str, status: str, elapsed: float, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status = ?, elapsed = ?, error = ? WHERE id = ?",
                (status, elapsed, error, run_id),
            )
            self._conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created, label, source, status, total, done, elapsed"
                " FROM runs ORDER BY created DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_results(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM run_results WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # --- Company lookup table --------------------------------------------

    def get_company(self, key: str, ttl: float) -> dict | None:
        """Return a stored result for this normalised company name, if fresh."""
        if not key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, updated FROM companies WHERE key = ?", (key,)
            ).fetchone()
        if not row or (time.time() - row["updated"]) > ttl:
            return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

    def save_company(self, key: str, result: Result) -> None:
        """Remember a result. Only worth storing when we actually found a site."""
        if not key or not result.website:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO companies"
                " (key, name, country, website, confidence, payload, updated)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, result.company, result.country, result.website,
                 result.confidence, json.dumps(asdict(result), ensure_ascii=False),
                 time.time()),
            )
            self._conn.commit()

    def count_companies(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

    def search_companies(self, term: str, limit: int = 50) -> list[dict]:
        """Free-text lookup over everything learned so far."""
        like = f"%{term.lower()}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, name, country, website, confidence, updated FROM companies"
                " WHERE lower(name) LIKE ? OR lower(website) LIKE ?"
                " ORDER BY confidence DESC LIMIT ?",
                (like, like, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM run_results WHERE run_id = ?", (run_id,))
            self._conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            self._conn.commit()
