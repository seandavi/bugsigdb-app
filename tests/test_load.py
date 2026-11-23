from bugsigdb_app.load import build_bsdb, parse_taxa

IDS = "1783272|201174|1760;1783272|201174|1761"
NAMES = "k__Bacillati|p__Actinomycetota|c__Actinomycetes,k__Bacillati|p__Actinomycetota|c__Other"


def test_parse_taxa_aligns_ids_names_and_ranks():
    paths = parse_taxa(IDS, NAMES)
    assert paths[0] == [(1783272, "Bacillati", "kingdom"), (201174, "Actinomycetota", "phylum"),
                        (1760, "Actinomycetes", "class")]
    assert paths[1][-1] == (1761, "Other", "class")


def test_build_bsdb_links_leaves_and_parents():
    row = {"BSDB ID": "bsdb:1/1/1", "NCBI Taxonomy IDs": IDS, "MetaPhlAn taxon names": NAMES,
           "Study": "Study 1", "PMID": "1", "DOI": "", "Title": "t", "Year": "2020",
           "Experiment": "Experiment 1", "Host species": "Homo sapiens", "Body site": "Feces",
           "Condition": "IBD", "Group 0 name": "a", "Group 1 name": "b",
           "Abundance in Group 1": "increased", "Signature page name": "Signature 1",
           "Source": "Fig 1", "Description": "d", "State": "NA"}
    taxa, sigs, links = build_bsdb([row])
    assert links == [("bsdb:1/1/1", 1760), ("bsdb:1/1/1", 1761)]
    assert {t[0]: t[3] for t in taxa} == {1783272: None, 201174: 1783272, 1760: 201174, 1761: 201174}
    assert sigs[0][2] is not None and sigs[0][3] is None and sigs[0][-1] is None  # DOI "" and State "NA" -> NULL


def test_no_taxa_and_no_signature_rows():
    assert parse_taxa("NA", "NA") == []
    row = {"BSDB ID": "bsdb:1/2/NA", "NCBI Taxonomy IDs": "NA", "MetaPhlAn taxon names": "NA"}
    assert build_bsdb([row]) == ([], [], [])


def test_closure_includes_self_and_all_ancestors():
    from bugsigdb_app.load import closure
    taxa = [(1, "a", "kingdom", None), (2, "b", "phylum", 1), (3, "c", "class", 2)]
    assert sorted(closure(taxa)) == [(1, 1, 0), (2, 1, 1), (2, 2, 0), (3, 1, 2), (3, 2, 1), (3, 3, 0)]
