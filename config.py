import os
from dataclasses import dataclass

# Configuration Defaults
DEFAULT_MAX_THREADS_COMPANIES = 5
DEFAULT_MAX_THREADS_PAGES = 5
DEFAULT_TIMEOUT = 10

SKIP_EMAIL_PREFIXES = {
    "example", "test", "sample", "yourname", "youremail",
    "noreply", "no-reply", "donotreply", "donotreply",
}

CONTACT_HINTS = [
    "contact", "about", "team", "staff", "company", "support",
    "sales", "office", "imprint", "privacy", "export", "import",
    "wholesale", "distribution", "distributor", "buyer", "purchase",
    "purchasing", "procurement",
]

COMMON_EMAIL_PREFIXES = [
    "info", "sales", "hello", "contact", "support", "office",
    "admin", "export", "import", "purchase", "purchasing",
    "procurement", "business",
]

ROLE_PRIORITY = [
    "purchasing", "purchase", "procurement", "buyer", "import",
    "sourcing", "sales", "export", "trade", "contact", "info",
]

SOCIAL_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com",
    "twitter.com", "youtube.com",
}

WHATSAPP_HINTS = ["whatsapp", "wa.me", "api.whatsapp.com"]

# Hosts that provide no useful company info or are too large/generic
SKIP_SEARCH_HOSTS = {
    "duckduckgo.com", "wikipedia.org", "youtube.com", "x.com", "twitter.com",
    "instagram.com", "pinterest.com", "tiktok.com",
}

# Hosts where we only extract info from DuckDuckGo snippets (difficult to scrape directly)
SNIPPET_ONLY_HOSTS = {
    "linkedin.com", "facebook.com", "indiamart.com", "tradeindia.com",
    "alibaba.com", "globalsources.com", "amazon.com", "ebay.com",
    "yellowpages.com", "yelp.com", "crunchbase.com", "zoominfo.com",
}

@dataclass
class Config:
    max_threads_companies: int = DEFAULT_MAX_THREADS_COMPANIES
    max_threads_pages: int = DEFAULT_MAX_THREADS_PAGES
    timeout: int = DEFAULT_TIMEOUT
    max_pages: int = 10
    delay: float = 0.5
    json_out: bool = False
    hunter_api_key: str | None = os.getenv("HUNTER_API_KEY")
