"""Async HTTP layer: one shared client, per-host politeness, cache in front."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass

import httpx

from core.cache import Cache
from core.config import Config, USER_AGENT
from core.utils import host_of

MAX_BODY_BYTES = 2_000_000  # a contact page is never bigger than this
CIRCUIT_FAILURE_THRESHOLD = 2

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
    error: str | None = None

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
        self._inflight: dict[str, asyncio.Task] = {}
        self._host_failures: dict[str, int] = defaultdict(int)
        self._blocked_hosts: set[str] = set()
        self.stats = {
            "requests": 0, "cache_hits": 0, "inflight_joins": 0,
            "circuits_opened": 0,
        }

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
        pending = list(self._inflight.values())
        self._inflight.clear()
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
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

    @staticmethod
    def _add_metric(metrics: dict | None, key: str, value: float | int = 1) -> None:
        if metrics is not None:
            metrics[key] = metrics.get(key, 0) + value

    def _record_host_result(self, host: str, result: FetchResult) -> None:
        failed = (
            result.status == 0
            or result.status in (403, 429)
            or result.status >= 500
        )
        if not failed:
            self._host_failures.pop(host, None)
            return
        self._host_failures[host] += 1
        if (
            self._host_failures[host] >= CIRCUIT_FAILURE_THRESHOLD
            and host not in self._blocked_hosts
        ):
            self._blocked_hosts.add(host)
            self.stats["circuits_opened"] += 1

    async def _fetch_and_cache(self, url: str, host: str) -> FetchResult:
        async with self._host_sem(host):
            await self._respect_delay(host)
            self.stats["requests"] += 1
            result = await self._get_uncached(url)
        self._record_host_result(host, result)
        self.cache.set_http(url, result.final_url, result.status, result.html)
        return result

    async def get(self, url: str, telemetry: dict | None = None) -> FetchResult:
        self._add_metric(telemetry, "http_requests")
        cached = self.cache.get_http(url)
        if cached is not None:
            final_url, status, body = cached
            self.stats["cache_hits"] += 1
            self._add_metric(telemetry, "http_cache_hits")
            return FetchResult(url, final_url, status, body, from_cache=True)

        if self._client is None:
            raise RuntimeError("Fetcher must be used as an async context manager")

        host = host_of(url)
        if host in self._blocked_hosts:
            self._add_metric(telemetry, "http_circuit_skips")
            return FetchResult(url, url, 0, None, error="circuit_open")

        task = self._inflight.get(url)
        owner = task is None
        if owner:
            task = asyncio.create_task(self._fetch_and_cache(url, host))
            self._inflight[url] = task
        else:
            self.stats["inflight_joins"] += 1
            self._add_metric(telemetry, "http_inflight_joins")

        started = asyncio.get_running_loop().time()
        try:
            result = await asyncio.shield(task)
            self._add_metric(
                telemetry, "http_wait_ms",
                round((asyncio.get_running_loop().time() - started) * 1000, 2),
            )
            if owner:
                self._add_metric(telemetry, "http_network_requests")
            if result.error:
                self._add_metric(telemetry, "http_errors")
            return result
        finally:
            if task.done() and self._inflight.get(url) is task:
                self._inflight.pop(url, None)

    async def _get_uncached(self, url: str) -> FetchResult:
        try:
            async with self._client.stream("GET", url) as resp:
                final_url = str(resp.url)
                ctype = resp.headers.get("content-type", "").lower()
                if resp.status_code >= 400 or ("html" not in ctype and "xml" not in ctype):
                    await resp.aclose()
                    error = (
                        f"http_{resp.status_code}"
                        if resp.status_code >= 400 else "unsupported_content"
                    )
                    return FetchResult(
                        url, final_url, resp.status_code, None, error=error
                    )

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
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Timeouts, bad certs and transport errors stay observable without
            # taking down the rest of the batch.
            return FetchResult(
                url, url, 0, None, error=type(exc).__name__.lower()
            )

    async def get_many(
        self, urls: list[str], telemetry: dict | None = None
    ) -> list[FetchResult]:
        if not urls:
            return []
        return list(
            await asyncio.gather(*(self.get(u, telemetry=telemetry) for u in urls))
        )
