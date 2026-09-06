"""Web search: pluggable backends, bounded parallelism, cached results.

Every backend returns the same shape — a list of {title, href, body} dicts —
so the rest of the pipeline never learns which one is in use.
"""

import asyncio
import hashlib
import queue
import random
import time

import httpx
from ddgs.ddgs import DDGS
from ddgs.exceptions import DDGSException

from core.cache import Cache
from core.config import SEARCH_CONCURRENCY, SKIP_HOSTS, USER_AGENT, Config
from core.models import Candidate
from core.utils import host_of, normalize_url, region_for_country

MAX_ATTEMPTS = 3
SEARCH_CACHE_VERSION = 2
PER_COMPANY_SEARCH_CONCURRENCY = 2
MAX_PROVIDER_COOLDOWN = 30.0
FREE_SEARCH_BACKENDS = ("duckduckgo", "bing")

# ISO region -> DuckDuckGo region code (DDG uses `uk` rather than `gb`)
_DDG_REGION_OVERRIDE = {"GB": "uk"}


def ddg_region(country: str | None) -> str | None:
    iso = region_for_country(country)
    if not iso:
        return None
    return f"{_DDG_REGION_OVERRIDE.get(iso, iso).lower()}-en"


class SearchError(RuntimeError):
    """Raised when a backend is misconfigured, so it fails loudly not silently."""


