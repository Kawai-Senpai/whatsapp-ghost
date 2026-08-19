#!/usr/bin/env bash
# Start WhatsApp Ghost. Creates the virtualenv and .env on first run.
#
#   ./start.sh                  # http://127.0.0.1:8787
#   ./start.sh --port 9000      # a different port
#   ./start.sh --reload         # auto-reload on source changes
#   ./start.sh --open           # also open the console in a browser
set -euo pipefail

cd "$(dirname "$0")"

HOST=127.0.0.1
PORT=8787
OPEN=0
EXTRA=()

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --open) OPEN=1; shift ;;
    --reload) EXTRA+=(--reload); shift ;;
    --mode) EXTRA+=(--mode "$2"); shift 2 ;;
    -h|--help)
      sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) EXTRA+=("$1"); shift ;;
  esac
done

# Windows venvs put executables in Scripts/, POSIX in bin/.
if [ -x .venv/Scripts/python.exe ]; then
  PYTHON=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
else
  echo "No virtualenv found, creating one..."
  if command -v uv >/dev/null 2>&1; then
    uv sync
  else
    python -m venv .venv
    if [ -x .venv/Scripts/python.exe ]; then PYTHON=.venv/Scripts/python.exe; else PYTHON=.venv/bin/python; fi
    "$PYTHON" -m pip install --quiet --upgrade pip
    "$PYTHON" -m pip install --quiet -e .
  fi
  if [ -x .venv/Scripts/python.exe ]; then PYTHON=.venv/Scripts/python.exe; else PYTHON=.venv/bin/python; fi
fi

[ -f .env ] || { cp .env.example .env && echo "Created .env from .env.example"; }

# The startup banner prints arrows that crash on Windows' cp1252 console.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

echo "WhatsApp Ghost starting on http://$HOST:$PORT"
echo "  Console  http://$HOST:$PORT/console"
echo "  Phone    http://$HOST:$PORT/phone"
echo "  Docs     http://$HOST:$PORT/docs"
echo

if [ "$OPEN" = "1" ]; then
  ( sleep 2
    if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://$HOST:$PORT/console"
    elif command -v open >/dev/null 2>&1; then open "http://$HOST:$PORT/console"
    elif command -v start >/dev/null 2>&1; then start "http://$HOST:$PORT/console"
    fi ) >/dev/null 2>&1 &
fi

exec "$PYTHON" ghost.py start --host "$HOST" --port "$PORT" ${EXTRA+"${EXTRA[@]}"}
