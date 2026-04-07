# Global B2B Contact Finder Makefile

VENV_DIR = venv
PYTHON = $(VENV_DIR)/bin/python3
PIP = $(VENV_DIR)/bin/pip

# Default config
IN ?= companies.csv
TOP ?= 5

.PHONY: help install run keyword batch clean

help:
	@echo "Available commands:"
	@echo "  make install           - Set up virtual environment and dependencies"
	@echo "  make run QUERY=\"...\"   - Run a simple search for a company or domain"
	@echo "  make keyword Q=\"...\"   - Discover NEW leads using a product keyword"
	@echo "  make batch [IN=...]    - Process list from CSV (default: companies.csv)"
	@echo "  make clean             - Remove virtual environment and results"

install:
	test -d $(VENV_DIR) || python3 -m venv $(VENV_DIR)
	$(PIP) install -r requirements.txt

run:
	@if [ -z "$(QUERY)" ]; then \
		echo "Error: Need QUERY. Example: make run QUERY=\"google.com\""; \
	else \
		$(PYTHON) main.py "$(QUERY)" --top-results $(TOP) $(ARGS); \
	fi

keyword:
	@if [ -z "$(Q)" ]; then \
		echo "Error: Need Q (keyword). Example: make keyword Q=\"coffee beans\""; \
	else \
		$(PYTHON) main.py "$(Q)" --keyword --limit 10 --top-results $(TOP) --out leads_$(shell date +%Y%m%d_%H%M%S).csv; \
	fi

batch:
	@if [ ! -f "$(IN)" ]; then \
		echo "Error: File $(IN) not found."; \
	else \
		echo "Processing $(IN)..."; \
		$(PYTHON) main.py "$(IN)" --top-results $(TOP) --out results_$(shell date +%Y%m%d_%H%M%S).csv; \
	fi

clean:
	rm -rf $(VENV_DIR)
	rm -f leads_*.csv results_*.csv
