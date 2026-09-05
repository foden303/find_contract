#!/usr/bin/env bash
# Quick installer for Linux and macOS.
#
#   ./install.sh            # Docker if available, otherwise a local venv
#   ./install.sh docker     # force Docker + self-hosted SearXNG
#   ./install.sh native     # force a local Python venv
#
set -euo pipefail

cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; OFF=$'\033[0m'
say()  { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$BOLD" "$OFF" "$*"; }
warn() { printf '%s !%s %s\n' "$RED" "$OFF" "$*" >&2; }
ok()   { printf '%s ✓%s %s\n' "$GREEN" "$OFF" "$*"; }

MODE="${1:-auto}"
PORT="${FINDER_PORT:-8765}"

have() { command -v "$1" >/dev/null 2>&1; }

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@";
  elif have docker-compose; then docker-compose "$@";
  else return 1; fi
}

random_secret() {
  if have openssl; then openssl rand -hex 32
  elif [ -r /dev/urandom ]; then head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  else python3 -c 'import secrets;print(secrets.token_hex(32))'
  fi
}

# --- pick a mode -------------------------------------------------------------

if [ "$MODE" = "auto" ]; then
  if have docker && docker info >/dev/null 2>&1 && compose version >/dev/null 2>&1; then
    MODE=docker
  else
    MODE=native
  fi
fi

# --- Docker ------------------------------------------------------------------

if [ "$MODE" = "docker" ]; then
  step "Installing with Docker (app + self-hosted SearXNG)"

  have docker || { warn "Docker is not installed. See https://docs.docker.com/get-docker/"; exit 1; }
  docker info >/dev/null 2>&1 || { warn "Docker is installed but not running. Start Docker Desktop and retry."; exit 1; }
  compose version >/dev/null 2>&1 || { warn "Docker Compose v2 not found. Update Docker."; exit 1; }

  if [ ! -f .env ]; then
    step "Generating .env with a fresh SearXNG secret"
    printf 'SEARXNG_SECRET=%s\nFINDER_PORT=%s\n' "$(random_secret)" "$PORT" > .env
    chmod 600 .env
    ok "Wrote .env"
  else
    ok ".env already exists — leaving it alone"
  fi

  step "Building and starting containers (first run pulls images, a few minutes)"
  compose up -d --build

  step "Waiting for the app to answer"
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
      ok "Ready"
      say ""
      say "  ${BOLD}Open http://127.0.0.1:${PORT}${OFF}"
      say "  ${DIM}Search backend: self-hosted SearXNG (no API key)${OFF}"
      say ""
      say "  Logs:  docker compose logs -f"
      say "  Stop:  docker compose down"
      exit 0
    fi
    sleep 2
  done

  warn "The app did not come up in time. Check: docker compose logs"
  exit 1
fi

# --- Native venv -------------------------------------------------------------

step "Installing natively (Python venv, DuckDuckGo search)"

PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if have "$c" && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done

if [ -z "$PY" ]; then
  warn "Python 3.10+ not found."
  case "$(uname -s)" in
    Darwin) say "  Install it with:  brew install python@3.12" ;;
    Linux)  say "  Install it with:  sudo apt install python3 python3-venv   (or your package manager)" ;;
  esac
  exit 1
fi
ok "Using $($PY --version)"

[ -d venv ] || "$PY" -m venv venv
./venv/bin/pip install -q --upgrade pip
step "Installing dependencies"
./venv/bin/pip install -q -r requirements.txt
ok "Dependencies installed"

./venv/bin/python3 -c "import core.pipeline, web.app" || { warn "Import check failed"; exit 1; }

say ""
say "  ${BOLD}Start it with:  make web${OFF}     ${DIM}(or ./venv/bin/python3 -m web.app)${OFF}"
say "  Then open http://127.0.0.1:${PORT}"
say ""
say "  ${DIM}DuckDuckGo needs no API key, but upstream engines may throttle requests."
say "  For large files, run ./install.sh docker to get SearXNG as well.${OFF}"
