"""Server-rendered HTML pages over the same query functions as REST and MCP."""

from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from bugsigdb_app import queries

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
router = APIRouter(default_response_class=HTMLResponse)

EVIDENCE = {
    "exp": "Experimental evidence",
    "igc": "Inferred from genomic context",
    "tas": "Traceable author statement",
    "nas": "Non-traceable author statement",
    "tax": "Inferred from taxonomy",
    "asr": "Predicted by ancestral state reconstruction",
}
ITALIC_RANKS = {"genus", "species", "strain"}


def wiki(*parts: str | None) -> str:
    return "https://bugsigdb.org/" + "/".join(quote((p or "").replace(" ", "_")) for p in parts)


def group_traits(traits: list[dict]) -> list[dict]:
    """One entry per attribute; one value per distinct value (best score, all sources)."""
    by_attr: dict[str, dict[str, dict]] = defaultdict(dict)
    for t in traits:
        v = by_attr[t["attribute"]].setdefault(t["value"], {
            "value": t["value"], "score": t["score"], "evidence": set(), "sources": set(),
            "type": t["attribute_type"] or "", "frequency": t["frequency"]})
        if (t["score"] or 0) > (v["score"] or 0):
            v["score"], v["frequency"] = t["score"], t["frequency"]
        v["evidence"].add(t["evidence"])
        v["sources"].add(t["attribute_source"])
    out = []
    for attr, values in sorted(by_attr.items()):
        vals = sorted(values.values(), key=lambda v: -(v["score"] or 0))
        kind = "binary" if vals[0]["type"].startswith("binary") else (
            "numeric" if vals[0]["type"].startswith("numeric") else "multistate")
        for v in vals:
            v["evidence"] = sorted(e for e in v["evidence"] if e)
            v["sources"] = sorted(s for s in v["sources"] if s)
            v["predicted"] = v["evidence"] == ["asr"]
        out.append({"attribute": attr, "kind": kind, "values": vals})
    return out


templates.env.globals.update(wiki=wiki, EVIDENCE=EVIDENCE, ITALIC_RANKS=ITALIC_RANKS)
templates.env.filters["num"] = lambda n: f"{n:,}" if isinstance(n, int) else n
templates.env.filters["commas"] = lambda s: s.replace(",", ", ").replace(",  ", ", ")


def sig(v: str | None) -> str | None:
    """Numeric trait values arrive as raw floats (2590.88986211578): 2,591 / 47.6 / 0.7."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return v
    return f"{round(x):,}" if abs(x) >= 100 else f"{x:.3g}"


templates.env.filters["sig"] = sig


def render(request: Request, name: str, status: int = 200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status)


@router.get("/")
async def home(request: Request):
    pool = request.app.state.pool
    return render(request, "home.html",
                  stats=await queries.stats(pool),
                  genera=await queries.top_taxa(pool, "genus", 12),
                  species=await queries.top_taxa(pool, "species", 12))


@router.get("/search")
async def search(request: Request, q: str = ""):
    if not q.strip():
        return RedirectResponse("/", status_code=303)
    results = await queries.search_taxa(request.app.state.pool, q, 50)
    if len(results) == 1 or (results and results[0]["name"].lower() == q.strip().lower()):
        return RedirectResponse(f"/taxon/{results[0]['tax_id']}", status_code=303)
    return render(request, "search.html", q=q, results=results)


@router.get("/taxon/{tax_id}")
async def taxon(request: Request, tax_id: int):
    card = await queries.get_taxon(request.app.state.pool, tax_id, signature_limit=500)
    if card is None:
        return render(request, "404.html", status=404, tax_id=tax_id)
    conds = card["conditions"]
    scale = max((max(c["increased"], c["decreased"]) for c in conds), default=1) or 1
    return render(request, "taxon.html", t=card, traits=group_traits(card["traits"]), scale=scale,
                  max_site=max((b["n"] for b in card["body_sites"]), default=1))
