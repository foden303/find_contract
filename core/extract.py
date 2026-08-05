"""Contact extraction and in-site link discovery."""

import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

import phonenumbers
from selectolax.parser import HTMLParser

from core.cache import Cache
from core.config import (
    COMMON_EMAIL_PREFIXES,
    CONTACT_HINTS_MEDIUM,
    CONTACT_HINTS_STRONG,
    CONTACT_HINTS_WEAK,
    GUESS_PATHS,
    ROLE_PRIORITY,
    SOCIAL_DOMAINS,
)
from core.models import ContactExtract
from core.utils import (
    OBFUSCATED_EMAIL_RES,
    EMAIL_RE,
    PHONE_CANDIDATE_RE,
    clean_email,
    clean_text,
    decode_cfemail,
    extract_whatsapp_number_from_link,
    is_whatsapp_link,
    normalize_phone,
)

MAILTO_RE = re.compile(r"mailto:([^\"'\s>?&\\]+)", re.I)

# Share buttons look like social links but point back at the page itself
_SHARE_MARKERS = (
    "/sharer", "share.php", "/sharearticle", "share?url=", "share_channel",
    "intent/tweet", "twitter.com/share", "/plugins/", "shareopengraph",
    "/dialog/", "pin/create", "submit?url=", "?share=", "/oauth",
)
_LD_EMAIL_KEYS = {"email", "emails"}
_LD_PHONE_KEYS = {"telephone", "phone", "faxnumber"}


def parse_html(html: str) -> HTMLParser:
    return HTMLParser(html)


def _is_social_profile(url: str) -> bool:
    """Keep real profile links; drop share widgets and bare platform roots."""
    lowered = url.lower()
    if any(marker in lowered for marker in _SHARE_MARKERS):
        return False
    path = urlparse(lowered).path.strip("/")
    # "https://www.facebook.com/" is a footer icon, not the company's page
    return bool(path) and path not in ("home", "login", "signup")


def visible_text(tree: HTMLParser) -> str:
    for node in tree.css("script, style, noscript, template"):
        node.decompose()
    return clean_text(tree.text(separator=" "))


# --- Emails ---------------------------------------------------------------


def _emails_from_jsonld(tree: HTMLParser) -> tuple[set[str], set[str]]:
    """Pull email/telephone out of schema.org blocks, where they are clean."""
    emails: set[str] = set()
    phones: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = key.lower()
                if lowered in _LD_EMAIL_KEYS and isinstance(value, str):
                    emails.add(value.replace("mailto:", ""))
                elif lowered in _LD_PHONE_KEYS and isinstance(value, str):
                    phones.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for script in tree.css('script[type="application/ld+json"]'):
        raw = script.text()
        if not raw:
            continue
        try:
            walk(json.loads(raw))
        except (json.JSONDecodeError, RecursionError):
            continue
    return emails, phones


def _emails_from_cfemail(tree: HTMLParser) -> set[str]:
    """Cloudflare replaces addresses with a hex blob; undo that."""
    found = set()
    for node in tree.css("[data-cfemail]"):
        encoded = node.attributes.get("data-cfemail")
        if encoded:
            decoded = decode_cfemail(encoded)
            if decoded:
                found.add(decoded)
    return found


def _emails_from_obfuscation(text: str) -> set[str]:
    return {
        f"{m.group(1)}@{m.group(2)}.{m.group(3)}"
        for pattern in OBFUSCATED_EMAIL_RES
        for m in pattern.finditer(text)
    }


# --- Phones ---------------------------------------------------------------


def _phones_from_text(text: str, region: str | None) -> set[str]:
    """Use libphonenumber's matcher — far fewer false positives than a regex."""
    phones = set()
    matcher_region = region or "ZZ"  # ZZ only matches full international form
    try:
        for match in phonenumbers.PhoneNumberMatcher(
            text, matcher_region, leniency=phonenumbers.PhoneNumberMatcher.VALID
        ):
            if phonenumbers.is_valid_number(match.number):
                phones.add(
                    phonenumbers.format_number(
                        match.number, phonenumbers.PhoneNumberFormat.E164
                    )
                )
    except Exception:
        pass

    # When the site's country differs from the requested one, the matcher above
    # misses local-format numbers. International-form numbers still get caught.
    if region and matcher_region != "ZZ":
        try:
            for match in phonenumbers.PhoneNumberMatcher(
                text, "ZZ", leniency=phonenumbers.PhoneNumberMatcher.VALID
            ):
                if phonenumbers.is_valid_number(match.number):
                    phones.add(
                        phonenumbers.format_number(
                            match.number, phonenumbers.PhoneNumberFormat.E164
                        )
                    )
        except Exception:
            pass

    # The matcher rejects bare local digit runs ("9416205768" on an Indian
    # site) because they carry no formatting clue. Sweep them up separately;
    # normalize_phone validates each against the country's numbering plan,
    # which rejects most junk — though not an id that happens to land inside
    # a real mobile range.
    if region:
        for candidate in PHONE_CANDIDATE_RE.findall(text):
            normalized = normalize_phone(candidate, region)
            if normalized:
                phones.add(normalized)

    return phones


# --- Main extraction ------------------------------------------------------


