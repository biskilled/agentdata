# Quickstart

Connect a database to AgentData and run your first flow. Two ways to connect a database:

- **Cloud / reachable** — the hosted backend can reach it directly (a managed/cloud DB or
  one exposed over a VPN). Just paste a connection string in the UI; no connector needed.
- **On-premise** — the DB is on your private network. Install a **connector** next to it
  (this repo); the backend reaches it over the connector's outbound HTTPS poll.

This guide covers the on-premise path with the **relational connector**.

## 1. Sign in

Open your AgentData UI (e.g. `https://agentdata.ui.<your-domain>`) and sign in.

## 2. Register a connector

In the UI, go to **Sources → Add → On my network** (for a data source) or
**Flows → Staging → Add → On-premise** (for a write/staging database). Click **Register**
— you'll get a **one-time token**. Keep it; it's shown only once.

> A connector serves two roles, and a single connector can do both:
> - **Source** (read) — a **read-only** DB user, for discovery + queries.
> - **Staging** (write) — a **write/admin** DB user, for flows that write results.

## 3. Install + run the connector

On a machine that can reach your database:

1. Download this repo (or just the [`connectors/relational/`](connectors/relational/) folder).
2. Save the `.env` the UI generated next to the connector (or copy `.env.example` → `.env`
   and fill in `AGENT_TOKEN` + your database URL(s)):
   ```
   AGENTDATA_URL=https://agentdata.<your-domain>
   AGENT_TOKEN=<from the UI, shown once>
   SOURCE_DATABASE_URL=postgresql+psycopg2://ro_user:pass@localhost:5432/db   # read-only (sources)
   STAGING_DATABASE_URL=postgresql+psycopg2://admin:pass@localhost:5432/staging # write/admin (staging)
   ```
3. Run the installer for your OS (creates a venv, installs deps, starts it):
   ```bash
   ./install-macos.sh      # macOS
   ./install-linux.sh      # Linux
   ```
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install-windows.ps1   # Windows
   ```
   You should see `connector up · … · engine=postgres · …`.

Prerequisites and per-database driver notes are in
[`connectors/relational/README.md`](connectors/relational/README.md).

## 4. Confirm it's online

Back in the UI, the connector shows **● online** within ~30s. Click **Test** to confirm
(for staging it also checks the control tables). It authenticates with the one-time token
on every poll — no inbound login, no OAuth.

## 5. Use it

- **Source:** finish the Add-source step and run discovery — your tables are profiled
  through the connector; only metadata and query results come back.
- **Staging:** select the online connector and click **Use as staging DB**.

## 6. Build a flow (optional)

Flows → New flow opens a visual builder: drag sources onto rows, map source → target
columns (with AI auto-map), add calculated columns, verify, and run. Targets become
sources for later rows. Results can land in a staging table, a key/value dictionary, or
a CSV you can review.

## Security model

- Outbound HTTPS only — no inbound port, works behind NAT/firewalls.
- DB credentials stay in the connector's `.env` on your machine; **never** sent to AgentData.
- The source role is restricted to `SELECT`/`WITH`; give it a least-privilege read-only user.
- The token is stored hashed on the backend and can be rotated from the UI at any time.
