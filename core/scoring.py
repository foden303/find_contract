"""Relevance scoring for search candidates.

The old code took DuckDuckGo's first hit unconditionally, which is how an
Indian spice trader ended up matched to a dissolved UK company. Everything
here exists to answer one question: is this URL actually *this* company?
"""

import re

from rapidfuzz import fuzz

from core.config import DIRECTORY_HOSTS, SNIPPET_ONLY_HOSTS
from core.models import Candidate
from core.utils import (
    CCTLD_TO_REGION,
    EMAIL_RE,
    domain_core,
    host_of,
    region_for_country,
    url_suffix,
)

# Actual legal forms. Dropping these is always safe: "X Ltd" and "X Limited"
# are the same company, so this set defines a company's *identity*.
LEGAL_TOKENS = {
    "private", "limited", "ltd", "pvt", "plc", "llc", "llp", "inc",
    "incorporated", "corp", "corporation", "company", "co", "gmbh", "ag",
    "bv", "nv", "sa", "srl", "spa", "pte", "sdn", "bhd", "as", "ab", "oy",
    "kft", "sro", "doo", "jsc", "ooo", "pt", "cv", "sarl", "sas", "kg",
    "ltda", "lda", "aps", "oyj", "asa", "sl", "sp", "zoo", "dmcc", "fzc",
    "fze", "fzco", "wll", "sae", "scs", "snc",
    # Frequent misspellings in customs data — treating them as the legal form
    # they clearly are beats trying to catch them with fuzzy matching later.
    "lcc", "llp.", "iuc", "imc",
}

# Descriptor words. Dropping these helps match a name to a domain ("X Trading
# Co" often lives at x.com), but they are NOT safe to drop when deciding
# whether two names are the same company — "FRESHDRINKUS GLOBAL LLC" and
# "FRESHDRINKUS LLC" may be separate legal entities.
DESCRIPTOR_TOKENS = {
    "group", "holdings", "holding", "international", "intl", "enterprises",
    "enterprise", "trading", "traders", "industries", "industry",
    "exports", "export", "imports", "import", "impex", "overseas", "global",
}

# Snippet phrases that mean the record is dead or irrelevant
NEGATIVE_PHRASES = [
    "dissolved", "no longer trading", "struck off", "liquidation",
    "ceased trading", "closed down", "in administration", "deregistered",
]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Blog/news/article URLs — the page talks about a company rather than being it
ARTICLE_PATH_RE = re.compile(
    r"(?:^|/)(?:blog|news|article|articles|post|posts|press|"
    r"press-release|story|stories|magazine|\d{4}/\d{2})(?:[/\-_]|$)"
)


def _merge_initials(tokens: list[str]) -> list[str]:
    """Glue runs of single letters back together: ["b", "v"] -> ["bv"].

    Punctuation stripping turns "B.V." into two tokens, which then miss the
    legal-suffix list and make "NEDSPICE EMEA B.V" look different from
    "NEDSPICE EMEA BV".
    """
    merged: list[str] = []
    run: list[str] = []
    for token in tokens:
        if len(token) == 1:
            run.append(token)
            continue
        if run:
            merged.append("".join(run))
            run = []
        merged.append(token)
    if run:
        merged.append("".join(run))
    return merged


def normalize_company(name: str, strict: bool = False) -> tuple[str, list[str]]:
    """Return (compact form, meaningful tokens) with legal suffixes removed.

    `strict=True` drops legal forms only, keeping descriptors — use it when
    deciding whether two names are the same company. The default also drops
    descriptors, which suits matching a name against a domain.
    """
    drop = LEGAL_TOKENS if strict else (LEGAL_TOKENS | DESCRIPTOR_TOKENS)
    lowered = _NON_ALNUM.sub(" ", (name or "").lower()).strip()
    tokens = _merge_initials([t for t in lowered.split() if t])
    core = [t for t in tokens if t not in drop]
    if not core:
        core = tokens
    return "".join(core), core


def name_similarity(company: str, target: str) -> float:
    """0-100 similarity between a company name and a domain/title string."""
    compact, tokens = normalize_company(company)
    target_compact = _NON_ALNUM.sub("", (target or "").lower())
    if not compact or not target_compact:
        return 0.0

    score = float(fuzz.ratio(compact, target_compact))

    # A shorter distinctive name fully contained in the target still counts,
    # but is discounted so "nandan" does not perfectly match "abhinandan".
    # The target must be of comparable length: "triveni" appearing somewhere
    # inside "indiayellowpagesonline" says nothing about ownership.
    if (
        len(compact) >= 5
        and len(target_compact) >= 5
        and len(target_compact) <= len(compact) * 2.5
    ):
        score = max(score, fuzz.partial_ratio(compact, target_compact) * 0.85)

    # Acronym form: "MGG Foods Private Limited" -> "mgg"
    if len(tokens) >= 2:
        acronym = "".join(t[0] for t in tokens)
        if len(acronym) >= 3 and target_compact.startswith(acronym):
            score = max(score, 78.0)

    return min(score, 100.0)


