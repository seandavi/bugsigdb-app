from bugsigdb_app.load import build_bsdb, closure, parse_bsdb_id, parse_conditions, parse_taxa

IDS = "1783272|201174|1760;1783272|201174|1761"
NAMES = "k__Bacillati|p__Actinomycetota|c__Actinomycetes,k__Bacillati|p__Actinomycetota|c__Other"


def row(bsdb_id, ids=IDS, names=NAMES, **kw):
    base = {"BSDB ID": bsdb_id, "NCBI Taxonomy IDs": ids, "MetaPhlAn taxon names": names,
            "Study": "Study 1", "PMID": "1", "DOI": "", "URL": "NA", "Title": "t", "Authors list": "A",
            "Journal": "J", "Year": "2020", "Keywords": "NA", "Study design": "case-control",
            "Experiment": "Experiment 1", "Host species": "Homo sapiens", "Body site": "Feces",
            "Condition": "IBD", "EFO ID": "EFO:1", "Abundance in Group 1": "increased",
            "Signature page name": "Signature 1", "State": "NA"}
    base.update(kw)
    return {**{k: "" for k in HEADERS}, **base}


HEADERS = ["Location of subjects", "UBERON ID", "Group 0 name", "Group 1 name", "Group 1 definition",
           "Group 0 sample size", "Group 1 sample size", "Antibiotics exclusion", "Sequencing type",
           "16S variable region", "Sequencing platform", "Data transformation", "Statistical test",
           "Significance threshold", "MHT correction", "LDA Score above", "Matched on",
           "Confounders controlled for", "Pielou", "Shannon", "Chao1", "Simpson", "Inverse Simpson",
           "Richness", "Source", "Description", "Curated date", "Curator", "Revision editor", "Reviewer"]


def test_parse_taxa_aligns_ids_names_and_ranks():
    paths = parse_taxa(IDS, NAMES)
    assert paths[0] == [(1783272, "Bacillati", "kingdom"), (201174, "Actinomycetota", "phylum"),
                        (1760, "Actinomycetes", "class")]
    assert paths[1][-1] == (1761, "Other", "class")
    assert parse_taxa("NA", "NA") == []


def test_parse_bsdb_id():
    assert parse_bsdb_id("bsdb:11/1/2") == ("11", 1, 2)
    assert parse_bsdb_id("bsdb:19533811/2/NA") == ("19533811", 2, None)
    assert parse_bsdb_id("bsdb:10.31083 CEOG44510/1/1") == ("10.31083 CEOG44510", 1, 1)


def test_parse_conditions_keeps_comma_space_inside_names():
    assert parse_conditions("Hepatitis, Alcoholic", "MONDO:1") == [("MONDO:1", "Hepatitis, Alcoholic")]
    assert parse_conditions("A,B", "EFO:1,EFO:2") == [("EFO:1", "A"), ("EFO:2", "B")]
    assert parse_conditions("A, b,C", "EFO:1,EFO:2") == [("EFO:1", "A, b"), ("EFO:2", "C")]
    assert parse_conditions("A,B,C", "EFO:1,EFO:2") == [("EFO:1", ""), ("EFO:2", "")]
    assert parse_conditions("A", "NA") == []


def test_build_bsdb_normalizes_rows():
    t = build_bsdb([
        row("bsdb:1/1/1"),
        row("bsdb:1/1/2", ids="1783272|201174|1760", names="k__Bacillati|p__Actinomycetota|c__Actinomycetes",
            **{"Signature page name": "Signature 2", "Abundance in Group 1": "decreased"}),
        row("bsdb:1/2/NA", ids="NA", names="NA", Experiment="Experiment 2", Condition="Wrong", **{"EFO ID": "EFO:1"}),
    ])
    assert len(t["study"]) == 1 and len(t["experiment"]) == 2 and len(t["signature"]) == 2
    assert t["signature_taxon"] == [("bsdb:1/1/1", 1760), ("bsdb:1/1/1", 1761), ("bsdb:1/1/2", 1760)]
    assert {x[0]: x[3] for x in t["taxon"]} == {1783272: None, 201174: 1783272, 1760: 201174, 1761: 201174}
    assert t["condition"] == [("EFO:1", "IBD")]  # most common name wins over "Wrong"
    assert t["experiment_condition"] == [("1", 1, "EFO:1"), ("1", 2, "EFO:1")]
    study = t["study"][0]
    assert study[0] == "1" and study[3] is None and study[4] is None  # DOI "" and URL "NA" -> NULL


def test_closure_includes_self_and_all_ancestors():
    taxa = [(1, "a", "kingdom", None), (2, "b", "phylum", 1), (3, "c", "class", 2)]
    assert sorted(closure(taxa)) == [(1, 1, 0), (2, 1, 1), (2, 2, 0), (3, 1, 2), (3, 2, 1), (3, 3, 0)]
