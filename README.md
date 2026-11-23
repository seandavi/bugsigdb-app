# bugsigdb-app

Web app for browsing [BugSigDB](https://bugsigdb.org): pages for each taxon,
study, signature and so on.

Design: #1. A loader fetches the public BugSigDB and bugphyzz exports into
Postgres; one FastAPI app serves REST (`/api`) and MCP (`/mcp`) over the
same query functions (`src/bugsigdb_app/queries.py`).

Live proof of concept: https://bugsigdb-poc.cancerdatasci.org
(`/api/taxa?q=akkermansia`, `/api/taxa/239934`, MCP at `/mcp`).

## Develop

```sh
just up      # Postgres + app in compose, app on 127.0.0.1:8042
just load    # fetch the exports and swap them in (~5 s)
just test
```

Deploy on onclappc02: `deploy/onclappc02/` (`just deploy`, `just load`).
