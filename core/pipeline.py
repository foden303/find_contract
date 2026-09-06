"""Orchestration: turn a company name into a scored, contact-filled Result."""

import asyncio
import time
from dataclasses import dataclass, field
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

# Stop after the two strongest queries when they already identify a plausible
# first-party site. Lower scores still receive the complete fallback query set.
SEARCH_EXPANSION_SCORE = 70.0


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

def _has_complete_contact(found: ContactExtract) -> bool:
    priority, _ = prioritize_emails(sorted(found.emails))
    return bool(priority and found.phones)


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
    telemetry: dict | None = None,
) -> list[Candidate]:
    queries = build_company_queries(query, country, products, locality)
    primary = queries[:2]
    found = await search.search_many(
        primary, limit=8, country=country, telemetry=telemetry
    )
    ranked = rank_candidates(found, query, country, products, locality) if found else []

    sufficient_score = max(config.min_score, SEARCH_EXPANSION_SCORE)
    if (
        ranked
        and ranked[0].score >= sufficient_score
        and not ranked[0].is_directory
        and not ranked[0].is_snippet_only
    ):
        return ranked

    fallback = queries[2:]
    if not fallback:
        return ranked
    additional = await search.search_many(
        fallback, limit=8, country=country, telemetry=telemetry
    )
    merged = {candidate.url: candidate for candidate in found}
    for candidate in additional:
        merged.setdefault(candidate.url, candidate)
    return rank_candidates(
        list(merged.values()), query, country, products, locality
    )


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
    home = await fetcher.get(cand.url, telemetry=res.performance)
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

    if config.early_exit and _has_complete_contact(collected):
        res.notes.append("early exit: contact found on homepage")
        return

    if not pages:
        return

    # Strong links are first. Fetch two at a time so a successful contact page
    # prevents the remaining weak/privacy/guessed paths from consuming network.
    wave_size = 2
    for offset in range(0, len(pages), wave_size):
        pending = {
            asyncio.create_task(fetcher.get(url, telemetry=res.performance))
            for url in pages[offset : offset + wave_size]
        }
        try:
            while pending:
                completed, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in completed:
                    page = await task
                    if not page.ok:
                        continue
                    found = extract_contacts(page.html, page.final_url, region)
                    if _is_listing_page(found):
                        # A directory page can expose dozens of contacts that
                        # belong to other companies.
                        res.notes.append(f"skipped listing page: {page.final_url}")
                        continue
                    collected.merge(found)
                    res.pages_scanned.append(page.final_url)
                    if config.early_exit and _has_complete_contact(collected):
                        res.notes.append("early exit: contact found on contact page")
                        for remaining in pending:
                            remaining.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        return
        finally:
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

@dataclass
class _CompanyWork:
    query: str
    country: str | None
    products: list[str]
    address: str | None
    started: float
    deadline_at: float
    region: str | None
    locality: str
    res: Result
    collected: ContactExtract = field(default_factory=ContactExtract)
    candidates: list[Candidate] = field(default_factory=list)
    scannable: list[Candidate] = field(default_factory=list)
    best: Candidate | None = None
    complete: bool = False
    timed_out: bool = False
    consolidated: bool = False


def _new_company_work(
    query: str,
    country: str | None,
    products: list[str],
    address: str | None,
    config: Config,
) -> _CompanyWork:
    loop = asyncio.get_running_loop()
    started = time.perf_counter()
    locality = extract_locality(address or "", country)
    return _CompanyWork(
        query=query,
        country=country,
        products=products,
        address=address,
        started=started,
        deadline_at=loop.time() + max(0.1, config.company_timeout),
        region=region_for_country(country),
        locality=locality,
        res=Result(
            query=query, country=country, products=products, address=address
        ),
    )


