# B2B Contact Finder

B2B Contact Finder reads company names from Excel or CSV files, discovers the
most likely company websites, and extracts business contact information:

- priority and general email addresses;
- phone and WhatsApp numbers;
- social profiles;
- website confidence scores and match reasons;
- the pages and alternative websites considered during each lookup.

The application runs locally and provides an English/Vietnamese browser UI.
No account or API key is required when using the default DuckDuckGo search
provider.

## Supported desktop platforms

| Platform | Supported package | Requirements |
|---|---|---|
| Windows 10 1809 or newer | x64 installer | 64-bit x64 Windows |
| macOS 11 Big Sur or newer | Apple Silicon app | arm64 Mac |
| macOS 11 Big Sur or newer | Intel app | x86_64 Mac |

The desktop packages include Python and all application dependencies. End users
do not need Python, Git, Docker, Homebrew, or administrator access.

Linux remains supported through the source/Docker development workflow; there
is no Linux desktop package yet.

## Install on Windows

### One-command install

Open PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/foden303/find_contract/master/install.ps1 | iex
```

The bootstrap:

1. detects the native Windows architecture and supported OS version;
2. reads the latest stable release from this repository;
3. downloads the Windows x64 installer and checksum manifest over HTTPS;
4. verifies the asset name, version, size, and SHA-256 checksum;
5. verifies Authenticode when the release is signed;
6. launches the same per-user installer available on the Releases page.

It does not disable Defender, SmartScreen, PowerShell policy, or any other
security control.

### Graphical install

Download this asset from the latest GitHub Release:

```text
B2BContactFinder-<version>-windows-x64-setup.exe
```

Run it and follow the setup wizard. It installs under:

```text
%LOCALAPPDATA%\Programs\B2BContactFinder
```

The installer creates a Start Menu shortcut and can optionally create a Desktop
shortcut. It does not require administrator permission.

### Start and stop on Windows

Open **B2B Contact Finder** from the Start Menu. The app starts a private local
server, opens the default browser, and remains available in the notification
area.

Use the tray menu to:

- **Open** the browser UI again;
- **Open logs** for diagnostics;
- **Exit** the application cleanly.

Opening the shortcut twice reuses the running application instead of starting a
second server.

## Install on macOS

### One-command install

Open Terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/foden303/find_contract/master/install-macos.sh | bash
```

The bootstrap:

1. detects Apple Silicon or Intel automatically, including Rosetta shells;
2. downloads the matching app archive and checksum manifest over HTTPS;
3. verifies the release tag, asset, size, SHA-256 checksum, and app signature;
4. preserves the previous app until the new copy is installed successfully;
5. installs the app for the current user in `~/Applications`;
6. opens the installed application.

It never runs `sudo`, removes quarantine attributes, or changes Gatekeeper.

### Graphical install

Download the correct asset from the latest GitHub Release:

```text
B2BContactFinder-<version>-macos-arm64.zip   # Apple Silicon
B2BContactFinder-<version>-macos-x64.zip     # Intel
```

Extract it and move **B2B Contact Finder.app** into `/Applications` or
`~/Applications`.

A Developer ID signed and notarized build opens normally. If a release is
explicitly marked unsigned, macOS may require **Control-click → Open** the first
time. Never disable Gatekeeper globally. SHA-256 confirms release integrity but
is not a substitute for publisher identity.

### Start and stop on macOS

Open **B2B Contact Finder** from Applications. It opens the browser UI and stays
available in the macOS menu bar.

Use the menu-bar icon to:

- **Open** the browser UI;
- **Open logs**;
- **Exit** cleanly.

Opening the app again reuses the existing instance.

## Release availability

Both one-command installers require a published stable GitHub Release containing
all platform assets and `SHA256SUMS.txt`. Before the first release is published,
the commands stop safely with an error; use the development workflow below.

Release signing status is recorded in each `build-info-*.json` asset. Unsigned
artifacts may trigger Windows SmartScreen or macOS Gatekeeper warnings.

