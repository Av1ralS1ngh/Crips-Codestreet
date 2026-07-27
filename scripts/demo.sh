#!/usr/bin/env bash
#
# PLUMBLINE — one command, clean clone to running demo.
#
#   ./scripts/demo.sh
#
# What it does, in order, stopping at the first failure:
#
#   0. preflight   — every prerequisite, checked by name, with the exact fix printed
#   1. backend     — backend/tests + agent/tests
#   2. console     — frontend typecheck, production build, and the rendered smoke assertions
#   3. bench       — verify the committed artifact; run --quick to a TEMP path to prove the
#                    harness executes. The committed artifact is never overwritten.
#   4. headline    — the scenario fingerprint, computed twice, plus every headline number
#   5. agents      — all three agent paths on their replay traces
#   6. serve       — uvicorn and vite together, both waited for, both killed on Ctrl-C
#
# There is no ANTHROPIC_API_KEY anywhere in this path. Every agent has a replay mode and
# this script uses it, so a judge on a locked-down laptop sees the same run we rehearsed.
#
# Written for bash 3.2, which is what stock macOS still ships as /bin/bash. No `wait -n`,
# no associative arrays, no `${arr[@]}` on an empty array under `set -u`. A judge's laptop
# is not a place to discover a shell-version dependency.
#
# Flags:
#   --check-only   steps 0-5, no servers. What CI would run.
#   --serve-only   steps 0 and 6. For the second run of the day.
#   --skip-console skip step 2 when node is unavailable. Prints what was skipped.
#   --api-port N   default 8000
#   --web-port N   default 5173

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY="$REPO_ROOT/.venv/bin/python"
# The kernel-only path leaves the repository root off sys.path, so `agent` stops being
# importable and two /api/scenario/* routes answer 503 naming the fix. Both are needed.
export PYTHONPATH="$REPO_ROOT/backend:$REPO_ROOT"

API_PORT=8000
WEB_PORT=5173
DO_CHECKS=1
DO_SERVE=1
DO_CONSOLE=1
# A newline-separated string rather than an array: bash 3.2 under `set -u` treats
# "${arr[@]}" on an empty array as an unbound variable and aborts.
SKIPPED=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)   DO_SERVE=0; shift ;;
    --serve-only)   DO_CHECKS=0; shift ;;
    --skip-console) DO_CONSOLE=0; shift ;;
    --api-port)     API_PORT="${2:?--api-port needs a number}"; shift 2 ;;
    --web-port)     WEB_PORT="${2:?--web-port needs a number}"; shift 2 ;;
    -h|--help)      sed -n '2,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)              printf 'unknown flag: %s (try --help)\n' "$1" >&2; exit 2 ;;
  esac
done

# ------------------------------------------------------------------------------------------
# Output. Colour only when stdout is a terminal, so piped logs stay greppable.
# ------------------------------------------------------------------------------------------

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
  BOLD=''; DIM=''; RED=''; GREEN=''; YELLOW=''; RESET=''
fi

STEP=0
step()  { STEP=$((STEP + 1)); printf '\n%s══ %d. %s%s\n' "$BOLD" "$STEP" "$1" "$RESET"; }
ok()    { printf '%s   ok%s  %s\n' "$GREEN" "$RESET" "$1"; }
note()  { printf '%s   ·   %s%s\n' "$DIM" "$1" "$RESET"; }
warn()  { printf '%s   !   %s%s\n' "$YELLOW" "$1" "$RESET"; }

# Fail loudly: what broke, then what to type. Never just a non-zero exit.
die() {
  printf '\n%s╳ FAILED — %s%s\n' "$RED$BOLD" "$1" "$RESET" >&2
  shift
  for line in "$@"; do printf '    %s\n' "$line" >&2; done
  printf '\n' >&2
  exit 1
}

# ------------------------------------------------------------------------------------------
# 0. Preflight
# ------------------------------------------------------------------------------------------