class SearchClient:
    """One entry point over several search backends, with cache and retries."""

    def __init__(self, config: Config, cache: Cache | None = None):
        self.config = config
        self.cache = cache or Cache(config.cache_path, config.cache_ttl, config.use_cache)

        self.provider = (config.search_provider or "ddg").lower()
        if self.provider not in SEARCH_CONCURRENCY:
            raise SearchError(
                f"Unknown search provider {self.provider!r}. "
                f"Choose one of: {', '.join(SEARCH_CONCURRENCY)}"
            )
        if self.provider in ("brave", "serper") and not config.search_api_key:
            raise SearchError(
                f"Provider {self.provider!r} needs an API key — set FINDER_SEARCH_API_KEY."
            )

        self.concurrency = SEARCH_CONCURRENCY[self.provider]
        self._adaptive_limit = self.concurrency
        self._active_requests = 0
        self._limit_condition = asyncio.Condition()
        self._http: httpx.AsyncClient | None = None
        self._cooldown_until = 0.0
        self._throttle_streak = 0
        self._success_streak = 0
        self._inflight: dict[str, asyncio.Task] = {}
        self.stats = {
            "requests": 0, "cache_hits": 0, "retries": 0, "cooldowns": 0,
            "inflight_joins": 0, "adaptive_limit": self._adaptive_limit,
        }

        # DDGS is synchronous, so it gets a bounded pool driven from threads.
        self._pool: queue.Queue = queue.Queue()
        if self.provider == "ddg":
            for _ in range(self.concurrency):
                self._pool.put(
                    DDGS(timeout=config.timeout, verify=not config.insecure_tls)
                )

    # --- backends --------------------------------------------------------

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self.config.timeout,
                verify=not self.config.insecure_tls,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        return self._http

    def _blocking_search(self, query: str, limit: int, region: str | None) -> list[dict]:
        client = self._pool.get()
        try:
            kwargs = {"max_results": limit}
            if region:
                kwargs["region"] = region
            last_error: Exception | None = None
            for backend in FREE_SEARCH_BACKENDS:
                try:
                    rows = client.text(query, backend=backend, **kwargs) or []
                except DDGSException as exc:
                    last_error = exc
                    continue
                if rows:
                    return rows
            raise last_error or DDGSException("No results found.")
        finally:
            self._pool.put(client)

    @staticmethod
    def _add_metric(metrics: dict | None, key: str, value: float | int = 1) -> None:
        if metrics is not None:
            metrics[key] = metrics.get(key, 0) + value

    async def _wait_for_cooldown(self, metrics: dict | None = None) -> None:
        started = time.perf_counter()
        waited = False
        while True:
            wait = self._cooldown_until - asyncio.get_running_loop().time()
            if wait <= 0:
                break
            waited = True
            await asyncio.sleep(wait)
        if waited:
            self._add_metric(
                metrics, "search_cooldown_ms",
                round((time.perf_counter() - started) * 1000, 2),
            )

    def _looks_throttled(self, exc: Exception) -> bool:
        if self.provider == "ddg" and isinstance(exc, DDGSException):
            # The HTML backend maps non-200 responses, including 429, to an
            # empty result and DDGSException. Treat it as shared pressure.
            return True
        return (
            isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code in (429, 503)
        )

    async def _apply_cooldown(self, attempt: int) -> None:
        delay = min(MAX_PROVIDER_COOLDOWN, 1.5 * (2**attempt) + random.random())
        async with self._limit_condition:
            self._throttle_streak = min(self._throttle_streak + 1, 5)
            self._success_streak = 0
            delay = min(MAX_PROVIDER_COOLDOWN, delay * self._throttle_streak)
            self._cooldown_until = max(
                self._cooldown_until, asyncio.get_running_loop().time() + delay
            )
            # AIMD: halve pressure immediately, then recover one slot after a
            # sustained run of successes.
            self._adaptive_limit = max(1, self._adaptive_limit // 2)
            self.stats["adaptive_limit"] = self._adaptive_limit
            self.stats["cooldowns"] += 1
            self._limit_condition.notify_all()

    async def _record_success(self) -> None:
        async with self._limit_condition:
            self._throttle_streak = max(0, self._throttle_streak - 1)
            self._success_streak += 1
            if self._success_streak >= 12 and self._adaptive_limit < self.concurrency:
                self._adaptive_limit += 1
                self._success_streak = 0
                self.stats["adaptive_limit"] = self._adaptive_limit
                self._limit_condition.notify_all()

    async def _acquire_backend_slot(self, metrics: dict | None = None) -> None:
        started = time.perf_counter()
        while True:
            await self._wait_for_cooldown(metrics)
            async with self._limit_condition:
                if (
                    self._cooldown_until <= asyncio.get_running_loop().time()
                    and self._active_requests < self._adaptive_limit
                ):
                    self._active_requests += 1
                    self._add_metric(
                        metrics, "search_queue_ms",
                        round((time.perf_counter() - started) * 1000, 2),
                    )
                    return
                cooldown_wait = (
                    self._cooldown_until - asyncio.get_running_loop().time()
                )
                if cooldown_wait > 0:
                    try:
                        await asyncio.wait_for(
                            self._limit_condition.wait(), timeout=cooldown_wait
                        )
                    except TimeoutError:
                        pass
                else:
                    await self._limit_condition.wait()

    async def _release_backend_slot(self) -> None:
        async with self._limit_condition:
            self._active_requests -= 1
            self._limit_condition.notify_all()

    async def _run_limited_backend(
        self, query: str, limit: int, region: str | None,
        metrics: dict | None = None,
    ) -> list[dict]:
        await self._acquire_backend_slot(metrics)
        try:
            self.stats["requests"] += 1
            self._add_metric(metrics, "search_requests")
            return await self._run_backend(query, limit, region)
        finally:
            await self._release_backend_slot()

    async def _searxng(self, query: str, limit: int, region: str | None) -> list[dict]:
        """Self-hosted SearXNG. Aggregates several engines; no key, no quota."""
        params = {"q": query, "format": "json", "categories": "general", "safesearch": "0"}
        if region:
            params["language"] = region.split("-")[0]

        resp = await self._http_client().get(
            self.config.searxng_url.rstrip("/") + "/search", params=params
        )
        if resp.status_code == 403:
            raise SearchError(
                "SearXNG refused the request. Enable the JSON output in its "
                "settings.yml:  search:\\n    formats:\\n      - html\\n      - json"
            )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError:
            raise SearchError(
                "SearXNG returned HTML rather than JSON — the json format is "
                "not enabled in its settings.yml."
            )
        return [
            {"title": r.get("title", ""), "href": r.get("url", ""), "body": r.get("content", "")}
            for r in (payload.get("results") or [])[:limit]
        ]

    async def _brave(self, query: str, limit: int, region: str | None) -> list[dict]:
        resp = await self._http_client().get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(limit, 20)},
            headers={"X-Subscription-Token": self.config.search_api_key,
                     "Accept": "application/json"},
        )
        resp.raise_for_status()
        results = (resp.json().get("web") or {}).get("results") or []
        return [
            {"title": r.get("title", ""), "href": r.get("url", ""),
             "body": r.get("description", "")}
            for r in results[:limit]
        ]

    async def _serper(self, query: str, limit: int, region: str | None) -> list[dict]:
        resp = await self._http_client().post(
            "https://google.serper.dev/search",
            json={"q": query, "num": min(limit, 20)},
            headers={"X-API-KEY": self.config.search_api_key},
        )
        resp.raise_for_status()
        return [
            {"title": r.get("title", ""), "href": r.get("link", ""),
             "body": r.get("snippet", "")}
            for r in (resp.json().get("organic") or [])[:limit]
        ]

    async def _run_backend(self, query: str, limit: int, region: str | None) -> list[dict]:
        if self.provider == "ddg":
            return await asyncio.to_thread(self._blocking_search, query, limit, region)
        if self.provider == "searxng":
            return await self._searxng(query, limit, region)
        if self.provider == "brave":
            return await self._brave(query, limit, region)
        return await self._serper(query, limit, region)

    async def aclose(self) -> None:
        pending = list(self._inflight.values())
        self._inflight.clear()
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._http is not None:
            await self._http.aclose()
            self._http = None
    async def _search_uncached(
        self, key: str, query: str, limit: int, region: str | None
    ) -> tuple[list[dict], dict]:
        metrics: dict[str, float | int] = {}
        rows: list[dict] = []
        for attempt in range(MAX_ATTEMPTS):
            await self._wait_for_cooldown(metrics)
            try:
                rows = await self._run_limited_backend(
                    query, limit, region, metrics
                )
                await self._record_success()
                break
            except SearchError:
                raise
            except Exception as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise SearchError(
                        f"{self.provider} search failed after {MAX_ATTEMPTS} attempts. "
                        "Check your network and search settings, or try again later."
                    ) from exc
                self.stats["retries"] += 1
                self._add_metric(metrics, "search_retries")
                if self._looks_throttled(exc):
                    await self._apply_cooldown(attempt)
                    await self._wait_for_cooldown(metrics)
                else:
                    delay = 1.5 * (2**attempt) + random.random()
                    await asyncio.sleep(delay)
                    self._add_metric(metrics, "search_backoff_ms", round(delay * 1000, 2))

        candidates = []
        seen = set()
        for row in rows:
            href = row.get("href") or ""
            if not href:
                continue
            host = host_of(href)
            if any(bad in host for bad in SKIP_HOSTS):
                continue
            url = normalize_url(href)
            if url in seen:
                continue
            seen.add(url)
            candidates.append(
                Candidate(
                    url=url,
                    title=row.get("title") or "",
                    snippet=row.get("body") or "",
                    query=query,
                )
            )

        payload = [c.__dict__ for c in candidates]
        self.cache.set_search(key, payload)
        return payload, metrics

    async def search(
        self, query: str, limit: int = 10, country: str | None = None,
        telemetry: dict | None = None,
    ) -> list[Candidate]:
        self._add_metric(telemetry, "logical_queries")
        region = ddg_region(country)
        backend = "duckduckgo-bing" if self.provider == "ddg" else self.provider
        key = hashlib.sha1(
            f"{SEARCH_CACHE_VERSION}|{backend}|{query}|{limit}|{region}".encode()
        ).hexdigest()

        cached = self.cache.get_search(key)
        if cached is not None:
            self.stats["cache_hits"] += 1
            self._add_metric(telemetry, "search_cache_hits")
            return [Candidate(**c) for c in cached]

        task = self._inflight.get(key)
        owner = task is None
        if owner:
            task = asyncio.create_task(
                self._search_uncached(key, query, limit, region)
            )
            self._inflight[key] = task
        else:
            self.stats["inflight_joins"] += 1
            self._add_metric(telemetry, "search_inflight_joins")

        started = time.perf_counter()
        try:
            payload, metrics = await asyncio.shield(task)
            if owner and telemetry is not None:
                for name, value in metrics.items():
                    self._add_metric(telemetry, name, value)
            elif not owner:
                self._add_metric(
                    telemetry, "search_inflight_wait_ms",
                    round((time.perf_counter() - started) * 1000, 2),
                )
            return [Candidate(**c) for c in payload]
        finally:
            if task.done() and self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    async def search_many(
        self, queries: list[str], limit: int = 10, country: str | None = None,
        telemetry: dict | None = None,
    ) -> list[Candidate]:
        """Search with bounded per-company fanout and merge first sightings."""
        if not queries:
            return []
        batches: list[list[Candidate] | BaseException | None] = [None] * len(queries)
        next_index = 0
        index_lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal next_index
            while True:
                async with index_lock:
                    if next_index >= len(queries):
                        return
                    index = next_index
                    next_index += 1
                try:
                    batches[index] = await self.search(
                        queries[index], limit=limit, country=country,
                        telemetry=telemetry,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    batches[index] = exc

        workers = [
            asyncio.create_task(worker())
            for _ in range(min(PER_COMPANY_SEARCH_CONCURRENCY, len(queries)))
        ]
        try:
            await asyncio.gather(*workers)
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        merged: dict[str, Candidate] = {}
        for batch in batches:
            if isinstance(batch, SearchError):
                raise batch
            if isinstance(batch, BaseException) or batch is None:
                continue
            for cand in batch:
                if cand.url not in merged:
                    merged[cand.url] = cand
        return list(merged.values())


def build_company_queries(
    company: str, country: str | None, products: list[str], locality: str = ""
) -> list[str]:
    """Queries from most to least specific.

    All of them run — the previous version stopped after the first, which is
    why country and product context never reached the search engine. Capped at
    four: search is the batch's bottleneck, and a fifth query measured as pure
    cost with no new domains found.

    The names are deliberately not quoted. An exact-phrase search on a
    registry-style name ("SOILEX LIFE SCIENCE PRIVATE LIMITED") drops the
    company's own site, which spells itself differently; unquoted, DuckDuckGo
    returns soilexlifescience.com first. Precision is scoring's job, not the
    query's.
    """
    prod = ", ".join(products[:3]) if products else ""

    queries = [company]
    if country and prod:
        queries.append(f"{company} {country} {prod}")
    elif prod:
        queries.append(f"{company} {prod}")
    elif country:
        queries.append(f"{company} {country}")

    # With both filters set, query 2 is narrow; a product-only query keeps
    # reach for companies whose site never names their country.
    if country and prod:
        queries.append(f"{company} {prod}")

    # The city disambiguates same-named companies far better than the country
    if locality:
        queries.append(f"{company} {locality}")

    queries.append(
        f"{company} {country} contact email" if country else f"{company} official website contact"
    )

    # De-duplicate, preserving order
    seen, ordered = set(), []
    for q in queries:
        if q not in seen:
            seen.add(q)
            ordered.append(q)
    return ordered


def build_discovery_queries(keyword: str, region: str = "") -> list[str]:
    suffixes = [
        "importers", "exporters", "wholesalers", "distributors",
        "trading company", "suppliers contact email",
    ]
    return [f'"{keyword}" {s} {region}'.strip() for s in suffixes]
