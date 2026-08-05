import re
from html import unescape
from urllib.parse import urlparse, parse_qs

import phonenumbers
import tldextract

from core.config import (
    BAD_EMAIL_SUFFIXES,
    SKIP_EMAIL_DOMAINS,
    SKIP_EMAIL_PREFIXES,
    WHATSAPP_LINK_HOSTS,
)

# Use the bundled public-suffix snapshot so importing never hits the network.
_extract = tldextract.TLDExtract(suffix_list_urls=())

EMAIL_RE = re.compile(r"[a-z0-9][a-z0-9\.\-+_]{0,63}@[a-z0-9][a-z0-9\.\-]{0,253}\.[a-z]{2,24}", re.I)

# Obfuscated forms: "info [at] acme (dot) com", "info AT acme DOT com".
# At least one separator must be an explicit marker, otherwise ordinary prose
# such as "the site is at ease.production" parses as an address.
_USER = r"([a-z0-9][a-z0-9\.\-+_]{0,63})"
_HOST = r"([a-z0-9][a-z0-9\.\-]{0,253})"
_TLD = r"([a-z]{2,24})\b"
_AT_MARKED = r"\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\})\s*"
_AT_WORD = r"\s+at\s+"
_DOT_ANY = r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+|\.)\s*"
_DOT_MARKED = r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+)\s*"

OBFUSCATED_EMAIL_RES = (
    re.compile(_USER + _AT_MARKED + _HOST + _DOT_ANY + _TLD, re.I),
    re.compile(_USER + _AT_WORD + _HOST + _DOT_MARKED + _TLD, re.I),
)

# Loose candidate matcher; every hit is then validated by `phonenumbers`
PHONE_CANDIDATE_RE = re.compile(r"(\+?\d[\d\s\-\.\(\)]{7,20}\d)")

_WS_RE = re.compile(r"\s+")

# Country name -> CLDR region, for parsing local-format numbers.
COUNTRY_TO_REGION = {
    "india": "IN", "vietnam": "VN", "viet nam": "VN", "china": "CN",
    "united states": "US", "usa": "US", "us": "US", "america": "US",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "britain": "GB",
    "germany": "DE", "france": "FR", "italy": "IT", "spain": "ES",
    "netherlands": "NL", "holland": "NL", "belgium": "BE", "poland": "PL",
    "turkey": "TR", "türkiye": "TR", "russia": "RU", "ukraine": "UA",
    "japan": "JP", "south korea": "KR", "korea": "KR", "taiwan": "TW",
    "thailand": "TH", "indonesia": "ID", "malaysia": "MY", "singapore": "SG",
    "philippines": "PH", "cambodia": "KH", "myanmar": "MM", "laos": "LA",
    "bangladesh": "BD", "pakistan": "PK", "sri lanka": "LK", "nepal": "NP",
    "australia": "AU", "new zealand": "NZ", "canada": "CA", "mexico": "MX",
    "brazil": "BR", "argentina": "AR", "chile": "CL", "colombia": "CO",
    "peru": "PE", "ecuador": "EC", "uae": "AE", "united arab emirates": "AE",
    "saudi arabia": "SA", "qatar": "QA", "kuwait": "KW", "oman": "OM",
    "bahrain": "BH", "israel": "IL", "egypt": "EG", "morocco": "MA",
    "south africa": "ZA", "nigeria": "NG", "kenya": "KE", "ghana": "GH",
    "ethiopia": "ET", "tanzania": "TZ", "uganda": "UG",
    "switzerland": "CH", "austria": "AT", "sweden": "SE", "norway": "NO",
    "denmark": "DK", "finland": "FI", "ireland": "IE", "portugal": "PT",
    "greece": "GR", "czech republic": "CZ", "czechia": "CZ", "romania": "RO",
    "hungary": "HU", "bulgaria": "BG", "croatia": "HR", "serbia": "RS",
    "hong kong": "HK", "macau": "MO",
}

# ccTLD -> region, used as a country signal when scoring candidates
CCTLD_TO_REGION = {
    "in": "IN", "vn": "VN", "cn": "CN", "uk": "GB", "de": "DE", "fr": "FR",
    "it": "IT", "es": "ES", "nl": "NL", "be": "BE", "pl": "PL", "tr": "TR",
    "ru": "RU", "ua": "UA", "jp": "JP", "kr": "KR", "tw": "TW", "th": "TH",
    "id": "ID", "my": "MY", "sg": "SG", "ph": "PH", "bd": "BD", "pk": "PK",
    "lk": "LK", "np": "NP", "au": "AU", "nz": "NZ", "ca": "CA", "mx": "MX",
    "br": "BR", "ar": "AR", "cl": "CL", "co": "CO", "pe": "PE", "ae": "AE",
    "sa": "SA", "qa": "QA", "kw": "KW", "om": "OM", "bh": "BH", "il": "IL",
    "eg": "EG", "ma": "MA", "za": "ZA", "ng": "NG", "ke": "KE", "gh": "GH",
    "ch": "CH", "at": "AT", "se": "SE", "no": "NO", "dk": "DK", "fi": "FI",
    "ie": "IE", "pt": "PT", "gr": "GR", "cz": "CZ", "ro": "RO", "hu": "HU",
    "hk": "HK",
}


