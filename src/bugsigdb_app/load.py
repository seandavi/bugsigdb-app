"""Load the public BugSigDB and bugphyzz exports into Postgres.

BugSigDB's full_dump has one row per signature, with its experiment and study
columns repeated (and one row per experiment that has no signature yet). The
loader splits that into study, experiment, signature and condition tables.

Everything is built in a `staging` schema, then swapped in as `bsdb` in one
transaction, so readers never see a half-loaded database.

    python -m bugsigdb_app.load
"""

import asyncio
import csv
import io
import os
import re
from collections import Counter, defaultdict

import asyncpg
import httpx

BSDB_DUMP = "https://raw.githubusercontent.com/waldronlab/BugSigDBExports/devel/full_dump.csv"
BUGPHYZZ = "https://media.githubusercontent.com/media/waldronlab/bugphyzzExports/devel/bugphyzz_{}.csv"
BUGPHYZZ_KINDS = ("binary", "multistate", "numeric")

RANKS = {"k": "kingdom", "p": "phylum", "c": "class", "o": "order",
         "f": "family", "g": "genus", "s": "species", "t": "strain"}

# table column -> full_dump header. Order here is column order in the table.
STUDY_COLS = {
    "name": "Study", "pmid": "PMID", "doi": "DOI", "url": "URL", "title": "Title",
    "authors": "Authors list", "journal": "Journal", "year": "Year", "keywords": "Keywords",
    "design": "Study design",
}
EXPERIMENT_COLS = {
    "name": "Experiment", "host_species": "Host species", "location": "Location of subjects",
    "body_site": "Body site", "uberon_id": "UBERON ID", "condition": "Condition",
    "group0": "Group 0 name", "group1": "Group 1 name", "group1_definition": "Group 1 definition",
    "n0": "Group 0 sample size", "n1": "Group 1 sample size",
    "antibiotics_exclusion": "Antibiotics exclusion", "sequencing_type": "Sequencing type",
    "region_16s": "16S variable region", "platform": "Sequencing platform",
    "transformation": "Data transformation", "test": "Statistical test",
    "threshold": "Significance threshold", "mht": "MHT correction", "lda": "LDA Score above",
    "matched_on": "Matched on", "confounders": "Confounders controlled for",
    "pielou": "Pielou", "shannon": "Shannon", "chao1": "Chao1", "simpson": "Simpson",
    "inverse_simpson": "Inverse Simpson", "richness": "Richness",
}
SIGNATURE_COLS = {
    "name": "Signature page name", "direction": "Abundance in Group 1", "source": "Source",
    "description": "Description", "curated_date": "Curated date", "curator": "Curator",
    "revision_editor": "Revision editor", "reviewer": "Reviewer", "state": "State",
}


def cols(d: dict[str, str]) -> str:
    return ", ".join(f"{c} text" for c in d)


SCHEMA = f"""
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
create table study (study_id text primary key, {cols(STUDY_COLS)});
create table experiment (
    study_id text not null, experiment_id integer not null, {cols(EXPERIMENT_COLS)},
    primary key (study_id, experiment_id)
);
create table condition (ontology_id text primary key, name text not null);
create table experiment_condition (
    study_id text not null, experiment_id integer not null, ontology_id text not null,
    primary key (study_id, experiment_id, ontology_id)
);
create table signature (
    bsdb_id text primary key,
    study_id text not null, experiment_id integer not null, signature_id integer not null,
    {cols(SIGNATURE_COLS)}
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

POST = """
-- trusted extension (PG13+): the database owner can create it. Lives in public so it survives the schema swap.
create extension if not exists pg_trgm with schema public;
create index on taxon (parent_id);
create index on taxon (lower(name) text_pattern_ops);
create index on taxon_ancestor (tax_id);
create index on signature (study_id, experiment_id);
create index on signature_taxon (tax_id);
create index on experiment_condition (ontology_id);
create index on trait (tax_id);
-- signature with its experiment and study columns, as the taxon card reads them
create view signature_full as
    select s.bsdb_id, s.study_id, s.experiment_id, s.signature_id, s.name as signature,
           s.direction, s.source, s.description, s.state,
           st.name as study, st.pmid, st.doi, st.title, st.year,
           e.name as experiment, e.host_species, e.body_site, e.condition, e.group0, e.group1
    from signature s
    join experiment e using (study_id, experiment_id)
    join study st using (study_id);
update taxon t set n_signatures = s.n, n_studies = s.k, n_increased = s.up, n_decreased = s.down from (
    select a.ancestor_id, count(distinct st.bsdb_id) as n, count(distinct sg.study_id) as k,
           count(distinct st.bsdb_id) filter (where sg.direction = 'increased') as up,
           count(distinct st.bsdb_id) filter (where sg.direction = 'decreased') as down
    from signature_taxon st
    join taxon_ancestor a using (tax_id)
    join signature sg using (bsdb_id)
    group by a.ancestor_id
) s where s.ancestor_id = t.tax_id;
-- one row per searchable entity; `text` is matched, `ids` are exact identifiers, `weight` ranks ties
create table search_doc as
    select 'taxon' as kind, tax_id::text as key, name as label, rank as sub,
           name as text, array[tax_id::text] as ids, n_signatures as weight  -- ids are lowercase
    from taxon
    union all
    select 'condition', cn.ontology_id, cn.name, cn.ontology_id, cn.name || ' ' || cn.ontology_id,
           array[lower(cn.ontology_id), lower(replace(cn.ontology_id, ':', '_'))], count(s.bsdb_id)
    from condition cn
    join experiment_condition ec using (ontology_id)
    left join signature s on (s.study_id, s.experiment_id) = (ec.study_id, ec.experiment_id)
    group by cn.ontology_id, cn.name
    union all
    select 'study', st.study_id, coalesce(st.title, st.name), concat_ws(' · ', st.journal, st.year),
           concat_ws(' ', st.title, st.authors, st.journal, st.keywords, st.doi),
           array_remove(array[lower(st.study_id), st.pmid, lower(st.doi)], null),
           (select count(*) from signature s where s.study_id = st.study_id)
    from study st;
