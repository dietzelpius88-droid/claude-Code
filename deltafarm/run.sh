#!/usr/bin/env bash
# Startet Backend und Frontend lokal. Beides bindet nur an 127.0.0.1.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Virtuelle Umgebung fehlt. Einmalig: make setup"
  exit 1
fi

if [ ! -f .env ]; then
  echo "Hinweis: keine .env vorhanden - es gelten die Standardwerte"
  echo "         (DRY_RUN=true, Umgebung=testnet)."
fi

backend_pid=""
frontend_pid=""
aufraeumen() {
  [ -n "$backend_pid" ] && kill "$backend_pid" 2>/dev/null || true
  [ -n "$frontend_pid" ] && kill "$frontend_pid" 2>/dev/null || true
}
trap aufraeumen EXIT INT TERM

echo "Backend  -> http://127.0.0.1:${DELTAFARM_PORT:-8787}"
.venv/bin/python -m app.main &
backend_pid=$!

if [ -d web/node_modules ]; then
  echo "Frontend -> http://127.0.0.1:5173"
  (cd web && npm run dev -- --host 127.0.0.1) &
  frontend_pid=$!
else
  echo "Frontend uebersprungen (web/node_modules fehlt - einmalig: make setup)"
fi

wait