def extract_contacts(
    html: str,
    page_url: str,
    region: str | None = None,
    tree: HTMLParser | None = None,
) -> ContactExtract:
    """Extract contacts from a page.

    Pass `tree` to reuse a parse already done for link discovery — note this
    consumes the tree, since building the visible text strips script/style.
    """
    data = ContactExtract()
    if not html:
        return data

    # 1. mailto: straight off the raw source, so addresses inside inline
    #    <script> blocks are not lost when we strip scripts below.
    raw_emails = set(MAILTO_RE.findall(html))

    if tree is None:
        tree = parse_html(html)
    raw_emails |= _emails_from_cfemail(tree)
    ld_emails, ld_phones = _emails_from_jsonld(tree)
    raw_emails |= ld_emails

    # 2. Links, before scripts are stripped
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href:
            continue

        if href.lower().startswith("mailto:"):
            raw_emails.add(href[7:].split("?")[0])
            continue

        if href.lower().startswith("tel:"):
            phone = normalize_phone(href[4:].strip(), region)
            if phone:
                data.phones.add(phone)
            continue

        if href.startswith(("javascript:", "#", "data:")):
            continue

        full = urljoin(page_url, href)
        host = urlparse(full).netloc.lower()

        if any(sd in host for sd in SOCIAL_DOMAINS) and _is_social_profile(full):
            data.social_links.add(full)

        if is_whatsapp_link(full):
            data.whatsapp_links.add(full)
            number = extract_whatsapp_number_from_link(full, region)
            if number:
                data.whatsapp_numbers.add(number)

    # 3. Visible text (this decomposes script/style on the tree)
    text = visible_text(tree)
    raw_emails |= set(EMAIL_RE.findall(text))
    raw_emails |= _emails_from_obfuscation(text)

    for candidate in raw_emails:
        cleaned = clean_email(candidate)
        if cleaned:
            data.emails.add(cleaned)

    data.phones |= _phones_from_text(text, region)
    for raw_phone in ld_phones:
        phone = normalize_phone(raw_phone, region)
        if phone:
            data.phones.add(phone)

    return data


# --- Link discovery -------------------------------------------------------


def _link_weight(url_and_text: str) -> int:
    for hint in CONTACT_HINTS_STRONG:
        if hint in url_and_text:
            return 3
    for hint in CONTACT_HINTS_MEDIUM:
        if hint in url_and_text:
            return 2
    for hint in CONTACT_HINTS_WEAK:
        if hint in url_and_text:
            return 1
    return 0


def discover_contact_pages(
    base_url: str, tree: HTMLParser, max_pages: int
) -> list[str]:
    """Rank same-domain links by how likely they are to hold contact details.

    Links actually present on the page come first; guessed paths only fill
    leftover budget. The previous implementation had this backwards, so real
    contact links were never scanned.
    """
    base_domain = urlparse(base_url).netloc.lower()
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue

        full = urljoin(base_url + "/", href)
        parsed = urlparse(full)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.lower() != base_domain:
            continue

        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        if normalized in seen or normalized == base_url.rstrip("/"):
            continue

        text = clean_text(node.text(strip=True)).lower()
        weight = _link_weight(f"{full} {text}".lower())
        if weight:
            seen.add(normalized)
            scored.append((weight, normalized))

    scored.sort(key=lambda pair: -pair[0])
    pages = [url for _, url in scored][:max_pages]

    # Fill any remaining budget with the usual paths, in case the homepage
    # hides its navigation behind JavaScript.
    if len(pages) < max_pages:
        root = f"{urlparse(base_url).scheme}://{base_domain}"
        for path in GUESS_PATHS:
            if len(pages) >= max_pages:
                break
            guess = root + path
            if guess not in seen and guess != base_url.rstrip("/"):
                seen.add(guess)
                pages.append(guess)

    return pages


# --- Post-processing ------------------------------------------------------


def prioritize_emails(emails: list[str]) -> tuple[list[str], list[str]]:
    """Split into decision-maker addresses and the rest, best role first."""
    priority: list[tuple[int, str]] = []
    others: list[str] = []
    for email in emails:
        prefix = email.split("@")[0].lower()
        rank = next(
            (i for i, role in enumerate(ROLE_PRIORITY) if role in prefix), None
        )
        if rank is None:
            others.append(email)
        else:
            priority.append((rank, email))
    priority.sort()
    return [e for _, e in priority], sorted(others)


def _has_mx_blocking(domain: str) -> bool:
    import dns.resolver

    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 4.0
        resolver.timeout = 2.0
        answers = resolver.resolve(domain, "MX")
        return len(answers) > 0
    except Exception:
        return False


async def domain_has_mx(domain: str, cache: Cache | None = None) -> bool:
    """Does this domain accept mail? Cached, because DNS is the slow part."""
    if not domain:
        return False
    if cache:
        hit = cache.get_mx(domain)
        if hit is not None:
            return hit
    result = await asyncio.to_thread(_has_mx_blocking, domain)
    if cache:
        cache.set_mx(domain, result)
    return result


async def guess_emails(
    domain: str, known: set[str], cache: Cache | None = None
) -> list[str]:
    """Offer info@/sales@… only when the domain actually accepts mail."""
    if not domain or not await domain_has_mx(domain, cache):
        return []
    known_lower = {e.lower() for e in known}
    return [
        candidate
        for prefix in COMMON_EMAIL_PREFIXES
        if (candidate := f"{prefix}@{domain}") not in known_lower
    ]
