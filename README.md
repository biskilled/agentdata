# AgentData

**AgentData** is a Data Fabric / Semantic Layer that auto-discovers business entities
across heterogeneous databases, maintains a unified semantic model, and serves
analytical queries through a REST API and MCP — so people and LLMs can ask questions
of your data in plain language.

This is the **public** repository for AgentData: the **on-prem connectors** you install
to connect private databases, the connector **protocol**, and **getting-started docs**.
The product itself (backend, UI, semantic engine) is hosted — you don't run it from here.

## What's here

| Path | What it is |
|------|------------|
| [`connectors/`](connectors/) | On-prem connectors you install on your own network |
| [`connectors/relational/`](connectors/relational/) | The relational connector — PostgreSQL, MySQL, SQL Server, SQLite (read sources and/or write staging DBs) |
| [`connectors/PROTOCOL.md`](connectors/PROTOCOL.md) | The job contract between a connector and the backend (build your own in any language) |
| [`QUICKSTART.md`](QUICKSTART.md) | Connect a database and run your first flow |

## Why a connector?

A connector lets the hosted AgentData backend reach a database on **your** network
**without exposing it to the internet and without copying its data out**. It runs inside
your network, holds the database credentials locally, makes only **outbound HTTPS** calls
(it polls — no inbound port), and returns only metadata and query/flow results. Raw rows
and credentials never leave your machine.

```
 AgentData (hosted)  ◄── outbound HTTPS poll ── connector (your network) ──► your database
        │                                              │
        └──────────── metadata / results only ─────────┘
```

## Get started

See **[QUICKSTART.md](QUICKSTART.md)**, then the connector you need —
e.g. **[connectors/relational/](connectors/relational/)** for SQL databases.

## License

[MIT](LICENSE) — you're free to read, run, and adapt the connectors for connecting your
own systems to AgentData.
