"""Read-side queries. REST, MCP and (later) HTML cards all call these."""

from typing import Any

import asyncpg

SUBTREE = """
with recursive sub as (
    select tax_id from bsdb.taxon where tax_id = $1
    union all
    select t.tax_id from bsdb.taxon t join sub on t.parent_id = sub.tax_id
)
"""


async def get_taxon(pool: asyncpg.Pool, tax_id: int, signature_limit: int = 100) -> dict[str, Any] | None:
    """A taxon card: lineage, children, signatures (own and descendants'), traits."""
    async with pool.acquire() as c:
        taxon = await c.fetchrow("select tax_id, name, rank from bsdb.taxon where tax_id = $1", tax_id)
        if taxon is None:
            return None
        lineage = await c.fetch("""
            with recursive up as (
                select tax_id, name, rank, parent_id, 0 as depth from bsdb.taxon where tax_id = $1
                union all
                select t.tax_id, t.name, t.rank, t.parent_id, up.depth + 1
                from bsdb.taxon t join up on t.tax_id = up.parent_id
            )
            select tax_id, name, rank from up where depth > 0 order by depth desc""", tax_id)
        children = await c.fetch(
            "select tax_id, name, rank from bsdb.taxon where parent_id = $1 order by name", tax_id)
        summary = await c.fetch(SUBTREE + """
            select s.direction, count(distinct s.bsdb_id) as n
            from sub join bsdb.signature_taxon st using (tax_id) join bsdb.signature s using (bsdb_id)
            group by s.direction order by n desc""", tax_id)
        signatures = await c.fetch(SUBTREE + """
            select distinct on (s.bsdb_id) s.bsdb_id, s.pmid, s.title, s.condition, s.body_site,
                   s.group0, s.group1, s.direction, st.tax_id as via_tax_id
            from sub join bsdb.signature_taxon st using (tax_id) join bsdb.signature s using (bsdb_id)
            order by s.bsdb_id, (st.tax_id = $1) desc
            limit $2""", tax_id, signature_limit)
        traits = await c.fetch("""
            select attribute, value, attribute_type, evidence, frequency, score, attribute_source, confidence
            from bsdb.trait where tax_id = $1 order by attribute, score desc nulls last""", tax_id)
    return {
        **dict(taxon),
        "lineage": [dict(r) for r in lineage],
        "children": [dict(r) for r in children],
        "signature_counts": {r["direction"] or "NA": r["n"] for r in summary},
        "signatures": [dict(r) for r in signatures],
        "traits": [dict(r) for r in traits],
    }


async def search_taxa(pool: asyncpg.Pool, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Taxa whose name contains `q` (case-insensitive), most-studied first."""
    rows = await pool.fetch("""
        select t.tax_id, t.name, t.rank, count(st.bsdb_id) as signatures
        from bsdb.taxon t left join bsdb.signature_taxon st using (tax_id)
        where t.name ilike '%' || $1 || '%' or t.tax_id::text = $1
        group by t.tax_id
        order by (lower(t.name) = lower($1)) desc, signatures desc, t.name
        limit $2""", q, limit)
    return [dict(r) for r in rows]
