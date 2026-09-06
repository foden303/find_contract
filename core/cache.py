"""SQLite-backed cache for HTTP responses and search results.

Re-running the same query is the common case while tuning, and every avoided
round trip is worth more than any parsing optimisation. Safe to share across
threads; writes are serialised behind a lock.
"""

import json
import sqlite3
import threading
import time

DEFAULT_MAX_CACHE_BYTES = 512 * 1024 * 1024
PRUNE_INTERVAL = 25
PRUNE_TARGET_RATIO = 0.9

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    url       TEXT PRIMARY KEY,
    final_url TEXT,
    status    INTEGER,
    body      TEXT,
    ts        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_http_cache_ts ON http_cache (ts);
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
    def __init__(
        self, path: str, ttl: int, enabled: bool = True,
        max_bytes: int = DEFAULT_MAX_CACHE_BYTES,
    ):
        self.path = path
        self.ttl = ttl
        self.enabled = enabled
        self.max_bytes = max_bytes
        self._writes_since_prune = 0
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

    def _used_database_bytes_locked(self) -> int:
        page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
        free_pages = self._conn.execute("PRAGMA freelist_count").fetchone()[0]
        return (page_count - free_pages) * page_size

    def _prune_locked(self) -> None:
        self._writes_since_prune = 0
        if self.max_bytes <= 0 or self._used_database_bytes_locked() <= self.max_bytes:
            return
        target = int(self.max_bytes * PRUNE_TARGET_RATIO)
        to_remove: list[tuple[str]] = []
        freed = 0
        required = self._used_database_bytes_locked() - target
        rows = self._conn.execute(
            "SELECT url, COALESCE(length(CAST(body AS BLOB)), 0)"
            " + length(url) + COALESCE(length(final_url), 0) AS bytes"
            " FROM http_cache"
            " ORDER BY CASE WHEN status = 0 OR status >= 400 OR body IS NULL"
            " THEN 0 ELSE 1 END, ts"
        )
        for url, size in rows:
            to_remove.append((url,))
            freed += size
            if freed >= required:
                break
        if to_remove:
            self._conn.executemany("DELETE FROM http_cache WHERE url = ?", to_remove)

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
            self._writes_since_prune += 1
            if self._writes_since_prune >= PRUNE_INTERVAL:
                self._prune_locked()
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
