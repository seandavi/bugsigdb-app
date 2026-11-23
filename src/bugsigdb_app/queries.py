"""Read-side queries. REST, MCP and the HTML pages all call these.

Signatures "of" a taxon include its descendants': a genus card covers its
species. bsdb.taxon_ancestor (closure, self at depth 0) makes that one join.
"""

from typing import Any

import asyncpg

# signatures in the subtree of $1, one row per (signature, linked taxon)
SUBTREE = """
    from bsdb.taxon_ancestor a
    join bsdb.signature_taxon st on st.tax_id = a.tax_id
    join bsdb.signature s on s.bsdb_id = st.bsdb_id
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
        conditions = await c.fetch(f"""
            select s.condition,
                   count(distinct s.bsdb_id) filter (where s.direction = 'increased') as increased,
                   count(distinct s.bsdb_id) filter (where s.direction = 'decreased') as decreased,
                   count(distinct s.study) as studies
            {SUBTREE} and s.condition is not null
            group by s.condition order by count(distinct s.bsdb_id) desc, s.condition limit 15""", tax_id)
        body_sites = await c.fetch(f"""
            select min(s.body_site) as body_site, count(distinct s.bsdb_id) as n
            {SUBTREE} and s.body_site is not null
            group by lower(s.body_site) order by n desc limit 8""", tax_id)
        # one row per signature; via_* names the closest linked taxon (the card's own, if linked)
        signatures = await c.fetch("""
            select * from (
                select distinct on (s.bsdb_id) s.bsdb_id, s.study, s.experiment, s.signature, s.pmid, s.title,
                       s.year, s.condition, s.body_site, s.host_species, s.group0, s.group1, s.direction,
                       st.tax_id as via_tax_id, vt.name as via_name
                from bsdb.taxon_ancestor a
                join bsdb.signature_taxon st on st.tax_id = a.tax_id
                join bsdb.signature s on s.bsdb_id = st.bsdb_id
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
               (select count(distinct study) from bsdb.signature) as studies,
               (select count(distinct tax_id) from bsdb.trait) as taxa_with_traits"""))
