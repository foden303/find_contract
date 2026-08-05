# Plans — Global B2B Contact Finder

Goal: raise **result quality**, cut **time to find**, and add a **localhost web UI**
for use on a personal machine.

Markers: `cc:TODO` not started · `cc:WIP` in progress · `cc:done` finished.

---

## Baseline diagnosis (from `results_debug.csv`)

4 of 5 companies resolved to the wrong company; 0 emails, 0 phones.

| # | Bug | Location (old code) | Consequence |
|---|-----|---------------------|-------------|
| B1 | `collect_candidate_pages` pushes 15 guessed paths into the list *first*, then `break`s once `len >= max_pages` | `scraper.py:34-71` | Real contact links on the page are **never** scanned (max_pages=5 < 16) |
| B2 | Only the first DDG query runs, then `break` | `search_engine.py:82-93` | Queries carrying country/product never execute |
| B3 | Takes DDG's first hit unconditionally, no scoring | `search_engine.py:76-93` | Matches companies in the wrong country or industry |
| B4 | Directory blacklist incomplete (`importgenius`, `checkcompany`, `tofler`…) | `config.py:40-50` | Aggregator pages displace the company's real site |
| B5 | `validate_whatsapp_status` returns True for any `+` number or codes 84/91/1/44 | `utils.py:39-52` | `whatsapp_verified` column is pure noise |
| B6 | If the page contains the word "whatsapp", **every** phone on it becomes a WhatsApp number | `scraper.py:121-125` | Heavy noise |
| B7 | `PHONE_RE` far too loose, no per-country validation | `utils.py:9` | Captures dates and tax IDs |
| B8 | No decoding of obfuscated emails (cfemail, `[at]`, JSON-LD) | `scraper.py:74-127` | Misses emails on common B2B sites |
| B9 | `hunter_emails` / `guessed_emails` / `is_direct_hit` declared but never populated | `models.py` | Dead code |

Slow points:

| # | Problem | Location | Cost |
|---|---------|----------|------|
| P1 | Every query opens a fresh `DDGS()` | `search_engine.py:35` | ~1-2s wasted per query |
| P2 | DDG queries run sequentially | `search_engine.py:82` | linear in query count |
| P3 | Homepage fetched and parsed **twice** | `scraper.py:191` & `205` | 1 wasted request + 1 wasted parse per company |
| P4 | 15 guessed paths, mostly 404, one round trip each | `scraper.py:41-50` | largest single cost |
| P5 | `pool_maxsize` defaults to 10 while 5×5=25 threads run | `scraper.py:9` | connection-pool contention |
| P6 | No cache — a re-run repeats every search and fetch | throughout | very costly while tuning |
| P7 | `--delay` accepted as an argument but never used anywhere | `main.py:51` | dead code |
| P8 | No early exit once enough contacts are found | `scraper.py:143` | wasted scanning |

---

## Target architecture

```
core/
  config.py     constants, blacklists, Config dataclass
  models.py     Result (+ confidence, match_reason), ContactExtract
  utils.py      regexes, URL/phone/email normalisation
  cache.py      SQLite cache (HTTP + search) with TTL
  fetch.py      httpx.AsyncClient + per-host rate limiting + cache
  search.py     DDG: client pool, parallel queries, cache
  scoring.py    relevance scoring for candidate URLs
  extract.py    contact extraction (selectolax, cfemail, JSON-LD, phonenumbers)
  tabular.py    read Excel/CSV, guess columns, dedupe -> job list
  pipeline.py   async scan_company + process_batch
  store.py      run history (SQLite)
web/
  app.py        FastAPI, bound to 127.0.0.1, SSE progress
  static/index.html   single-page UI, no CDN
main.py         CLI — existing interface preserved
```

CLI and web share `core/`. The existing CLI interface is not broken.

**Note:** the old root-level files (`scraper.py`, `search_engine.py`,
`config.py`, `models.py`, `utils.py`) are fully superseded by `core/` and are
now **imported by nothing**. They remain in place because the delete was
declined; removing them is safe at any time (the originals stay in commit
`d4d9ede`).

---

## Phase 1 — Fix the quality bugs  `cc:done`

