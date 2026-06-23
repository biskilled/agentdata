#!/usr/bin/env python3
"""
AgentData on-prem connector.

Runs INSIDE the customer network. Holds the read-only database credentials
locally, opens only OUTBOUND HTTPS to the AgentData backend, polls for jobs, and
executes them against the local database. Only schema metadata, small profiling
samples, and query RESULTS ever leave the network — never raw tables, never the
DB credentials.

Jobs it handles. See ../PROTOCOL.md for the full job contract.
  Read path (uses SOURCE_DATABASE_URL, READ-ONLY user):
  • ping         → liveness
  • list_objects → tables/views (qualified)
  • profile      → one object's columns/stats/samples (ObjectProfile dict)
  • run_sql      → execute a read-only SELECT, return (columns, rows)
  Write path (uses STAGING_DATABASE_URL, WRITE/admin user — staging DBs only):
  • staging_exec → execute one backend-built statement; {sql, params, fetch}
                   → {rows} when fetch (NO read-only guard — backend owns the SQL)

Config via environment (or a local .env — see .env.example):
  AGENTDATA_URL        backend base URL,  e.g. https://mdm-production-1739.up.railway.app
  AGENT_TOKEN          token from POST /api/agent/register (shown once)
  SOURCE_DATABASE_URL  SQLAlchemy URL of the on-prem DB (use a READ-ONLY user!)
                       postgresql+psycopg2://ro_user:pass@localhost:5432/db
                       mysql+pymysql://ro_user:pass@localhost:3306/db
  STAGING_DATABASE_URL optional — SQLAlchemy URL of a WRITE/admin staging DB, only
                       if this connector backs a staging DB (flows write target).
                       Separate creds from SOURCE_DATABASE_URL; write/DDL capable.
  POLL_INTERVAL        seconds between polls when idle (default 1.5)
  MAX_ROWS             hard cap on rows returned by run_sql (default 5000)

Run:  python agent.py     (deps: pip install -r requirements.txt)
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001 — dotenv optional
    pass

AGENTDATA_URL = (os.environ.get("AGENTDATA_URL") or "").rstrip("/")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN") or ""
SOURCE_DATABASE_URL = os.environ.get("SOURCE_DATABASE_URL") or ""
STAGING_DATABASE_URL = os.environ.get("STAGING_DATABASE_URL") or ""
FILES_DIR = os.environ.get("FILES_DIR") or ""   # a local folder this connector can read/write as a file store
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL") or 1.5)
MAX_ROWS = int(os.environ.get("MAX_ROWS") or 5000)
HTTP_TIMEOUT = 60

# TLS context for the outbound HTTPS calls. Prefer certifi's CA bundle (and a
# SSL_CERT_FILE override) so verification works on machines whose Python lacks a
# configured trust store (a common macOS issue). Verification stays ON.
def _ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE")
    if not cafile:
        try:
            import certifi
            cafile = certifi.where()
        except Exception:  # noqa: BLE001 — fall back to the system default store
            cafile = None
    return ssl.create_default_context(cafile=cafile)


_SSL_CTX = _ssl_context()

# ── Profiling helpers (kept in sync with backend/adapters/relational.py) ─────────
_REGEX_PROBES = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "uuid": re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I),
    "phone": re.compile(r"^\+?[\d\s().-]{7,}$"),
    "url": re.compile(r"^https?://"),
    "currency": re.compile(r"^[$€£]\s?\d"),
}
_TYPE_MAP = [
    ("int", "number"), ("numeric", "number"), ("decimal", "number"),
    ("float", "number"), ("double", "number"), ("real", "number"), ("money", "number"),
    ("bool", "boolean"),
    ("date", "time"), ("time", "time"), ("timestamp", "time"), ("datetime", "time"),
    ("char", "string"), ("text", "string"), ("string", "string"), ("clob", "string"),
    ("uuid", "string"), ("json", "string"),
]
_SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "sys", "performance_schema"}


def _normalize_type(raw: str) -> str:
    low = raw.lower()
    for needle, kind in _TYPE_MAP:
        if needle in low:
            return kind
    return "other"


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    if isinstance(v, (dict, list)):
        # JSON/JSONB columns deserialize to dict/list — keep them STRUCTURED so the
        # job's json.dumps emits real JSON (str(dict) would yield an unparseable
        # single-quoted Python repr, which corrupts e.g. a flow's `node`).
        return v
    return str(v)


class LocalDB:
    """Thin read-only wrapper over the on-prem database via SQLAlchemy."""

    def __init__(self, url: str) -> None:
        self._engine: Engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)

    @staticmethod
    def _qualify(schema, table, default_schema):
        return f"{schema}.{table}" if schema and schema != default_schema else table

    @staticmethod
    def _split(qualified):
        if "." in qualified:
            s, t = qualified.split(".", 1)
            return s, t
        return None, qualified

    def ping(self) -> dict:
        with self._engine.connect() as c:
            c.execute(text("SELECT 1"))
        return {}

    def list_objects(self) -> dict:
        insp = inspect(self._engine)
        default_schema = insp.default_schema_name
        names: list[str] = []
        for schema in insp.get_schema_names():
            if (schema or "").lower() in _SYSTEM_SCHEMAS:
                continue
            for tbl in insp.get_table_names(schema=schema):
                names.append(self._qualify(schema, tbl, default_schema))
            for view in insp.get_view_names(schema=schema):
                names.append(self._qualify(schema, view, default_schema))
        return {"objects": names}

    def profile(self, qualified_name: str, sample_rows: int = 500) -> dict:
        schema, table = self._split(qualified_name)
        insp = inspect(self._engine)
        cols_meta = insp.get_columns(table, schema=schema)
        try:
            fks = insp.get_foreign_keys(table, schema=schema)
        except Exception:  # noqa: BLE001
            fks = []
        try:
            pk = insp.get_pk_constraint(table, schema=schema).get("constrained_columns", []) or []
        except Exception:  # noqa: BLE001
            pk = []

        prep = self._engine.dialect.identifier_preparer
        quoted = f"{prep.quote(schema)}.{prep.quote(table)}" if schema else prep.quote(table)

        total = 0
        sample: list[dict] = []
        with self._engine.connect() as c:
            try:
                total = c.execute(text(f"SELECT COUNT(*) FROM {quoted}")).scalar() or 0
            except Exception:  # noqa: BLE001
                total = 0
            if sample_rows > 0:
                try:
                    res = c.execute(
                        text(f"SELECT * FROM {quoted}").execution_options(stream_results=True)
                    )
                    keys = list(res.keys())
                    for i, row in enumerate(res):
                        if i >= sample_rows:
                            break
                        sample.append(dict(zip(keys, row)))
                except Exception:  # noqa: BLE001
                    sample = []

        columns = [self._profile_column(m, sample) for m in cols_meta]
        return {
            "qualified_name": qualified_name,
            "object_type": "table",
            "row_estimate": int(total),
            "pk": list(pk),
            "declared_fks": [
                {
                    "columns": fk.get("constrained_columns", []),
                    "references": f"{fk.get('referred_schema') or ''}.{fk.get('referred_table')}".lstrip("."),
                    "referred_columns": fk.get("referred_columns", []),
                }
                for fk in fks
            ],
            "columns": columns,
        }

    @staticmethod
    def _profile_column(meta: dict, sample: list[dict]) -> dict:
        name = meta["name"]
        raw_type = str(meta.get("type", ""))
        values = [r.get(name) for r in sample]
        non_null = [v for v in values if v is not None]
        n = len(values) or 1
        distinct = len({str(v) for v in non_null})
        null_pct = (len(values) - len(non_null)) / n
        str_vals = [str(v) for v in non_null[:100]]
        patterns = [
            tag for tag, rx in _REGEX_PROBES.items()
            if str_vals and sum(bool(rx.match(s)) for s in str_vals) / len(str_vals) > 0.8
        ]
        samples = [s if len(s) <= 32 and "@" not in s else f"<{len(s)} chars>" for s in str_vals[:5]]
        return {
            "name": name,
            "type": _normalize_type(raw_type),
            "raw_type": raw_type,
            "null_pct": round(null_pct, 4),
            "distinct_count": distinct,
            "sample_values": samples,
            "regex_patterns": patterns,
            "is_nullable": bool(meta.get("nullable", True)),
        }

    def run_sql(self, sql: str, params: dict | None = None, limit: int = 1000) -> dict:
        stripped = sql.strip().rstrip(";").lower()
        if not stripped.startswith(("select", "with")):
            raise ValueError("Only read-only SELECT/WITH queries are permitted")
        cap = min(int(limit or 1000), MAX_ROWS)
        with self._engine.connect() as c:
            res = c.execute(text(sql), params or {})
            cols = list(res.keys())
            rows = [[_jsonable(v) for v in r] for r in res.fetchmany(cap)]
        return {"columns": cols, "rows": rows}


class StagingDB:
    """WRITE/admin executor for connector-backed staging DBs (flows write target).

    Unlike LocalDB there is NO read-only guard: the AgentData backend builds every
    statement (dialect-aware: create-if-missing, truncate/delete, chunked INSERT,
    native UPSERT) and the connector merely executes it. Each `staging_exec` job is
    one statement in its own short transaction — staging re-runs are idempotent, so
    no cross-statement transaction is needed. Uses STAGING_DATABASE_URL (separate,
    write/DDL-capable creds) — keep it distinct from the read-only SOURCE_DATABASE_URL."""

    def __init__(self, url: str) -> None:
        self._engine: Engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)

    def ping(self) -> dict:
        with self._engine.connect() as c:
            c.execute(text("SELECT 1"))
        return {}

    def exec(self, sql: str, params: dict | None, fetch: bool) -> dict:
        with self._engine.begin() as c:
            res = c.execute(text(sql), params or {})
            if fetch and res.returns_rows:
                cols = list(res.keys())
                rows = [[_jsonable(v) for v in r] for r in res.fetchall()]
                # `columns` lets the backend rebuild named dict rows (schedules, flow lists);
                # older backends ignore it and just read positional `rows`.
                return {"columns": cols, "rows": rows}
        return {}


class Files:
    """Read/write/list/delete files under a local folder (FILES_DIR) — so a customer-controlled
    folder is a flow file store. Paths are relative to the root and traversal-guarded; nothing
    escapes FILES_DIR."""

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(os.path.expanduser(root))
        os.makedirs(self.root, exist_ok=True)

    def _abs(self, rel: str) -> str:
        rel = os.path.normpath((rel or "").lstrip("/"))
        if rel == ".":
            rel = ""
        if rel.startswith("..") or os.path.isabs(rel) or ".." in rel.split(os.sep):
            raise ValueError("path traversal is not allowed")
        full = os.path.abspath(os.path.join(self.root, rel))
        if not (full == self.root or full.startswith(self.root + os.sep)):
            raise ValueError("path escapes the files root")
        return full

    def list(self, path: str = "") -> dict:
        base = self._abs(path)
        entries = []
        if os.path.isdir(base):
            for n in sorted(os.listdir(base)):
                fp = os.path.join(base, n)
                isdir = os.path.isdir(fp)
                entries.append({"name": n, "dir": isdir,
                                "size": (0 if isdir else os.path.getsize(fp)),
                                "modified": (None if isdir else __import__("datetime").datetime.utcfromtimestamp(os.path.getmtime(fp)).isoformat())})
        return {"entries": entries}

    def read(self, path: str) -> dict:
        with open(self._abs(path), encoding="utf-8") as f:
            return {"text": f.read()}

    def write(self, path: str, text: str) -> dict:
        full = self._abs(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(text or "")
        return {"ok": True}

    def delete(self, path: str) -> dict:
        full = self._abs(path)
        if os.path.isfile(full):
            os.remove(full)
        return {"ok": True}


class Ctx:
    """Holds the handles a job may need: the read-only source, the write/admin staging DB,
    and a local files folder (each created lazily — only if its env var is set)."""

    def __init__(self, source: "LocalDB | None", staging: "StagingDB | None", files: "Files | None" = None) -> None:
        self.source = source
        self.staging = staging
        self.files = files

    def need_files(self) -> "Files":
        if self.files is None:
            raise ValueError("this connector has no FILES_DIR configured — set it in .env to use it as a file store")
        return self.files

    def need_staging(self) -> StagingDB:
        if self.staging is None:
            raise ValueError("this connector has no STAGING_DATABASE_URL configured "
                             "— it cannot back a staging DB (set it in .env to enable writes)")
        return self.staging

    def need_source(self) -> "LocalDB":
        if self.source is None:
            raise ValueError("this connector has no SOURCE_DATABASE_URL configured")
        return self.source


# ── Backend polling loop ────────────────────────────────────────────────────────
def _api(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{AGENTDATA_URL}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {AGENT_TOKEN}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=_SSL_CTX) as r:
        raw = r.read().decode() or "{}"
    return json.loads(raw)


_HANDLERS = {
    # Read path → SOURCE_DATABASE_URL (read-only).
    "ping": lambda c, p: c.need_source().ping(),
    "list_objects": lambda c, p: c.need_source().list_objects(),
    "profile": lambda c, p: c.need_source().profile(p["qualified_name"], int(p.get("sample_rows", 500))),
    "run_sql": lambda c, p: c.need_source().run_sql(p["sql"], p.get("params") or {}, int(p.get("limit", 1000))),
    # Write path → STAGING_DATABASE_URL (write/admin); backend builds every statement.
    "staging_exec": lambda c, p: c.need_staging().exec(p["sql"], p.get("params") or {}, bool(p.get("fetch"))),
    # File store → FILES_DIR (a local folder); list/read/write/delete, traversal-guarded.
    "file_list": lambda c, p: c.need_files().list(p.get("path") or ""),
    "file_read": lambda c, p: c.need_files().read(p["path"]),
    "file_write": lambda c, p: c.need_files().write(p["path"], p.get("text") or ""),
    "file_delete": lambda c, p: c.need_files().delete(p["path"]),
}


def _engine_name(url: str) -> str | None:
    """Normalized engine name from a SQLAlchemy URL (postgresql→postgres), reported to
    the backend so the UI lists only relevant connectors (e.g. PG for a PG staging DB)."""
    try:
        from sqlalchemy.engine import make_url
        backend = make_url(url).get_backend_name()
    except Exception:  # noqa: BLE001
        return None
    return {"postgresql": "postgres"}.get(backend, backend)


# ── dependency preflight ─────────────────────────────────────────────────────
# (db-dialect → (import name, pip package)). SQLite is stdlib (no driver).
_DRIVERS = {
    "postgresql": ("psycopg2", "psycopg2-binary"),
    "mysql": ("pymysql", "pymysql"),
    "mariadb": ("pymysql", "pymysql"),
    "mssql": ("pyodbc", "pyodbc"),
    "oracle": ("oracledb", "oracledb"),
    "sqlite": (None, None),
}


def _odbc_hint() -> str:
    """OS-specific instruction for installing the SQL Server system ODBC driver (pyodbc
    needs it at runtime — it is NOT a pip package)."""
    import platform
    s = platform.system()
    if s == "Darwin":
        return "brew install unixodbc msodbcsql18"
    if s == "Windows":
        return "Install 'ODBC Driver 18 for SQL Server' from Microsoft (msodbcsql18 / aka.ms/odbc)."
    return ("Debian/Ubuntu: sudo apt-get install -y unixodbc, then Microsoft's msodbcsql18 "
            "(packages.microsoft.com).  RHEL/Fedora: sudo dnf install unixODBC msodbcsql18.")


def _dialect(url: str) -> str:
    return (url.split("://", 1)[0].split("+", 1)[0] or "").lower()


def preflight(urls: list[tuple[str, str]]) -> list[str]:
    """For each configured DB URL, verify its Python driver is importable (and, for SQL
    Server, that a system ODBC driver is present). Returns a list of actionable problems."""
    problems: list[str] = []
    for label, url in urls:
        if not url:
            continue
        d = _dialect(url)
        if d not in _DRIVERS:
            problems.append(f"{label}: unrecognised database type '{d}' (expected "
                            f"postgresql / mysql / mssql / oracle / sqlite).")
            continue
        # SQL Server is special: pyodbc is pip-installed but ALSO needs a system ODBC
        # library/driver, so distinguish "driver not pip-installed" from "system ODBC
        # missing" (importing pyodbc fails on a missing libodbc) — different fixes.
        if d == "mssql":
            try:
                import pyodbc
            except Exception as e:  # noqa: BLE001
                m = str(e).lower()
                if any(t in m for t in ("libodbc", "odbc", "image not found", "library not loaded", "cannot open shared")):
                    problems.append(f"{label}: SQL Server needs the system ODBC driver "
                                    f"(pip cannot provide it):\n      {_odbc_hint()}")
                else:
                    problems.append(f"{label}: missing the SQL Server Python driver:\n"
                                    f"      python -m pip install pyodbc\n"
                                    f"      (it also needs a system ODBC driver: {_odbc_hint()})")
                continue
            if not pyodbc.drivers():
                problems.append(f"{label}: no SQL Server ODBC driver registered. "
                                f"Install it (pip cannot):\n      {_odbc_hint()}")
            continue
        mod, pip_name = _DRIVERS[d]
        if mod:
            try:
                __import__(mod)
            except Exception:  # noqa: BLE001 — driver not installed
                problems.append(f"{label}: missing the {d} Python driver. Install it:\n"
                                f"      python -m pip install {pip_name}")
    return problems


def main() -> int:
    missing = [k for k, v in {
        "AGENTDATA_URL": AGENTDATA_URL, "AGENT_TOKEN": AGENT_TOKEN,
    }.items() if not v]
    if missing:
        print(f"ERROR: missing required env: {', '.join(missing)}", file=sys.stderr)
        print("See connector/.env.example", file=sys.stderr)
        return 2
    if not SOURCE_DATABASE_URL and not STAGING_DATABASE_URL and not FILES_DIR:
        print("ERROR: set SOURCE_DATABASE_URL (read sources), STAGING_DATABASE_URL (write "
              "staging), and/or FILES_DIR (file store) — at least one is required", file=sys.stderr)
        return 2

    # Dependency preflight — fail early with a clear, OS-specific fix instead of a cryptic
    # SQLAlchemy "Can't load plugin" / ODBC error when the connector first hits the DB.
    probs = preflight([("SOURCE_DATABASE_URL", SOURCE_DATABASE_URL),
                       ("STAGING_DATABASE_URL", STAGING_DATABASE_URL)])
    if probs:
        print("ERROR: missing database prerequisites:", file=sys.stderr)
        for p in probs:
            print(f"  - {p}", file=sys.stderr)
        return 2

    # Line-buffer stdout so log lines appear promptly when piped/redirected
    # (e.g. systemd, Docker, nohup) rather than sitting in a block buffer.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001 — older/odd streams
        pass

    source = LocalDB(SOURCE_DATABASE_URL) if SOURCE_DATABASE_URL else None
    staging = StagingDB(STAGING_DATABASE_URL) if STAGING_DATABASE_URL else None
    for label, handle, var in (("SOURCE_DATABASE_URL", source, "source"), ("STAGING_DATABASE_URL", staging, "staging")):
        if handle is None:
            continue
        try:
            handle.ping()
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: cannot connect to {label}: {e}", file=sys.stderr)
            return 2
    files = Files(FILES_DIR) if FILES_DIR else None
    ctx = Ctx(source, staging, files)

    # Capabilities reported on every poll → the backend records engine + roles so the UI
    # can filter connectors by database type. Staging engine wins (it's what staging uses).
    caps = {
        "engine": _engine_name(STAGING_DATABASE_URL or SOURCE_DATABASE_URL or ""),
        "roles": ",".join(r for r, h in (("source", source), ("staging", staging), ("files", files)) if h),
    }
    roles = ", ".join(r for r, h in (("read", source), ("write/staging", staging), ("files", files)) if h)
    print(f"connector up · backend={AGENTDATA_URL} · engine={caps['engine']} · "
          f"polling every {POLL_INTERVAL}s · roles: {roles} · raw data stays on-prem")
    while True:
        try:
            job = _api("/api/agent/poll", {"caps": caps})
        except urllib.error.HTTPError as e:
            print(f"poll HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
            time.sleep(min(POLL_INTERVAL * 4, 15))
            continue
        except Exception as e:  # noqa: BLE001 — network blip; back off and retry
            print(f"poll error: {e}", file=sys.stderr)
            time.sleep(min(POLL_INTERVAL * 4, 15))
            continue

        if not job:
            time.sleep(POLL_INTERVAL)
            continue

        jid, kind, payload = job["id"], job["kind"], job.get("payload") or {}
        handler = _HANDLERS.get(kind)
        try:
            if handler is None:
                raise ValueError(f"unknown job kind '{kind}'")
            result = handler(ctx, payload)
            _api(f"/api/agent/jobs/{jid}/result", {"result": result})
            print(f"job {jid} {kind} → ok")
        except Exception as e:  # noqa: BLE001 — report failure, keep serving
            try:
                _api(f"/api/agent/jobs/{jid}/result", {"error": str(e)[:500]})
            except Exception:  # noqa: BLE001
                pass
            print(f"job {jid} {kind} → error: {e}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