## Quick start

### 1. Configure search

Open **Search settings and data import** at the top of the UI.

The default provider is **DuckDuckGo** and needs no setup. Optional providers:

| Provider | Configuration | Notes |
|---|---|---|
| DuckDuckGo | none | free, scraped upstream; may throttle or vary between runs |
| SearXNG | base URL | self-hosted JSON API; upstream engines can still throttle |
| Brave | API key | usage and billing follow the Brave plan |
| Serper | API key | usage and billing follow the Serper plan |

Click **Save settings**. Saving does not make a test request. A provider outage
is reported as an error after retries; it is not cached as “no results.”

On Windows, saved API keys are protected with the current Windows user's DPAPI
credentials. API keys are never returned to browser JavaScript, placed in
browser storage, or written into run history. Environment variables override
saved settings and appear as locked fields in the UI.
On macOS, saved keys remain in the per-user `settings.json` file with user-only
file permissions; they are not stored in Keychain. Treat the macOS account and
its backups as trusted.

### 2. Choose a file

Drag a file into the upload area or click it to browse. Supported formats:

- `.xlsx` and `.xlsm`;
- `.csv`, `.tsv`, and `.txt`;
- files up to 64 MB.

Legacy `.xls` files are not supported; open them in Excel or LibreOffice and
save them as `.xlsx` first.

### 3. Map columns

Select the columns that contain:

- **Company name** — required;
- **Country** — recommended for regional search and phone validation;
- **Product** — optional search context;
- **Address** — optional location evidence.

The UI detects common English and Vietnamese headers automatically. Always
review the preview before starting.

For customs/trade exports, one company often appears in many shipment rows.
**Merge duplicate companies** is enabled by default. Deduplication removes case,
spacing, punctuation, and legal-suffix differences without fuzzy-merging distinct
business names.

Use **Shared product keywords** when raw product descriptions are too detailed
or written in a language unlikely to appear on the target company's website.
For example:

```text
cinnamon, cassia, star anise
```

### 4. Review advanced options

| Option | Default | Meaning |
|---|---:|---|
| Parallel workers | 8 | companies processed concurrently |
| Pages per site | 10 | maximum pages fetched from a candidate site |
| Candidates per company | 3 | search results examined per company |
| Minimum score | 30 | candidates below this score are not scraped |
| Delay | 0 | minimum delay between requests to one host |
| Cache | enabled | reuse HTTP/search/MX results |
| Guess emails | enabled | validate common role-address guesses through MX |
| Merge all candidates | disabled | scan and combine every qualifying site |
| Company store | enabled | reuse recently completed company results |
| Skip TLS check | disabled | emergency option for trusted TLS-inspecting networks |

Start with defaults and a small row limit. Raise the minimum score when results
contain unrelated websites. Lower concurrency or add delay if the search
provider throttles a large batch.

### 5. Start searching

Click **Start searching**. Results stream into the table while the run remains
active. Closing the browser tab does not stop the desktop application. Reopen it
from the tray/menu-bar icon and use **History** to inspect the run.

Use **Stop** to cancel an active run. Results completed before cancellation are
retained.

### 6. Export results

Export the entire run as:

- UTF-8 CSV compatible with Excel;
- `.xlsx` with a formatted header row.

Filtering or pagination changes only the visible table; exports always contain
the complete run.

## Output fields

Important fields include:

| Field | Meaning |
|---|---|
| `company` | normalized display name from the selected source column |
| `website` | highest-ranked candidate website |
| `confidence` | website/company match score from 0 to 100 |
| `match_reason` | evidence contributing to that score |
| `priority_emails` | preferred role addresses such as sales or purchasing |
| `emails` | other extracted company-domain addresses |
| `guessed_emails` | generated role addresses whose domains have MX records |
| `phones` | validated E.164 phone numbers |
| `whatsapp_numbers` | normalized WhatsApp numbers |
| `social_links` | discovered social profiles |
| `address_confirmed` | whether the selected site mentions the expected locality |
| `pages_scanned` | pages fetched from the selected website |
| `sources` | candidate sites whose contacts contributed to the result |
| `alternates` | other ranked candidates considered |
| `notes` | cache, extraction, or row-level error information |

