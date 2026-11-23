"""Read-side queries. REST, MCP and the HTML pages all call these.

Signatures "of" a taxon include its descendants': a genus card covers its
species. bsdb.taxon_ancestor (closure, self at depth 0) makes that one join.
"""

import math
from typing import Any

import asyncpg

# signatures in the subtree of $1, one row per (signature, linked taxon)
SUBTREE = """
    from bsdb.taxon_ancestor a
    join bsdb.signature_taxon st on st.tax_id = a.tax_id
    join bsdb.signature_full s on s.bsdb_id = st.bsdb_id
    where a.ancestor_id = $1
"""


def rows(rs) -> list[dict[str, Any]]:
    return [dict(r) for r in rs]


async def get_taxon(pool: asyncpg.Pool, tax_id: int, signature_limit: int = 100) -> dict[str, Any] | None:
    """A taxon card: lineage, children, signature summary and list (own and descendants'), traits."""
    async with pool.acquire() as c:
        taxon = await c.fetchrow(
            "select tax_id, name, rank, n_signatures, n_studies, n_increased, n_decreased from bsdb.taxon where tax_id = $1", tax_id)
        if taxon is None:
            return None
        lineage = await c.fetch("""
            select t.tax_id, t.name, t.rank from bsdb.taxon_ancestor a join bsdb.taxon t on t.tax_id = a.ancestor_id
            where a.tax_id = $1 and a.depth > 0 order by a.depth desc""", tax_id)
        children = await c.fetch("""
            select tax_id, name, rank, n_signatures from bsdb.taxon where parent_id = $1
            order by n_signatures desc, name""", tax_id)
        direction = await c.fetch(f"""
            select coalesce(s.direction, 'NA') as direction, count(distinct s.bsdb_id) as n {SUBTREE}
            group by 1 order by n desc""", tax_id)
        n_direct = await c.fetchval(
            "select count(*) from bsdb.signature_taxon where tax_id = $1", tax_id)
        conditions = await c.fetch("""
            select ec.ontology_id, cn.name as condition,
                   count(distinct s.bsdb_id) filter (where s.direction = 'increased') as increased,
                   count(distinct s.bsdb_id) filter (where s.direction = 'decreased') as decreased,
                   count(distinct s.study_id) as studies
            from bsdb.taxon_ancestor a
            join bsdb.signature_taxon st on st.tax_id = a.tax_id
            join bsdb.signature s on s.bsdb_id = st.bsdb_id
            join bsdb.experiment_condition ec on (ec.study_id, ec.experiment_id) = (s.study_id, s.experiment_id)
            join bsdb.condition cn on cn.ontology_id = ec.ontology_id
            where a.ancestor_id = $1
            group by ec.ontology_id, cn.name order by count(distinct s.bsdb_id) desc, cn.name limit 15""", tax_id)
        body_sites = await c.fetch(f"""
            select min(s.body_site) as body_site, count(distinct s.bsdb_id) as n
            {SUBTREE} and s.body_site is not null
            group by lower(s.body_site) order by n desc limit 8""", tax_id)
        # one row per signature; via_* names the closest linked taxon (the card's own, if linked)
        signatures = await c.fetch("""
            select * from (
                select distinct on (s.bsdb_id) s.bsdb_id, s.study_id, s.experiment_id, s.signature_id, s.pmid, s.title,
                       s.year, s.condition, s.body_site, s.host_species, s.group0, s.group1, s.direction,
                       st.tax_id as via_tax_id, vt.name as via_name
                from bsdb.taxon_ancestor a
                join bsdb.signature_taxon st on st.tax_id = a.tax_id
                join bsdb.signature_full s on s.bsdb_id = st.bsdb_id
                join bsdb.taxon vt on vt.tax_id = st.tax_id
                where a.ancestor_id = $1
                order by s.bsdb_id, a.depth
            ) x order by (via_tax_id = $1) desc, year desc nulls last, bsdb_id
            limit $2""", tax_id, signature_limit)
        traits = await c.fetch("""
            select attribute, value, attribute_type, evidence, frequency, score, attribute_source, confidence
            from bsdb.trait where tax_id = $1 order by attribute, score desc nulls last""", tax_id)
    return {
        **dict(taxon),
        "n_direct": n_direct,
        "lineage": rows(lineage),
        "children": rows(children),
        "signature_counts": {r["direction"]: r["n"] for r in direction},
        "conditions": rows(conditions),
        "body_sites": rows(body_sites),
        "signatures": rows(signatures),
        "traits": rows(traits),
    }


