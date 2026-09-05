#!/usr/bin/env python3
import argparse
import asyncio
import csv
import json
import os
import sys
import time
from dataclasses import asdict

from core.cache import Cache
from core.config import (
    DEFAULT_MAX_PAGES,
    DEFAULT_MAX_THREADS_COMPANIES,
    Config,
)
from core.models import Result
from core.paths import data_dir
from core.pipeline import discover_trade_leads, process_batch
from core.store import Store
from core.tabular import build_jobs, load_table

TABLE_EXTS = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls")

CSV_FIELDS = [
    "query", "company", "country", "products", "website", "confidence",
    "match_reason", "priority_emails", "emails", "guessed_emails",
    "phones", "whatsapp_numbers", "whatsapp_links", "social_links",
    "address", "address_confirmed", "pages_scanned", "sources",
    "alternates", "elapsed_sec", "notes",
]


def parse_args(argv: list[str]):
    p = argparse.ArgumentParser(description="Global B2B Contact Finder")
    p.add_argument("input_value", help="Company name, domain, or path to a .csv/.xlsx file")
    p.add_argument("--keyword", action="store_true", help="Discovery mode using product keywords")
    p.add_argument("--country", help="Country of the target companies")
    p.add_argument("--product", help="Product filter (comma-separated)")
    p.add_argument("--address", help="Postal address of the target company")
    p.add_argument("--limit", type=int, default=10, help="Max leads in discovery mode")
    p.add_argument("--threads", type=int, default=DEFAULT_MAX_THREADS_COMPANIES,
                   help="Companies scanned concurrently")
    p.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                   help="Max sub-pages to scan per site")
    p.add_argument("--top-results", type=int, default=3,
                   help="Max search candidates to scan per company")
    p.add_argument("--delay", type=float, default=0.0,
                   help="Minimum seconds between requests to the same host")
    p.add_argument("--min-score", type=float, default=30.0,
                   help="Minimum relevance score (0-100) for a candidate to be scanned")
    p.add_argument("--no-cache", action="store_true", help="Bypass the local SQLite cache")
    p.add_argument("--insecure-tls", action="store_true",
                   help="Skip TLS verification (only behind a TLS-inspecting proxy)")
    p.add_argument("--no-guess", action="store_true", help="Do not generate info@/sales@ fallbacks")
    p.add_argument("--no-early-exit", action="store_true",
                   help="Scan every page even after contacts are found")
    p.add_argument("--merge-sources", action="store_true",
                   help="Scan every candidate above --min-score and merge their contacts, "
                        "instead of stopping at the first site that yields something")
    p.add_argument("--out", help="Output CSV file path")
    p.add_argument("--json", action="store_true", help="Print results as JSON")
    p.add_argument("--sheet", help="Worksheet name (Excel files with several sheets)")
    p.add_argument("--no-dedupe", action="store_true",
                   help="Keep one job per row instead of one per unique company")
    p.add_argument("--limit-rows", action="store_true",
                   help="Apply --limit to spreadsheet batches too")
    p.add_argument("--csv-column", default="company", help="Column holding the company/domain")
    p.add_argument("--country-column", default="country", help="Column holding the country")
    p.add_argument("--product-column", default="product", help="Column holding the product")
    p.add_argument("--address-column", default="address", help="Column holding the address")
    p.add_argument("--fuzzy-dedupe", action="store_true",
                   help="Also merge near-identical company names (risky, off by default)")
    p.add_argument("--no-company-store", action="store_true",
                   help="Do not reuse or record results in the company table")
    p.add_argument("--search-provider", default=None,
                   choices=["ddg", "searxng", "brave", "serper"],
                   help="Search backend (default ddg; searxng needs no API key)")
    p.add_argument("--searxng-url", default=None,
                   help="Base URL of your SearXNG instance")
    return p.parse_args(argv)


def write_csv(results: list[Result], out_path: str) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "query": r.query,
                "company": r.company or "",
                "country": r.country or "",
                "products": ", ".join(r.products),
                "website": r.website or "",
                "confidence": f"{r.confidence:.1f}",
                "match_reason": "; ".join(r.match_reason),
                "priority_emails": " | ".join(r.priority_emails),
                "emails": " | ".join(r.emails),
                "guessed_emails": " | ".join(r.guessed_emails),
                "phones": " | ".join(r.phones),
                "whatsapp_numbers": " | ".join(r.whatsapp_numbers),
                "whatsapp_links": " | ".join(r.whatsapp_links),
                "social_links": " | ".join(r.social_links),
                "address": r.address or "",
                "address_confirmed": "yes" if r.address_confirmed else "",
                "pages_scanned": " | ".join(r.pages_scanned),
                "sources": " | ".join(r.sources),
                "alternates": " | ".join(r.alternates),
                "elapsed_sec": f"{r.elapsed:.1f}",
                "notes": " | ".join(r.notes),
            })


