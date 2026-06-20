#!/usr/bin/env bash
# AgentData relational connector — macOS installer.
#
# Prerequisites (one-time):
#   • Python 3.10+      →  brew install python@3.12   (or python.org installer)
#   • For SQL Server only: brew install unixodbc msodbcsql18
#   (PostgreSQL / MySQL / SQLite need no system packages — the pip drivers are self-contained.)
#
# What this does: creates a local venv, installs deps, checks your .env, and starts
# the connector. Re-run it any time to restart. Nothing is installed system-wide.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.10+ (brew install python@3.12) and re-run." >&2
  exit 1
fi
# Require 3.10+
"$PY" - <<'EOF' || { echo "ERROR: Python 3.10+ required." >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
EOF

# A venv hardcodes absolute paths in its scripts, so a MOVED/renamed connector folder
# leaves a broken .venv (pip → "bad interpreter"). Detect that and rebuild from scratch.
if [ -d .venv ] && ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
  echo "· existing .venv is broken (folder moved?) — rebuilding …"
  rm -rf .venv
fi
echo "· creating virtual environment (.venv) …"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
# Use `python -m pip` (not the bare `pip` wrapper, whose shebang can be stale).
python -m pip install --quiet --upgrade pip
echo "· installing dependencies …"
python -m pip install --quiet -r requirements.txt
# SQL Server driver: best-effort (it also needs a system ODBC driver at runtime, so a
# build failure here must NOT block Postgres/MySQL/SQLite/Oracle users). The connector's
# startup preflight prints the exact fix if you actually point it at SQL Server.
python -m pip install --quiet pyodbc 2>/dev/null \
  || echo "  · pyodbc not installed — only needed for SQL Server (connector will tell you what to add)"

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "  >> Created .env from the template. Open it and fill in:"
  echo "       AGENT_TOKEN           (from the AgentData UI when you registered this connector)"
  echo "       SOURCE_DATABASE_URL   (a READ-ONLY user — only if this connector reads a data source)"
  echo "       STAGING_DATABASE_URL  (a WRITE/admin user — only if this connector backs a staging DB)"
  echo "     then re-run ./install-macos.sh"
  exit 0
fi

echo "· starting connector (Ctrl-C to stop) …"
exec python agent.py
