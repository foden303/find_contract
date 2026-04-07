import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from ddgs import DDGS
from config import SKIP_SEARCH_HOSTS, SNIPPET_ONLY_HOSTS, Config
from utils import normalize_url, extract_domain, EMAIL_RE, PHONE_RE, normalize_phone, digits_only
from models import Result
from scraper import scan_company


def build_search_queries(company: str, country: str | None, products: list[str]) -> list[str]:
    queries = [company]
    prod_str = ", ".join(products) if products else ""

    if country:
        queries.append(f"{company} {country}")
    if prod_str:
        queries.append(f"{company} {prod_str}")
    if country and prod_str:
        queries.append(f"{company} {country} {prod_str}")

    # If multiple products, try adding each one individually for broader discovery
    if len(products) > 1:
        for p in products[:3]:  # Limit to first 3 to avoid excessive searching
            queries.append(f"{company} {p}")
            if country:
                queries.append(f"{company} {country} {p}")

    return queries


def ddg_search_all(query: str, limit: int = 15) -> list[tuple[str, str, str]]:
    """Returns list of (title, href, snippet) from DuckDuckGo."""
    results = []
    try:
        with DDGS() as ddgs:
            ddgs_gen = ddgs.text(query, max_results=limit)
            if not ddgs_gen:
                return []

            for r in ddgs_gen:
                title = r.get("title", "")
                href = r.get("href", "")
                snippet = r.get("body", "")

                if not href:
                    continue

                parsed = urlparse(href)
                host = parsed.netloc.lower()
                
                # Global skip list (search engines, wiki, etc)
                if any(bad in host for bad in SKIP_SEARCH_HOSTS):
                    continue

                results.append((title, normalize_url(href), snippet))
                if len(results) >= limit:
                    break
    except Exception as e:
        print(f"[!] Search error for '{query}': {e}")

    return results


def extract_contacts_from_snippet(snippet: str) -> tuple[set[str], set[str]]:
    """Quickly extract emails and phones from a text snippet."""
    emails = set(EMAIL_RE.findall(snippet))
    phones = set()
    for p in PHONE_RE.findall(snippet):
        norm = normalize_phone(p)
        if len(digits_only(norm)) >= 8:
            phones.add(norm)
    return emails, phones


def ddg_search_top_websites(company: str, country: str | None = None, products: list[str] = [], limit: int = 1) -> list[tuple[str, str]]:
    """Returns a list of (url, snippet) for the top matching results."""
    queries = build_search_queries(company, country, products)
    all_results = []
    seen_urls = set()

    for query in queries:
        found = ddg_search_all(query, limit=limit * 2) # Get more to account for skips
        for _, url, snippet in found:
            if url not in seen_urls:
                all_results.append((url, snippet))
                seen_urls.add(url)
            if len(all_results) >= limit:
                break
        if len(all_results) >= limit:
            break

    return all_results[:limit]


def ddg_search_first_website(company: str, country: str | None = None, products: list[str] = []) -> tuple[str | None, str | None]:
    """Returns (website_url, search_snippet) for the first matching result."""
    queries = build_search_queries(company, country, products)
    for query in queries:
        results = ddg_search_all(query, limit=1)
        if results:
            return results[0][1], results[0][2]
    return None, None


def process_batch(rows: list[tuple[str, str | None, list[str]]], config: Config, top_results: int = 1) -> list[Result]:
    """Processes a batch of companies in parallel."""
    results = []
    total = len(rows)
    print(f"[*] Starting parallel batch processing of {total} companies (scanning top {top_results} websites per company)...")

    with ThreadPoolExecutor(max_workers=config.max_threads_companies) as executor:
        future_to_row = {
            executor.submit(
                scan_company,
                name,
                country=country,
                products=prods,
                max_pages=config.max_pages,
                delay=config.delay,
                top_results=top_results
            ): (name, country, prods)
            for name, country, prods in rows
        }

        completed = 0
        for future in as_completed(future_to_row):
            completed += 1
            try:
                res = future.result()
                results.append(res)
                print(
                    f"[{completed}/{total}] {res.company or 'Unknown'} -> {len(res.emails)} emails, {len(res.phones)} phones")
            except Exception as e:
                row = future_to_row[future]
                print(f"[!] Error processing {row[0]}: {e}")

    return results


def print_human(res: Result):
    print("\n" + "="*50)
    print(f"COMPANY: {res.company or '???'}")
    print(f"COUNTRY: {res.country or '???'}")
    print(f"PRODUCTS: {', '.join(res.products)}")
    print(f"WEBSITE: {res.website or 'Not Found'}")
    print("-" * 50)
    if res.priority_emails:
        print(f"PRIORITY EMAILS: {', '.join(res.priority_emails)}")
    if res.emails:
        print(f"ALL EMAILS: {', '.join(res.emails)}")
    if res.phones:
        print(f"PHONES: {', '.join(res.phones)}")
    if res.whatsapp_numbers:
        print(f"WHATSAPP: {', '.join(res.whatsapp_numbers)}")
    if res.whatsapp_verified:
        print(f"WHATSAPP VERIFIED: {', '.join(res.whatsapp_verified)}")
    # status is not in the model anymore
    print("="*50 + "\n")


def discover_trade_leads(keyword: str, region: str = "", limit: int = 10) -> list[tuple[str, str, str]]:
    """Discovers new companies based on keywords."""
    # Handle comma-separated keywords for discovery too
    keywords = [k.strip() for k in keyword.split(",") if k.strip()]

    all_leads = []
    seen_domains = set()

    for kw in keywords:
        queries = [
            f'"{kw}" importers {region}',
            f'"{kw}" exporters {region}',
            f'"{kw}" wholesalers {region}',
            f'"{kw}" trading company {region}',
        ]

        for q in queries:
            found = ddg_search_all(q, limit=limit)
            for title, url, snippet in found:
                domain = extract_domain(url)
                if domain not in seen_domains:
                    all_leads.append((title, url, snippet))
                    seen_domains.add(domain)
            if len(all_leads) >= limit:
                break
        if len(all_leads) >= limit:
            break

    return all_leads[:limit]
