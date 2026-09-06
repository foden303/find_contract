"""Web search: pluggable backends, bounded parallelism, cached results.

Every backend returns the same shape — a list of {title, href, body} dicts —
so the rest of the pipeline never learns which one is in use.
"""

import asyncio
import hashlib
import queue
import random

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
        self._sem = asyncio.Semaphore(self.concurrency)
        self._http: httpx.AsyncClient | None = None
        self._cooldown_lock = asyncio.Lock()
        self._cooldown_until = 0.0
        self._throttle_streak = 0
        self.stats = {
            "requests": 0, "cache_hits": 0, "retries": 0, "cooldowns": 0,
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

    async def _wait_for_cooldown(self) -> None:
        while True:
            wait = self._cooldown_until - asyncio.get_running_loop().time()
            if wait <= 0:
                return
            await asyncio.sleep(wait)

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
        async with self._cooldown_lock:
            self._throttle_streak = min(self._throttle_streak + 1, 5)
            delay = min(MAX_PROVIDER_COOLDOWN, delay * self._throttle_streak)
            self._cooldown_until = max(
                self._cooldown_until, asyncio.get_running_loop().time() + delay
            )
            self.stats["cooldowns"] += 1

    async def _record_success(self) -> None:
        async with self._cooldown_lock:
            self._throttle_streak = max(0, self._throttle_streak - 1)

    async def _run_limited_backend(
        self, query: str, limit: int, region: str | None
    ) -> list[dict]:
        while True:
            await self._wait_for_cooldown()
            async with self._sem:
                # Do not occupy a scarce permit if another request established
                # a cooldown between our first check and permit acquisition.
                if self._cooldown_until > asyncio.get_running_loop().time():
                    continue
                self.stats["requests"] += 1
                return await self._run_backend(query, limit, region)

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
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def search(
        self, query: str, limit: int = 10, country: str | None = None
    ) -> list[Candidate]:
        region = ddg_region(country)
        # The cache format includes the controlled backend sequence. Older
        # "ddg" entries came from auto/meta search and must not leak into it.
        backend = "duckduckgo-bing" if self.provider == "ddg" else self.provider
        key = hashlib.sha1(
            f"{SEARCH_CACHE_VERSION}|{backend}|{query}|{limit}|{region}".encode()
        ).hexdigest()

        cached = self.cache.get_search(key)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return [Candidate(**c) for c in cached]

        rows: list[dict] = []
        for attempt in range(MAX_ATTEMPTS):
            await self._wait_for_cooldown()
            try:
                rows = await self._run_limited_backend(query, limit, region)
                await self._record_success()
                break
            except SearchError:
                # Misconfiguration: retrying cannot help, and swallowing it
                # would look exactly like "this company has no web presence".
                raise
            except Exception as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise SearchError(
                        f"{self.provider} search failed after {MAX_ATTEMPTS} attempts. "
                        "Check your network and search settings, or try again later."
                    ) from exc
                self.stats["retries"] += 1
                if self._looks_throttled(exc):
                    # This is provider-wide pressure. Release the request permit
                    # and pause every query instead of sleeping inside the slot.
                    await self._apply_cooldown(attempt)
                    await self._wait_for_cooldown()
                else:
                    await asyncio.sleep(1.5 * (2**attempt) + random.random())

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

        self.cache.set_search(key, [c.__dict__ for c in candidates])
        return candidates

    async def search_many(
        self, queries: list[str], limit: int = 10, country: str | None = None
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
                        queries[index], limit=limit, country=country
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
