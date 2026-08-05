"""Reading raw Excel/CSV exports and turning chosen columns into scan jobs.

Customs and trade exports have one row per shipment, so the same importer
appears dozens of times. Collapsing those into one job per company is the
difference between 1007 lookups and 427.
"""

import csv
import io
import os
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field

MAX_PREVIEW_ROWS = 8
EXCEL_EXTS = (".xlsx", ".xlsm")
CSV_EXTS = (".csv", ".tsv", ".txt")


@dataclass
class SheetInfo:
    name: str
    rows: int
    columns: int


@dataclass
class TablePreview:
    sheets: list[SheetInfo]
    sheet: str
    headers: list[str]
    sample: list[list[str]]
    total_rows: int


@dataclass
class ScanJob:
    company: str
    country: str | None = None
    products: list[str] = field(default_factory=list)
    address: str | None = None
    occurrences: int = 1
    # Every raw spelling this job was built from, so results can be joined
    # back onto the original shipment rows.
    variants: list[str] = field(default_factory=list)

    def as_row(self) -> tuple[str, str | None, list[str], str | None]:
        return (self.company, self.country, self.products, self.address)


class UnsupportedFile(ValueError):
    pass


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


# --- Loading ---------------------------------------------------------------


def _load_csv(path: str) -> tuple[list[str], list[list[str]]]:
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        head = f.read(64_000)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(head, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        rows = [[_cell(c) for c in row] for row in reader]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _load_excel(path: str, sheet: str | None) -> tuple[list[str], list[list[str]], list[SheetInfo], str]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = [
            SheetInfo(name=n, rows=(wb[n].max_row or 0), columns=(wb[n].max_column or 0))
            for n in wb.sheetnames
        ]
        active = sheet if sheet in wb.sheetnames else wb.sheetnames[0]
        ws = wb[active]

        headers: list[str] = []
        body: list[list[str]] = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            cells = [_cell(c) for c in row]
            if i == 0:
                headers = cells
            elif any(cells):
                body.append(cells)
        return headers, body, sheets, active
    finally:
        wb.close()


def load_table(path: str, sheet: str | None = None):
    """Return (headers, rows, sheets, active_sheet) for a spreadsheet or CSV."""
    ext = _ext(path)
    if ext in EXCEL_EXTS:
        return _load_excel(path, sheet)
    if ext in CSV_EXTS:
        headers, rows = _load_csv(path)
        name = os.path.basename(path)
        return headers, rows, [SheetInfo(name=name, rows=len(rows) + 1, columns=len(headers))], name
    if ext == ".xls":
        raise UnsupportedFile(
            "Legacy .xls is not supported — re-save the file as .xlsx and try again."
        )
    raise UnsupportedFile(f"Unsupported file type: {ext or 'unknown'}")


def preview_table(path: str, sheet: str | None = None) -> TablePreview:
    headers, rows, sheets, active = load_table(path, sheet)
    # De-duplicate blank/repeated headers so the UI can key on them safely
    seen: Counter = Counter()
    clean_headers = []
    for i, h in enumerate(headers):
        name = h or f"Column {i + 1}"
        seen[name] += 1
        clean_headers.append(name if seen[name] == 1 else f"{name} ({seen[name]})")

    return TablePreview(
        sheets=sheets,
        sheet=active,
        headers=clean_headers,
        sample=[r[: len(clean_headers)] for r in rows[:MAX_PREVIEW_ROWS]],
        total_rows=len(rows),
    )


# --- Column guessing -------------------------------------------------------

_COMPANY_HINTS = [
    "tên cty nhập khẩu", "tên công ty nhập khẩu", "importer", "buyer",
    "consignee", "customer", "tên cty", "công ty", "company", "cty",
    "supplier", "exporter", "tên khách hàng", "name",
]
_COUNTRY_HINTS = [
    "tên nước nhập khẩu", "nước nhập khẩu", "importer country", "country",
    "quốc gia", "nước", "destination", "nation",
]
_PRODUCT_HINTS = [
    "tên hàng", "mặt hàng", "sản phẩm", "product", "goods",
    "description", "commodity", "item", "hs code",
]
_ADDRESS_HINTS = [
    "địa chỉ cty nhập khẩu", "địa chỉ công ty nhập khẩu", "địa chỉ nhập khẩu",
    "importer address", "consignee address", "buyer address",
    "địa chỉ", "address", "dia chi", "location", "street",
]


def _guess(headers: list[str], hints: list[str]) -> str | None:
    lowered = [(h, h.lower().strip()) for h in headers]
    for hint in hints:  # hints are ordered most- to least-specific
        for original, low in lowered:
            if low == hint:
                return original
        for original, low in lowered:
            if hint in low:
                return original
    return None


def guess_columns(headers: list[str]) -> dict[str, str | None]:
    return {
        "company": _guess(headers, _COMPANY_HINTS),
        "country": _guess(headers, _COUNTRY_HINTS),
        "product": _guess(headers, _PRODUCT_HINTS),
        "address": _guess(headers, _ADDRESS_HINTS),
    }


# --- Company name normalisation --------------------------------------------

# Two normalised names this similar are treated as the same company.
FUZZY_THRESHOLD = 92
# Only compare names sharing this prefix, so large files stay linear-ish
_BLOCK_CHARS = 3


def dedupe_key(name: str) -> str:
    """Normalised identity of a company name.

    Collapses case, whitespace, punctuation and legal suffixes, so
    "MC CORMICK GLOBAL INGREDIENTS LIMITED", "MCCORMICK ... LTD." and
    "McCormick Global Ingredients Limited" all land on one key.
    """
    from core.scoring import normalize_company

    compact, _ = normalize_company(name, strict=True)
    return compact or " ".join((name or "").lower().split())


def cluster_keys(keys: list[str], threshold: int = FUZZY_THRESHOLD) -> dict[str, str]:
    """Map each key to a canonical key, merging near-identical spellings.

    Exact normalisation already handles most of it; this catches the leftovers
    (a dropped letter, a joined word). Keys are blocked by their first few
    characters so this does not become quadratic over the whole file.
    """
    from rapidfuzz import fuzz

    canonical: dict[str, str] = {}
    blocks: dict[str, list[str]] = {}
    # Longest first: the fuller spelling makes the better canonical form
    for key in sorted(set(keys), key=lambda k: (-len(k), k)):
        block = blocks.setdefault(key[:_BLOCK_CHARS], [])
        match = next(
            (seen for seen in block if fuzz.ratio(key, seen) >= threshold), None
        )
        if match:
            canonical[key] = canonical[match]
        else:
            canonical[key] = key
            block.append(key)
    return canonical


# --- Address ----------------------------------------------------------------

# Postcodes sit before the city in some countries (NL "2132 NG Hoofddorp") and
# after it in others, so they are stripped wherever they appear, not just at
# the end of a segment.
_POSTCODE_RES = [
    re.compile(r"\b\d{4}\s?[A-Z]{2}\b"),                        # NL
    re.compile(r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}\b"),    # UK
    re.compile(r"\b\d{5}(?:-\d{4})?\b"),                        # US, DE, VN
    re.compile(r"\b\d{6}\b"),                                   # IN, CN, SG
    re.compile(r"\b\d{4}\b"),                                   # AU, AT, BE…
]


def extract_locality(address: str, country: str | None = None) -> str:
    """Reduce a full postal address to the city (and region) worth searching.

    A whole address is useless as a search term — no page contains it verbatim.
    The city is what disambiguates two companies with the same name.
    """
    from core.utils import region_for_country

    if not address:
        return ""

    # Strip postcodes before anything else, so "London SW1A 1AA" becomes plain
    # "London" and is no longer mistaken for a street line.
    keep: list[str] = []
    for part in re.split(r"[,\n;|]+", address):
        for pattern in _POSTCODE_RES:
            part = pattern.sub(" ", part)
        part = re.sub(r"\s{2,}", " ", part).strip(" ,-.")
        if not part or re.fullmatch(r"[\d\s\-/.]+", part):
            continue
        if region_for_country(part):                   # the country itself
            continue
        if country and part.lower() == country.strip().lower():
            continue
        keep.append(part)

    # Whatever still carries a number at the front of the address is a street
    # line: "Unit 5", "12 Baker Street", "Kruisweg 855".
    while len(keep) > 1 and re.search(r"\d", keep[0]):
        keep = keep[1:]
    if not keep or re.search(r"\d", keep[0]) and len(keep) == 1:
        return ""

    # City then region is the useful tail of most address formats
    return ", ".join(keep[-2:])


# --- Product terms ---------------------------------------------------------

_PRODUCT_SPLIT = re.compile(r"[,;(){}\[\]#/|]")
_PRODUCT_NOISE = re.compile(r"\b(hàng mới|100%|nsx|sp|tên kh|tên khoa học|dùng để|đóng gói)\b", re.I)


def sanitize_product(text: str, max_words: int = 6) -> str:
    """Trim a shipment description down to something worth searching for.

    Customs `Tên hàng` values are full descriptions — "Vỏ quế khô cắt đoạn
    dài <50 cm, chưa qua chế biến (SP thu mua...)". Pasting that into a search
    engine finds nothing, so keep only the leading clause.
    """
    if not text:
        return ""
    first = _PRODUCT_SPLIT.split(text)[0]
    first = _PRODUCT_NOISE.sub(" ", first)
    first = re.sub(r"[<>]+\s*\d*\s*\w*", " ", first)
    words = [w for w in first.split() if w]
    trimmed = " ".join(words[:max_words]).strip(" .-–:")
    return trimmed if len(trimmed) >= 3 else ""


# --- Job building ----------------------------------------------------------


def build_jobs(
    headers: list[str],
    rows: list[list[str]],
    company_col: str,
    country_col: str | None = None,
    product_col: str | None = None,
    address_col: str | None = None,
    dedupe: bool = True,
    fuzzy: bool = False,
    limit: int | None = None,
    max_products: int = 3,
    product_override: list[str] | None = None,
) -> list[ScanJob]:
    """Collapse raw rows into one scan job per company.

    `product_override` replaces the per-row product terms entirely — useful
    when the sheet's description column is in another language and you know
    the English keywords for the whole file.

    Normalisation alone merges "MC CORMICK ... LIMITED", "MCCORMICK ... LTD."
    and "McCormick Global Ingredients Limited" into one lookup.

    `fuzzy` additionally merges near-identical keys by edit distance. It is off
    by default because it cannot be made safe: on real customs data
    "VINAY ENTERPRISES" vs "VINAYAK ENTERPRISES" (different companies) scores
    94, while "FRESHDRINKUS GLOBAL LLC" vs "...LCC" (a typo, same company)
    scores 92 — the wrong merge outranks the right one. Known misspellings are
    handled in LEGAL_TOKENS instead, which is exact.
    """
    if company_col not in headers:
        raise ValueError(f"Column not found: {company_col}")

    ci = headers.index(company_col)
    coi = headers.index(country_col) if country_col in headers else None
    pi = headers.index(product_col) if product_col in headers else None
    ai = headers.index(address_col) if address_col in headers else None

    def at(row: list[str], index: int | None) -> str:
        if index is None or index >= len(row):
            return ""
        return (row[index] or "").strip()

    override = [p.strip() for p in (product_override or []) if p and p.strip()]

    def products_for(raw: str) -> list[str]:
        if override:
            return list(override)
        term = sanitize_product(raw)
        return [term] if term else []

    if not dedupe:
        jobs = []
        for row in rows:
            company = at(row, ci)
            if not company:
                continue
            jobs.append(ScanJob(
                company=company,
                country=at(row, coi) or None,
                products=products_for(at(row, pi)),
                address=at(row, ai) or None,
                variants=[company],
            ))
        return jobs[:limit] if limit else jobs

    # Normalised identity first, then optionally merge near-identical keys.
    raw_keys = {}
    for row in rows:
        company = at(row, ci)
        if company and company not in raw_keys:
            raw_keys[company] = dedupe_key(company)
    canonical = cluster_keys(list(raw_keys.values())) if fuzzy else {}

    grouped: "OrderedDict[str, dict]" = OrderedDict()
    for row in rows:
        company = at(row, ci)
        if not company:
            continue
        key = raw_keys[company]
        key = canonical.get(key, key)
        bucket = grouped.setdefault(
            key,
            {"name": Counter(), "country": Counter(), "products": Counter(),
             "address": Counter(), "count": 0},
        )
        bucket["count"] += 1
        bucket["name"][company] += 1
        country = at(row, coi)
        if country:
            bucket["country"][country] += 1
        product = sanitize_product(at(row, pi))
        if product:
            bucket["products"][product] += 1
        address = at(row, ai)
        if address:
            bucket["address"][address] += 1

    jobs = []
    for bucket in grouped.values():
        # Products are free-text shipment descriptions; the most frequent few
        # are the useful search terms, the rest is noise.
        products = (
            list(override)
            if override
            else [p for p, _ in bucket["products"].most_common(max_products)]
        )
        jobs.append(ScanJob(
            company=bucket["name"].most_common(1)[0][0],
            country=(bucket["country"].most_common(1)[0][0] if bucket["country"] else None),
            products=products,
            address=(bucket["address"].most_common(1)[0][0] if bucket["address"] else None),
            occurrences=bucket["count"],
            variants=[n for n, _ in bucket["name"].most_common()],
        ))

    # Frequent trading partners first — they are the leads worth having
    jobs.sort(key=lambda j: -j.occurrences)
    return jobs[:limit] if limit else jobs


def jobs_to_csv(jobs: list[ScanJob]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["company", "country", "product", "address", "occurrences", "variants"])
    for job in jobs:
        writer.writerow([job.company, job.country or "", ", ".join(job.products),
                         job.address or "", job.occurrences, " | ".join(job.variants)])
    return buf.getvalue()