async def _prepare_company(
    work: _CompanyWork, config: Config, search: SearchClient
) -> None:
    phase_started = time.perf_counter()
    try:
        if is_domain_like(work.query):
            url = normalize_url(work.query)
            work.candidates = [
                Candidate(
                    url=url, title=work.query, score=100.0,
                    reasons=["direct domain"],
                )
            ]
            work.res.company = extract_domain(url).split(".")[0].capitalize()
            work.res.is_direct_hit = True
        else:
            work.res.company = work.query
            work.candidates = await _select_candidates(
                work.query,
                work.country,
                work.products,
                config,
                search,
                work.locality,
                telemetry=work.res.performance,
            )
    finally:
        work.res.performance["search_ms"] = round(
            (time.perf_counter() - phase_started) * 1000, 2
        )

    if not work.candidates:
        work.res.notes.append("no search results")
        work.complete = True
        return

    best = work.candidates[0]
    work.best = best
    work.res.website = best.url
    work.res.confidence = round(best.score, 1)
    work.res.match_reason = list(best.reasons)
    work.res.alternates = [
        f"{candidate.score:.0f} {candidate.url}"
        for candidate in work.candidates[1:4]
    ]

    above_bar = [
        candidate
        for candidate in work.candidates
        if candidate.score >= config.min_score
    ]
    if config.merge_sources:
        by_domain: dict[str, Candidate] = {}
        for candidate in above_bar:
            domain = extract_domain(candidate.url)
            if domain not in by_domain:
                by_domain[domain] = candidate
        merged = list(by_domain.values())
        own = [
            candidate
            for candidate in merged
            if candidate is best
            or name_similarity(
                work.query, domain_core(candidate.url)
            ) >= MERGE_NAME_FLOOR
        ]
        kept_ids = {id(candidate) for candidate in own}
        skipped = [
            candidate for candidate in merged if id(candidate) not in kept_ids
        ]
        if skipped:
            work.res.notes.append(
                "merge skipped unrelated domains: "
                + ", ".join(extract_domain(candidate.url) for candidate in skipped[:5])
            )
        above_bar = own

    work.scannable = above_bar[: config.top_results]
    if not work.scannable:
        work.res.match_reason.append(
            f"below threshold ({best.score:.0f} < {config.min_score:.0f}) — not scanned"
        )
        work.res.notes.append(
            "weak match: verify the website by hand before using it"
        )
        work.complete = True


def _consolidate_contacts(work: _CompanyWork) -> None:
    if work.consolidated or work.best is None:
        return
    work.consolidated = True
    res = work.res
    collected = work.collected
    scanned_domains = {
        extract_domain(source.split(" ", 1)[-1]) for source in res.sources
    }
    if res.website:
        scanned_domains.add(extract_domain(res.website))
    scanned_domains.discard("")
    kept, rejected = _filter_emails(
        collected.emails, scanned_domains, res.confidence
    )
    if rejected:
        res.notes.append(f"ignored off-domain emails: {', '.join(rejected[:5])}")

    res.emails = kept
    res.phones = sorted(collected.phones)
    res.social_links = sorted(collected.social_links)
    res.whatsapp_links = sorted(collected.whatsapp_links)
    res.whatsapp_numbers = sorted(collected.whatsapp_numbers)
    res.whatsapp_verified = sorted(collected.whatsapp_numbers)
    res.priority_emails, res.emails = prioritize_emails(res.emails)

    if work.best.is_directory:
        res.match_reason.append("directory listing, not the company's own site")