preflight() {
  step "preflight"

  [[ -x "$PY" ]] || die "no Python virtualenv at .venv/" \
    "This repository pins its dependencies. Create the environment:" \
    "  python3 -m venv .venv" \
    "  .venv/bin/pip install -r requirements.txt"

  local pyver
  pyver="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  "$PY" - <<'EOF' || exit 1
import sys
if sys.version_info < (3, 11):
    sys.exit("needs Python 3.11+ for the typing syntax used throughout")
EOF
  ok "python $pyver at .venv/bin/python"

  local missing=()
  for mod in pytest fastapi uvicorn pymacaroons z3 mcp; do
    "$PY" -c "import $mod" >/dev/null 2>&1 || missing+=("$mod")
  done
  [[ ${#missing[@]} -eq 0 ]] || die "missing Python packages: ${missing[*]}" \
    "  .venv/bin/pip install -r requirements.txt"
  ok "kernel, api, solver and mcp imports resolve"

  # The claim the verifier rests on is that a counterparty checks an issuer's arithmetic
  # without a solver. That is true of the algorithm and easy to make false in the import
  # graph, so it is checked here as well as in the test suite.
  "$PY" - <<'EOF' || die "the witness verifier pulled z3 into the process" \
      "backend/plumbline/witness.py must not reach a module that imports z3." \
      "Its whole claim is that verification is arithmetic needing no solver."
import sys
import plumbline.witness  # noqa: F401
sys.exit(1 if "z3" in sys.modules else 0)
EOF
  ok "the witness verifier imports no solver"

  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    note "ANTHROPIC_API_KEY is set and will be ignored; this script runs replay only"
  else
    ok "no ANTHROPIC_API_KEY — replay traces, exactly as a judge's laptop would run it"
  fi

  if [[ "$DO_CONSOLE" -eq 1 ]]; then
    command -v node >/dev/null 2>&1 || die "node is not on PATH" \
      "The console is a Vite + React app. Install Node 20+ (https://nodejs.org)," \
      "or run with --skip-console to check the backend alone."
    local nodemajor
    nodemajor="$(node -p 'process.versions.node.split(".")[0]')"
    [[ "$nodemajor" -ge 20 ]] || die "node $nodemajor is too old; Vite 8 needs Node 20+" \
      "  nvm install 20 && nvm use 20"
    command -v npm >/dev/null 2>&1 || die "npm is not on PATH" "Install Node 20+, which ships npm."
    ok "node $(node --version), npm $(npm --version)"

    if [[ ! -d frontend/node_modules ]]; then
      note "frontend/node_modules is absent — installing (once, from the lockfile)"
      npm ci --prefix frontend --no-audit --no-fund \
        || die "npm ci failed in frontend/" \
             "Delete frontend/node_modules and frontend/package-lock.json, then npm install."
    fi
    ok "frontend dependencies present"
  fi

  [[ -f artifacts/plumbline_bench.json ]] || die "artifacts/plumbline_bench.json is missing" \
    "It is committed on purpose: it is the provenance for every latency and gap figure." \
    "Regenerate the FULL run (~5 minutes, nearly all of it in Z3):" \
    "  PYTHONPATH=backend .venv/bin/python -m plumbline.bench" \
    "Do NOT substitute --quick; it writes smaller numbers under the same schema."
  ok "bench artifact present"
}

# ------------------------------------------------------------------------------------------
# 1. Test suites
# ------------------------------------------------------------------------------------------

run_tests() {
  step "backend + agent test suites"
  note "backend/tests and agent/tests, together — the kernel-only path skips two"
  "$PY" -m pytest backend/tests agent/tests -q \
    || die "the test suite is red" \
         "Nothing else in this script means anything until it is green." \
         "Re-run the failures alone:" \
         "  PYTHONPATH=backend:. .venv/bin/python -m pytest backend/tests agent/tests -q --lf"
  ok "both suites pass"
}

# ------------------------------------------------------------------------------------------
# 2. Console
# ------------------------------------------------------------------------------------------

run_console() {
  if [[ "$DO_CONSOLE" -ne 1 ]]; then
    SKIPPED="${SKIPPED}console build and smoke (--skip-console)"$'\n'
    warn "skipping the console build and smoke"
    return
  fi
  step "console — typecheck, production build, rendered smoke assertions"
  npm run build --prefix frontend \
    || die "the console did not build" \
         "  npm run typecheck --prefix frontend    # for the type errors alone"
  ok "production build"

  # The console ships a second witness verifier. A console verifier that is MISSING a check
  # does not error — it prints VERIFIED under a number the evaluator refuses, on the
  # projector. The smoke run hand-builds a forged witness for every failure code.
  npm run smoke --prefix frontend \
    || die "the console smoke assertions failed" \
         "The console re-implements the witness verifier; the smoke run pins it to the" \
         "kernel's. A failure here means the two verifiers disagree, which is worse than" \
         "shipping no console verifier at all."
  ok "console verifier agrees with the kernel"
}

# ------------------------------------------------------------------------------------------
# 3. Bench — verify, never overwrite
# ------------------------------------------------------------------------------------------

run_bench() {
  step "bench harness — quick run to a temp path, committed artifact left alone"
  local tmp
  tmp="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN
  note "the committed artifact holds the FULL run; a --quick run writes smaller numbers"
  note "under the same schema, so it goes to $tmp and never to artifacts/"
  "$PY" -m plumbline.bench --quick --quiet --out "$tmp/plumbline_bench_quick.json" \
    || die "the bench harness did not run" \
         "This measures the allocator against the offline Z3 oracle. Try it directly:" \
         "  PYTHONPATH=backend .venv/bin/python -m plumbline.bench --quick"
  "$PY" - "$tmp/plumbline_bench_quick.json" <<'EOF' || die "the quick bench artifact is malformed" \
      "The harness ran and produced something this script cannot read."
import json, sys
report = json.loads(open(sys.argv[1], encoding="utf-8").read())
for key in ("schema", "headline", "config", "environment", "latency", "gap"):
    if key not in report:
        sys.exit(f"quick artifact is missing {key!r}")
EOF
  ok "harness executes end to end and writes a well-formed artifact"
}

# ------------------------------------------------------------------------------------------
# 4. Headline numbers and the determinism proof
# ------------------------------------------------------------------------------------------

run_headline() {
  step "headline numbers — the fingerprint is computed twice and compared"
  "$PY" scripts/headline.py \
    || die "a headline number could not be reproduced" \
         "If determinism failed, something in the decision path is reading a wall clock," \
         "iterating a set, or hashing an address. Runs must replay."
  ok "every deterministic figure replays; every measured figure carries its conditions"
}

# ------------------------------------------------------------------------------------------
# 5. Agents, on replay
# ------------------------------------------------------------------------------------------

run_agents() {
  step "agent paths — replay traces, no API key, no network"
  local -a paths=(
    "agent.shopper:--scenario injection --replay --json --no-color:prompt injection against a mandate"
    "agent.selector:--replay --json --no-color:guess from marketing copy vs derive over MCP"
    "agent.author:--replay --json --no-color:draft a manifest; the validator decides"
  )
  for entry in "${paths[@]}"; do
    local mod flags label
    IFS=':' read -r mod flags label <<<"$entry"
    # shellcheck disable=SC2086
    "$PY" -m "$mod" $flags >/dev/null \
      || die "$mod failed on its replay trace" \
           "Every agent path must run with no ANTHROPIC_API_KEY present. Reproduce:" \
           "  PYTHONPATH=backend:. .venv/bin/python -m $mod $flags"
    ok "$mod — $label"
  done
}

# ------------------------------------------------------------------------------------------
# 6. Serve
# ------------------------------------------------------------------------------------------

API_PID=""
WEB_PID=""
# Both the signal traps and the poll loop below can reach `shutdown`. It runs once.
SHUTTING_DOWN=0

# `npm run dev` is npm, which execs node, which runs vite. Killing the npm process alone
# orphans the dev server: the port stays bound and the next run dies on "port in use" with
# no visible culprit. So the whole tree goes, depth first.
kill_tree() {
  local pid="$1" child
  [[ -n "$pid" ]] || return 0
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child"
  done
  kill -TERM "$pid" 2>/dev/null || true
}

reap() {
  local pid="$1" tries=20
  [[ -n "$pid" ]] || return 0
  while ((tries--)); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.25
  done
  kill -KILL "$pid" 2>/dev/null || true
}

shutdown() {
  [[ "$SHUTTING_DOWN" -eq 1 ]] && return 0
  SHUTTING_DOWN=1
  trap - INT TERM EXIT
  printf '\n%s   stopping…%s\n' "$DIM" "$RESET"
  kill_tree "$WEB_PID"
  kill_tree "$API_PID"
  reap "$WEB_PID"
  reap "$API_PID"
  wait 2>/dev/null || true
  printf '%s   stopped.%s\n' "$DIM" "$RESET"
}

# True (exit 0) when NOTHING is listening on the port.
#
# Two traps are worth naming. `connect_ex` returns 0 on a SUCCESSFUL connect — that is, on
# a port already taken — so the inversion is spelled out rather than negated inline. And
# vite binds `localhost`, which on macOS resolves to ::1 before 127.0.0.1: probing IPv4
# alone reports a running dev server as down, which is a confusing way to fail.
port_free() {
  "$PY" - "$1" <<'EOF'
import socket, sys

port = int(sys.argv[1])
for family, socktype, proto, _canon, addr in socket.getaddrinfo(
    "localhost", port, proto=socket.IPPROTO_TCP
):
    with socket.socket(family, socktype, proto) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(addr) == 0:
            sys.exit(1)  # something is listening
sys.exit(0)
EOF
}

wait_for_port() {
  local port="$1" name="$2" pid="$3" tries=90
  while ((tries--)); do
    kill -0 "$pid" 2>/dev/null || return 1
    port_free "$port" || return 0
    sleep 0.4
  done
  return 1
}

run_serve() {
  step "serving — uvicorn and vite together"

  for spec in "$API_PORT:API" "$WEB_PORT:console"; do
    local port="${spec%%:*}" what="${spec##*:}"
    port_free "$port" || die "port $port is already in use ($what)" \
      "Something is listening there. Free it, or pass a different port:" \
      "  ./scripts/demo.sh --api-port 8001 --web-port 5174"
  done

  "$PY" -m uvicorn caveat.api:app --port "$API_PORT" --log-level warning &
  API_PID=$!
  trap shutdown INT TERM EXIT

  wait_for_port "$API_PORT" "API" "$API_PID" \
    || die "uvicorn did not come up on port $API_PORT" \
         "Start it alone to see the traceback:" \
         "  PYTHONPATH=backend:. .venv/bin/python -m uvicorn caveat.api:app --port $API_PORT"
  ok "API on http://127.0.0.1:$API_PORT   (docs at /docs)"

  if [[ "$DO_CONSOLE" -ne 1 ]]; then
    warn "console skipped; the API is up on its own — Ctrl-C to stop"
    wait "$API_PID"
    return
  fi

  # Which transport the console gets is decided by ASKING the API, not by assuming. Five of
  # the six screens read one envelope from /api/plumbline/state; if that route is not serving,
  # live mode renders a "valuation service unavailable" banner across all five. Discovering
  # that on stage is the failure this probe exists to prevent.
  local transport="mock" reason
  if "$PY" - "$API_PORT" <<'EOF'
import json, sys, urllib.request
url = f"http://127.0.0.1:{sys.argv[1]}/api/plumbline/state"
try:
    with urllib.request.urlopen(url, timeout=30) as r:
        if r.status != 200:
            sys.exit(1)
        body = json.load(r)
except Exception:
    sys.exit(1)
# An envelope that parses but carries no valuation would light the screens up empty.
sys.exit(0 if isinstance(body, dict) and body else 1)
EOF
  then
    transport="live"
    reason="the console reads the API, not fixtures"
  else
    reason="/api/plumbline/state did not serve an envelope; fixtures instead, badge reads MOCK"
    warn "falling back to the mock transport — $reason"
  fi

  VITE_CAVEAT_TRANSPORT="$transport" \
    npm run dev --prefix frontend -- --port "$WEB_PORT" --strictPort &
  WEB_PID=$!

  wait_for_port "$WEB_PORT" "console" "$WEB_PID" \
    || die "vite did not come up on port $WEB_PORT" \
         "Start it alone to see the error:" \
         "  npm run dev:live --prefix frontend"

  printf '\n%s%s%s\n' "$BOLD" "$(printf '─%.0s' $(seq 74))" "$RESET"
  printf '%s  PLUMBLINE is up.%s\n' "$BOLD" "$RESET"
  printf '    console     http://127.0.0.1:%s\n' "$WEB_PORT"
  printf '    API         http://127.0.0.1:%s      (docs at /docs)\n' "$API_PORT"
  printf '    transport   %s — %s\n' "$transport" "$reason"
  printf '%s  Ctrl-C stops both.%s\n' "$BOLD" "$RESET"
  printf '%s%s%s\n\n' "$BOLD" "$(printf '─%.0s' $(seq 74))" "$RESET"

  # Return as soon as EITHER dies, so a crashed API never leaves a console serving stale
  # panels to a room. `wait -n` would say this in one line and needs bash 4.3; this polls,
  # and runs on the bash macOS actually ships.
  while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do
    sleep 1
  done
  if [[ "$SHUTTING_DOWN" -eq 0 ]]; then
    kill -0 "$API_PID" 2>/dev/null || warn "the API exited on its own; stopping the console"
    kill -0 "$WEB_PID" 2>/dev/null || warn "the console exited on its own; stopping the API"
  fi
  shutdown
}

# ------------------------------------------------------------------------------------------

main() {
  printf '%sPLUMBLINE — clean clone to running demo%s\n' "$BOLD" "$RESET"
  printf '%s%s%s\n' "$DIM" "$REPO_ROOT" "$RESET"

  preflight
  if [[ "$DO_CHECKS" -eq 1 ]]; then
    run_tests
    run_console
    run_bench
    run_headline
    run_agents
  fi

  if [[ -n "$SKIPPED" ]]; then
    printf '\n%sSKIPPED:%s\n' "$YELLOW" "$RESET"
    printf '%s' "$SKIPPED" | while IFS= read -r line; do
      [[ -n "$line" ]] && printf '  - %s\n' "$line"
    done
  fi

  if [[ "$DO_SERVE" -eq 1 ]]; then
    run_serve
  else
    printf '\n%s%sALL CHECKS PASSED%s — run without --check-only to start the demo.\n' \
      "$GREEN" "$BOLD" "$RESET"
  fi
}

main
