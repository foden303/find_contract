"""DuckDuckGo access: pooled clients, bounded parallelism, cached results."""

import asyncio
import hashlib
import queue
import random

from ddgs.ddgs import DDGS

from core.cache import Cache
from core.config import SKIP_HOSTS, Config
from core.models import Candidate
from core.utils import host_of, normalize_url, region_for_country

# DDG throttles hard, but 3 was a global bottleneck across the whole batch.
# Six in flight with backoff measures faster end-to-end than three without.
MAX_CONCURRENT_SEARCHES = 6
MAX_ATTEMPTS = 3

# ISO region -> DuckDuckGo region code (DDG uses `uk` rather than `gb`)
_DDG_REGION_OVERRIDE = {"GB": "uk"}


def ddg_region(country: str | None) -> str | None:
    iso = region_for_country(country)
    if not iso:
        return None
    return f"{_DDG_REGION_OVERRIDE.get(iso, iso).lower()}-en"


class SearchClient:
    """Wraps DDGS in a small reusable pool and runs queries off the event loop."""

    def __init__(self, config: Config, cache: Cache | None = None):
        self.config = config
        self.cache = cache or Cache(config.cache_path, config.cache_ttl, config.use_cache)
        self._sem = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
        self._pool: queue.Queue = queue.Queue()
        for _ in range(MAX_CONCURRENT_SEARCHES):
            self._pool.put(
                DDGS(timeout=self.config.timeout, verify=not self.config.insecure_tls)
            )

    def _blocking_search(self, query: str, limit: int, region: str | None) -> list[dict]:
        client = self._pool.get()
        try:
            kwargs = {"max_results": limit}
            if region:
                kwargs["region"] = region
            return client.text(query, **kwargs) or []
        finally:
            self._pool.put(client)

    async def search(
        self, query: str, limit: int = 10, country: str | None = None
    ) -> list[Candidate]:
        region = ddg_region(country)
        key = hashlib.sha1(f"{query}|{limit}|{region}".encode()).hexdigest()

        cached = self.cache.get_search(key)
        if cached is not None:
            return [Candidate(**c) for c in cached]

        rows: list[dict] = []
        async with self._sem:
            for attempt in range(MAX_ATTEMPTS):
                try:
                    rows = await asyncio.to_thread(self._blocking_search, query, limit, region)
                    break
                except Exception as exc:
                    if attempt == MAX_ATTEMPTS - 1:
                        print(f"[!] Search failed for '{query}': {exc}")
                        rows = []
                        break
                    # Exponential backoff with jitter; DDG rate-limits bursts
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
            if isinstance(batch, BaseException):
                continue
            for cand in batch:
                if cand.url not in merged:
                    merged[cand.url] = cand
        return list(merged.values())


def build_company_queries(
    company: str, country: str | None, products: list[str]
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
