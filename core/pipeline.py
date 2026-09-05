"""Orchestration: turn a company name into a scored, contact-filled Result."""

import asyncio
import time
from typing import Awaitable, Callable

from core.cache import Cache
from core.config import Config
from core.extract import (
    discover_contact_pages,
    extract_contacts,
    guess_emails,
    parse_html,
    prioritize_emails,
)
from core.fetch import Fetcher
from core.tabular import dedupe_key, extract_locality
from core.models import Candidate, ContactExtract, Result
from core.scoring import name_similarity, rank_candidates
from core.search import (
    SearchClient,
    SearchError,
    build_company_queries,
    build_discovery_queries,
)
from core.utils import (
    EMAIL_RE,
    clean_email,
    domain_core,
    extract_domain,
    is_domain_like,
    normalize_url,
    region_for_country,
)

ProgressFn = Callable[[dict], None | Awaitable[None]]

# Above this the site is almost certainly the company's, so an address on a
# different domain is plausible (small traders do use gmail) rather than a
# stray third party's.
OFF_DOMAIN_TRUST = 45.0

# In merge mode, how much a secondary domain must resemble the company name
# before we trust it as another of that company's own sites.
MERGE_NAME_FLOOR = 45.0


# One company's contact page lists a handful of numbers. Anything past this is
# a directory of other people's companies.
MAX_PHONES_PER_PAGE = 12
MAX_EMAILS_PER_PAGE = 25


def _mentions_locality(html: str, locality: str) -> bool:
    """Does the page name the city we expect this company to be in?"""
    lowered = (html or "").lower()
    return any(
        token in lowered
        for token in (t.strip().lower() for t in locality.split(","))
        if len(token) > 3
    )


def _is_listing_page(found: ContactExtract) -> bool:
    return (
        len(found.phones) > MAX_PHONES_PER_PAGE
        or len(found.emails) > MAX_EMAILS_PER_PAGE
    )


def _filter_emails(
    emails: set[str], site_domains: set[str], confidence: float
) -> tuple[list[str], list[str]]:
    """Separate the company's own addresses from third parties on the page.

    A journal article lists its authors' addresses, a directory lists its own
    support inbox — both look identical to a real find until you notice the
    domain does not match any site we scanned. `site_domains` holds every
    scraped source, so merge mode does not discard the second site's own mail.
    """
    kept, rejected = [], []
    for email in sorted(emails):
        _, _, domain_part = email.partition("@")
        if not domain_part:
            continue
        if extract_domain(domain_part) in site_domains or confidence >= OFF_DOMAIN_TRUST:
            kept.append(email)
        else:
            rejected.append(email)
    return kept, rejected


async def _emit(on_event: ProgressFn | None, payload: dict) -> None:
    if not on_event:
        return
    result = on_event(payload)
    if asyncio.iscoroutine(result):
        await result


def _contacts_from_snippet(snippet: str, region: str | None) -> ContactExtract:
    """Cheap pre-pass: directories often expose the email in the snippet."""
    from core.extract import _phones_from_text

    data = ContactExtract()
    if not snippet:
        return data
    for raw in EMAIL_RE.findall(snippet):
        cleaned = clean_email(raw)
        if cleaned:
            data.emails.add(cleaned)
    data.phones |= _phones_from_text(snippet, region)
    return data


async def _select_candidates(
    query: str,
    country: str | None,
    products: list[str],
    config: Config,
    search: SearchClient,
    locality: str = "",
) -> list[Candidate]:
    queries = build_company_queries(query, country, products, locality)
    found = await search.search_many(queries, limit=8, country=country)
    if not found:
        return []

    return rank_candidates(found, query, country, products, locality)


async def _scan_site(
    cand: Candidate,
    config: Config,
    fetcher: Fetcher,
    region: str | None,
    collected: ContactExtract,
    res: Result,
    max_pages: int | None = None,
    locality: str = "",
) -> None:
    """Fetch a candidate's homepage plus its best contact pages."""
    page_budget = config.max_pages if max_pages is None else max_pages
    home = await fetcher.get(cand.url)
    if not home.ok:
        res.notes.append(f"unreachable: {cand.url}")
        return

    if res.website == cand.url and home.final_url:
        res.website = normalize_url(home.final_url)

    # One parse serves both link discovery and extraction
    tree = parse_html(home.html)
    pages = discover_contact_pages(home.final_url, tree, page_budget)
    homepage_data = extract_contacts(home.html, home.final_url, region, tree=tree)
    collected.merge(homepage_data)
    res.pages_scanned.append(home.final_url)

    # Finding the company's own city on its own site is the strongest
    # confirmation available short of contacting them.
    if locality and not res.address_confirmed and _mentions_locality(home.html, locality):
        res.address_confirmed = True
        res.match_reason.append("address confirmed on site")

    if config.early_exit:
        priority, _ = prioritize_emails(sorted(collected.emails))
        if priority and collected.phones:
            res.notes.append("early exit: contact found on homepage")
            return

    if not pages:
        return

    fetched = await fetcher.get_many(pages)
    for page in fetched:
        if not page.ok:
            continue
        found = extract_contacts(page.html, page.final_url, region)
        if _is_listing_page(found):
            # A "top Indian importers" page yields dozens of numbers, none of
            # which belong to the company we asked about.
            res.notes.append(f"skipped listing page: {page.final_url}")
            continue
        collected.merge(found)
        res.pages_scanned.append(page.final_url)


