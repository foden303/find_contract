import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from config import CONTACT_HINTS, SOCIAL_DOMAINS, WHATSAPP_HINTS, SKIP_EMAIL_PREFIXES
from models import ContactExtract, Result
from utils import clean_text, normalize_url, normalize_phone, extract_whatsapp_number_from_link, EMAIL_RE, PHONE_RE, digits_only

# Shared Session for better cookie handling and performance
session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
})


def fetch(url: str, timeout: int = 15) -> tuple[str | None, str | None]:
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        if "text/html" not in resp.headers.get("Content-Type", ""):
            return resp.url, None
        return resp.url, resp.text
    except requests.RequestException:
        return None, None


def collect_candidate_pages(base_url: str, html: str, max_pages: int) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base_domain = urlparse(base_url).netloc.lower()

    candidates: list[str] = [base_url]
    seen = {base_url}

    common_paths = [
        "/contact", "/contact-us", "/about", "/about-us", "/team",
        "/our-team", "/company", "/support", "/sales", "/export",
        "/import", "/wholesale", "/distributors", "/procurement",
    ]
    for path in common_paths:
        full = urljoin(base_url + "/", path.lstrip("/"))
        if full not in seen:
            candidates.append(full)
            seen.add(full)

    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        text = clean_text(a.get_text(" ", strip=True)).lower()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full = urljoin(base_url + "/", href)
        parsed = urlparse(full)
        if parsed.netloc.lower() != base_domain:
            continue
        hay = (full + " " + text).lower()
        if any(hint in hay for hint in CONTACT_HINTS):
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip(
                "/")
            if normalized not in seen:
                candidates.append(normalized)
                seen.add(normalized)
        if len(candidates) >= max_pages:
            break

    return candidates[:max_pages]


def extract_contacts_from_html(html: str, page_url: str) -> ContactExtract:
    soup = BeautifulSoup(html, "lxml")
    text = clean_text(soup.get_text(" ", strip=True))
    data = ContactExtract()

    for email in EMAIL_RE.findall(text):
        email = email.lower()
        prefix = email.split("@")[0]
        if prefix not in SKIP_EMAIL_PREFIXES and not email.endswith((".png", ".jpg", ".jpeg")):
            data.emails.add(email)

    for phone in PHONE_RE.findall(text):
        norm = normalize_phone(phone)
        if len(digits_only(norm)) >= 8:
            data.phones.add(norm)

    lower_text = text.lower()

    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue

        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip().lower()
            if email and email.split("@")[0] not in SKIP_EMAIL_PREFIXES:
                data.emails.add(email)
            continue

        if href.startswith("tel:"):
            phone = normalize_phone(href.replace("tel:", "").strip())
            if len(digits_only(phone)) >= 8:
                data.phones.add(phone)
            continue

        full = urljoin(page_url, href)
        host = urlparse(full).netloc.lower()

        if any(sd in host for sd in SOCIAL_DOMAINS):
            data.social_links.add(full)

        if any(h in full.lower() for h in WHATSAPP_HINTS):
            data.whatsapp_links.add(full)
            wa_num = extract_whatsapp_number_from_link(full)
            if wa_num:
                data.whatsapp_numbers.add(wa_num)

    if "whatsapp" in lower_text:
        for phone in PHONE_RE.findall(text):
            norm = normalize_phone(phone)
            if len(digits_only(norm)) >= 8:
                data.whatsapp_numbers.add(norm)

    return data


def prioritize_emails(emails: list[str]) -> tuple[list[str], list[str]]:
    from config import ROLE_PRIORITY
    priority = []
    others = []
    for email in emails:
        prefix = email.split("@")[0].lower()
        if any(role in prefix for role in ROLE_PRIORITY):
            priority.append(email)
        else:
            others.append(email)
    return priority, others


def scan_company(
    query: str,
    country: str | None = None,
    products: list[str] = [],
    max_pages: int = 5,
    delay: float = 0.5,
    top_results: int = 1
) -> Result:
    # Avoid circular import if needed
    from search_engine import ddg_search_top_websites, extract_contacts_from_snippet
    from config import SNIPPET_ONLY_HOSTS
    from utils import is_domain_like, validate_whatsapp_status
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    res = Result(query=query, country=country, products=products)
    all_extracted = ContactExtract()

    # 1. Determine Websites
    if is_domain_like(query):
        candidates = [
            (f"https://{query}" if not query.startswith("http") else query, "")]
        res.company = query.split(".")[0].capitalize()
    else:
        candidates = ddg_search_top_websites(
            query, country, products, limit=top_results)
        res.company = query

    if not candidates:
        return res

    for url, snippet in candidates:
        if not res.website:
            res.website = url

        # 1.1 Extract from snippet
        s_emails, s_phones = extract_contacts_from_snippet(snippet)
        all_extracted.emails.update(s_emails)
        all_extracted.phones.update(s_phones)
        if snippet:
            res.notes.append(f"Snippet ({url}): {snippet}")

        # 1.2 Check if we should scrape deeply
        host = urlparse(url).netloc.lower()
        if any(h in host for h in SNIPPET_ONLY_HOSTS):
            continue

        # 2. Fetch Homepage
        final_url, html = fetch(url)
        if not html:
            continue

        # Update primary website if it was redirected
        if url == res.website:
            res.website = final_url

        # 3. Discover Sub-pages
        pages = collect_candidate_pages(final_url, html, max_pages)
        res.pages_scanned.extend(pages)

        # 4. Scan Pages in Parallel
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(fetch, p) for p in pages]
            for fut in as_completed(futures):
                p_url, p_html = fut.result()
                if p_html:
                    ext = extract_contacts_from_html(p_html, p_url)
                    all_extracted.emails.update(ext.emails)
                    all_extracted.phones.update(ext.phones)
                    all_extracted.social_links.update(ext.social_links)
                    all_extracted.whatsapp_links.update(ext.whatsapp_links)
                    all_extracted.whatsapp_numbers.update(ext.whatsapp_numbers)
        
        # Soft validation of WhatsApp status against the main HTML content
        for phone in list(all_extracted.phones):
            if validate_whatsapp_status(phone, html):
                res.whatsapp_verified.append(phone)

    # 5. Post-Process
    res.emails = sorted(list(all_extracted.emails))
    res.phones = sorted(list(all_extracted.phones))
    res.social_links = sorted(list(all_extracted.social_links))
    res.whatsapp_links = sorted(list(all_extracted.whatsapp_links))
    res.whatsapp_numbers = sorted(list(all_extracted.whatsapp_numbers))

    # (Verification already done during scanning)
    res.whatsapp_verified = sorted(list(set(res.whatsapp_verified)))

    # Separate priority emails
    res.priority_emails, res.emails = prioritize_emails(res.emails)

    return res