def clean_text(text: str) -> str:
    return _WS_RE.sub(" ", unescape(text or "")).strip()


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if url.startswith(("http://", "https://")) else f"https://{url}")
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path or ""
    return f"{parsed.scheme or 'https'}://{netloc}{path}".rstrip("/")


def extract_domain(url: str) -> str:
    """Registrable domain, e.g. https://shop.acme.co.uk/x -> acme.co.uk"""
    parts = _extract(url if "://" in url else f"https://{url}")
    return f"{parts.domain}.{parts.suffix}".strip(".").lower() if parts.suffix else parts.domain.lower()


def domain_core(url_or_domain: str) -> str:
    """The registrable name without the suffix: acme.co.uk -> acme"""
    parts = _extract(url_or_domain if "://" in url_or_domain else f"https://{url_or_domain}")
    return parts.domain.lower()


def url_suffix(url_or_domain: str) -> str:
    parts = _extract(url_or_domain if "://" in url_or_domain else f"https://{url_or_domain}")
    return parts.suffix.lower()


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def is_domain_like(value: str) -> bool:
    value = value.strip().lower()
    if " " in value or "." not in value:
        return False
    if value.startswith(("http://", "https://")):
        return True
    return bool(url_suffix(value))


def region_for_country(country: str | None) -> str | None:
    """Map a country label to a CLDR region code.

    Customs exports use long official forms — "Korea (Republic)", "United
    States of America" — so an exact dictionary lookup is not enough.
    """
    if not country:
        return None
    key = country.strip().lower()
    if len(key) == 2 and key.upper() in phonenumbers.SUPPORTED_REGIONS:
        return key.upper()

    key = re.sub(r"\(.*?\)", " ", key)          # drop "(Republic)"
    key = re.sub(r"[^a-z ]+", " ", key)
    key = " ".join(key.split())
    if not key:
        return None
    if key in COUNTRY_TO_REGION:
        return COUNTRY_TO_REGION[key]

    # Longest known name appearing as whole words: "korea republic" -> KR,
    # "united states of america" -> US (and never "romania" -> "oman").
    best: tuple[int, str] | None = None
    for name, iso in COUNTRY_TO_REGION.items():
        if len(name) < 4:
            continue
        if re.search(rf"\b{re.escape(name)}\b", key) and (best is None or len(name) > best[0]):
            best = (len(name), iso)
    return best[1] if best else None


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


def normalize_phone(value: str, region: str | None = None) -> str | None:
    """Return an E.164 number, or None when the text is not a real phone number.

    Tries the international form first, then falls back to the company's
    country so local-format numbers on the page still parse.
    """
    raw = (value or "").strip()
    if not raw:
        return None

    digits = digits_only(raw)
    if not (7 <= len(digits) <= 15):
        return None
    # Long runs of one digit are placeholders (000 000 0000, 123456789)
    if len(set(digits)) <= 2:
        return None

    for attempt_region in ([None] if raw.startswith("+") else []) + [region, None]:
        try:
            parsed = phonenumbers.parse(raw, attempt_region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return None


def phone_region(e164: str) -> str | None:
    try:
        return phonenumbers.region_code_for_number(phonenumbers.parse(e164, None))
    except phonenumbers.NumberParseException:
        return None


def clean_email(raw: str) -> str | None:
    """Normalise an email and drop the junk our regexes inevitably pick up."""
    if not raw:
        return None
    email = raw.strip().strip(".,;:<>()[]\"'").lower()
    if email.count("@") != 1:
        return None

    prefix, _, domain = email.partition("@")
    if not prefix or not domain or "." not in domain:
        return None
    if email.endswith(BAD_EMAIL_SUFFIXES):
        return None
    if prefix in SKIP_EMAIL_PREFIXES:
        return None
    if domain in SKIP_EMAIL_DOMAINS or extract_domain(domain) in SKIP_EMAIL_DOMAINS:
        return None
    # e.g. "…@2x.png" style asset paths that survived the suffix check
    if not re.fullmatch(r"[a-z]{2,24}", domain.rsplit(".", 1)[-1]):
        return None
    # Hex blobs from minified JS
    if len(prefix) > 40 and re.fullmatch(r"[0-9a-f]+", prefix):
        return None
    return email


def decode_cfemail(hex_str: str) -> str | None:
    """Decode a Cloudflare `data-cfemail` attribute back into an address."""
    try:
        data = bytes.fromhex(hex_str.strip())
    except ValueError:
        return None
    if len(data) < 2:
        return None
    key = data[0]
    return "".join(chr(b ^ key) for b in data[1:])


def extract_whatsapp_number_from_link(url: str, region: str | None = None) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    num = None
    if "wa.me" in host:
        num = parsed.path.lstrip("/").split("/")[0]
    elif "whatsapp.com" in host:
        qs = parse_qs(parsed.query)
        num = (qs.get("phone") or qs.get("number") or [None])[0]
        if not num and parsed.path.startswith("/send"):
            num = None
    if not num or not digits_only(num):
        return None
    candidate = num if num.startswith("+") else f"+{digits_only(num)}"
    return normalize_phone(candidate, region)


def is_whatsapp_link(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h in host for h in WHATSAPP_LINK_HOSTS)
