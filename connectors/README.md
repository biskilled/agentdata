# AgentData connectors

A **connector** runs inside a customer's network and lets a deployed (cloud) AgentData
backend reach private databases **without exposing them to the internet or copying their
data out**. It opens only **outbound** HTTPS, polls the backend for jobs, runs them
locally, and returns only metadata / query results.

This folder holds one connector per integration family. Each subfolder is a
self-contained package a customer downloads and installs.

| Folder | Connects | Status |
|--------|----------|--------|
| [`relational/`](relational/) | Relational databases — PostgreSQL, MySQL, SQL Server, SQLite (read sources and/or write staging DBs) | ✅ available |

- **`PROTOCOL.md`** — the job contract shared by every connector and the backend
  (`backend/adapters/agent.py`): the job kinds, their payloads/results, and the
  source(read-only) vs staging(write/admin) role split. Read this if you're building or
  re-implementing a connector (any language — it just has to speak the protocol).

Pick the connector for the database you're connecting and follow its README. Each ships
with per-OS installers (macOS / Windows / Linux) and lists its prerequisites.