A high confidence score means the website is likely to belong to the requested
company. It does not verify that every contact is currently active. Guessed
email addresses are clearly separated from addresses found on pages.

## Data storage and privacy

The desktop server listens only on loopback. Launcher sessions use a random
secret, an HttpOnly/SameSite cookie, host validation, and cross-origin request
checks. Do not expose the local port to a network without adding authentication
for remote users.

The application stores data locally, but search queries and page requests are
sent to the selected search provider and discovered websites.

### Windows data directory

```text
%LOCALAPPDATA%\B2BContactFinder
  data\       SQLite cache and run history
  uploads\    uploaded source files
  logs\       rotating diagnostic logs
  settings.json
```

### macOS data directory

```text
~/Library/Application Support/B2BContactFinder
  data/       SQLite cache and run history
  uploads/    uploaded source files
  logs/       rotating diagnostic logs
  settings.json
```

Upgrades preserve this directory.

- Windows uninstall keeps it by default and offers an explicit purge option.
- Removing the macOS app keeps it. Delete the data directory separately only if
  you intentionally want to erase settings, history, uploads, and logs.

Custom `FINDER_HOME`, `FINDER_CACHE_DIR`, and `FINDER_UPLOAD_DIR` locations are
never removed by desktop uninstallers.

## Import data from an older source checkout

Older versions stored `.runs.db`, `.cache.db`, and `.uploads` beside the source
code. To migrate:

1. stop the old application;
2. install and open the new desktop app;
3. do not upload a new file or start a run yet;
4. open **Search settings and data import**;
5. expand **Advanced: import data from an old installation**;
6. enter the absolute path to the old project folder;
7. confirm the import.

Import is accepted only when the destination history and uploads are empty and
no search is active. SQLite databases are integrity-checked and copied through
the SQLite backup API, including committed WAL data. Source files are never
modified or deleted.

## Troubleshooting

### The application does not open

Open the diagnostic log:

- Windows: tray menu **Open logs**, or
  `%LOCALAPPDATA%\B2BContactFinder\logs\desktop.log`;
- macOS: menu-bar item **Open logs**, or
  `~/Library/Application Support/B2BContactFinder/logs/desktop.log`.

Opening the app a second time should reopen the browser UI. If the application
was force-terminated, the next launch takes ownership of the stale instance
state and recovers SQLite automatically.

### Search backend failed

The application retries transient failures and then stops the run with an error.
Check:

- internet access;
- provider status and quota;
- SearXNG URL and JSON API configuration;
- Brave/Serper API key;
- corporate proxy or TLS interception.

Do not interpret a backend error as “the company has no website.” Failed
searches are not cached as empty successful searches.

### Corporate TLS interception

Preferred fix: configure `SSL_CERT_FILE` to point at the corporate CA bundle.
The **Skip TLS check** option disables certificate verification and should be
used only on a trusted network when the CA cannot be installed correctly.

### Results look stale

Disable **Cache** for the next run. Developer installations can run
`make clean-cache` after stopping the app. The company store has a separate
90-day lifetime and can also be disabled for a run.

### Wrong websites rank highly

Provide the country, product keywords, and address/locality. Increase
**Minimum score** to 40–45. Check `alternates` to see whether the correct site
was found but ranked below the selected candidate.

### Port 8765 is occupied

The desktop launcher automatically selects another loopback port. Source mode
can use:

```bash
make web PORT=9000
```

## Developer installation

Desktop users should use the packaged installers. For development on macOS or
Linux:

```bash
git clone https://github.com/foden303/find_contract.git
cd find_contract
./install.sh native
make web
```

For Docker with self-hosted SearXNG:

```bash
./install.sh docker
make logs
make down
```

Docker publishes the unauthenticated UI only on `127.0.0.1`. SearXNG is not
published to the host by default. Docker data lives in named volumes;
`docker compose down -v` permanently removes those volumes.

