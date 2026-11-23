from bugsigdb_app.queries import sign_test


def test_sign_test_matches_exact_binomial():
    assert sign_test(0, 0) == 1.0
    assert sign_test(5, 5) == 1.0
    assert sign_test(10, 0) == 2 / 1024  # 0.00195
    assert abs(sign_test(8, 2) - 0.109375) < 1e-12  # scipy.stats.binomtest(8, 10).pvalue
    assert sign_test(2, 8) == sign_test(8, 2)


def test_rich_keeps_inline_tags_only():
    from bugsigdb_app.web import rich
    assert rich("<i>F. prausnitzii</i> & H<sub>2</sub>S") == "<i>F. prausnitzii</i> &amp; H<sub>2</sub>S"
    assert rich('<script>x</script><i onclick="y">') == "&lt;script&gt;x&lt;/script&gt;&lt;i onclick=&#34;y&#34;&gt;"
