import os
from dataclasses import dataclass, field

# Concurrency / timing defaults
DEFAULT_MAX_THREADS_COMPANIES = 5
DEFAULT_MAX_PAGES = 10
DEFAULT_TIMEOUT = 12
DEFAULT_PER_HOST_CONCURRENCY = 4
DEFAULT_CACHE_TTL = 7 * 24 * 3600  # a week

def data_dir() -> str:
    """Where the SQLite files live.

    Defaults to the project directory; a container overrides it with
    FINDER_CACHE_DIR so the data survives on a mounted volume.
    """
    path = os.getenv("FINDER_CACHE_DIR") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    os.makedirs(path, exist_ok=True)
    return path


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# --- Email filtering -------------------------------------------------------

SKIP_EMAIL_PREFIXES = {
    "example", "test", "sample", "yourname", "youremail", "email",
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "user", "username", "name", "your", "abc", "xxx", "someone",
}

# Domains that show up in page source but are never the company's real mailbox
SKIP_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.net", "domain.com", "email.com",
    "yourdomain.com", "sentry.io", "sentry-next.wixpress.com", "wixpress.com",
    "wix.com", "squarespace.com", "godaddy.com", "shopify.com",
    "cloudflare.com", "jquery.com", "w3.org", "schema.org", "gravatar.com",
    "2x.png", "sentry.wixpress.com",
}

# Non-TLD suffixes that mean the regex swallowed part of a filename/asset
BAD_EMAIL_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js",
    ".woff", ".woff2", ".ttf", ".ico", ".mp4", ".pdf",
)

COMMON_EMAIL_PREFIXES = [
    "info", "sales", "contact", "export", "enquiry", "enquiries",
    "office", "admin", "support", "hello", "marketing", "purchase",
]

# Prefixes that indicate a decision-maker / trade contact, in priority order
ROLE_PRIORITY = [
    "purchasing", "purchase", "procurement", "buyer", "sourcing",
    "import", "export", "trade", "sales", "business", "bd",
    "contact", "enquiry", "enquiries", "info", "office",
]

# --- Link discovery --------------------------------------------------------

# Ordered strongest-first: used to rank discovered links, not just filter them
CONTACT_HINTS_STRONG = [
    "contact", "contact-us", "contactus", "reach-us", "get-in-touch",
    "enquiry", "enquiries", "inquiry",
]
CONTACT_HINTS_MEDIUM = [
    "about", "about-us", "team", "our-team", "staff", "company",
    "imprint", "impressum", "kontakt", "office",
]
CONTACT_HINTS_WEAK = [
    "support", "sales", "export", "import", "wholesale", "distribution",
    "distributor", "buyer", "purchase", "purchasing", "procurement",
    "legal", "privacy", "terms",
]
CONTACT_HINTS = CONTACT_HINTS_STRONG + CONTACT_HINTS_MEDIUM + CONTACT_HINTS_WEAK

# Guessed paths, tried only when discovered links do not fill the page budget
GUESS_PATHS = [
    "/contact", "/contact-us", "/about-us", "/about", "/contactus",
    "/company/contact", "/en/contact", "/pages/contact",
]

SOCIAL_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com",
    "twitter.com", "youtube.com", "tiktok.com",
}

WHATSAPP_LINK_HOSTS = ("wa.me", "api.whatsapp.com", "web.whatsapp.com", "whatsapp.com")

# --- Host classification ---------------------------------------------------

# Never useful as a company website; dropped from search results entirely
SKIP_HOSTS = {
    "duckduckgo.com", "google.com", "bing.com", "yahoo.com", "baidu.com",
    "wikipedia.org", "wikimedia.org", "youtube.com", "x.com", "twitter.com",
    "instagram.com", "pinterest.com", "tiktok.com", "reddit.com",
    "quora.com", "medium.com", "blogspot.com", "wordpress.com",
    "archive.org", "scribd.com", "slideshare.net", "issuu.com",
}

# Real info, but the page itself blocks scraping: keep the snippet only
SNIPPET_ONLY_HOSTS = {
    "linkedin.com", "facebook.com", "crunchbase.com", "zoominfo.com",
    "bloomberg.com", "glassdoor.com", "owler.com", "dnb.com",
    "rocketreach.co", "apollo.io", "lusha.com", "signalhire.com",
}

