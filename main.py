#!/usr/bin/env python3
import argparse
import json
import os
import sys
from dataclasses import asdict

from config import Config, DEFAULT_MAX_THREADS_COMPANIES
from models import Result
from utils import is_domain_like
from scraper import scan_company
from search_engine import discover_trade_leads, process_batch
import csv

def load_companies_from_csv(
    path: str, 
    company_col: str, 
    country_col: str | None = None, 
    product_col: str | None = None
) -> list[tuple[str, str | None, list[str]]]:
    rows = []
    if not os.path.exists(path):
        print(f"[!] File not found: {path}")
        return []

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            company = row.get(company_col)
            if not company:
                continue
            
            country = row.get(country_col) if country_col else None
            
            product_raw = row.get(product_col) if product_col else None
            products = [p.strip() for p in product_raw.split(",")] if product_raw else []
            
            rows.append((company, country, products))
    return rows

def parse_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Global B2B Contact Finder")
    parser.add_argument("input_value", help="Company Name, Domain, or CSV file path")
    parser.add_argument("--keyword", action="store_true", help="Discovery mode using keywords")
    parser.add_argument("--country", help="Filter by country")
    parser.add_argument("--product", help="Filter by product (comma-separated for multiple)")
    parser.add_argument("--limit", type=int, default=10, help="Max outcomes for discovery")
    parser.add_argument("--threads", type=int, default=DEFAULT_MAX_THREADS_COMPANIES, help="Max parallel threads")
    parser.add_argument("--max-pages", type=int, default=5, help="Max sub-pages to scan per company")
    parser.add_argument("--top-results", type=int, default=1, help="Max search results to scan per company")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between requests")
    parser.add_argument("--out", help="Output CSV file path")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--csv-column", default="company", help="Column name for company/domain")
    parser.add_argument("--country-column", default="country", help="Column name for country")
    parser.add_argument("--product-column", default="product", help="Column name for product")
    return parser.parse_args(argv)

def write_csv(results: list[Result], out_path: str) -> None:
    fieldnames = [
        "query", "company", "country", "products", "website",
        "priority_emails", "emails", "hunter_emails", "phones",
        "whatsapp_numbers", "whatsapp_verified", "whatsapp_links",
        "social_links", "pages_scanned", "notes",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "query": r.query,
                "company": r.company,
                "country": r.country or "",
                "products": ", ".join(r.products),
                "website": r.website or "",
                "priority_emails": " | ".join(r.priority_emails),
                "emails": " | ".join(r.emails),
                "hunter_emails": " | ".join(r.hunter_emails),
                "phones": " | ".join(r.phones),
                "whatsapp_numbers": " | ".join(r.whatsapp_numbers),
                "whatsapp_verified": " | ".join(r.whatsapp_verified),
                "whatsapp_links": " | ".join(r.whatsapp_links),
                "social_links": " | ".join(r.social_links),
                "pages_scanned": " | ".join(r.pages_scanned),
                "notes": " | ".join(r.notes),
            })

def main(argv: list[str]) -> int:
    args = parse_args(argv)
    
    # Parse products if provided in CLI
    cli_products = [p.strip() for p in args.product.split(",")] if args.product else []
    
    config = Config(
        max_threads_companies=args.threads,
        max_pages=args.max_pages,
        delay=args.delay,
        json_out=args.json
    )
    
    results: list[Result] = []

    # 1. Discovery Mode (--keyword)
    if args.keyword:
        print(f"[*] Discovering companies for: '{args.input_value}' in {args.country or 'Global'}...")
        leads = discover_trade_leads(args.input_value, region=args.country or "", limit=args.limit)
        print(f"[*] Found {len(leads)} potential companies. Starting contact extraction...")
        
        rows = [(name, args.country, cli_products) for name, url, snippet in leads]
        results = process_batch(rows, config, top_results=args.top_results)

    # 2. Batch CSV Mode
    elif os.path.isfile(args.input_value) and args.input_value.lower().endswith(".csv"):
        rows = load_companies_from_csv(
            args.input_value,
            company_col=args.csv_column,
            country_col=args.country_column,
            product_col=args.product_column,
        )
        results = process_batch(rows, config, top_results=args.top_results)

    # 3. Single Mode
    else:
        result = scan_company(
            args.input_value,
            country=args.country,
            products=cli_products,
            max_pages=config.max_pages,
            delay=config.delay,
            top_results=args.top_results
        )
        results.append(result)
        from search_engine import print_human
        if not args.json:
            print_human(result)

    # Output Handling
    if args.json:
        out_json = [asdict(r) for r in results]
        print(json.dumps(out_json, ensure_ascii=False, indent=2))

    if args.out:
        write_csv(results, args.out)
        print(f"\n[+] SUCCESS: Results saved to {args.out}")

    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
