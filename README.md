# Global B2B Contact Finder

A professional tool for discovering global B2B contact information (Email, Phone, WhatsApp, Social Links).

## Installation

Use the provided `Makefile` to set up your virtual environment and install dependencies:

```bash
make install
```

This command will automatically create a `venv` directory and install the packages listed in `requirements.txt`.

## Usage

### 1. Search by Company Name or Domain
```bash
./venv/bin/python3 main.py "Company Name"
# or
./venv/bin/python3 main.py "example.com"
```

### 2. Discover New Leads (Product Keywords)
```bash
./venv/bin/python3 main.py "coffee beans" --keyword --limit 10 --out coffee_leads.csv
```

### 3. Batch Processing from CSV
```bash
./venv/bin/python3 main.py companies.csv --csv-column company --out results.csv
```

## Makefile Shortcuts
You can quickly run searches using the Makefile:
```bash
make run QUERY="google.com"
```

## Requirements
- `requests`
- `beautifulsoup4`
- `lxml`

These are managed via `requirements.txt`.
