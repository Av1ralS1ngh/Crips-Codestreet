# CAVEAT VALOREM — American Express CodeStreet 2026, Problem Statement #5.
#
#   make demo      one command, clean clone to running demo. This is the one to type.
#   make help      everything else.
#
# Every target routes through the same interpreter and the same PYTHONPATH. The kernel-only
# path leaves the repository root off sys.path, so `agent` stops being importable and two
# /api/scenario/* routes answer 503 naming the fix — which is why PYTHONPATH is set here
# once rather than remembered at each call site.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PY := .venv/bin/python
export PYTHONPATH := backend:.

API_PORT ?= 8000
WEB_PORT ?= 5173

.PHONY: help demo check test test-kernel console build smoke bench bench-full fingerprint \
        headline agents api web fixtures venv clean

help:
	@printf '\nCAVEAT VALOREM\n\n'
	@printf '  make demo         preflight, both suites, console, bench, fingerprint,\n'
	@printf '                    agent replays, then uvicorn + vite together\n'
	@printf '  make check        the same checks with no servers (what CI runs)\n\n'
	@printf '  make test         backend/tests + agent/tests           (all of them)\n'
	@printf '  make test-kernel  backend/tests only, kernel path       (two skip, on purpose)\n'
	@printf '  make console      frontend build + rendered smoke assertions\n'
	@printf '  make headline     every headline number, with its conditions\n'
	@printf '  make fingerprint  the scenario fingerprint, one line\n'
	@printf '  make agents       all three agent paths on replay traces (no API key)\n'
	@printf '  make bench        quick bench run to a temp path (artifact untouched)\n'
	@printf '  make bench-full   THE FULL RUN, ~5 min — OVERWRITES artifacts/valorem_bench.json\n'
	@printf '  make fixtures     regenerate the console fixtures from the real engine\n\n'
	@printf '  make api          uvicorn alone on port $(API_PORT)\n'
	@printf '  make web          vite alone on port $(WEB_PORT), live transport\n\n'
	@printf '  make venv         create .venv and install requirements.txt\n'
	@printf '  make clean        remove build output, caches and the venv\n\n'

# ------------------------------------------------------------------------------------------
# The one command
# ------------------------------------------------------------------------------------------

demo:
	@./scripts/demo.sh --api-port $(API_PORT) --web-port $(WEB_PORT)

check:
	@./scripts/demo.sh --check-only

# ------------------------------------------------------------------------------------------
# Pieces
# ------------------------------------------------------------------------------------------

test:
	$(PY) -m pytest backend/tests agent/tests -q

# Deliberately without the repository root on the path, which is the deployment shape the
# API's 503 branch exists for. Two tests skip here and pass under `make test`.
test-kernel:
	cd backend && PYTHONPATH=. ../$(PY) -m pytest tests -q

console: build smoke

build:
	npm run build --prefix frontend

smoke:
	npm run smoke --prefix frontend

headline:
	$(PY) scripts/headline.py

fingerprint:
	@$(PY) scripts/headline.py --fingerprint-only

agents:
	$(PY) -m agent.shopper  --scenario injection --replay --no-color
	$(PY) -m agent.selector --replay --no-color
	$(PY) -m agent.author   --replay --no-color

# Writes to a temp path. The committed artifact is the provenance for every quoted latency
# and gap figure, and a quick run produces smaller numbers under the same schema.
bench:
	$(PY) -m valorem.bench --quick --out "$$(mktemp -d)/valorem_bench_quick.json"

bench-full:
	@printf 'This overwrites artifacts/valorem_bench.json, which every quoted latency and\n'
	@printf 'gap figure is read from. It takes about five minutes, nearly all in Z3.\n'
	@read -p 'Type yes to continue: ' ans && [ "$$ans" = yes ]
	$(PY) -m valorem.bench

fixtures:
	$(PY) frontend/scripts/gen_fixtures.py
	$(PY) frontend/scripts/gen_valorem_fixtures.py
	$(PY) frontend/scripts/gen_valorem_beats.py

api:
	$(PY) -m uvicorn caveat.api:app --port $(API_PORT)

web:
	npm run dev:live --prefix frontend -- --port $(WEB_PORT) --strictPort

# ------------------------------------------------------------------------------------------
# Environment
# ------------------------------------------------------------------------------------------

venv:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

clean:
	rm -rf .venv .pytest_cache frontend/dist frontend/.smoke frontend/node_modules
	find backend agent -name __pycache__ -type d -prune -exec rm -rf {} +
