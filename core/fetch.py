"""Async HTTP layer: one shared client, per-host politeness, cache in front."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass

import httpx

from core.cache import Cache
from core.config import Config, USER_AGENT
from core.utils import host_of

MAX_BODY_BYTES = 2_000_000  # a contact page is never bigger than this

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    html: str | None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.html)


class Fetcher:
    """Shared across a whole run so connections and the cache are reused."""

    def __init__(self, config: Config, cache: Cache | None = None):
        self.config = config
        self.cache = cache or Cache(config.cache_path, config.cache_ttl, config.use_cache)
        self._host_locks: dict[str, asyncio.Semaphore] = {}
        self._last_hit: dict[str, float] = defaultdict(float)
        self._delay_locks: dict[str, asyncio.Lock] = {}
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Fetcher":
        total = max(20, self.config.max_threads_companies * self.config.per_host_concurrency)
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=httpx.Timeout(self.config.timeout, connect=min(8, self.config.timeout)),
            follow_redirects=True,
            http2=True,
            limits=httpx.Limits(
                max_connections=total,
                max_keepalive_connections=total,
                keepalive_expiry=30.0,
            ),
            verify=not self.config.insecure_tls,
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _host_sem(self, host: str) -> asyncio.Semaphore:
        sem = self._host_locks.get(host)
        if sem is None:
            sem = asyncio.Semaphore(self.config.per_host_concurrency)
            self._host_locks[host] = sem
        return sem

    async def _respect_delay(self, host: str) -> None:
        """Keep at least `config.delay` seconds between hits on the same host."""
        if self.config.delay <= 0:
            return
        lock = self._delay_locks.setdefault(host, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            wait = self._last_hit[host] + self.config.delay - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_hit[host] = loop.time()

    async def get(self, url: str) -> FetchResult:
        cached = self.cache.get_http(url)
        if cached is not None:
            final_url, status, body = cached
            return FetchResult(url, final_url, status, body, from_cache=True)

        if self._client is None:
            raise RuntimeError("Fetcher must be used as an async context manager")

        host = host_of(url)
        async with self._host_sem(host):
            await self._respect_delay(host)
            result = await self._get_uncached(url)

        # Cache failures too, but briefly is not worth the complexity: a dead
        # host stays dead for the TTL, which is what we want during batch runs.
        self.cache.set_http(url, result.final_url, result.status, result.html)
        return result

    async def _get_uncached(self, url: str) -> FetchResult:
        try:
            async with self._client.stream("GET", url) as resp:
                final_url = str(resp.url)
                ctype = resp.headers.get("content-type", "").lower()
                if resp.status_code >= 400 or ("html" not in ctype and "xml" not in ctype):
                    await resp.aclose()
                    return FetchResult(url, final_url, resp.status_code, None)

                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_BODY_BYTES:
                        break

                raw = b"".join(chunks)
                encoding = resp.charset_encoding or "utf-8"
                try:
                    html = raw.decode(encoding, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    html = raw.decode("utf-8", errors="replace")
                return FetchResult(url, final_url, resp.status_code, html)
        except Exception:
            # Timeouts, bad certs, malformed URLs, exotic transport failures —
            # a single dead site must never take down the batch.
            return FetchResult(url, url, 0, None)

    async def get_many(self, urls: list[str]) -> list[FetchResult]:
        if not urls:
            return []
        return list(await asyncio.gather(*(self.get(u) for u in urls)))
