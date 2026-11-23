"""FastAPI app: HTML pages, REST under /api, MCP (streamable HTTP) at /mcp."""

import os
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from bugsigdb_app import queries, web

state: dict[str, asyncpg.Pool] = {}


def pool() -> asyncpg.Pool:
    return state["pool"]


mcp = MCPServer(
    "bugsigdb",
    instructions="BugSigDB: published microbiome signatures (sets of taxa reported increased or decreased "
    "in a case group vs a control group), with bugphyzz traits for each taxon. "
    "Hierarchy: study > experiment (one comparison: condition, body site, groups) > signature (direction + taxa). "
    "Start with search (any entity, any ID); search_taxa, search_studies and search_conditions narrow to one kind. "
    "get_condition ranks taxa by how consistently they go up or down in that condition (sign test). "
    "Study IDs are usually PMIDs; signature IDs look like bsdb:<study>/<experiment>/<signature>.",
)


@mcp.tool()
async def search(q: str, per_kind: int = 5, kind: str | None = None) -> dict[str, Any]:
    """Search everything at once: taxa, conditions and studies by name, title, author, keyword, or by exact ID
    (NCBI tax ID, PMID, DOI or DOI prefix, EFO/MONDO ID). Tolerates typos. Results are grouped by kind,
    best group first, each with a total count. `kind` limits to one of taxon, condition, study.
    A query like bsdb:<study>/<experiment>/<signature> returns a `redirect` path instead."""
    return await queries.search(pool(), q, min(per_kind, 50), kind)


@mcp.tool()
async def search_taxa(q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Find taxa by (partial) name or exact NCBI tax ID, most-studied first."""
    return await queries.search_taxa(pool(), q, limit)


@mcp.tool()
async def get_taxon(tax_id: int, signature_limit: int = 100) -> dict[str, Any] | None:
    """Taxon card: lineage, children, BugSigDB signatures (own and descendants'), bugphyzz traits."""
    return await queries.get_taxon(pool(), tax_id, signature_limit)


@mcp.tool()
async def search_studies(q: str = "", limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    """Find studies by text in title, authors, journal, PMID or DOI; newest first."""
    return await queries.search_studies(pool(), q, limit, offset)


@mcp.tool()
async def get_study(study_id: str) -> dict[str, Any] | None:
    """A study (usually keyed by PMID) with all its experiments and their signatures' taxa."""
    return await queries.get_study(pool(), study_id)


@mcp.tool()
async def get_experiment(study_id: str, experiment_id: int) -> dict[str, Any] | None:
    """One experiment: compared groups and sizes, condition, body site, methods, diversity, signatures with taxa."""
    return await queries.get_experiment(pool(), study_id, experiment_id)


@mcp.tool()
async def get_signature(study_id: str, experiment_id: int, signature_id: int) -> dict[str, Any] | None:
    """One signature (bsdb:<study>/<experiment>/<signature>): direction, taxa, context, and similar signatures."""
    return await queries.get_signature(pool(), study_id, experiment_id, signature_id)


@mcp.tool()
async def search_conditions(q: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Find conditions (EFO/MONDO terms) by name or ontology ID, with study and signature counts."""
    return await queries.list_conditions(pool(), q, limit)


@mcp.tool()
async def get_condition(ontology_id: str, taxa_limit: int = 50) -> dict[str, Any] | None:
    """A condition's studies, body sites, and taxa ranked by consistency of direction (sign-test p)."""
    return await queries.get_condition(pool(), ontology_id, taxa_limit)


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
async def lifespan(app: FastAPI):
    state["pool"] = app.state.pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    async with mcp.session_manager.run():
        yield
    await state.pop("pool").close()


app = FastAPI(title="bugsigdb-app", lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(web.router)
app.mount("/static", StaticFiles(directory=web.HERE / "static"), name="static")


def found(x, what: str):
    if x is None:
        raise HTTPException(404, f"{what} not found")
    return x


@app.get("/api/search", tags=["search"])
async def api_search(q: str, per_kind: int = 5, kind: str | None = None):
    """Taxa, conditions and studies matching `q` (names, titles, authors, IDs; typo-tolerant), grouped by kind."""
    if kind is not None and kind not in queries.SEARCH_KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(queries.SEARCH_KINDS)}")
    return await queries.search(pool(), q, min(per_kind, 200), kind)


@app.get("/api/taxa", tags=["taxa"])
async def api_search_taxa(q: str, limit: int = 20):
    return await queries.search_taxa(pool(), q, min(limit, 200))


@app.get("/api/taxa/{tax_id}", tags=["taxa"])
async def api_get_taxon(tax_id: int, signature_limit: int = 100):
    return found(await queries.get_taxon(pool(), tax_id, min(signature_limit, 1000)), "taxon")


@app.get("/api/studies", tags=["studies"])
async def api_search_studies(q: str = "", limit: int = 50, offset: int = 0):
    return await queries.search_studies(pool(), q, min(limit, 500), offset)


@app.get("/api/studies/{study_id}", tags=["studies"])
async def api_get_study(study_id: str):
    return found(await queries.get_study(pool(), study_id), "study")


@app.get("/api/studies/{study_id}/experiments/{experiment_id}", tags=["studies"])
async def api_get_experiment(study_id: str, experiment_id: int):
    return found(await queries.get_experiment(pool(), study_id, experiment_id), "experiment")


@app.get("/api/studies/{study_id}/experiments/{experiment_id}/signatures/{signature_id}", tags=["studies"])
async def api_get_signature(study_id: str, experiment_id: int, signature_id: int):
    return found(await queries.get_signature(pool(), study_id, experiment_id, signature_id), "signature")


@app.get("/api/conditions", tags=["conditions"])
async def api_list_conditions(q: str = "", limit: int = 2000):
    return await queries.list_conditions(pool(), q, limit)


@app.get("/api/conditions/{ontology_id}", tags=["conditions"])
async def api_get_condition(ontology_id: str, taxa_limit: int = 150, min_signatures: int = 2):
    return found(await queries.get_condition(pool(), ontology_id, taxa_limit, min_signatures), "condition")


@app.get("/healthz")
async def healthz():
    await pool().fetchval("select 1")
    return {"ok": True}


app.mount("/", mcp_app)  # serves /mcp; mounted last so /api routes win