def print_human(res: Result) -> None:
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"COMPANY   : {res.company or '???'}")
    print(f"COUNTRY   : {res.country or '-'}")
    if res.products:
        print(f"PRODUCTS  : {', '.join(res.products)}")
    print(f"WEBSITE   : {res.website or 'Not found'}")
    print(f"CONFIDENCE: {res.confidence:.0f}/100"
          + (f"  ({'; '.join(res.match_reason)})" if res.match_reason else ""))
    print("-" * 60)
    if res.priority_emails:
        print(f"KEY EMAILS: {', '.join(res.priority_emails)}")
    if res.emails:
        print(f"EMAILS    : {', '.join(res.emails)}")
    if res.guessed_emails:
        print(f"GUESSED   : {', '.join(res.guessed_emails)}  (MX verified, not confirmed)")
    if res.phones:
        print(f"PHONES    : {', '.join(res.phones)}")
    if res.whatsapp_numbers:
        print(f"WHATSAPP  : {', '.join(res.whatsapp_numbers)}")
    if res.social_links:
        print(f"SOCIAL    : {', '.join(res.social_links[:5])}")
    if not res.has_contact and not res.guessed_emails:
        print("No contact details found.")
    print(f"PAGES     : {len(res.pages_scanned)} scanned in {res.elapsed:.1f}s")
    print(bar)


async def run(args) -> list[Result]:
    config = Config(
        max_threads_companies=args.threads,
        max_pages=args.max_pages,
        delay=args.delay,
        top_results=args.top_results,
        min_score=args.min_score,
        use_cache=not args.no_cache,
        insecure_tls=args.insecure_tls,
        use_company_store=not args.no_company_store,
        guess_emails=not args.no_guess,
        early_exit=not args.no_early_exit,
        merge_sources=args.merge_sources,
        json_out=args.json,
    )
    if args.search_provider:
        config.search_provider = args.search_provider
    if args.searxng_url:
        config.searxng_url = args.searxng_url

    cache = Cache(config.cache_path, config.cache_ttl, config.use_cache)
    store = None
    try:
        store = Store(os.path.join(data_dir(), ".runs.db")) if config.use_company_store else None
        cli_products = [p.strip() for p in args.product.split(",") if p.strip()] if args.product else []

        def on_event(event: dict) -> None:
            if args.json or event.get("type") != "progress":
                return
            r = event["result"]
            contacts = len(r.priority_emails) + len(r.emails)
            print(f"[{event['done']}/{event['total']}] {r.company or r.query} "
                  f"-> conf {r.confidence:.0f}, {contacts} emails, {len(r.phones)} phones "
                  f"({r.elapsed:.1f}s)")

        if args.keyword:
            if not args.json:
                print(f"[*] Discovering companies for '{args.input_value}' "
                      f"in {args.country or 'Global'}...")
            leads = await discover_trade_leads(
                args.input_value, config, region=args.country or "", limit=args.limit, cache=cache
            )
            if not args.json:
                print(f"[*] {len(leads)} candidate companies found. Extracting contacts...")
            rows = [(lead.title or lead.url, args.country, cli_products) for lead in leads]
            return await process_batch(rows, config, on_event=on_event, cache=cache, store=store)

        if args.input_value.lower().endswith(TABLE_EXTS):
            if not os.path.isfile(args.input_value):
                raise SystemExit(f"[!] File not found: {args.input_value}")
            headers, table, _, sheet = load_table(args.input_value, args.sheet)
            jobs = build_jobs(
                headers, table,
                company_col=args.csv_column,
                country_col=args.country_column if args.country_column in headers else None,
                product_col=args.product_column if args.product_column in headers else None,
                address_col=args.address_column if args.address_column in headers else None,
                dedupe=not args.no_dedupe,
                fuzzy=args.fuzzy_dedupe,
                limit=args.limit if args.limit_rows else None,
                product_override=cli_products,
            )
            if not args.json:
                print(f"[*] {sheet}: {len(table)} rows -> {len(jobs)} companies to scan...")
            return await process_batch(
                [j.as_row() for j in jobs], config, on_event=on_event, cache=cache, store=store
            )

        results = await process_batch(
            [(args.input_value, args.country, cli_products, args.address)],
            config, cache=cache, store=store,
        )
        if not args.json:
            for result in results:
                print_human(result)
        return results
    finally:
        cache.close()
        if store is not None:
            store.close()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    results = asyncio.run(run(args))
    elapsed = time.perf_counter() - started

    if args.json:
        print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
    else:
        with_contact = sum(1 for r in results if r.has_contact)
        print(f"\n[+] {with_contact}/{len(results)} companies with contacts "
              f"in {elapsed:.1f}s")

    if args.out:
        write_csv(results, args.out)
        print(f"[+] Saved to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
