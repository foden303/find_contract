# Global B2B Contact Finder

Takes a list of company names — typically the importer/exporter column of a
customs or trade export — and finds each one's website, emails, phone numbers,
WhatsApp contacts and social profiles.

Runs entirely on your own machine. No API keys, no accounts, no data leaves the
box except the searches and page fetches themselves.

Every result carries a **confidence score** and the reason behind it, because
the hard part is not finding *a* website but confirming it is *that company's*
website.

---

## Contents

1. [Install](#1-install)
2. [Quick start](#2-quick-start)
3. [Web UI walkthrough](#3-web-ui-walkthrough)
4. [Working with a raw Excel export](#4-working-with-a-raw-excel-export)
5. [Command line reference](#5-command-line-reference)
6. [Understanding the output](#6-understanding-the-output)
7. [How it works](#7-how-it-works)
8. [Where to change things](#8-where-to-change-things)
9. [Troubleshooting](#9-troubleshooting)
10. [Search backends](#10-search-backends)
11. [Company name normalisation](#11-company-name-normalisation)
12. [The company store](#12-the-company-store)
13. [Using the address column](#13-using-the-address-column)
14. [Measured results](#14-measured-results)

---

## 1. Install

### Fastest path

**Linux / macOS**

```bash
./install.sh
```

**Windows** (PowerShell)

```powershell
.\install.ps1
```

The installer picks Docker if it is running, otherwise a local Python venv.
Force either with `./install.sh docker` or `./install.sh native`.

### What each mode gives you

| | Docker | Native venv |
|---|---|---|
| Search backend | self-hosted **SearXNG** (federates Google/Bing/Brave) | DuckDuckGo |
| Parallel searches | 16 | 6 |
| API key | none | none |
| Python needed | no | 3.10+ |
| Start | `make up` | `make web` |

Docker is worth it for large files: DuckDuckGo throttles at about six
concurrent queries, and that ceiling — not the worker count — is what limits a
2000-company run.

### Docker by hand

```bash
./install.sh docker     # generates .env with a SearXNG secret, then builds
make logs               # follow along
make down               # stop
make rebuild            # after changing code
```

Compose starts two containers: the app and SearXNG. The app is published on
`127.0.0.1:8765` only — the UI has **no authentication**, so it must not be
reachable from the network. SearXNG is not published at all; only the app
container talks to it. Uncomment its `ports:` block if you want to search by
hand at `http://127.0.0.1:8080`.

Data (caches, run history, uploads) lives in named volumes and survives a
rebuild. `docker compose down -v` deletes it.

Compose deliberately has **no default** for `SEARXNG_SECRET` — running it
without `.env` fails immediately rather than starting every install with the
same publicly-known secret.

### Native install

- Python 3.10 or newer (uses `str | None` syntax)
- An internet connection
- No API key of any kind

```bash
make install     # same as ./install.sh native
make web
```

That creates `venv/` and installs everything in `requirements.txt`:

| Package | Used for |
|---|---|
| `httpx[http2]` | async HTTP with connection reuse |
| `selectolax` | fast HTML parsing (BeautifulSoup/lxml kept as fallback) |
| `ddgs` | DuckDuckGo search |
| `phonenumbers` | validating and normalising phone numbers per country |
| `rapidfuzz` | company-name ↔ domain similarity |
| `tldextract` | registrable-domain extraction (bundled PSL, no network call) |
| `dnspython` | MX lookups for guessed emails |
| `openpyxl` | reading and writing `.xlsx` |
| `fastapi`, `uvicorn`, `python-multipart` | the local web UI |

---

## 2. Quick start

**Web UI** — the easiest path, and the only one that lets you pick columns
visually:

```bash
make web              # http://127.0.0.1:8765
make web PORT=9000    # if 8765 is taken
```

**Command line** — one company:

```bash
make run QUERY="RIDDHI SIDDHI IMPEX" ARGS='--country India --product cassia'
```

**Command line** — a whole spreadsheet:

```bash
make batch IN=data.xlsx ARGS='--csv-column "Tên Cty nhập khẩu" --country-column "Tên nước nhập khẩu" --product "cinnamon, cassia"'
```

---

## 3. Web UI walkthrough

The interface is in **English and Vietnamese** — toggle `EN`/`VI` in the header,
English by default. Next to it, the 🌗 button cycles the theme through
**auto → light → dark**; auto follows your operating system. Both choices are
remembered in your browser.

**Step 1 — Choose a raw data file.** Drag in a `.xlsx`, `.xlsm` or `.csv`
(up to 64 MB). The file is stored under `.uploads/` and parsed immediately.

**Step 2 — Pick the columns.** The three dropdowns are pre-filled by
auto-detection, which recognises both English and Vietnamese headers
(`Tên Cty nhập khẩu`, `importer`, `buyer`, `consignee`, `company`, …). Only the
company column is required, but supplying country and product measurably
improves accuracy — country drives both the search region and phone-number
validation.

Below the dropdowns:

- **Shared product keywords** — overrides the product column for the whole
  file. See [section 4](#4-working-with-a-raw-excel-export).
- **Limit number of companies** — leave blank for all; useful for a trial run.
- **Merge duplicate companies** — on by default. A customs export has one row
  per shipment, so this is usually the difference between 1007 lookups and 427.

A live line under the button shows `1007 raw rows → 427 companies to look up`
along with the first few names, so you can confirm the mapping before starting.

**Advanced options** (collapsed):

| Option | Default | Meaning |
|---|---|---|
| Parallel workers | 8 | companies scanned at once |
| Pages per site | 10 | max sub-pages crawled per website |
| Candidates per company | 3 | max search hits examined per company |
| Minimum score | 30 | below this a site is recorded but not scraped |
| Delay (seconds) | 0 | minimum gap between requests to the same host |
| Cache | on | reuse the local SQLite cache |
| Guess emails | on | generate `info@`/`sales@` when nothing is found |
| Merge all candidates | off | scrape every qualifying site and merge, instead of stopping at the first that yields something |
| Company store | on | reuse a result already found for the same company |
| Search backend | DuckDuckGo | DuckDuckGo, SearXNG (self-hosted), Brave or Serper |
| Skip TLS check | off | only for TLS-inspecting proxies — see section 9 |

**Step 3 — Results.** Rows stream in as each company finishes. Sort by score,
filter by text, or tick *Only rows with contacts*. The table is paged (25/50/100
or all) so a 400-company run stays readable; changing the filter or page size
returns to page 1, but rows arriving during a live run leave your page alone.
Export to CSV or Excel — the download always contains the **whole** run, not
the page on screen.
**Stop** cancels a run in progress; results already found are kept.

**History** (header button) lists every past run with its status and timing.
*Open* reloads a run's results; *Excel* downloads it directly. History lives in
`.runs.db`.

---

## 4. Working with a raw Excel export

This is the case the tool is built around. A Vietnamese customs export looks
like this:

| Năm | Tháng | MST Cty xuất khẩu | Tên Cty xuất khẩu | **Tên Cty nhập khẩu** | Tên hàng | **Tên nước nhập khẩu** | … |
|---|---|---|---|---|---|---|---|
| 2025 | 6 | 110909581 | CÔNG TY … GREEN LEAF | FRESHDRINKUS LTD | Hộp trà túi lọc… | United Kingdom | |

You want contacts for the **importers** (`Tên Cty nhập khẩu`), so map:

- Company column → `Tên Cty nhập khẩu` (importer company)
- Country column → `Tên nước nhập khẩu` (importer country)
- Product column → `Tên hàng` (goods description)

### Deduplication

1007 shipment rows collapse to 427 unique importers. Jobs are ordered by
shipment count, so your most frequent trading partners are scanned first — if
you set a limit, you get the leads that matter most.

For each company the tool keeps the most common spelling of the name, the most
common country, and the three most common product descriptions.

### Why you usually want the shared keywords field

`Tên hàng` holds full Vietnamese shipment descriptions:

```
Vỏ quế khô cắt đoạn dài <50 cm, chưa qua chế biến (SP thu mua)
```

A foreign importer's website never contains that string, so using it as a
search term actively hurts. The tool already trims descriptions to their leading
clause, but the better move is to type the English trade terms into **shared
product keywords** once:

```
cinnamon, cassia, star anise
```

Those replace the per-row terms for the entire file. On the CLI the equivalent
is `--product "cinnamon, cassia"`.

### Country names

Long official forms are handled: `Korea (Republic)` → `KR`,
`United States of America` → `US`, `Viet Nam` → `VN`. Matching is
whole-word, so `Romania` is never mistaken for `Oman`.

---

## 5. Command line reference

```
python3 main.py <input> [options]
```

`<input>` is a company name, a domain, or a path to a `.csv`/`.xlsx` file.
A path ending in a table extension that does not exist is a hard error — it is
not silently searched for as a company name.

### Modes

| Mode | Trigger | Example |
|---|---|---|
| Single company | any plain string | `main.py "BRAMA PTE LTD" --country Singapore` |
| Single domain | looks like a domain | `main.py bramafoods.com` |
| Batch | path ends `.csv`/`.xlsx`/`.xlsm`/`.tsv` | `main.py data.xlsx --csv-column "Tên Cty nhập khẩu"` |
| Discovery | `--keyword` | `main.py "star anise" --keyword --country India --limit 20` |

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--country TEXT` | — | country of the targets; drives search region and phone validation |
| `--product TEXT` | — | comma-separated product keywords; in batch mode this **overrides** the product column |
| `--threads N` | 5 | companies scanned concurrently |
| `--max-pages N` | 10 | max sub-pages per website |
| `--top-results N` | 3 | max search candidates examined per company |
| `--min-score F` | 30 | minimum relevance score (0–100) for a site to be scraped |
| `--delay F` | 0 | minimum seconds between requests to the same host |
| `--limit N` | 10 | number of leads in `--keyword` mode |
| `--limit-rows` | off | also apply `--limit` to spreadsheet batches |
| `--sheet NAME` | first | worksheet to read from a multi-sheet workbook |
| `--csv-column NAME` | `company` | column holding the company name or domain |
| `--country-column NAME` | `country` | column holding the country |
| `--product-column NAME` | `product` | column holding the product |
| `--no-dedupe` | off | one job per row instead of one per unique company |
| `--no-cache` | off | bypass the SQLite cache entirely |
| `--no-guess` | off | do not generate `info@`/`sales@` fallbacks |
| `--no-early-exit` | off | keep scanning even after contacts are found |
| `--merge-sources` | off | scan every candidate above `--min-score` and merge their contacts |
| `--address TEXT` | — | postal address of the target company (single lookup) |
| `--address-column NAME` | `address` | column holding the address |
| `--fuzzy-dedupe` | off | also merge near-identical names (see the warning below) |
| `--no-company-store` | off | do not reuse or record results in the company table |
| `--search-provider NAME` | `ddg` | `ddg`, `searxng`, `brave` or `serper` |
| `--searxng-url URL` | `http://127.0.0.1:8080` | base URL of your SearXNG instance |
| `--insecure-tls` | off | skip TLS verification (see section 9) |
| `--out PATH` | — | write results to a CSV |
| `--json` | off | print results as JSON instead of a human summary |

### Examples

```bash
# one company, full detail
./venv/bin/python3 main.py "TRIVENI IMPEX PVT LTD" --country India --product "cassia, spices"

# a domain you already know
./venv/bin/python3 main.py nedspice.com

# spreadsheet, top 50 importers only, English keywords, 12 workers
./venv/bin/python3 main.py data.xlsx \
  --csv-column "Tên Cty nhập khẩu" \
  --country-column "Tên nước nhập khẩu" \
  --product "cinnamon, cassia" \
  --limit 50 --limit-rows --threads 12 \
  --out leads.csv

# be polite to a fragile host
./venv/bin/python3 main.py data.xlsx --threads 3 --delay 1.5

# find new prospects rather than looking up known ones
./venv/bin/python3 main.py "star anise" --keyword --country India --limit 20 --out prospects.csv

# machine-readable
./venv/bin/python3 main.py "BRAMA PTE LTD" --country Singapore --json
```

---

## 6. Understanding the output

| Column | Meaning |
|---|---|
| `company`, `country`, `products` | echoed back from the input |
| `website` | the best-scoring candidate |
| `confidence` | 0–100; how sure we are this is that company's site |
| `match_reason` | why it scored that way, e.g. `domain~name 85%; ccTLD .in; root domain` |
| `priority_emails` | role addresses, best first (`purchasing@`, `sales@`, `export@`, `info@`…) |
| `emails` | everything else found on the site |
| `guessed_emails` | generated `info@`/`sales@` patterns, kept only if the domain has an MX record — **never confirmed to exist** |
| `phones` | E.164 format, validated against the country |
| `whatsapp_numbers` | taken from real `wa.me`/`api.whatsapp.com` links only |
| `social_links` | profile URLs; share buttons and bare platform roots are filtered out |
| `pages_scanned` | exactly which URLs were read |
| `address` | the address supplied for this company |
| `address_confirmed` | `yes` when the scanned site names the company's own city |
| `sources` | the sites actually scraped, as `score url` — more than one in merge mode |
| `alternates` | runner-up candidates as `score url`, for manual checking |
| `notes` | snippets used, skipped listing pages, off-domain emails that were dropped |
| `elapsed` | seconds spent on this company |

### Reading confidence

| Range | Interpretation |
|---|---|
| **60+** | the company's own domain; treat as reliable |
| **35–60** | plausible, worth a glance at `match_reason` before use |
| **below 30** | not scraped at all — recorded as a weak lead only |
| **0** | no usable search result |

An empty row with confidence 0 is a deliberate outcome. A wrong contact in a
lead list costs more than a blank one.

---

## 7. How it works

### Pipeline for one company

```
name + country + products
        │
        ├─ 1. build up to 4 search queries        core/search.py
        │      (unquoted — see below)
        ├─ 2. run them in parallel, merge hits    core/search.py
        ├─ 3. score every candidate 0-100         core/scoring.py
        │      drop anything under --min-score
        ├─ 4. for the best candidate:             core/pipeline.py
        │      fetch homepage                     core/fetch.py
        │      parse once, rank internal links    core/extract.py
        │      fetch the best contact pages
        │      extract emails/phones/WhatsApp     core/extract.py
        ├─ 5. filter out third parties' contacts  core/pipeline.py
        └─ 6. if still empty, guess + MX check    core/extract.py
```

### One site or several

By default only the **best** candidate is scraped in full. Runner-ups are
visited only if it yields nothing, and get a third of the page budget. This is
why "Candidates per company: 3" usually results in one site being read — 3 is a
ceiling, not a target.

`--merge-sources` (UI: *Merge all candidates*) changes that: every candidate
above `--min-score` is scraped with the full page budget and their contacts are
pooled. Two extra rules keep it honest:

- Candidates are deduplicated by registrable domain first, so three pages of
  one site count as one source.
- A secondary domain is only merged if its name resembles the company
  (similarity ≥ 45). Listings sites clear the score threshold on country and
  product alone, and scraping one hands back its own support inbox as the
  company's — there are far too many to blacklist by hand. Skipped domains are
  logged in `notes`.

Expect roughly one extra source per company and a longer run. Use it when
coverage matters more than speed.

### Scoring

A candidate URL earns points for domain↔name similarity (the strongest single
signal, up to 45), title↔name similarity, ccTLD and country text agreement,
product keywords in the snippet, and being the site root. It loses points for
being a B2B directory, a scrape-blocked host, a deep path, a blog/news article,
or containing phrases like *dissolved* or *no longer trading*.

Two rules exist because of specific failures observed in testing:

- **A title match without a domain match is worth little.** A packaging
  supplier's blog post titled *"Korea's CK Pharm Co., Ltd. visits Mingke"*
  scored 85% on title alone. It is not CK Pharm's website.
- **Country plus product without any name evidence cannot carry a result.**
  Otherwise a Chinese spice importer matches an airline on a `.cn` domain
  purely for being Chinese.

### Search queries are not quoted

Exact-phrase search on a registry-style name
(`"SOILEX LIFE SCIENCE PRIVATE LIMITED"`) drops the company's own site, which
spells itself differently. Unquoted, DuckDuckGo returns `soilexlifescience.com`
first. This single change was the largest quality improvement in the project.
Precision is scoring's job, not the query's.

### Guards on what gets harvested

- **Off-domain emails** are dropped unless confidence ≥ 45. This is what stops
  a journal article's author addresses, or a directory's own support inbox,
  being reported as the company's contacts.
- **Listing pages** — any single page yielding more than 12 phone numbers is a
  directory of other people's companies, and its entire harvest is discarded.
- **Obfuscation** is decoded where possible: Cloudflare `data-cfemail`,
  `info [at] acme (dot) com`, JSON-LD `schema.org/Organization`, and `mailto:`
  inside inline `<script>`. Plain prose like *"the site is at ease.production"*
  is deliberately not treated as an address.
- **Phones** are validated by `libphonenumber` against the company's country and
  emitted as E.164, so `+917971191237` and `07971191237` collapse to one entry
  and tax IDs are rejected. Bare local digit runs are swept up separately, since
  the strict matcher skips numbers with no formatting clue.

### Caching

`.cache.db` (SQLite, one-week TTL) stores HTTP responses, search results and MX
lookups. A repeat run of the same list is effectively instant, which is what
makes tuning practical. `make clean-cache` drops it; `--no-cache` bypasses it.

Run history is separate, in `.runs.db`.

---

## 8. Where to change things

| You want to… | Edit |
|---|---|
| add a directory/marketplace to downrank | `DIRECTORY_HOSTS` in `core/config.py` |
| block a host outright | `SKIP_HOSTS` in `core/config.py` |
| mark a host as snippet-only (blocks scrapers) | `SNIPPET_ONLY_HOSTS` in `core/config.py` |
| change which email prefixes count as priority | `ROLE_PRIORITY` in `core/config.py` |
| change which addresses get guessed | `COMMON_EMAIL_PREFIXES` in `core/config.py` |
| add contact-page URL hints | `CONTACT_HINTS_STRONG/MEDIUM/WEAK` in `core/config.py` |
| retune the scoring weights | `score_candidate()` in `core/scoring.py` |
| add a country name or ccTLD | `COUNTRY_TO_REGION` / `CCTLD_TO_REGION` in `core/utils.py` |
| change how columns are auto-detected | `_COMPANY_HINTS` etc. in `core/tabular.py` |
| change how shipment descriptions are trimmed | `sanitize_product()` in `core/tabular.py` |
| add or change search queries | `build_company_queries()` in `core/search.py` |
| add a UI string or language | `STRINGS` in `web/static/index.html` |
| add an API endpoint | `web/app.py` |

### Project layout

```
core/
  config.py     constants, host blacklists, Config dataclass
  models.py     Result, Candidate, ContactExtract
  utils.py      regexes, URL/phone/email normalisation, country maps
  cache.py      SQLite cache (HTTP + search + MX) with TTL
  fetch.py      httpx.AsyncClient, per-host limits, cache in front
  search.py     DuckDuckGo: client pool, parallel queries, backoff
  scoring.py    relevance scoring for candidate URLs
  extract.py    contact extraction and internal link ranking
  tabular.py    Excel/CSV → deduplicated scan jobs
  pipeline.py   orchestration: scan_company, process_batch
  store.py      run history
web/
  app.py              FastAPI on 127.0.0.1, SSE progress, CSV/XLSX export
  static/index.html   single-page UI, EN/VI, no external assets
main.py               command line entry point
```

Both entry points share `core/`; nothing in `core/` imports from `web/` or
`main.py`.

> The root-level `scraper.py`, `search_engine.py`, `config.py`, `models.py` and
> `utils.py` are the superseded originals. Nothing imports them any more. They
> are safe to delete; the originals remain in commit `d4d9ede`.

### Using the engine from your own code

```python
import asyncio
from core.config import Config
from core.pipeline import process_batch

rows = [("BRAMA PTE LTD", "Singapore", ["cinnamon"])]
results = asyncio.run(process_batch(rows, Config(max_threads_companies=4)))

for r in results:
    print(r.company, r.website, r.confidence, r.priority_emails)
```

`process_batch` accepts an `on_event` callback receiving
`{"type": "progress", "index", "done", "total", "result"}` — that is exactly how
the web UI drives its progress stream.

---

## 9. Troubleshooting

**Everything returns confidence 0 / no results at all.** Almost always TLS.
Behind a corporate proxy every search fails with *"self-signed certificate in
certificate chain"*, and a failed search is indistinguishable from a company
with no web presence. Preferred fix — point Python at your proxy's CA:

```bash
export SSL_CERT_FILE=/path/to/corporate-ca.pem
```

Failing that, use `--insecure-tls`, tick *Skip TLS check* in the UI, or set
`FINDER_INSECURE_TLS=1`. It is off by default and disables certificate
verification, so only use it on a network you trust.

**Searches start failing partway through a big batch.** DuckDuckGo is rate
limiting. Lower `--threads`, or add `--delay 1`. The client already retries
three times with exponential backoff.

**`Legacy .xls is not supported`.** Open the file and re-save it as `.xlsx`.

**Results look stale after changing settings.** The cache holds pages for a
week. Use `--no-cache`, untick *Cache*, or run `make clean-cache`.

**A company I know has a website shows confidence 0.** Check `alternates` in
the CSV — the site may have been found but scored below the threshold. Lower
`--min-score`, or add the country and product to give the scorer more to work
with.

**Port already in use.** `make web PORT=9000`.

**Too many wrong matches.** Raise `--min-score` to 40–45. You will get fewer
rows with contacts and fewer wrong ones.

---

## 10. Search backends

DuckDuckGo is the default and needs nothing installed, but it is scraped rather
than API-served: it throttles at about six concurrent queries, occasionally
fails, and returns slightly different results between runs. That ceiling — not
the worker count — is what limits throughput on a large file.

| Backend | Setup | Concurrency | Cost |
|---|---|---|---|
| `ddg` | none | 6 | free |
| `searxng` | self-host, see below | 16 | free |
| `brave` | `FINDER_SEARCH_API_KEY` | 10 | per query |
| `serper` | `FINDER_SEARCH_API_KEY` | 10 | per query |

### SearXNG (recommended for large files)

SearXNG is a self-hosted metasearch engine. It federates Google, Bing, Brave
and others behind one endpoint, so recall is better than DuckDuckGo alone,
there is no API key and no per-query cost, and the rate limit is yours to set.

The supplied `docker-compose.yml` sets it up for you — `./install.sh docker`
is all you need. `searxng/settings.yml` in this repo already enables the JSON
API, disables the rate limiter (it would throttle your own batch runs), and
tightens the outgoing timeouts.

To run SearXNG yourself instead:

```bash
docker run -d --name searxng -p 8080:8080 \
  -v "$PWD/searxng:/etc/searxng" \
  -e SEARXNG_SECRET="$(openssl rand -hex 32)" \
  searxng/searxng
```

**Its JSON API is off by default** — that is the single most common setup
mistake. Without this in `settings.yml` the tool reports that SearXNG returned
HTML rather than JSON:

```yaml
search:
  formats:
    - html
    - json
```

Point a native install at it with:

```bash
./venv/bin/python3 main.py data.xlsx --search-provider searxng
# or persistently
export FINDER_SEARCH_PROVIDER=searxng
export FINDER_SEARXNG_URL=http://127.0.0.1:8080
```

A configured backend that cannot be reached **aborts the run with an error**
rather than reporting every company as having no web presence. DuckDuckGo stays
lenient, because it flakes on individual queries and aborting a whole batch for
one failed query would be worse.

---

## 11. Company name normalisation

A customs export spells the same importer several ways. Before deduplication,
names are reduced to a normalised identity — case, whitespace, punctuation and
legal suffixes removed, dotted initials rejoined:

```
MCCORMICK GLOBAL INGREDIENTS LIMITED   ┐
MC CORMICK GLOBAL INGREDIENTS LIMITED  ├─> mccormickglobalingredients
MCCORMICK GLOBAL INGREDIENTS LTD.      │
McCormick Global Ingredients Limited   ┘
```

On a real 1007-row file this merged 427 raw names into **408 companies** across
18 groups, every one of them an exact match after normalisation.

Two distinctions matter:

- **Legal forms** (`ltd`, `llc`, `pvt`, `b.v.`) are dropped when deciding
  identity — `X Ltd` and `X Limited` are the same company.
- **Descriptors** (`global`, `international`, `trading`, `exports`) are *not*.
  They are dropped only when matching a name against a domain. Dropping them
  from identity would merge `FRESHDRINKUS LLC` with `FRESHDRINKUS GLOBAL LLC`,
  which may be separate legal entities.

### Why `--fuzzy-dedupe` is off

Edit-distance matching cannot be made safe on this data. Measured on the real
file:

| Pair | Score | Wanted |
|---|---|---|
| `VINAY ENTERPRISES` vs `VINAYAK ENTERPRISES` | 94 | keep separate |
| `FRESHDRINKUS GLOBAL LLC` vs `...LCC` (typo) | 92 | merge |

The wrong merge outranks the right one, so no threshold separates them. Known
misspellings are handled as exact tokens in `LEGAL_TOKENS` instead. Every merge
worth having already scores 100 after normalisation.

---

## 12. The company store

Results are written to a `companies` table in `.runs.db`, keyed by normalised
name. Re-running a file, or running a different file containing the same
importer, becomes a database read:

```
first lookup of RIDDHI SIDDHI IMPEX    26.0s
same company, second run                0.2s   (note: "from company store")
```

It matches across spellings — a run for `RIDDHI SIDDHI IMPEX.` hits the entry
stored for `RIDDHI SIDDHI IMPEX`. Entries expire after 90 days
(`Config.company_ttl`), on the assumption that contact details drift slowly.
`--no-company-store` bypasses it entirely.

This is separate from the HTTP/search cache in `.cache.db`, which has a
one-week TTL and works at the URL level.

---

## 13. Using the address column

If your export has an importer address column, map it — it is the sharpest
disambiguator available for generic company names.

The full address is never used as a search term; no page contains it verbatim.
Instead the **city** is extracted and used two ways: as one query variant, and
as a scoring signal worth 14 points when it appears on a candidate page. When
the scanned site names the company's own city, `address_confirmed` is set.

Locality extraction handles the common formats, including postcodes that
precede the city:

```
123 Main St, Mathura, Uttar Pradesh 281001, India  ->  Mathura, Uttar Pradesh
Kruisweg 855, 2132 NG Hoofddorp, Netherlands       ->  Hoofddorp
Unit 5, 12 Baker Street, London SW1A 1AA, UK       ->  London
```

---

## 14. Measured results

Two comparisons against the original implementation (git `d4d9ede`), same
machine, same day, same inputs.

**5 Indian spice companies** (`companies.csv`, 5 workers)

| | original | now |
|---|---|---|
| companies with contacts | 2/5 (one junk: `webmaster@ec21.com`) | **4/5** |
| matched the company's real site | 2/5 | **4/5** |
| cold run | 26s | 28s |
| repeat run | 26s | **<1s** |

The original matched `ABHINANDAN` to a spice encyclopedia article and
`MGG FOODS` to a dissolved UK company. Both now resolve correctly or report
zero confidence rather than guessing.

**12 importers from a customs export** (`data.xlsx`, 12 workers)

| | original | now |
|---|---|---|
| rows → companies scanned | no dedup | 1007 rows → **427 unique** |
| companies with contacts | 8/12, of which ~5 were directory or blog noise | **9/12** |
| cold run | 55s | **36s** |
| repeat run | 55s | **<1s** |

Read crudely, 8/12 versus 9/12 looks like a wash. The difference is that all 9
new results point at the company's own domain, whereas roughly 5 of the old 8
were third parties' contact details.

Three of the twelve still resolve poorly. `ONT8 - AMAZON` is a warehouse code
rather than a company name, and generic names like `CK PHARM CO., LTD` attract
unrelated pharmacies. They surface with low confidence rather than wrong data.
