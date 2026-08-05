"""SQLite-backed cache for HTTP responses and search results.

Re-running the same query is the common case while tuning, and every avoided
round trip is worth more than any parsing optimisation. Safe to share across
threads; writes are serialised behind a lock.
"""

import json
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    url       TEXT PRIMARY KEY,
    final_url TEXT,
    status    INTEGER,
    body      TEXT,
    ts        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS search_cache (
    key     TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    ts      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mx_cache (
    domain  TEXT PRIMARY KEY,
    has_mx  INTEGER NOT NULL,
    ts      REAL NOT NULL
);
"""


class Cache:
    def __init__(self, path: str, ttl: int, enabled: bool = True):
        self.path = path
        self.ttl = ttl
        self.enabled = enabled
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        if enabled:
            self._connect()

    def _connect(self) -> None:
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _fresh(self, ts: float) -> bool:
        return (time.time() - ts) < self.ttl

    # --- HTTP ------------------------------------------------------------

    def get_http(self, url: str) -> tuple[str, int, str | None] | None:
        if not self.enabled or not self._conn:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT final_url, status, body, ts FROM http_cache WHERE url = ?", (url,)
            ).fetchone()
        if not row or not self._fresh(row[3]):
            return None
        return row[0], row[1], row[2]

    def set_http(self, url: str, final_url: str, status: int, body: str | None) -> None:
        if not self.enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO http_cache (url, final_url, status, body, ts)"
                " VALUES (?, ?, ?, ?, ?)",
                (url, final_url, status, body, time.time()),
            )
            self._conn.commit()

    # --- Search ----------------------------------------------------------

    def get_search(self, key: str):
        if not self.enabled or not self._conn:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, ts FROM search_cache WHERE key = ?", (key,)
            ).fetchone()
        if not row or not self._fresh(row[1]):
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def set_search(self, key: str, payload) -> None:
        if not self.enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO search_cache (key, payload, ts) VALUES (?, ?, ?)",
                (key, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            self._conn.commit()

    # --- MX lookups ------------------------------------------------------

    def get_mx(self, domain: str) -> bool | None:
        if not self.enabled or not self._conn:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT has_mx, ts FROM mx_cache WHERE domain = ?", (domain,)
            ).fetchone()
        if not row or not self._fresh(row[1]):
            return None
        return bool(row[0])

    def set_mx(self, domain: str, has_mx: bool) -> None:
        if not self.enabled or not self._conn:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO mx_cache (domain, has_mx, ts) VALUES (?, ?, ?)",
                (domain, int(has_mx), time.time()),
            )
            self._conn.commit()

    def close(self) -> None:
        if self._conn:
            with self._lock:
                self._conn.close()
            self._conn = None