### Command line examples

One company:

```bash
./venv/bin/python main.py "RIDDHI SIDDHI IMPEX" \
  --country India --product "cinnamon, cassia"
```

Spreadsheet:

```bash
./venv/bin/python main.py data.xlsx \
  --csv-column "Company" \
  --country-column "Country" \
  --product-column "Product" \
  --out contacts.csv
```

Use `./venv/bin/python main.py --help` for all CLI options.

## Build desktop packages

Release builds use Python `3.12.10` and hash-locked binary dependencies.

### Windows x64

From native 64-bit Windows PowerShell:

```powershell
./packaging/build.ps1
```

The release workflow also builds the Inno Setup installer, runs the frozen
smoke test, and verifies per-user install, upgrade retention, and uninstall.

### macOS

From a native Apple Silicon or Intel Mac with Python `3.12.10`:

```bash
PYTHON=/path/to/python3.12 packaging/build_macos.sh
packaging/sign_macos.sh "dist/B2B Contact Finder.app"
packaging/smoke_macos.sh "dist/B2B Contact Finder.app" macos-smoke-result.json
```

Without credentials, `sign_macos.sh` applies an ad-hoc signature and labels the
build unsigned. A signed release requires all of:

- `MACOS_SIGNING_P12`;
- `MACOS_SIGNING_PASSWORD`;
- `MACOS_SIGNING_IDENTITY`;
- `APPLE_ID`;
- `APPLE_APP_PASSWORD`;
- `APPLE_TEAM_ID`.

Windows signing uses:

- `WINDOWS_SIGNING_PFX`;
- `WINDOWS_SIGNING_PASSWORD`;
- `WINDOWS_SIGNING_PUBLISHER`.

Partial signing configuration fails the build. Credentials are never replaced
with fake certificates.

## Release process

`.github/workflows/consumer-release.yml` runs on pull requests, manual dispatch,
and version tags. It builds:

- Windows x64;
- macOS Apple Silicon;
- macOS Intel.

Each build runs regression tests and a frozen offline HTTP smoke scenario that
covers session authentication, static UI, CSV upload, column planning, SQLite
history, CSV/XLSX export, and clean shutdown. Version tags must exactly match
`core.version.VERSION`, for example `v1.0.0`.

A version tag is published only after every platform build succeeds. The release
contains platform archives/installers, per-platform build provenance, and one
`SHA256SUMS.txt` manifest used by both bootstrap installers.

## Project structure

```text
core/
  cache.py        SQLite HTTP/search/MX cache
  config.py       runtime tuning and search configuration
  extract.py      email, phone, WhatsApp, and social extraction
  migration.py    safe import from older source checkouts
  models.py       result and candidate data models
  paths.py        platform-specific writable directories
  pipeline.py     batch orchestration
  scoring.py      candidate relevance scoring
  search.py       DuckDuckGo, SearXNG, Brave, and Serper clients
  settings.py     persistent provider settings and Windows DPAPI handling
  store.py        run history and reusable company results
  tabular.py      Excel/CSV loading, preview, mapping, and deduplication
web/
  app.py          local FastAPI application and lifecycle
  static/
    index.html    dependency-free EN/VI browser UI
desktop.py        Windows/macOS desktop launcher
main.py           CLI entry point
install.ps1       Windows release bootstrap
install-macos.sh  macOS release bootstrap
install.sh        source/Docker developer installer
packaging/        frozen builds, signing, smoke, and installer checks
```

## Known limits

- Search providers and target websites can throttle or block automated requests.
- Self-hosting SearXNG does not remove upstream search-engine limits.
- Some sites render contacts only through JavaScript or block non-browser HTTP
  clients.
- Contact extraction and confidence scoring reduce false matches but cannot
  guarantee identity or data freshness.
- Windows ARM64 and 32-bit Windows are not packaged.
- The desktop UI is single-user and local-only; it has no remote-user account
  system.
