# Global B2B Contact Finder

VENV_DIR = venv
PYTHON = $(VENV_DIR)/bin/python3
PIP = $(VENV_DIR)/bin/pip

IN ?= companies.csv
PORT ?= 8765
TOP ?= 3

.PHONY: help install web run keyword batch clean clean-cache up down logs rebuild

help:
	@echo "  make install            - create the venv and install dependencies"
	@echo "  make web [PORT=8765]    - open the local web UI (pick an Excel file in the browser)"
	@echo ""
	@echo "  Docker (app + self-hosted SearXNG, no API key):"
	@echo "  make up                 - build and start everything"
	@echo "  make down               - stop it"
	@echo "  make logs               - follow the logs"
	@echo "  make rebuild            - rebuild after a code change"
	@echo ""
	@echo "  make run QUERY=\"...\"    - look up one company or domain"
	@echo "  make keyword Q=\"...\"    - discover new leads from a product keyword"
	@echo "  make batch [IN=file]    - process a .csv or .xlsx file"
	@echo "  make clean-cache        - drop the HTTP/search cache only"
	@echo "  make clean              - remove the development venv (keep user data)"

install:
	test -d $(VENV_DIR) || python3 -m venv $(VENV_DIR)
	$(PIP) install -q -r requirements.txt
	@echo "Done. Run 'make web' to start."

web:
	@echo "Opening http://127.0.0.1:$(PORT)"
	$(PYTHON) -c "from web.app import serve; serve(port=$(PORT))"

run:
	@if [ -z "$(QUERY)" ]; then \
		echo "Error: need QUERY. Example: make run QUERY=\"google.com\""; \
	else \
		$(PYTHON) main.py "$(QUERY)" --top-results $(TOP) $(ARGS); \
	fi

keyword:
	@if [ -z "$(Q)" ]; then \
		echo "Error: need Q. Example: make keyword Q=\"coffee beans\""; \
	else \
		$(PYTHON) main.py "$(Q)" --keyword --limit 10 --top-results $(TOP) \
			--out leads_$(shell date +%Y%m%d_%H%M%S).csv $(ARGS); \
	fi

batch:
	@if [ ! -f "$(IN)" ]; then \
		echo "Error: file $(IN) not found."; \
	else \
		$(PYTHON) main.py "$(IN)" --top-results $(TOP) \
			--out results_$(shell date +%Y%m%d_%H%M%S).csv $(ARGS); \
	fi

# --- Docker ------------------------------------------------------------------
# `up` runs the installer first so .env (and its SearXNG secret) always exists;
# compose refuses to start without it rather than using a shared default.

up:
	@./install.sh docker

down:
	docker compose down

logs:
	docker compose logs -f

rebuild:
	docker compose up -d --build

clean-cache:
	@echo "Stop the app before clearing the cache."
	$(PYTHON) -c "from pathlib import Path; from core.paths import data_dir; root = Path(data_dir()); [(root / ('.cache.db' + suffix)).unlink(missing_ok=True) for suffix in ('', '-wal', '-shm')]"

clean:
	rm -rf $(VENV_DIR)