- [x] `T1.1` Stand up the `core/` package, move `config/models/utils` across `cc:done`
- [x] `T1.2` Rewrite `collect_candidate_pages`: prefer **discovered** links, guessed paths only fill leftover budget, rank by contact-signal strength (B1, P4) `cc:done`
- [x] `T1.3` Drop the second homepage fetch/parse, reuse the HTML already held (P3) `cc:done`
- [x] `T1.4` Fix the `break` so every refined query runs (B2) `cc:done`
- [x] `T1.5` Widen the blacklist, split `SKIP_HOSTS` / `SNIPPET_ONLY_HOSTS` / `DIRECTORY_HOSTS` (B4) `cc:done`
- [x] `T1.6` Remove the "page mentions whatsapp -> every phone is WhatsApp" rule; accept numbers only from `wa.me`/`api.whatsapp.com` links (B6) `cc:done`
- [x] `T1.7` Replace `validate_whatsapp_status` with evidence-based verification (B5) `cc:done`

## Phase 2 — Scoring and verification  `cc:done`

- [x] `T2.1` `core/scoring.py`: score candidates on name↔domain similarity (rapidfuzz), product in snippet/page, country signals (ccTLD/address/dialling code), directory penalty (B3) `cc:done`
- [x] `T2.2` Add `confidence` + `match_reason` to `Result`, emit in CSV/JSON (B9) `cc:done`
- [x] `T2.3` Stronger extraction: decode Cloudflare `data-cfemail`, `[at]`/`(dot)` obfuscation, JSON-LD `schema.org/Organization`, `mailto` inside `<script>` (B8) `cc:done`
- [x] `T2.4` Validate phones with `phonenumbers` against the country, emit E.164 (B7) `cc:done`
- [x] `T2.5` Populate `guessed_emails`: generate `info@/sales@/export@domain` and keep only those whose domain has an **MX record** via `dnspython` (B9) `cc:done`
- [x] `T2.6` Filter junk emails: CMS/asset/sentry/wixpress domains, file extensions `cc:done`

## Phase 3 — Speed  `cc:done`

- [x] `T3.1` `core/fetch.py`: `httpx.AsyncClient`, HTTP/2, correct limits, per-host rate limiting (P5) `cc:done`
- [x] `T3.2` Parse with `selectolax`, BeautifulSoup as fallback `cc:done`
- [x] `T3.3` `core/search.py`: reusable `DDGS` pool, bounded parallel queries + backoff on rate limiting (P1, P2) `cc:done`
- [x] `T3.4` `core/cache.py`: SQLite cache for HTTP + search, configurable TTL, `--no-cache` flag (P6) `cc:done`
- [x] `T3.5` Exit early once a priority email and a phone are held (P8) `cc:done`
- [x] `T3.6` Wire `--delay` into the real rate limiter, drop the dead code (P7) `cc:done`

## Phase 4 — Localhost web UI  `cc:done`

- [x] `T4.1` `web/app.py`: FastAPI bound to `127.0.0.1`, in-process job queue `cc:done`
- [x] `T4.2` SSE stream of per-company progress `cc:done`
- [x] `T4.3` Persist run history to SQLite, revisit and compare `cc:done`
- [x] `T4.4` Single-page UI: company/domain/keyword input, CSV upload, country and product selection, table sorted by `confidence`, "why matched" detail, CSV export `cc:done`
- [x] `T4.5` `make web` + README update `cc:done`
- [x] `T4.6` EN/VI language toggle in the UI, English by default, choice remembered `cc:done`
- [x] `T4.7` Theme toggle (auto → light → dark), remembered; explicit choice overrides the OS `cc:done`
- [x] `T4.8` `--merge-sources` / *Merge all candidates*: scrape every qualifying site and pool the contacts, instead of stopping at the first productive one `cc:done`

## Phase 6 — Pipeline hardening  `cc:done`

Driven by an architecture review of ten proposed solutions; the LLM steps were
explicitly deferred.

- [x] `T6.1` Normalise company names before dedup — wire the existing `normalize_company` into `build_jobs`; split legal forms from descriptors so identity and domain-matching use different rules `cc:done`
- [x] `T6.2` Company store: a `companies` table keyed by normalised name, so a repeat lookup is a SELECT (26s → 0.2s), 90-day TTL `cc:done`
- [x] `T6.3` Pluggable search backends behind one interface: DuckDuckGo (default), self-hosted SearXNG, Brave, Serper — with per-provider concurrency `cc:done`
- [x] `T6.4` Address column: extract the locality, use it as a query variant and a 14-point scoring signal, set `address_confirmed` when the site names the city `cc:done`
- [x] `T6.5` A configured backend that cannot be reached aborts the run instead of reporting every company as having no web presence `cc:done`

## Phase 5 — Verification  `cc:done`

- [x] `T5.1` Re-run `companies.csv`, compare against `results_debug.csv` `cc:done`
- [x] `T5.2` Measure before/after timings `cc:done`
- [x] `T5.3` Read raw Excel, choose columns, dedupe (arose from `data.xlsx`) `cc:done`

---

## Added dependencies

