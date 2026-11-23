"""Load the public BugSigDB and bugphyzz exports into Postgres.

Builds everything in a `staging` schema, then swaps it in as `bsdb` in one
transaction, so readers never see a half-loaded database.

    python -m bugsigdb_app.load
"""

import asyncio
import csv
import io
import os

import asyncpg
import httpx

BSDB_DUMP = "https://raw.githubusercontent.com/waldronlab/BugSigDBExports/devel/full_dump.csv"
BUGPHYZZ = "https://media.githubusercontent.com/media/waldronlab/bugphyzzExports/devel/bugphyzz_{}.csv"
BUGPHYZZ_KINDS = ("binary", "multistate", "numeric")

RANKS = {"k": "kingdom", "p": "phylum", "c": "class", "o": "order",
         "f": "family", "g": "genus", "s": "species", "t": "strain"}

SCHEMA = """
create table taxon (
    tax_id integer primary key,
    name text not null,
    rank text,
    parent_id integer,
    n_signatures integer not null default 0,  -- own + descendants'
    n_studies integer not null default 0,
    n_increased integer not null default 0,
    n_decreased integer not null default 0
);
create table taxon_ancestor (  -- closure: every (taxon, ancestor-or-self) pair
    tax_id integer not null,
    ancestor_id integer not null,
    depth integer not null,  -- 0 = self
    primary key (ancestor_id, tax_id)
);
create table signature (
    bsdb_id text primary key,
    study text, pmid text, doi text, title text, year text,
    experiment text, host_species text, body_site text, condition text,
    group0 text, group1 text, direction text,
    signature text, source text, description text, state text
);
create table signature_taxon (
    bsdb_id text not null,
    tax_id integer not null,
    primary key (bsdb_id, tax_id)
);
create table trait (
    tax_id integer not null,
    attribute text not null,
    value text,
    attribute_type text,
    evidence text,
    frequency text,
    score double precision,
    attribute_source text,
    confidence text
);
"""

INDEXES = """
create index on taxon (parent_id);
create index on taxon_ancestor (tax_id);
update taxon t set n_signatures = s.n, n_studies = s.k, n_increased = s.up, n_decreased = s.down from (
    select a.ancestor_id, count(distinct st.bsdb_id) as n, count(distinct sg.study) as k,
           count(distinct st.bsdb_id) filter (where sg.direction = 'increased') as up,
           count(distinct st.bsdb_id) filter (where sg.direction = 'decreased') as down
    from signature_taxon st
    join taxon_ancestor a using (tax_id)
    join signature sg using (bsdb_id)
    group by a.ancestor_id
) s where s.ancestor_id = t.tax_id;
create index on taxon (lower(name) text_pattern_ops);
create index on signature_taxon (tax_id);
create index on trait (tax_id);
"""

COLUMNS = {"taxon": ["tax_id", "name", "rank", "parent_id"]}  # rest are computed

SIGNATURE_COLS = {
    "bsdb_id": "BSDB ID", "study": "Study", "pmid": "PMID", "doi": "DOI",
    "title": "Title", "year": "Year", "experiment": "Experiment",
    "host_species": "Host species", "body_site": "Body site",
    "condition": "Condition", "group0": "Group 0 name", "group1": "Group 1 name",
    "direction": "Abundance in Group 1", "signature": "Signature page name",
    "source": "Source", "description": "Description", "state": "State",
}


def na(v: str) -> str | None:
    return None if v in ("", "NA") else v


def read_csv(text: str) -> list[dict[str, str]]:
    """Both exports put a `# ...` provenance line above the header."""
    return list(csv.DictReader(line for line in io.StringIO(text) if not line.startswith("#")))


def parse_taxa(ids: str, names: str) -> list[list[tuple[int, str, str | None]]]:
    """One signature's taxa as lineage paths of (tax_id, name, rank), root first.

    IDs are `;`-separated paths of `|`-joined tax IDs; names are the matching
    `,`-separated MetaPhlAn strings (`k__Bacillati|p__...`).
    """
    if na(ids) is None:
        return []  # signature with no taxa
    paths = []
    for id_path, name_path in zip(ids.split(";"), names.split(","), strict=True):
        if not id_path:
            continue
        nodes = []
        for tax_id, label in zip(id_path.split("|"), name_path.split("|"), strict=True):
            prefix, _, name = label.partition("__")
            nodes.append((int(tax_id), name or label, RANKS.get(prefix)))
        paths.append(nodes)
    return paths


def build_bsdb(rows: list[dict[str, str]]):
    taxa: dict[int, tuple] = {}
    signatures, links = [], set()
    for r in rows:
        if r["BSDB ID"].endswith("/NA"):
            continue  # experiment row with no signature (and taxa "NA")
        signatures.append(tuple(na(r[c]) for c in SIGNATURE_COLS.values()))
        for path in parse_taxa(r["NCBI Taxonomy IDs"], r["MetaPhlAn taxon names"]):
            parent = None
            for tax_id, name, rank in path:
                # ponytail: first-seen name and parent win if exports disagree
                taxa.setdefault(tax_id, (tax_id, name, rank, parent))
                parent = tax_id
            links.add((r["BSDB ID"], path[-1][0]))
    return list(taxa.values()), signatures, sorted(links)


def closure(taxa: list[tuple]) -> list[tuple[int, int, int]]:
    """(tax_id, ancestor_id, depth) for every taxon and each of its ancestors, self at depth 0."""
    parent = {t[0]: t[3] for t in taxa}
    out = []
    for tax_id in parent:
        node, depth = tax_id, 0
        while node is not None:
            out.append((tax_id, node, depth))
            node, depth = parent.get(node), depth + 1
    return out


def build_traits(rows: list[dict[str, str]], keep: set[int]):
    out = []
    for r in rows:
        tax_id = int(r["NCBI_ID"])
        if tax_id not in keep:
            continue
        score = na(r["Score"])
        out.append((tax_id, r["Attribute"], na(r["Attribute_value"]), na(r["Attribute_type"]),
                    na(r["Evidence"]), na(r["Frequency"]), float(score) if score else None,
                    na(r["Attribute_source"]), na(r["Confidence_in_curation"])))
    return out


async def fetch(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url)
    r.raise_for_status()
    return r.text


async def main() -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        texts = await asyncio.gather(fetch(client, BSDB_DUMP),
                                     *(fetch(client, BUGPHYZZ.format(k)) for k in BUGPHYZZ_KINDS))
    taxa, signatures, links = build_bsdb(read_csv(texts[0]))
    keep = {t[0] for t in taxa}
    traits = [t for text in texts[1:] for t in build_traits(read_csv(text), keep)]

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        async with conn.transaction():
            await conn.execute("drop schema if exists staging cascade; create schema staging; set search_path = staging")
            await conn.execute(SCHEMA)
            for table, records in [("taxon", taxa), ("taxon_ancestor", closure(taxa)),
                                   ("signature", signatures),
                                   ("signature_taxon", links), ("trait", traits)]:
                await conn.copy_records_to_table(table, records=records, schema_name="staging",
                                                columns=COLUMNS.get(table))
            await conn.execute(INDEXES)
            await conn.execute("drop schema if exists bsdb cascade; alter schema staging rename to bsdb")
    finally:
        await conn.close()
    print(f"loaded {len(taxa)} taxa, {len(signatures)} signatures, {len(links)} links, {len(traits)} traits")


if __name__ == "__main__":
    asyncio.run(main())
