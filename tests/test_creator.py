import pytest
from sqlalchemy import select

from app.errors import DomainError
from app.models import Campaign
from app.services.creator import ISO_ALPHA2, generate_alias, parse_geo

FORM = {"name": "Test AU MX", "geo": "AU, mx", "offer_id": "3749", "domain_id": "1", "group_id": "2",
        "traffic_source_id": "1"}  # fmt: skip


def new_campaigns(kt_state):
    return [c for c in kt_state.campaigns.values() if c["id"] not in kt_state.seed_ids]


def test_parse_geo():
    assert parse_geo(" mx,AU;ro  au ") == ["MX", "AU", "RO"]
    for bad in ("AUS", "1", "XX,AU"):
        with pytest.raises(DomainError):
            parse_geo(bad)
    with pytest.raises(DomainError):
        parse_geo(" , ")
    assert "GB" in ISO_ALPHA2 and "UK" not in ISO_ALPHA2


def test_alias():
    alias = generate_alias()
    assert len(alias) == 7 and alias.isalnum() and alias.isascii()


def test_creates_campaign_with_two_streams_and_opens_editor(client, db, kt_state):
    resp = client.post("/campaigns/new", data=FORM, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    [camp] = new_campaigns(kt_state)
    assert resp.headers["location"] == f"/campaigns/{camp['id']}/streams?created=1"
    assert (camp["domain_id"], camp["group_id"], camp["traffic_source_id"], camp["type"]) == (1, 2, 1, "position")
    assert len(camp["alias"]) == 7

    streams = sorted((s for s in kt_state.streams.values() if s["campaign_id"] == camp["id"]),
                     key=lambda s: s["position"])  # fmt: skip
    geo, offers = streams
    assert geo["schema"] == "redirect" and geo["action_payload"] == "https://google.com"
    assert [(f["name"], f["mode"], f["payload"]) for f in geo["filters"]] == [("country", "accept", ["AU", "MX"])]
    assert geo["offers"] == []
    assert offers["filters"] == [] and offers["schema"] == "landings"
    assert [(o["offer_id"], o["share"]) for o in offers["offers"]] == [(3749, 100)]

    local = db.scalar(select(Campaign).where(Campaign.keitaro_id == camp["id"]))
    assert local is not None and [len(s.offers) for s in local.streams] == [0, 1]
    page = client.get(resp.headers["location"])
    assert page.status_code == 200 and "Кампания создана" in page.text


@pytest.mark.parametrize(
    ("override", "field_text"),
    [({"geo": "AUS"}, "AUS"), ({"geo": "1"}, "Неизвестные коды"), ({"name": "  "}, "не может быть пустым"),
     ({"offer_id": "999999"}, "не найден"), ({"offer_id": ""}, "Выберите оффер"), ({"domain_id": "77"}, "домен")],
)  # fmt: skip
def test_validation_errors_keep_input_and_create_nothing(client, kt_state, override, field_text):
    resp = client.post("/campaigns/new", data={**FORM, **override})
    assert resp.status_code == 400
    assert field_text in resp.text
    assert 'value="Test AU MX"' in resp.text or override.get("name")
    assert new_campaigns(kt_state) == []


def test_stream_failure_rolls_back_campaign(client, kt_state):
    kt_state.fail_stream_creates = 1
    resp = client.post("/campaigns/new", data=FORM)
    assert resp.status_code == 502
    assert "откатана" in resp.text and "Injected failure" in resp.text
    assert new_campaigns(kt_state) == []
    assert all(s["campaign_id"] in kt_state.seed_ids for s in kt_state.streams.values())
    assert 'value="AU" data-en="Australia" selected' in resp.text and 'value="MX" data-en="Mexico" selected' in resp.text


def test_second_stream_failure_also_rolls_back(client, kt_state):
    original = kt_state.add_stream
    calls = {"n": 0}

    def flaky(body, sid=None):
        calls["n"] += 1
        if calls["n"] == 2:
            kt_state.fail_stream_creates = 0
            from mock_keitaro.app import KeitaroError

            raise KeitaroError(400, {"error": "offer stream rejected"})
        return original(body, sid)

    kt_state.add_stream = flaky
    resp = client.post("/campaigns/new", data=FORM)
    assert resp.status_code == 502 and "offer stream rejected" in resp.text
    assert new_campaigns(kt_state) == []


def test_alias_collision_is_retried(client, kt_state, monkeypatch):
    aliases = iter(["dEm0Cmp", "fresh01"])
    monkeypatch.setattr("app.services.creator.generate_alias", lambda: next(aliases))
    resp = client.post("/campaigns/new", data=FORM, follow_redirects=False)
    assert resp.status_code == 303
    assert [c["alias"] for c in new_campaigns(kt_state)] == ["fresh01"]


def test_form_defaults_from_settings(client):
    page = client.get("/campaigns/new").text
    assert 'value="1" selected' in page and "track.example.com" in page


def test_index_lists_campaigns(client):
    page = client.get("/").text
    assert "1–25</strong> из 37" in page and 'aria-label="Страницы"' in page
    page = client.get("/", params={"q": "demo campaign"}).text
    assert "Demo campaign" in page and "/campaigns/2/streams" in page and "1–1</strong> из 1" in page


def test_index_filters_and_ajax_fragment(client):
    page = client.get("/", params={"state": "disabled", "group": "Gambling"}).text
    assert "Keto" not in page or "disabled" in page
    frag = client.get("/", params={"q": "zzz"}, headers={"X-Requested-With": "XMLHttpRequest"}).text
    assert "<html" not in frag and "Ничего не найдено" in frag and 'id="facets"' in frag


def test_index_marks_opened_and_dirty_campaigns(client):
    client.get("/campaigns/2/streams")
    page = client.get("/", params={"mine": "1"}).text
    assert "Demo campaign" in page and "открыта" in page


def test_navbar_shows_mock_tracker(client):
    assert "Mock Keitaro" in client.get("/").text


def test_country_picker_shows_russian_names():
    from app.services.creator import country_choices

    by_code = {c.code: c for c in country_choices()}
    assert by_code["AU"].name == "Австралия" and by_code["GB"].name_en == "United Kingdom"
    assert set(by_code) == ISO_ALPHA2


def test_creator_accepts_countries_from_multi_select(client, kt_state):
    resp = client.post("/campaigns/new", data={**FORM, "geo": ["AU", "MX"]}, follow_redirects=False)
    assert resp.status_code == 303
    (campaign,) = new_campaigns(kt_state)
    geo_stream = next(s for s in kt_state.streams.values() if s["campaign_id"] == campaign["id"] and s["filters"])
    assert geo_stream["filters"][0]["payload"] == ["AU", "MX"]


def test_creator_form_lists_countries_and_keeps_selection(client):
    page = client.get("/campaigns/new").text
    assert '<option value="AU" data-en="Australia" >Австралия · AU</option>' in page
    page = client.post("/campaigns/new", data={**FORM, "name": " ", "geo": ["RO", "XX"]}).text
    assert 'value="RO" data-en="Romania" selected' in page
    assert "XX — неизвестный код" in page