# B2B directories / registries / trade-data aggregators. Usable as a fallback
# but must never outrank the company's own domain.
DIRECTORY_HOSTS = {
    # marketplaces
    "alibaba.com", "indiamart.com", "tradeindia.com", "exportersindia.com",
    "globalsources.com", "made-in-china.com", "ec21.com", "tradekey.com",
    "go4worldbusiness.com", "eworldtrade.com", "dhgate.com", "amazon.com",
    "ebay.com", "etsy.com", "justdial.com", "indiabizforsale.com",
    # registries
    "checkcompany.co.uk", "companieshouse.gov.uk", "opencorporates.com",
    "zaubacorp.com", "tofler.in", "instafinancials.com", "quickcompany.in",
    "thecompanycheck.com", "registrationwala.com", "falconebiz.com",
    "companydetails.in", "indiafilings.com", "bizapedia.com",
    # trade data / lead aggregators
    "importgenius.com", "panjiva.com", "volza.com", "importyeti.com",
    "seair.co.in", "exportgenius.in", "trademo.com", "connect2india.com",
    "cybo.com", "yellowpages.com", "yelp.com", "manta.com", "bizcommunity.com",
    "kompass.com", "europages.com", "sulekha.com", "indiacom.com",
    "tradeatlas.com", "listcompany.org", "listofcompaniesin.com",
    "companies.sg", "sgpbusiness.com", "companylist.org", "b2bmap.com",
    "exportersindia.in", "tradeford.com", "21food.com", "foodbev.com",
    "importinfo.com", "infobel.com", "wandoo.io", "dnb.co.in",
    "corporatedir.com", "companywall.com", "bizdirlib.com", "opengovsg.com",
    "spicesboard.in", "52wmb.com", "info-clipper.com", "clickedindia.net",
    "indiantradebird.com", "eximpulse.com", "jimtrade.com", "tradewheel.com",
}


# --- Search providers ------------------------------------------------------

# How many searches may be in flight at once, per provider. DuckDuckGo is
# scraped rather than API-served and throttles hard; a self-hosted SearXNG is
# limited only by your own machine and the engines it federates to.
SEARCH_CONCURRENCY = {
    "ddg": 6,
    "searxng": 16,
    "brave": 10,
    "serper": 10,
}

SEARCH_PROVIDERS = tuple(SEARCH_CONCURRENCY)


@dataclass
class Config:
    """Runtime knobs shared by the CLI and the web UI."""

    max_threads_companies: int = DEFAULT_MAX_THREADS_COMPANIES
    max_pages: int = DEFAULT_MAX_PAGES
    timeout: int = DEFAULT_TIMEOUT
    delay: float = 0.0
    per_host_concurrency: int = DEFAULT_PER_HOST_CONCURRENCY
    top_results: int = 3
    json_out: bool = False

    use_cache: bool = True
    cache_ttl: int = DEFAULT_CACHE_TTL
    cache_path: str = field(default_factory=lambda: os.path.join(data_dir(), ".cache.db"))

    # Skip TLS certificate verification. Off by default. Turn it on only on a
    # machine behind a TLS-inspecting proxy, where every request otherwise
    # fails with "self-signed certificate in certificate chain" and an empty
    # result is indistinguishable from a company with no web presence.
    # Prefer pointing SSL_CERT_FILE at the proxy's CA bundle where you can.
    insecure_tls: bool = field(
        default_factory=lambda: os.getenv("FINDER_INSECURE_TLS", "").lower()
        in ("1", "true", "yes")
    )

    # Which search backend to use. "ddg" needs nothing; "searxng" points at a
    # self-hosted instance (no API key, no per-query cost, and it federates
    # several engines so recall is better); "brave"/"serper" need an API key.
    search_provider: str = field(
        default_factory=lambda: os.getenv("FINDER_SEARCH_PROVIDER", "ddg").lower()
    )
    # Base URL of the SearXNG instance. Its settings.yml must enable the JSON
    # output format — `search: formats: [html, json]` — which is off by default.
    searxng_url: str = field(
        default_factory=lambda: os.getenv("FINDER_SEARXNG_URL", "http://127.0.0.1:8080")
    )
    search_api_key: str | None = field(
        default_factory=lambda: os.getenv("FINDER_SEARCH_API_KEY")
    )

    # How long a stored company result stays usable before it is looked up
    # again. Contact details drift slowly; three months is a sane refresh.
    company_ttl: int = 90 * 24 * 3600
    use_company_store: bool = True

    # Scan every candidate above min_score and merge their contacts, instead of
    # stopping at the first one that yields something. Slower, but it collects
    # from a company's main site, its regional site and its trade profile
    # together rather than whichever happened to rank first.
    merge_sources: bool = False

    # Stop scanning a site once we hold a priority email and a valid phone
    early_exit: bool = True
    # Generate info@/sales@… and keep the ones whose domain has an MX record
    guess_emails: bool = True
    # Minimum relevance score for a candidate to be scraped at all (0-100).
    # Below this the "match" is usually a directory listing or an article that
    # merely mentions the company, and its contacts belong to someone else —
    # an empty row is worth more than a wrong one in a lead list.
    min_score: float = 30.0
