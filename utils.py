import re
import os
from html import unescape
from urllib.parse import urlparse
from config import WHATSAPP_HINTS

# Regex Patterns
EMAIL_RE = re.compile(r"[a-z0-9\.\-+_]+@[a-z0-9\.\-+_]+\.[a-z]{2,8}", re.I)
PHONE_RE = re.compile(r"(\+?\d[\d\s\-\.\(\)]{8,20}\d)")

def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text or "")).strip()

def normalize_url(url: str) -> str:
    parsed = urlparse(url if url.startswith(("http://", "https://")) else f"https://{url}")
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path or ""
    return f"{scheme}://{netloc}{path}".rstrip("/")

def extract_domain(url: str) -> str:
    return urlparse(normalize_url(url)).netloc

def is_domain_like(value: str) -> bool:
    value = value.strip().lower()
    return "." in value and " " not in value and len(value.split(".")) >= 2

def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)

def normalize_phone(value: str) -> str:
    raw = value.strip()
    prefix = "+" if raw.startswith("+") else ""
    digits = digits_only(raw)
    return prefix + digits if digits else raw

def validate_whatsapp_status(phone: str, website_html: str = "") -> bool:
    """
    Check if a number has a high probability of being on WhatsApp.
    """
    digits = digits_only(phone)
    if not digits or len(digits) < 10:
        return False
    
    if website_html:
        if digits in website_html or phone in website_html:
            if any(hint in website_html.lower() for hint in WHATSAPP_HINTS):
                return True
    
    return phone.startswith("+") or digits.startswith(("84", "91", "1", "44"))

def extract_whatsapp_number_from_link(url: str) -> str | None:
    parsed = urlparse(url)
    if "wa.me" in parsed.netloc:
        num = parsed.path.lstrip("/")
        return normalize_phone(num) if num else None
    if "api.whatsapp.com" in parsed.netloc:
        query = dict([q.split("=") for q in parsed.query.split("&") if "=" in q])
        num = query.get("phone")
        return normalize_phone(num) if num else None
    return None
