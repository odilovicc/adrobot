from app.services.campaigns import ListQuery, build_page


def camp(cid, name, state="active", group="Nutra", created="2026-01-01 00:00:00", alias=None):
    return {"id": cid, "name": name, "alias": alias or f"a{cid}", "state": state, "group": group,
            "domain": "https://track.example.com/", "created_at": created}


DATA = [
    camp(1, "Keto AU", created="2026-01-03 00:00:00"),
    camp(2, "Slim MX", state="disabled", group="Tests", created="2026-01-01 00:00:00"),
    camp(3, "keto RO", group="", created="2026-01-02 00:00:00"),
    camp(4, "Gone", state="deleted"),
] + [camp(100 + i, f"Bulk {i}") for i in range(60)]


def q(**params):
    return ListQuery.parse({k: str(v) for k, v in params.items()})


def ids(page):
    return [r.id for r in page.rows]


def test_defaults_newest_first_and_deleted_hidden():
    page = build_page(DATA, q())
    assert page.total == 63 and page.found == 63
    assert ids(page)[0] == 159 and len(page.rows) == 25 and 4 not in ids(page)


def test_search_by_name_id_alias_case_insensitive():
    assert ids(build_page(DATA, q(q="KETO"))) == [3, 1]
    assert ids(build_page(DATA, q(q="#2"))) == [2]
    assert ids(build_page(DATA, q(q="a3"))) == [3]
    assert ids(build_page(DATA, q(q="keto ro"))) == [3]


def test_filters_and_facet_counts():
    page = build_page(DATA, q(state="disabled"))
    assert ids(page) == [2]
    assert page.state_counts == {"": 63, "active": 62, "disabled": 1}  # facet ignores its own filter
    assert ids(build_page(DATA, q(group="-"))) == [3]
    assert dict(build_page(DATA, q(q="keto")).groups) == {"Nutra": 1, "-": 1}
    assert ids(build_page(DATA, q(mine=1), opened={1, 2})) == [2, 1]


def test_sorting():
    assert ids(build_page(DATA, q(q="keto", sort="name", dir="asc"))) == [1, 3]
    assert ids(build_page(DATA, q(sort="created", dir="asc", q="o")))[:2] == [3, 1]  # Slim MX has no "o"
    assert ids(build_page(DATA, q(sort="bogus")))[0] == 159


def test_pagination_clamps_and_numbers():
    page = build_page(DATA, q(page=99))
    assert page.query.page == 3 and page.first == 51 and page.last == 63
    page = build_page(DATA, q(per=50))
    assert len(page.rows) == 50 and page.pages == 2
    assert build_page(DATA, q(per=7)).query.per == 25
    many = [camp(i, f"c{i}") for i in range(1, 501)]
    assert build_page(many, q(page=10)).page_numbers() == [1, None, 8, 9, 10, 11, 12, None, 20]


def test_urls_keep_filters_and_reset_page():
    query = q(q="keto", state="active", page=3)
    assert query.url(page=4) == "?q=keto&state=active&page=4"
    assert query.url(state="") == "?q=keto"
    assert query.sort_url("name") == "?q=keto&state=active&sort=name&dir=asc"
    assert q(sort="name", dir="asc").sort_url("name") == "?sort=name"


def test_name_sort_is_natural():
    data = [camp(1, "Keto #13"), camp(2, "Keto #4"), camp(3, "keto #100")]
    assert ids(build_page(data, q(sort="name", dir="asc"))) == [2, 1, 3]