async def search_taxa(pool: asyncpg.Pool, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Taxa whose name contains `q` (case-insensitive) or with that NCBI ID, most-studied first."""
    return rows(await pool.fetch("""
        select tax_id, name, rank, n_signatures as signatures, n_increased, n_decreased
        from bsdb.taxon
        where name ilike '%' || $1 || '%' or tax_id::text = $1
        order by (lower(name) = lower($1)) desc, (lower(name) like lower($1) || '%') desc,
                 n_signatures desc, name
        limit $2""", q.strip(), limit))


async def top_taxa(pool: asyncpg.Pool, rank: str, limit: int = 12) -> list[dict[str, Any]]:
    return rows(await pool.fetch("""
        select tax_id, name, rank, n_signatures, n_studies, n_increased, n_decreased from bsdb.taxon
        where rank = $1 order by n_signatures desc limit $2""", rank, limit))


async def stats(pool: asyncpg.Pool) -> dict[str, int]:
    return dict(await pool.fetchrow("""
        select (select count(*) from bsdb.taxon) as taxa,
               (select count(*) from bsdb.signature) as signatures,
               (select count(distinct study_id) from bsdb.signature) as studies,
               (select count(distinct tax_id) from bsdb.trait) as taxa_with_traits"""))


def sign_test(up: int, down: int) -> float:
    """Two-sided exact binomial (sign) test of up vs down against p = 0.5."""
    n = up + down
    if n == 0:
        return 1.0
    k = min(up, down)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n)


STUDY_SUMMARY = "select study_id, name, title, year, pmid, doi, journal from bsdb.study where study_id = $1"

# per-signature taxa, most-studied first
SIGNATURE_TAXA = """
    select st.bsdb_id, t.tax_id, t.name, t.rank, t.n_signatures
    from bsdb.signature_taxon st join bsdb.taxon t using (tax_id)
    where st.bsdb_id = any($1::text[])
    order by st.bsdb_id, t.n_signatures desc, t.name
"""


async def _conditions_for(c, study_id: str) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in await c.fetch("""
            select ec.experiment_id, ec.ontology_id, cn.name from bsdb.experiment_condition ec
            join bsdb.condition cn using (ontology_id) where ec.study_id = $1 order by cn.name""", study_id):
        out.setdefault(r["experiment_id"], []).append({"ontology_id": r["ontology_id"], "name": r["name"]})
    return out


async def _taxa_for(c, bsdb_ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {i: [] for i in bsdb_ids}
    for r in await c.fetch(SIGNATURE_TAXA, bsdb_ids):
        out[r["bsdb_id"]].append({k: r[k] for k in ("tax_id", "name", "rank", "n_signatures")})
    return out


async def get_study(pool: asyncpg.Pool, study_id: str) -> dict[str, Any] | None:
    """A study with its experiments, each with conditions and signatures (with taxa)."""
    async with pool.acquire() as c:
        study = await c.fetchrow("select * from bsdb.study where study_id = $1", study_id)
        if study is None:
            return None
        experiments = rows(await c.fetch(
            "select * from bsdb.experiment where study_id = $1 order by experiment_id", study_id))
        sigs = rows(await c.fetch("""
            select bsdb_id, experiment_id, signature_id, name, direction, source, description
            from bsdb.signature where study_id = $1 order by experiment_id, signature_id""", study_id))
        conditions = await _conditions_for(c, study_id)
        taxa = await _taxa_for(c, [s["bsdb_id"] for s in sigs])
    for s in sigs:
        s["taxa"] = taxa[s["bsdb_id"]]
    for e in experiments:
        e["conditions"] = conditions.get(e["experiment_id"], [])
        e["signatures"] = [s for s in sigs if s["experiment_id"] == e["experiment_id"]]
    return {**dict(study), "experiments": experiments, "n_signatures": len(sigs)}


async def get_experiment(pool: asyncpg.Pool, study_id: str, experiment_id: int) -> dict[str, Any] | None:
    """An experiment: groups, methods, diversity, conditions, and its signatures with taxa."""
    async with pool.acquire() as c:
        e = await c.fetchrow("select * from bsdb.experiment where study_id = $1 and experiment_id = $2",
                             study_id, experiment_id)
        if e is None:
            return None
        study = await c.fetchrow(STUDY_SUMMARY, study_id)
        sigs = rows(await c.fetch("""
            select bsdb_id, signature_id, name, direction, source, description, curator, curated_date, state
            from bsdb.signature where study_id = $1 and experiment_id = $2 order by signature_id""",
                                  study_id, experiment_id))
        conditions = (await _conditions_for(c, study_id)).get(experiment_id, [])
        siblings = await c.fetch(
            "select experiment_id, name from bsdb.experiment where study_id = $1 order by experiment_id", study_id)
        taxa = await _taxa_for(c, [s["bsdb_id"] for s in sigs])
    for s in sigs:
        s["taxa"] = taxa[s["bsdb_id"]]
    return {**dict(e), "study": dict(study), "conditions": conditions, "signatures": sigs,
            "experiments": rows(siblings)}


async def get_signature(pool: asyncpg.Pool, study_id: str, experiment_id: int, signature_id: int,
                        similar_limit: int = 10) -> dict[str, Any] | None:
    """A signature with its taxa, its experiment and study, and the most similar signatures (Jaccard)."""
    bsdb_id = f"bsdb:{study_id}/{experiment_id}/{signature_id}"
    async with pool.acquire() as c:
        s = await c.fetchrow("select * from bsdb.signature where bsdb_id = $1", bsdb_id)
        if s is None:
            return None
        e = await c.fetchrow("""
            select experiment_id, name, condition, body_site, host_species, group0, group1, n0, n1,
                   sequencing_type, test from bsdb.experiment where study_id = $1 and experiment_id = $2""",
                             study_id, experiment_id)
        study = await c.fetchrow(STUDY_SUMMARY, study_id)
        conditions = (await _conditions_for(c, study_id)).get(experiment_id, [])
        taxa = (await _taxa_for(c, [bsdb_id]))[bsdb_id]
        # ponytail: Jaccard over directly linked taxa, scanned per request (~20 ms); precompute if it grows
        similar = await c.fetch("""
            with mine as (select tax_id from bsdb.signature_taxon where bsdb_id = $1),
            overlap as (
                select st.bsdb_id, count(*) as shared from bsdb.signature_taxon st
                join mine using (tax_id) where st.bsdb_id <> $1 group by st.bsdb_id
                having count(*) >= least(2, (select count(*) from mine))  -- one shared taxon says little
            ),
            sizes as (
                select bsdb_id, count(*) as n from bsdb.signature_taxon
                where bsdb_id in (select bsdb_id from overlap) group by bsdb_id
            )
            select o.bsdb_id, o.shared, round(o.shared::numeric / (z.n + (select count(*) from mine) - o.shared), 3)::float8
                   as jaccard, f.study_id, f.experiment_id, f.signature_id, f.direction, f.condition, f.title, f.year
            from overlap o join sizes z using (bsdb_id) join bsdb.signature_full f using (bsdb_id)
            order by jaccard desc, o.shared desc limit $2""", bsdb_id, similar_limit)
    return {**dict(s), "experiment": dict(e), "study": dict(study), "conditions": conditions,
            "taxa": taxa, "similar": rows(similar)}


async def search_studies(pool: asyncpg.Pool, q: str = "", limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Studies whose title, authors, journal, PMID or DOI contain `q`; newest first."""
    return rows(await pool.fetch("""
        select st.study_id, st.name, st.title, st.authors, st.journal, st.year, st.pmid,
               (select count(*) from bsdb.experiment e where e.study_id = st.study_id) as n_experiments,
               (select count(*) from bsdb.signature s where s.study_id = st.study_id) as n_signatures
        from bsdb.study st
        where $1 = '' or concat_ws(' ', st.title, st.authors, st.journal, st.pmid, st.doi, st.study_id) ilike '%' || $1 || '%'
        order by st.year desc nulls last, st.study_id desc
        limit $2 offset $3""", q.strip(), limit, offset))


async def list_conditions(pool: asyncpg.Pool, q: str = "", limit: int = 2000) -> list[dict[str, Any]]:
    """Conditions (EFO/MONDO/... terms) by number of signatures."""
    return rows(await pool.fetch("""
        select cn.ontology_id, cn.name,
               count(distinct ec.study_id) as n_studies,
               count(distinct (ec.study_id, ec.experiment_id)) as n_experiments,
               count(s.bsdb_id) as n_signatures
        from bsdb.condition cn
        join bsdb.experiment_condition ec using (ontology_id)
        left join bsdb.signature s on (s.study_id, s.experiment_id) = (ec.study_id, ec.experiment_id)
        where $1 = '' or cn.name ilike '%' || $1 || '%' or cn.ontology_id ilike $1
        group by cn.ontology_id, cn.name
        order by n_signatures desc, cn.name limit $2""", q.strip(), limit))


async def get_condition(pool: asyncpg.Pool, ontology_id: str, taxa_limit: int = 150,
                        min_signatures: int = 2) -> dict[str, Any] | None:
    """A condition: its studies, body sites, and the taxa reported in its signatures, with how
    consistently each goes up or down (sign test). Counts are of directly linked taxa, not roll-ups."""
    async with pool.acquire() as c:
        cond = await c.fetchrow("select ontology_id, name from bsdb.condition where ontology_id = $1", ontology_id)
        if cond is None:
            return None
        studies = await c.fetch("""
            select st.study_id, st.name, st.title, st.year, st.pmid,
                   count(distinct ec.experiment_id) as n_experiments, count(s.bsdb_id) as n_signatures
            from bsdb.experiment_condition ec
            join bsdb.study st using (study_id)
            left join bsdb.signature s on (s.study_id, s.experiment_id) = (ec.study_id, ec.experiment_id)
            where ec.ontology_id = $1
            group by st.study_id order by st.year desc nulls last, st.study_id""", ontology_id)
        body_sites = await c.fetch("""
            select min(e.body_site) as body_site, count(*) as n from bsdb.experiment_condition ec
            join bsdb.experiment e using (study_id, experiment_id)
            where ec.ontology_id = $1 and e.body_site is not null
            group by lower(e.body_site) order by n desc limit 8""", ontology_id)
        taxa = await c.fetch("""
            select t.tax_id, t.name, t.rank,
                   count(*) filter (where s.direction = 'increased') as increased,
                   count(*) filter (where s.direction = 'decreased') as decreased,
                   count(distinct s.study_id) as studies
            from bsdb.experiment_condition ec
            join bsdb.signature s on (s.study_id, s.experiment_id) = (ec.study_id, ec.experiment_id)
            join bsdb.signature_taxon st using (bsdb_id)
            join bsdb.taxon t using (tax_id)
            where ec.ontology_id = $1
            group by t.tax_id having count(*) >= $2""", ontology_id, min_signatures)
    taxa = rows(taxa)
    for t in taxa:
        t["p"] = sign_test(t["increased"], t["decreased"])
    taxa.sort(key=lambda t: (t["p"], -(t["increased"] + t["decreased"]), t["name"]))
    n_sig = sum(s["n_signatures"] for s in studies)
    return {**dict(cond), "n_studies": len(studies), "n_signatures": n_sig,
            "n_experiments": sum(s["n_experiments"] for s in studies),
            "studies": rows(studies), "body_sites": rows(body_sites),
            "n_taxa": len(taxa), "taxa": taxa[:taxa_limit]}