`httpx[http2]`, `selectolax`, `phonenumbers`, `rapidfuzz`, `tldextract`, `dnspython`,
`openpyxl`, `fastapi`, `uvicorn[standard]`, `python-multipart`

All work offline with no API key. `tldextract` uses its bundled PSL snapshot
(`suffix_list_urls=()`) so importing never hits the network.

---

## Measured results (T5)

Against the original code (git `d4d9ede`), same machine, same day, same inputs.

### A. `companies.csv` — 5 Indian spice companies, 5 workers

| | original | now |
|---|---|---|
| with contacts | 2/5 (one of them junk: `webmaster@ec21.com`) | **4/5** |
| matched the company's real site | 2/5 | **4/5** |
| cold run | 26s | 28s |
| repeat run | 26s | **<1s** |

The original matched `ABHINANDAN` to a spice encyclopedia article and
`MGG FOODS` to a dissolved UK company. Both now resolve correctly or report
zero confidence instead of guessing.

### B. `data.xlsx` — 12 importers from a customs export, 12 workers

| | original | now |
|---|---|---|
| rows → companies scanned | no dedupe | 1007 rows → **427 unique** |
| with contacts | 8/12, of which ~5 were directory or blog noise | **9/12** |
| cold run | 55s | **36s** |
| repeat run | 55s | **<1s** |

Examples of the original's junk: `michael.chen@innovatex.com` (from a marketing
site), 7 phone numbers scraped off a Japanese blog, `exportersindia` directory
numbers.

### C. Single company, repeated

`RIDDHI SIDDHI IMPEX`: 17.8s (cold cache) → **0.03s** (warm cache).

---

## Honest caveats

- 3 of the 12 in group B still resolve poorly. `ONT8 - AMAZON` is a warehouse
  code rather than a company name, and generic names like `CK PHARM CO., LTD`
  attract unrelated pharmacies. They surface with low confidence and return
  **no** data, rather than wrong data.
- Cold-run time is dominated by the network, so group A is roughly level with
  the original. The real win is in group B (36s vs 55s) and in the cache on
  repeat runs.
- Read crudely, "8/12 vs 9/12" looks like a wash. The difference is that all 9
  new results point at the company's own domain, whereas ~5 of the old 8 were
  third parties' contact details.

## Work that arose outside the original plan

- `core/tabular.py` — read Excel/CSV, guess columns, dedupe, clean up shipment
  descriptions.
- Shared product keywords field: Vietnamese shipment descriptions
  (`Vỏ quế khô cắt đoạn…`) find no foreign company websites, so the per-row
  terms can be overridden with English trade keywords for the whole file.
- Dropping the quotes around the company name in queries — the single
  highest-impact quality change (`SOILEX`, `TWIN STAR` went from missed to
  correct).
- `--insecure-tls` flag (off by default) for machines behind a TLS-inspecting
  proxy, where certificate failures made searches look like companies with no
  web presence.
- CLI UX bug: a mistyped CSV path used to be searched for on the web as if it
  were a company name; it now fails with a clear error.
- Merge mode, after the observation that "Candidates per company: 3" was a
  ceiling rather than a target — the scan stopped at the first site that
  produced anything, so in practice one website was read. Building it surfaced
  two follow-on problems worth recording: search returns several pages of the
  same domain (fixed by deduplicating on registrable domain), and listings
  sites clear the score threshold on country and product alone (fixed by
  requiring a secondary domain to resemble the company name).
- Fuzzy name matching was **built, measured and then defaulted off**. On the
  real file `VINAY ENTERPRISES` vs `VINAYAK ENTERPRISES` (different companies)
  scores 94, while `FRESHDRINKUS GLOBAL LLC` vs `...LCC` (a typo, same company)
  scores 92 — the wrong merge outranks the right one, so no threshold works.
  Known misspellings became exact tokens instead. Every merge worth having
  already scores 100 after normalisation.
- Merge mode initially scraped a listings site and returned `care@magicpin.in`
  as the company's email; secondary domains must now resemble the company name.
- Pointing SearXNG at a dead URL first produced "no search results" with status
  `done` — the exact silent failure the design was meant to prevent. The
  exception was being swallowed twice: once by the generic retry handler, and
  again by `search_many`'s `return_exceptions=True`.
- `name_similarity` gave partial credit to a short name appearing anywhere
  inside a much longer domain — "triveni" inside "indiayellowpagesonline"
  scored 53. Partial matching now requires comparable lengths. This dropped a
  false CK PHARM match from 49.8 to 27.0, below the scrape threshold, so it
  returns nothing instead of an unrelated Armenian pharmacy's address.