async def scan_company(
    query: str,
    config: Config,
    fetcher: Fetcher,
    search: SearchClient,
    country: str | None = None,
    products: list[str] | None = None,
    address: str | None = None,
    on_event: ProgressFn | None = None,
) -> Result:
    started = time.perf_counter()
    products = products or []
    region = region_for_country(country)
    # A full postal address is useless as a search term, but the city it
    # contains both sharpens the query and confirms a match on the page.
    locality = extract_locality(address or "", country)
    res = Result(query=query, country=country, products=products, address=address)
    collected = ContactExtract()

    await _emit(on_event, {"type": "company_start", "query": query})

    # 1. Work out which site(s) to scan
    if is_domain_like(query):
        url = normalize_url(query)
        candidates = [Candidate(url=url, title=query, score=100.0, reasons=["direct domain"])]
        res.company = extract_domain(url).split(".")[0].capitalize()
        res.is_direct_hit = True
    else:
        res.company = query
        candidates = await _select_candidates(
            query, country, products, config, search, locality
        )

    if not candidates:
        res.notes.append("no search results")
        res.elapsed = time.perf_counter() - started
        await _emit(on_event, {"type": "company_done", "query": query, "result": res})
        return res

    best = candidates[0]
    res.website = best.url
    res.confidence = round(best.score, 1)
    res.match_reason = list(best.reasons)
    res.alternates = [f"{c.score:.0f} {c.url}" for c in candidates[1:4]]

    # Below the bar the best hit is a directory or an article that merely
    # mentions the company. Record it as a lead to check by hand, but do not
    # scrape it — its emails and phones belong to somebody else.
    above_bar = [c for c in candidates if c.score >= config.min_score]
    if config.merge_sources:
        # Search often returns three pages of one site. Merging those is not
        # "several sources", so keep the best-scoring hit per registrable
        # domain — the site's own crawl already covers its other pages.
        by_domain: dict[str, Candidate] = {}
        for cand in above_bar:
            domain = extract_domain(cand.url)
            if domain not in by_domain:
                by_domain[domain] = cand
        merged = list(by_domain.values())

        # Only merge from domains that plausibly belong to this company. A
        # listings site can clear min_score on country and product alone, and
        # scraping it hands back its own support inbox as the company's. There
        # are far too many such sites to blacklist by hand, so test the name.
        own = [
            c for c in merged
            if c is best or name_similarity(query, domain_core(c.url)) >= MERGE_NAME_FLOOR
        ]
        kept_ids = {id(c) for c in own}
        skipped = [c for c in merged if id(c) not in kept_ids]
        if skipped:
            res.notes.append(
                "merge skipped unrelated domains: "
                + ", ".join(extract_domain(c.url) for c in skipped[:5])
            )
        above_bar = own
    scannable = above_bar[: config.top_results]
    if not scannable:
        res.match_reason.append(
            f"below threshold ({best.score:.0f} < {config.min_score:.0f}) — not scanned"
        )
        res.notes.append("weak match: verify the website by hand before using it")
        res.elapsed = time.perf_counter() - started
        await _emit(on_event, {"type": "company_done", "query": query, "result": res})
        return res

    # 2. Harvest each candidate, best first
    scanned_domains: set[str] = set()
    for rank, cand in enumerate(scannable):
        collected.merge(_contacts_from_snippet(cand.snippet, region))
        if cand.snippet:
            res.notes.append(f"snippet[{extract_domain(cand.url)}]: {cand.snippet[:180]}")

        if cand.is_snippet_only:
            continue

        if rank > 0 and not config.merge_sources:
            # Runners-up are only worth the round trips when the best result
            # came back empty, and never when they score far below it.
            if collected.emails or collected.phones:
                break
            if cand.score < best.score - 25:
                res.notes.append(f"skipped weaker candidate {cand.url}")
                break

        # In merge mode every candidate gets the full budget, because each one
        # is a source in its own right rather than a fallback.
        if config.merge_sources:
            budget = config.max_pages
        else:
            budget = config.max_pages if rank == 0 else max(3, config.max_pages // 3)

        await _scan_site(cand, config, fetcher, region, collected, res, budget, locality)
        res.sources.append(f"{cand.score:.0f} {cand.url}")
        scanned_domains.add(extract_domain(cand.url))

        if config.early_exit and not config.merge_sources:
            priority, _ = prioritize_emails(sorted(collected.emails))
            if priority and collected.phones:
                break

    # 3. Consolidate, dropping addresses that belong to somebody else
    if res.website:
        scanned_domains.add(extract_domain(res.website))
    scanned_domains.discard("")
    kept, rejected = _filter_emails(collected.emails, scanned_domains, res.confidence)
    if rejected:
        res.notes.append(f"ignored off-domain emails: {', '.join(rejected[:5])}")

    res.emails = kept
    res.phones = sorted(collected.phones)
    res.social_links = sorted(collected.social_links)
    res.whatsapp_links = sorted(collected.whatsapp_links)
    res.whatsapp_numbers = sorted(collected.whatsapp_numbers)
    # "Verified" now means exactly one thing: the number came from a real
    # WhatsApp link on the site, not a guess about its country code.
    res.whatsapp_verified = sorted(collected.whatsapp_numbers)
    res.priority_emails, res.emails = prioritize_emails(res.emails)

    # 4. Fall back to pattern-guessed addresses only when we found none, and
    #    only on what looks like the company's own domain — guessing
    #    info@ on a directory just yields the directory's own inbox.
    own_site = bool(res.website) and not best.is_directory and not best.is_snippet_only
    if best.is_directory:
        res.match_reason.append("directory listing, not the company's own site")

    if (
        config.guess_emails
        and own_site
        and res.confidence >= 40
        and not res.priority_emails
        and not res.emails
    ):
        domain = extract_domain(res.website)
        res.guessed_emails = await guess_emails(
            domain, set(res.emails) | set(res.priority_emails), fetcher.cache
        )
        if res.guessed_emails:
            res.notes.append(f"guessed from MX record of {domain}")

    res.elapsed = time.perf_counter() - started
    await _emit(on_event, {"type": "company_done", "query": query, "result": res})
    return res


async def process_batch(
    rows: list[tuple],
    config: Config,
    on_event: ProgressFn | None = None,
    cache: Cache | None = None,
    store=None,
) -> list[Result]:
    """Scan many companies concurrently, preserving input order in the output."""
    if not rows:
        return []

    owns_cache = cache is None
    cache = cache or Cache(config.cache_path, config.cache_ttl, config.use_cache)
    search = None
    tasks = []
    try:
        search = SearchClient(config, cache)
        sem = asyncio.Semaphore(config.max_threads_companies)
        total = len(rows)
        done = 0
        lock = asyncio.Lock()

        async with Fetcher(config, cache) as fetcher:

            async def one(index: int, row: tuple) -> Result:
                nonlocal done
                name, country, products, *extra = row
                address = extra[0] if extra else None
                key = dedupe_key(name) if store and config.use_company_store else ""
                if key:
                    remembered = store.get_company(key, config.company_ttl)
                    if remembered:
                        res = Result(**remembered)
                        res.notes.append("from company store")
                        res.elapsed = 0.0
                        async with lock:
                            done += 1
                            position = done
                        await _emit(on_event, {
                            "type": "progress", "index": index,
                            "done": position, "total": total, "result": res,
                        })
                        return res

                async with sem:
                    try:
                        res = await scan_company(
                            name, config, fetcher, search,
                            country=country, products=products, address=address,
                            on_event=on_event,
                        )
                    except SearchError:
                        raise
                    except Exception as exc:
                        res = Result(query=name, company=name, country=country,
                                     products=products, address=address)
                        res.notes.append(f"error: {exc}")
                    else:
                        if key:
                            store.save_company(key, res)
                async with lock:
                    done += 1
                    position = done
                await _emit(on_event, {
                    "type": "progress", "index": index,
                    "done": position, "total": total, "result": res,
                })
                return res

            tasks = [asyncio.create_task(one(i, row)) for i, row in enumerate(rows)]
            try:
                return list(await asyncio.gather(*tasks))
            finally:
                # gather does not stop siblings when one raises. Drain them
                # before closing the Fetcher, cache or application Store.
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if search is not None:
            await search.aclose()
        if owns_cache:
            cache.close()


async def discover_trade_leads(
    keyword: str,
    config: Config,
    region: str = "",
    limit: int = 10,
    cache: Cache | None = None,
) -> list[Candidate]:
    """Find new companies for a product keyword, best-scoring first."""
    owns_cache = cache is None
    cache = cache or Cache(config.cache_path, config.cache_ttl, config.use_cache)
    search = None
    try:
        search = SearchClient(config, cache)
        keywords = [k.strip() for k in keyword.split(",") if k.strip()]
        queries: list[str] = []
        for kw in keywords:
            queries.extend(build_discovery_queries(kw, region))
        found = await search.search_many(queries, limit=max(8, limit), country=region or None)

        by_domain: dict[str, Candidate] = {}
        for cand in found:
            domain = extract_domain(cand.url)
            if not domain:
                continue
            existing = by_domain.get(domain)
            if existing is None or (existing.is_directory and not cand.is_directory):
                by_domain[domain] = cand

        from core.config import DIRECTORY_HOSTS
        from core.utils import host_of

        leads = []
        for cand in by_domain.values():
            cand.is_directory = any(d in host_of(cand.url) for d in DIRECTORY_HOSTS)
            leads.append(cand)
        leads.sort(key=lambda c: (not c.is_directory, len(c.snippet)), reverse=True)
        return leads[:limit]
    finally:
        if search is not None:
            await search.aclose()
        if owns_cache:
            cache.close()
