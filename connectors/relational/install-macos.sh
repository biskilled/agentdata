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

echo "· creating virtual environment (.venv) …"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
echo "· installing dependencies …"
pip install --quiet -r requirements.txt

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
