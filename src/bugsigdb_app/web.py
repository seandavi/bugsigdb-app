"""Server-rendered HTML pages over the same query functions as REST and MCP."""

import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

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
        return render(request, "404.html", status=404, what=f"NCBI taxon {tax_id}", tax_id=tax_id)
    conds = card["conditions"]
    scale = max((max(c["increased"], c["decreased"]) for c in conds), default=1) or 1
    return render(request, "taxon.html", t=card, traits=group_traits(card["traits"]), scale=scale,
                  max_site=max((b["n"] for b in card["body_sites"]), default=1))


def pfmt(p: float) -> str:
    return f"{p:.1e}" if p < 0.001 else f"{p:.3f}".rstrip("0").rstrip(".")


def authors_short(a: str | None, n: int = 3) -> str:
    names = [x.strip() for x in (a or "").split(",") if x.strip()]
    return ", ".join(names[:n]) + (" et al." if len(names) > n else "")


INLINE_TAGS = re.compile(r"&lt;(/?)(i|sub|sup)&gt;")


def rich(text: str | None) -> Markup:
    """Escape, but keep the <i>/<sub>/<sup> that BugSigDB titles carry (species names, chemical formulas)."""
    return Markup(INLINE_TAGS.sub(r"<\1\2>", str(escape(text or ""))))


templates.env.filters.update(rich=rich, pfmt=pfmt, authors_short=authors_short, urlq=lambda s: quote(str(s), safe=""))


@router.get("/studies")
async def studies(request: Request):
    return render(request, "studies.html", studies=await queries.search_studies(request.app.state.pool, "", 5000))


@router.get("/study/{study_id}")
async def study(request: Request, study_id: str):
    s = await queries.get_study(request.app.state.pool, study_id)
    if s is None:
        return render(request, "404.html", status=404, what=f"study {study_id}")
    return render(request, "study.html", s=s)


@router.get("/study/{study_id}/experiment/{experiment_id}")
async def experiment(request: Request, study_id: str, experiment_id: int):
    e = await queries.get_experiment(request.app.state.pool, study_id, experiment_id)
    if e is None:
        return render(request, "404.html", status=404, what=f"experiment {study_id}/{experiment_id}")
    return render(request, "experiment.html", e=e)


@router.get("/study/{study_id}/experiment/{experiment_id}/signature/{signature_id}")
async def signature(request: Request, study_id: str, experiment_id: int, signature_id: int):
    g = await queries.get_signature(request.app.state.pool, study_id, experiment_id, signature_id)
    if g is None:
        return render(request, "404.html", status=404, what=f"signature bsdb:{study_id}/{experiment_id}/{signature_id}")
    return render(request, "signature.html", g=g)


@router.get("/conditions")
async def conditions(request: Request):
    return render(request, "conditions.html", conditions=await queries.list_conditions(request.app.state.pool))


@router.get("/condition/{ontology_id}")
async def condition(request: Request, ontology_id: str):
    c = await queries.get_condition(request.app.state.pool, ontology_id, taxa_limit=300)
    if c is None:
        return render(request, "404.html", status=404, what=f"condition {ontology_id}")
    scale = max((max(t["increased"], t["decreased"]) for t in c["taxa"]), default=1) or 1
    return render(request, "condition.html", c=c, scale=scale,
                  max_site=max((b["n"] for b in c["body_sites"]), default=1))
