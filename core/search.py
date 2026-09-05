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

from core.cache import Cache
from core.config import SEARCH_CONCURRENCY, SKIP_HOSTS, USER_AGENT, Config
from core.models import Candidate
from core.utils import host_of, normalize_url, region_for_country

MAX_ATTEMPTS = 3

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

        # DDGS is synchronous, so it gets a pool of clients driven from threads.
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
            return client.text(query, **kwargs) or []
        finally:
            self._pool.put(client)

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
        # The provider is part of the key: results differ between backends, so
        # switching provider must not serve the previous one's cached answers.
        key = hashlib.sha1(
            f"{self.provider}|{query}|{limit}|{region}".encode()
        ).hexdigest()

        cached = self.cache.get_search(key)
        if cached is not None:
            return [Candidate(**c) for c in cached]

        rows: list[dict] = []
        async with self._sem:
            for attempt in range(MAX_ATTEMPTS):
                try:
                    rows = await self._run_backend(query, limit, region)
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
                    # Exponential backoff with jitter; engines rate-limit bursts
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
        """Run every query concurrently and merge, keeping the first sighting."""
        batches = await asyncio.gather(
            *(self.search(q, limit=limit, country=country) for q in queries),
            return_exceptions=True,
        )
        merged: dict[str, Candidate] = {}
        for batch in batches:
            if isinstance(batch, SearchError):
                # A broken backend must not be reduced to "found nothing"
                raise batch
            if isinstance(batch, BaseException):
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