def score_candidate(
    cand: Candidate,
    company: str,
    country: str | None,
    products: list[str],
    locality: str = "",
) -> Candidate:
    """Fill in `score`, `reasons` and the host flags on a candidate."""
    host = host_of(cand.url)
    cand.is_directory = any(d in host for d in DIRECTORY_HOSTS)
    cand.is_snippet_only = any(d in host for d in SNIPPET_ONLY_HOSTS)

    haystack = f"{cand.title} {cand.snippet}".lower()
    reasons: list[str] = []
    score = 0.0

    # 1. Does the domain look like the company? The strongest single signal.
    dom_sim = name_similarity(company, domain_core(cand.url))
    if dom_sim > 0:
        pts = dom_sim / 100 * 45
        score += pts
        if dom_sim >= 60:
            reasons.append(f"domain~name {dom_sim:.0f}%")

    # 2. Does the page title look like the company? Worth much less when the
    #    domain says otherwise — a supplier's blog post *about* a company
    #    scores high on title and is not that company's site.
    title_sim = name_similarity(company, cand.title)
    if title_sim > 0:
        weight = 14 if dom_sim >= 35 else 5
        score += title_sim / 100 * weight
        if title_sim >= 70 and dom_sim >= 35:
            reasons.append(f"title~name {title_sim:.0f}%")
        elif title_sim >= 70:
            reasons.append(f"title mentions name, unrelated domain")

    # 3. Country agreement
    iso = region_for_country(country)
    if iso:
        suffix = url_suffix(cand.url)
        tld_iso = CCTLD_TO_REGION.get(suffix.split(".")[-1])
        if tld_iso == iso:
            score += 12
            reasons.append(f"ccTLD .{suffix}")
        if country and country.lower() in haystack:
            score += 8
            reasons.append(f"country in text")
        elif tld_iso and tld_iso != iso:
            score -= 10
            reasons.append(f"foreign ccTLD .{suffix}")

    # 3b. City agreement. Much sharper than country: two companies with the
    #     same generic name are separated by where they actually are.
    if locality:
        for token in [t.strip().lower() for t in locality.split(",") if len(t.strip()) > 3]:
            if token in haystack:
                score += 14
                reasons.append(f"city: {token}")
                break

    # 4. Product agreement
    matched = [p for p in products if p and p.strip().lower() in haystack]
    if matched:
        score += min(len(matched), 3) * 5
        reasons.append(f"product: {', '.join(matched[:3])}")

    # 5. Own site vs aggregator
    if cand.is_directory:
        score -= 30
        reasons.append("directory/registry")
    if cand.is_snippet_only:
        score -= 12
        reasons.append("scrape-blocked host")

    path = cand.url.split(host, 1)[-1].strip("/") if host in cand.url else ""
    if not path:
        score += 8
        reasons.append("root domain")
    elif path.count("/") >= 2:
        score -= 4

    if ARTICLE_PATH_RE.search(path.lower()):
        score -= 12
        reasons.append("article/news page")

    # 6. Contact details already visible in the snippet
    if EMAIL_RE.search(cand.snippet or ""):
        score += 5
        reasons.append("email in snippet")

    # 7. Dead or irrelevant records
    for phrase in NEGATIVE_PHRASES:
        if phrase in haystack:
            score -= 25
            reasons.append(f"negative: {phrase}")
            break

    # Matching the country and the product but not the name is how a spice
    # importer gets matched to an airline on a .cn domain. Those signals
    # describe a market, not a company, so they cannot carry a result alone.
    if dom_sim < 25 and title_sim < 50:
        score *= 0.55
        reasons.append("no name evidence")

    cand.score = max(0.0, min(100.0, score))
    cand.reasons = reasons
    return cand


def rank_candidates(
    candidates: list[Candidate],
    company: str,
    country: str | None,
    products: list[str],
    locality: str = "",
) -> list[Candidate]:
    """Score every candidate and return them best-first."""
    scored = [score_candidate(c, company, country, products, locality) for c in candidates]
    scored.sort(key=lambda c: (c.score, not c.is_directory), reverse=True)
    return scored
