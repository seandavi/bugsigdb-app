"""FastAPI app: REST under /api, MCP (streamable HTTP) at /mcp."""

import os
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from bugsigdb_app import queries

state: dict[str, asyncpg.Pool] = {}


def pool() -> asyncpg.Pool:
    return state["pool"]


mcp = MCPServer(
    "bugsigdb",
    instructions="BugSigDB microbial signatures and bugphyzz traits, organized by NCBI taxon. "
    "Use search_taxa to find a tax_id, then get_taxon for its card.",
)


@mcp.tool()
async def search_taxa(q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Find taxa by (partial) name or exact NCBI tax ID, most-studied first."""
    return await queries.search_taxa(pool(), q, limit)


@mcp.tool()
async def get_taxon(tax_id: int, signature_limit: int = 100) -> dict[str, Any] | None:
    """Taxon card: lineage, children, BugSigDB signatures (own and descendants'), bugphyzz traits."""
    return await queries.get_taxon(pool(), tax_id, signature_limit)


# DNS-rebinding protection: allow only the hosts we actually serve on.
hosts = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
mcp_app = mcp.streamable_http_app(
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h for host in hosts for h in (host, f"{host}:*")],
        allowed_origins=[f"{s}://{h}" for host in hosts for s in ("http", "https") for h in (host, f"{host}:*")],
    ),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["pool"] = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    async with mcp.session_manager.run():
        yield
    await state.pop("pool").close()


app = FastAPI(title="bugsigdb-app", lifespan=lifespan)


@app.get("/api/taxa")
async def api_search_taxa(q: str, limit: int = 20):
    return await queries.search_taxa(pool(), q, min(limit, 200))


@app.get("/api/taxa/{tax_id}")
async def api_get_taxon(tax_id: int, signature_limit: int = 100):
    card = await queries.get_taxon(pool(), tax_id, min(signature_limit, 1000))
    if card is None:
        raise HTTPException(404, "taxon not in BugSigDB")
    return card


@app.get("/healthz")
async def healthz():
    await pool().fetchval("select 1")
    return {"ok": True}


app.mount("/", mcp_app)  # serves /mcp; mounted last so /api routes win
