# AgentData connector — job protocol

This is the contract between the AgentData **backend** and the **on-prem connector**
(`connector/agent.py`). It exists so the two halves can be built independently: the
backend defines the jobs; the connector implements them. The reference implementation
in `connector/agent.py` is a working starting point — you may reimplement the connector
in any language (Node, Go, …) as long as it speaks this protocol.

## Transport — outbound HTTPS poll only

The connector runs **inside the customer network** and makes only **outbound HTTPS**
calls to the backend. There is **no inbound port**, no firewall opening, no Railway
port to configure for the connector. It authenticates with a bearer `AGENT_TOKEN`
(issued once by `POST /api/agent/register`).

Loop:

1. `POST /api/agent/poll {caps?: {engine, roles}}` → returns one queued job
   `{id, kind, payload}` or `{}` (idle). The optional `caps` lets the connector
   self-report what it serves so the UI can list only relevant connectors:
   - `engine`: normalized DB engine — `postgres` | `mysql` | `mssql` | `sqlite`
     (map SQLAlchemy's `postgresql` → `postgres`). Prefer the staging engine when set,
     else the source engine.
   - `roles`: comma-joined of `source` and/or `staging` (whichever URL is configured).
   Sent on every poll; the backend stores the last values (omitting `caps` keeps them).
2. Run the job locally against the appropriate database.
3. `POST /api/agent/jobs/{id}/result {result: {...}}` on success, or
   `{error: "..."}` on failure.

Jobs are persisted in `mng_agent_jobs` (`status`: queued → claimed → done/error). The
backend side of this round-trip is `backend/adapters/agent.py::RemoteAgentAdapter._job`.

## Two database roles

A single connector may serve one or both roles; it pings whichever is configured at
startup and refuses jobs for a role it has no URL for.

| Role | Env var | DB user | Used by |
|------|---------|---------|---------|
| **source** (read) | `SOURCE_DATABASE_URL` | **READ-ONLY** | discovery + queries |
| **staging** (write) | `STAGING_DATABASE_URL` | **WRITE / admin** (CREATE TABLE, INSERT, …) | flows write target |

Keep them as **separate credentials**. The source path enforces a SELECT/WITH guard;
the staging path does **not** — see the safety note below.

## Read jobs (source role)

These mirror `backend/adapters/relational.py` (the source of truth for the profiling
shape). All run against `SOURCE_DATABASE_URL` with the read-only guard.

| `kind` | `payload` | `result` |
|--------|-----------|----------|
| `ping` | `{}` | `{}` |
| `list_objects` | `{}` | `{objects: ["schema.table", …]}` |
| `profile` | `{qualified_name, sample_rows}` | `ObjectProfile` dict (`columns`, `row_estimate`, `pk`, `declared_fks`, …) |
| `run_sql` | `{sql, params, limit}` | `{columns: [...], rows: [[...], …]}` — **SELECT/WITH only** |

## Write jobs (staging role)

A staging DB whose backend `conn_ref` is `agent://<agent_id>` is reached through the
connector. The **backend builds every SQL statement** (dialect-aware: create-if-missing,
truncate / delete-by-key / chunked multi-row INSERT, native UPSERT) using
`backend/transform/dialect.py` + `backend/adapters/staging/<engine>.sql`. The connector
is a **dumb executor**: it runs the statement verbatim and returns rows only when asked.

| `kind` | `payload` | `result` |
|--------|-----------|----------|
| `staging_exec` | `{sql: str, params: {name: value}, fetch: bool}` | `{rows: [[...], …]}` when `fetch` is true, else `{}` |

Semantics the connector must honor:

- Bind `params` by name (`:name`) — the backend always uses named binds, never string
  interpolation of values.
- Each `staging_exec` is **one statement in its own short transaction**. There is no
  multi-statement transaction across jobs; the load steps (create → truncate/delete →
  insert chunks) are individually idempotent on a staging table, so a retry is safe.
- When `fetch` is true and the statement returns rows, return them as `{rows: [[...]]}`
  (list of positional lists). When it returns no rows (DDL, INSERT), return `{}` —
  `scalar()` on the backend reads `rows[0][0]`, so an absent/empty `rows` reads as NULL.
- JSON-encode values safely: `Decimal → float`, `date/datetime → ISO string`,
  `bytes → null` (or a placeholder). The backend already applies the same normalization
  to outbound bind values.

### Control-table setup

When a connector-backed staging DB is registered (`POST /api/flows/staging` or the
`PUT` edit with `conn_url` = `agent://<id>`), the backend sends the per-engine control
DDL as a sequence of `staging_exec` jobs (no `fetch`) — one statement each — via
`backend/adapters/agent.py::staging_init`. The DDL comes from
`dialect(engine).init_statements()`. The connector needs no special handling: they are
ordinary `staging_exec` statements.

`POST /api/flows/staging/{id}/test` for a connector-backed DB runs a single
`staging_exec` probe (`SELECT 1 FROM flows WHERE 1=0`) to confirm the control tables
exist (`staging_present`).

## Safety notes

- **No read-only guard on `staging_exec`** — by design: the backend owns the SQL and
  must be trusted for the staging role. This is why the staging DB user is scoped to
  the staging database/schema only, and why `SOURCE_DATABASE_URL` (untrusted-query
  surface) keeps its SELECT/WITH guard and a *different*, read-only user.
- The connector should still cap result sizes for `run_sql` (`MAX_ROWS`); `staging_exec`
  fetches are backend-internal (introspection) and small.
- Credentials for both roles stay on the customer machine; only metadata, small
  profiling samples, and query/exec results cross the boundary.

## Engines

`postgres`, `mysql`, `mssql`, `sqlite`. The connector only needs the matching
SQLAlchemy driver installed for the URL(s) it serves (`sqlite` needs none — stdlib).
The dialect differences (identifier quoting, types, UPSERT) are all resolved
backend-side before the SQL reaches the connector.