async def _crawl_company(
    work: _CompanyWork, config: Config, fetcher: Fetcher
) -> None:
    phase_started = time.perf_counter()
    try:
        try:
            for rank, candidate in enumerate(work.scannable):
                work.collected.merge(
                    _contacts_from_snippet(candidate.snippet, work.region)
                )
                if candidate.snippet:
                    work.res.notes.append(
                        f"snippet[{extract_domain(candidate.url)}]: "
                        f"{candidate.snippet[:180]}"
                    )
                if candidate.is_snippet_only:
                    continue

                if rank > 0 and not config.merge_sources:
                    if work.collected.emails or work.collected.phones:
                        break
                    if candidate.score < work.best.score - 25:
                        work.res.notes.append(
                            f"skipped weaker candidate {candidate.url}"
                        )
                        break

                budget = (
                    config.max_pages
                    if config.merge_sources or rank == 0
                    else max(3, config.max_pages // 3)
                )
                await _scan_site(
                    candidate,
                    config,
                    fetcher,
                    work.region,
                    work.collected,
                    work.res,
                    budget,
                    work.locality,
                )
                work.res.sources.append(
                    f"{candidate.score:.0f} {candidate.url}"
                )

                if config.early_exit and not config.merge_sources:
                    priority, _ = prioritize_emails(
                        sorted(work.collected.emails)
                    )
                    if priority and work.collected.phones:
                        break
        finally:
            # A deadline may interrupt the crawl after useful pages completed.
            # Preserve those contacts instead of turning a timeout into an
            # apparently empty company.
            _consolidate_contacts(work)

        own_site = (
            bool(work.res.website)
            and not work.best.is_directory
            and not work.best.is_snippet_only
        )
        if (
            config.guess_emails
            and own_site
            and work.res.confidence >= 40
            and not work.res.priority_emails
            and not work.res.emails
        ):
            domain = extract_domain(work.res.website)
            work.res.guessed_emails = await guess_emails(
                domain,
                set(work.res.emails) | set(work.res.priority_emails),
                fetcher.cache,
            )
            if work.res.guessed_emails:
                work.res.notes.append(f"guessed from MX record of {domain}")
    finally:
        work.res.performance["crawl_ms"] = round(
            (time.perf_counter() - phase_started) * 1000, 2
        )


async def _run_before_deadline(
    work: _CompanyWork, operation: Callable[[], Awaitable[None]]
) -> None:
    remaining = work.deadline_at - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    async with asyncio.timeout(remaining):
        await operation()


def _mark_timed_out(work: _CompanyWork, config: Config) -> None:
    work.timed_out = True
    work.res.performance["timed_out"] = 1
    work.res.notes.append(
        f"timed out after {config.company_timeout:g}s; partial result retained"
    )
    _consolidate_contacts(work)


def _finish_work(work: _CompanyWork) -> Result:
    work.res.elapsed = time.perf_counter() - work.started
    work.res.performance["total_ms"] = round(work.res.elapsed * 1000, 2)
    return work.res



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
    work = _new_company_work(
        query, country, products or [], address, config
    )
    await _emit(on_event, {"type": "company_start", "query": query})
    try:
        await _run_before_deadline(
            work, lambda: _prepare_company(work, config, search)
        )
        if not work.complete:
            await _run_before_deadline(
                work, lambda: _crawl_company(work, config, fetcher)
            )
    except TimeoutError:
        _mark_timed_out(work, config)
    result = _finish_work(work)
    await _emit(
        on_event, {"type": "company_done", "query": query, "result": result}
    )
    return result


async def process_batch(
    rows: list[tuple],
    config: Config,
    on_event: ProgressFn | None = None,
    cache: Cache | None = None,
    store=None,
) -> list[Result]:
    """Run bounded search and crawl stages while preserving input order."""
    if not rows:
        return []

    owns_cache = cache is None
    cache = cache or Cache(config.cache_path, config.cache_ttl, config.use_cache)
    search: SearchClient | None = None
    stage_tasks: list[asyncio.Task] = []
    try:
        search = SearchClient(config, cache)
        total = len(rows)
        results: list[Result | None] = [None] * total
        done = 0
        progress_lock = asyncio.Lock()
        search_worker_count = min(
            total, max(1, min(config.max_threads_companies, search.concurrency))
        )
        crawl_worker_count = min(total, max(1, config.max_threads_companies))
        input_queue: asyncio.Queue = asyncio.Queue()
        crawl_queue: asyncio.Queue = asyncio.Queue(
            maxsize=max(2, crawl_worker_count * 2)
        )
        enqueued_at = asyncio.get_running_loop().time()
        for index, row in enumerate(rows):
            input_queue.put_nowait((index, row, enqueued_at))
        for _ in range(search_worker_count):
            input_queue.put_nowait(None)

        async with Fetcher(config, cache) as fetcher:
            async def finish(
                index: int,
                result: Result,
                company_key: str = "",
                save_company: bool = False,
            ) -> None:
                nonlocal done
                if save_company and company_key:
                    store.save_company(company_key, result)
                results[index] = result
                await _emit(
                    on_event,
                    {
                        "type": "company_done",
                        "query": result.query,
                        "result": result,
                    },
                )
                async with progress_lock:
                    done += 1
                    position = done
                await _emit(
                    on_event,
                    {
                        "type": "progress",
                        "index": index,
                        "done": position,
                        "total": total,
                        "result": result,
                    },
                )

            async def search_worker() -> None:
                while True:
                    item = await input_queue.get()
                    if item is None:
                        return
                    index, row, queued_at = item
                    name, country, products, *extra = row
                    address = extra[0] if extra else None
                    queue_ms = round(
                        (
                            asyncio.get_running_loop().time()
                            - queued_at
                        ) * 1000,
                        2,
                    )
                    key = (
                        dedupe_key(name)
                        if store and config.use_company_store else ""
                    )
                    if key:
                        remembered = store.get_company(
                            key, config.company_ttl
                        )
                        if remembered:
                            result = Result(**remembered)
                            result.notes.append("from company store")
                            result.elapsed = 0.0
                            result.performance = {
                                "company_queue_ms": queue_ms,
                                "company_store_hit": 1,
                                "total_ms": 0.0,
                            }
                            await finish(index, result)
                            continue

                    work = _new_company_work(
                        name, country, products, address, config
                    )
                    work.res.performance["company_queue_ms"] = queue_ms
                    await _emit(
                        on_event, {"type": "company_start", "query": name}
                    )
                    try:
                        await _run_before_deadline(
                            work,
                            lambda: _prepare_company(work, config, search),
                        )
                    except SearchError:
                        raise
                    except TimeoutError:
                        _mark_timed_out(work, config)
                        await finish(index, _finish_work(work))
                    except Exception as exc:
                        work.res.performance["failed"] = 1
                        work.res.notes.append(f"error: {exc}")
                        await finish(index, _finish_work(work))
                    else:
                        if work.complete:
                            await finish(
                                index, _finish_work(work), key, bool(key)
                            )
                        else:
                            await crawl_queue.put(
                                (
                                    index,
                                    work,
                                    key,
                                    asyncio.get_running_loop().time(),
                                )
                            )

            async def crawl_worker() -> None:
                while True:
                    item = await crawl_queue.get()
                    if item is None:
                        return
                    index, work, key, queued_at = item
                    work.res.performance["crawl_queue_ms"] = round(
                        (
                            asyncio.get_running_loop().time()
                            - queued_at
                        ) * 1000,
                        2,
                    )
                    save = bool(key)
                    try:
                        await _run_before_deadline(
                            work,
                            lambda: _crawl_company(work, config, fetcher),
                        )
                    except TimeoutError:
                        _mark_timed_out(work, config)
                        save = False
                    except Exception as exc:
                        work.res.performance["failed"] = 1
                        work.res.notes.append(f"error: {exc}")
                        save = False
                    await finish(
                        index, _finish_work(work), key, save
                    )

            search_tasks = [
                asyncio.create_task(search_worker())
                for _ in range(search_worker_count)
            ]
            crawl_tasks = [
                asyncio.create_task(crawl_worker())
                for _ in range(crawl_worker_count)
            ]
            stage_tasks = search_tasks + crawl_tasks
            try:
                await asyncio.gather(*search_tasks)
                for _ in range(crawl_worker_count):
                    await crawl_queue.put(None)
                await asyncio.gather(*crawl_tasks)
            finally:
                for task in stage_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*stage_tasks, return_exceptions=True)

        return [result for result in results if result is not None]
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