create index on search_doc using gin (lower(text) public.gin_trgm_ops);
create index on search_doc using gin (ids);
"""

COLUMNS = {"taxon": ["tax_id", "name", "rank", "parent_id"]}  # rest are computed


def na(v: str | None) -> str | None:
    return None if v in (None, "", "NA") else v


def read_csv(text: str) -> list[dict[str, str]]:
    """Both exports put a `# ...` provenance line above the header."""
    return list(csv.DictReader(line for line in io.StringIO(text) if not line.startswith("#")))


def parse_bsdb_id(bsdb_id: str) -> tuple[str, int, int | None]:
    """'bsdb:11/1/1' -> ('11', 1, 1); 'bsdb:34963452/2/NA' (experiment with no signature) -> (..., 2, None).

    The study part is usually a PMID or old serial number, but can be any text
    ('10.31083 CEOG44510'); it never contains '/'.
    """
    study, experiment, signature = bsdb_id.removeprefix("bsdb:").rsplit("/", 2)
    return study, int(experiment), None if signature == "NA" else int(signature)


def parse_conditions(names: str, ids: str) -> list[tuple[str, str]]:
    """Aligned (ontology_id, name) pairs. Lists are joined by ',' with no space;
    a ', ' is part of a name ('Hepatitis, Alcoholic')."""
    if na(ids) is None:
        return []
    id_list = [i.strip() for i in ids.split(",")]
    name_list = [n.strip() for n in re.split(r",(?!\s)", names or "")]
    if len(id_list) == 1:
        name_list = [(names or "").strip()]
    if len(id_list) != len(name_list):
        name_list = [""] * len(id_list)  # unalignable: keep the IDs, name them from other rows
    return list(zip(id_list, name_list))


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


def build_bsdb(rows: list[dict[str, str]]) -> dict[str, list[tuple]]:
    """Split full_dump rows into one record list per table."""
    taxa: dict[int, tuple] = {}
    studies: dict[str, tuple] = {}
    experiments: dict[tuple[str, int], tuple] = {}
    exp_conditions: set[tuple] = set()
    condition_names: dict[str, Counter] = defaultdict(Counter)
    signatures, links = [], set()
    for r in rows:
        study_id, experiment_id, signature_id = parse_bsdb_id(r["BSDB ID"])
        # ponytail: first row wins for study and experiment columns (they never differ in the dump)
        studies.setdefault(study_id, (study_id, *(na(r[c]) for c in STUDY_COLS.values())))
        if (study_id, experiment_id) not in experiments:
            experiments[study_id, experiment_id] = (
                study_id, experiment_id, *(na(r[c]) for c in EXPERIMENT_COLS.values()))
            for oid, name in parse_conditions(r["Condition"], r["EFO ID"]):
                exp_conditions.add((study_id, experiment_id, oid))
                names = condition_names[oid]  # created even when the name is unknown
                if name:
                    names[name] += 1
        if signature_id is None:
            continue
        signatures.append((r["BSDB ID"], study_id, experiment_id, signature_id,
                           *(na(r[c]) for c in SIGNATURE_COLS.values())))
        for path in parse_taxa(r["NCBI Taxonomy IDs"], r["MetaPhlAn taxon names"]):
            parent = None
            for tax_id, name, rank in path:
                # ponytail: first-seen name and parent win if exports disagree
                taxa.setdefault(tax_id, (tax_id, name, rank, parent))
                parent = tax_id
            links.add((r["BSDB ID"], path[-1][0]))
    # an ID's display name is its most common one (a few rows pair IDs with the wrong name)
    conditions = [(oid, names.most_common(1)[0][0] if names else oid)
                  for oid, names in condition_names.items()]
    return {
        "taxon": list(taxa.values()),
        "study": list(studies.values()),
        "experiment": list(experiments.values()),
        "condition": conditions,
        "experiment_condition": sorted(exp_conditions),
        "signature": signatures,
        "signature_taxon": sorted(links),
    }


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
    tables = build_bsdb(read_csv(texts[0]))
    tables["taxon_ancestor"] = closure(tables["taxon"])
    keep = {t[0] for t in tables["taxon"]}
    tables["trait"] = [t for text in texts[1:] for t in build_traits(read_csv(text), keep)]

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        async with conn.transaction():
            await conn.execute("drop schema if exists staging cascade; create schema staging; set search_path = staging")
            await conn.execute(SCHEMA)
            for table, records in tables.items():
                await conn.copy_records_to_table(table, records=records, schema_name="staging",
                                                 columns=COLUMNS.get(table))
            await conn.execute(POST)
            await conn.execute("drop schema if exists bsdb cascade; alter schema staging rename to bsdb")
    finally:
        await conn.close()
    print(", ".join(f"{len(v)} {k}" for k, v in tables.items()))


if __name__ == "__main__":
    asyncio.run(main())
