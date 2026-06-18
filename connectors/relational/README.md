# AgentData relational connector

Connect an on-premise / private-network **relational** database (PostgreSQL, MySQL,
SQL Server, SQLite) to a deployed AgentData backend **without exposing the database to
the internet and without copying its data to the cloud**. The connector runs inside
your network, holds the DB credentials locally, and makes only **outbound** HTTPS calls
to the backend (it *polls* for work — no inbound port, works behind NAT/firewalls).

It serves two roles; a single connector can do one or both:

| Role | Env var | DB user permissions | Used for |
|------|---------|---------------------|----------|
| **Source** (read) | `SOURCE_DATABASE_URL` | **READ-ONLY** — `SELECT` + catalog/`information_schema` (metadata). Nothing else. | Discovery + analytical queries. The connector rejects anything that isn't `SELECT`/`WITH`. |
| **Staging** (write) | `STAGING_DATABASE_URL` | **WRITE / admin** — `CREATE TABLE`, `INSERT`, `UPDATE`, `DELETE` (and `CREATE DATABASE` if AgentData should create the staging DB for you). | Flows write their results here. The backend builds every statement; the connector executes it. |

Keep them as **separate credentials**: a least-privilege read-only user for sources, a
write/admin user scoped to the staging database/schema for staging.

## What crosses the boundary (and what never does)

| Leaves your network | Stays on your network |
|---------------------|------------------------|
| Table/column names & types (schema) | The database itself |
| Small profiling **samples** (≤5 short values/column, emails/long values redacted) | Full table data |
| Query / flow **results** (filtered/aggregated, row-capped) | Raw rows; the query runs locally |
| — | **DB credentials** (only in this machine's `.env`) |

## Prerequisites per OS

| OS | Required | SQL Server only (optional) |
|----|----------|----------------------------|
| **macOS** | Python 3.10+ (`brew install python@3.12`) | `brew install unixodbc msodbcsql18` |
| **Windows** | Python 3.10+ (python.org, tick *Add to PATH*, or `winget install Python.Python.3.12`) | "ODBC Driver 18 for SQL Server" |
| **Linux** | Python 3.10+ + venv (`apt install python3 python3-venv python3-pip`) | `msodbcsql18` + unixODBC |

PostgreSQL / MySQL / SQLite need **no system packages** — their pip drivers are
self-contained (SQLite is built into Python). DB drivers come from `requirements.txt`.

## Install

1. **Register the connector** in the AgentData UI (Sources → Add → *On my network*, or
   Flows → Staging → *On-premise*). It issues a **token (shown once)** and an `.env`
   template — save it next to this README as `.env`.

2. **Run the installer for your OS** (creates a local `.venv`, installs deps, then starts it):

   ```bash
   ./install-macos.sh      # macOS
   ./install-linux.sh      # Linux
   ```
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install-windows.ps1   # Windows
   ```

   The first run creates `.env` from the template if it's missing and stops so you can
   fill in `AGENT_TOKEN` + the database URL(s); run it again to start. You should see:

   ```
   connector up · backend=https://… · polling every 1.5s · roles: read, write/staging · raw data stays on-prem
   ```

3. **Verify connectivity** back in the UI: the connector shows **online**, and the
   **Test** button confirms it (for staging, it also checks the control tables exist).

## How the backend authenticates / tests it

- **Auth:** a per-connector **bearer token** issued once by `POST /api/agent/register`
  (stored only as a SHA-256 hash on the backend). The connector sends it on every
  outbound poll — there is no inbound login, no OAuth, no session to manage.
- **Liveness:** every poll updates `last_seen_at`; the UI shows **online** if seen in
  the last ~30 s. The **Test** button enqueues a `ping` (source) / control-table probe
  (staging) job and waits for the connector to answer.

## How it works

```
 backend ──enqueue job──► mng_agent_jobs ◄──poll (outbound HTTPS)── connector ──► on-prem DB
   ▲                                                                   │
   └──────────────── result (metadata / rows only) ───────────────────┘
```

The backend's `agent` adapter (`backend/adapters/agent.py`) turns each call into a job
(`ping`/`list_objects`/`profile`/`run_sql` for sources; `staging_exec` for staging); the
connector claims it via `POST /api/agent/poll`, runs it locally, and returns the result
via `POST /api/agent/jobs/{id}/result`. See `../PROTOCOL.md` for the full contract.

## Manual run (without the installer)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill AGENT_TOKEN + SOURCE_/STAGING_DATABASE_URL
python agent.py
```

## Security notes

- Give the source role a **read-only** DB account; give the staging role a write/admin
  account scoped to the staging database/schema only.
- The `AGENT_TOKEN` authenticates this connector; treat it as a secret. Rotate by
  registering a new connector and deleting the old one (`DELETE /api/agent/{id}`).
- `MAX_ROWS` caps how many rows any single source query may return.
- Profiling redacts emails and values longer than 32 chars; tune in `agent.py`
  (`_profile_column`) if you need stricter masking.

## Files

- `agent.py` — the connector (stdlib `urllib` + SQLAlchemy).
- `requirements.txt` — deps (uncomment the driver for your database).
- `.env.example` — config template.
- `install-{macos,linux}.sh`, `install-windows.ps1` — per-OS installers.
