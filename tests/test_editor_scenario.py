"""The demo-video scenario, step by step, through the HTTP API against mock Keitaro."""

import pytest
from sqlalchemy import select

from app.models import Stream, StreamOffer

CAMPAIGN = 2
FLOW1, FLOW2 = 564220, 564221
OFFER4, OFFER5 = 3750, 3751


def kt_shares(kt_state, stream_kid=FLOW2):
    return {o["offer_id"]: o["share"] for o in kt_state.streams[stream_kid]["offers"]}


def stream(db, kid=FLOW2) -> Stream:
    db.expire_all()
    return db.scalar(select(Stream).where(Stream.keitaro_id == kid))


def shares(db, kid=FLOW2):
    return {link.offer_id: link.share for link in stream(db, kid).offers}


def link_id(db, offer_id, kid=FLOW2) -> int:
    return next(link.id for link in stream(db, kid).offers if link.offer_id == offer_id)


def ok(resp):
    assert resp.status_code == 200, resp.text
    return resp


@pytest.fixture
def editor(client, db):
    ok(client.get(f"/campaigns/{CAMPAIGN}/streams"))  # first open imports
    return client


def test_fetch_shows_redirect_stream_and_offer_stream(editor, db):
    flow1 = stream(db, FLOW1)
    assert flow1.offers == [] and not flow1.accepts_offers
    assert "country" in flow1.summary and "google.com" in flow1.summary
    assert shares(db) == {3747: 33, 3748: 33, 3749: 34}
    assert not stream(db).is_dirty
    page = editor.get(f"/campaigns/{CAMPAIGN}/streams").text
    assert "Flow 1" in page and "Keto Burn AU" in page and "33%" in page


def test_add_makes_25s_and_dirty_without_touching_kt(editor, db, kt_state):
    sid = stream(db).id
    html = ok(editor.post(f"/api/streams/{sid}/offers", json={"offer_id": OFFER4})).text
    assert "stream-dirty" in html
    assert shares(db) == {3747: 25, 3748: 25, 3749: 25, OFFER4: 25}
    assert stream(db).is_dirty
    assert kt_shares(kt_state) == {3747: 33, 3748: 33, 3749: 34}


def test_cancel_drops_new_offer_and_restores_weights(editor, db):
    sid = stream(db).id
    ok(editor.post(f"/api/streams/{sid}/offers", json={"offer_id": OFFER4}))
    html = ok(editor.post(f"/api/streams/{sid}/cancel")).text
    assert "stream-dirty" not in html
    assert shares(db) == {3747: 33, 3748: 33, 3749: 34}
    assert db.scalar(select(StreamOffer).where(StreamOffer.offer_id == OFFER4)) is None
    assert not stream(db).is_dirty


def test_full_video_scenario(editor, db, kt_state):
    sid = stream(db).id

    # 2:28 Add + Push → 4 × 25 in Keitaro, stream clean
    ok(editor.post(f"/api/streams/{sid}/offers", json={"offer_id": OFFER4}))
    ok(editor.post(f"/api/streams/{sid}/push"))
    assert kt_shares(kt_state) == {3747: 25, 3748: 25, 3749: 25, OFFER4: 25}
    assert not stream(db).is_dirty

    # 3:25 Remove → grey 0%, rest 34/33/33, dirty
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3748)}/remove"))
    assert shares(db) == {3747: 34, 3748: 0, 3749: 33, OFFER4: 33}
    assert stream(db).is_dirty

    # 3:40 Push → gone from Keitaro, archived here
    ok(editor.post(f"/api/streams/{sid}/push"))
    assert kt_shares(kt_state) == {3747: 34, 3749: 33, OFFER4: 33}
    archived = next(link for link in stream(db).offers if link.offer_id == 3748)
    assert archived.state == "removed" and archived.share == 0
    assert not stream(db).is_dirty

    # 3:56 Bring back + Push → back in Keitaro
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3748)}/restore"))
    assert stream(db).is_dirty
    ok(editor.post(f"/api/streams/{sid}/push"))
    assert set(kt_shares(kt_state)) == {3747, 3748, 3749, OFFER4}
    assert sum(kt_shares(kt_state).values()) == 100

    # 4:11 Two offers deleted directly in Keitaro → Fetch → removed, not gone
    _kt_delete(kt_state, 3748, 3749)
    html = ok(editor.post(f"/api/campaigns/{CAMPAIGN}/fetch")).text
    assert "Bring back" in html
    states = {link.offer_id: link.state for link in stream(db).offers}
    assert states == {3747: "active", OFFER4: "active", 3748: "removed", 3749: "removed"}

    # 5:00 Pin one at 25% (manual input pins it), then Bring back → 25 / 38 / 37
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3747)}/share", json={"share": 25}))
    assert shares(db)[3747] == 25 and shares(db)[OFFER4] == 75
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3748)}/restore"))
    current = shares(db)
    assert current[3747] == 25
    assert sorted([current[OFFER4], current[3748]], reverse=True) == [38, 37]

    # Push → same numbers in Keitaro; pin survives
    ok(editor.post(f"/api/streams/{sid}/push"))
    assert kt_shares(kt_state) == {k: v for k, v in current.items() if v}
    assert next(link for link in stream(db).offers if link.offer_id == 3747).pinned


def _kt_delete(kt_state, *offer_ids):
    """Simulate a manual edit in the Keitaro admin."""
    s = kt_state.streams[FLOW2]
    s["offers"] = [o for o in s["offers"] if o["offer_id"] not in offer_ids]


def test_pin_keeps_weight_on_add_and_unpin_rebalances(editor, db):
    sid = stream(db).id
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3749)}/pin", json={"pinned": True}))
    ok(editor.post(f"/api/streams/{sid}/offers", json={"offer_id": OFFER4}))
    assert shares(db) == {3747: 22, 3748: 22, 3749: 34, OFFER4: 22}
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3749)}/pin", json={"pinned": False}))
    assert shares(db) == {3747: 25, 3748: 25, 3749: 25, OFFER4: 25}


def test_share_over_100_or_pinned_sum_over_100_rejected_without_damage(editor, db):
    before = shares(db)
    resp = editor.post(f"/api/stream-offers/{link_id(db, 3747)}/share", json={"share": 101})
    assert resp.status_code == 400 and "100" in resp.json()["error"]
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3747)}/share", json={"share": 60}))
    resp = editor.post(f"/api/stream-offers/{link_id(db, 3748)}/share", json={"share": 50})
    assert resp.status_code == 400 and "110" in resp.json()["error"]
    after = shares(db)
    assert after[3747] == 60 and after[3748] == 20 and sum(after.values()) == 100
    assert before != after


def test_push_with_sum_not_100_is_rejected(editor, db, kt_state):
    sid = stream(db).id
    for offer_id in (3747, 3748):
        ok(editor.post(f"/api/stream-offers/{link_id(db, offer_id)}/share", json={"share": 30}))
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3749)}/share", json={"share": 30}))
    resp = editor.post(f"/api/streams/{sid}/push")
    assert resp.status_code == 400 and "90" in resp.json()["error"]
    assert kt_shares(kt_state) == {3747: 33, 3748: 33, 3749: 34}


def test_cannot_add_offer_to_redirect_stream(editor, db):
    resp = editor.post(f"/api/streams/{stream(db, FLOW1).id}/offers", json={"offer_id": OFFER4})
    assert resp.status_code == 400 and "landings" in resp.json()["error"]


def test_duplicate_and_unknown_offer(editor, db):
    sid = stream(db).id
    resp = editor.post(f"/api/streams/{sid}/offers", json={"offer_id": 3747})
    assert resp.status_code == 400 and "уже в потоке" in resp.json()["error"]
    resp = editor.post(f"/api/streams/{sid}/offers", json={"offer_id": 999999})
    assert resp.status_code == 400 and "не найден" in resp.json()["error"]


def test_adding_removed_offer_brings_it_back(editor, db):
    sid = stream(db).id
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3747)}/remove"))
    ok(editor.post(f"/api/streams/{sid}/offers", json={"offer_id": 3747}))
    assert shares(db) == {3747: 34, 3748: 33, 3749: 33}


def test_push_refuses_when_keitaro_changed_after_fetch(editor, db, kt_state):
    sid = stream(db).id
    ok(editor.post(f"/api/streams/{sid}/offers", json={"offer_id": OFFER4}))
    kt_state.streams[FLOW2]["offers"][0]["share"] = 50  # someone edited the stream in Keitaro
    resp = editor.post(f"/api/streams/{sid}/push")
    assert resp.status_code == 409 and "Fetch" in resp.json()["error"]
    assert OFFER4 not in kt_shares(kt_state)


def test_fetch_discards_drafts_and_syncs_deleted_streams(editor, db, kt_state):
    sid = stream(db).id
    ok(editor.post(f"/api/streams/{sid}/offers", json={"offer_id": OFFER4}))
    kt_state.streams[FLOW1]["state"] = "deleted"
    ok(editor.post(f"/api/campaigns/{CAMPAIGN}/fetch"))
    assert stream(db, FLOW1) is None
    assert shares(db) == {3747: 33, 3748: 33, 3749: 34}


def test_push_keeps_other_stream_fields_and_kt_disabled_state(editor, db, kt_state):
    kt_state.streams[FLOW2]["offers"][2]["state"] = "disabled"
    ok(editor.post(f"/api/campaigns/{CAMPAIGN}/fetch"))
    sid = stream(db).id
    ok(editor.post(f"/api/streams/{sid}/offers", json={"offer_id": OFFER4}))
    ok(editor.post(f"/api/streams/{sid}/push"))
    kt_stream = kt_state.streams[FLOW2]
    assert {o["offer_id"]: o["state"] for o in kt_stream["offers"]}[3749] == "disabled"
    assert kt_stream["schema"] == "landings" and kt_stream["name"] == "Flow 2"


def test_offer_autocomplete(client):
    data = client.get("/api/offers", params={"q": "slim"}).json()
    assert data["results"] == [{"id": 3748, "text": "#3748 Slim Fit MX"}]
    data = client.get("/api/offers", params={"q": "3750"}).json()
    assert data["results"][0]["id"] == 3750


def test_keitaro_errors_become_502(client, kt):
    kt.http.headers["Api-Key"] = "wrong"
    resp = client.post(f"/api/campaigns/{CAMPAIGN}/fetch")
    assert resp.status_code == 502 and "Unauthorized" in resp.json()["error"]


def test_autocomplete_hides_offers_already_in_stream(editor, db):
    sid = stream(db).id
    ids = [r["id"] for r in editor.get("/api/offers", params={"stream_id": sid}).json()["results"]]
    assert 3747 not in ids and OFFER4 in ids
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3747)}/remove"))
    ids = [r["id"] for r in editor.get("/api/offers", params={"stream_id": sid}).json()["results"]]
    assert 3747 in ids


# --- review fixes -------------------------------------------------------------


def test_push_of_stream_without_offers_is_blocked(editor, db, kt_state):
    sid = stream(db).id
    for offer_id in (3747, 3748, 3749):
        ok(editor.post(f"/api/stream-offers/{link_id(db, offer_id)}/remove"))
    html = editor.get(f"/campaigns/{CAMPAIGN}/streams").text
    assert "не осталось активных офферов" in html
    resp = editor.post(f"/api/streams/{sid}/push")
    assert resp.status_code == 400 and "не осталось активных офферов" in resp.json()["error"]
    assert kt_shares(kt_state) == {3747: 33, 3748: 33, 3749: 34}


def test_fetch_after_offers_deleted_in_kt_proposes_100_percent_draft(editor, db, kt_state):
    _kt_delete(kt_state, 3748)  # Keitaro now has 33 + 34 = 67%
    ok(editor.post(f"/api/campaigns/{CAMPAIGN}/fetch"))
    assert shares(db) == {3747: 50, 3748: 0, 3749: 50}
    assert stream(db).is_dirty  # Push is offered right away
    ok(editor.post(f"/api/streams/{stream(db).id}/push"))
    assert kt_shares(kt_state) == {3747: 50, 3749: 50}


def test_pin_is_dropped_when_keitaro_has_a_different_value(editor, db):
    sid = stream(db).id
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3747)}/share", json={"share": 25}))
    ok(editor.post(f"/api/campaigns/{CAMPAIGN}/fetch"))  # draft 25 discarded, Keitaro has 33
    link = next(link for link in stream(db).offers if link.offer_id == 3747)
    assert link.share == 33 and not link.pinned
    ok(editor.post(f"/api/streams/{sid}/offers", json={"offer_id": OFFER4}))
    assert shares(db) == {3747: 25, 3748: 25, 3749: 25, OFFER4: 25}


def test_pin_survives_fetch_when_keitaro_agrees(editor, db):
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3749)}/pin", json={"pinned": True}))
    ok(editor.post(f"/api/campaigns/{CAMPAIGN}/fetch"))
    assert next(link for link in stream(db).offers if link.offer_id == 3749).pinned


def test_pin_and_unpin_alone_do_not_make_stream_dirty(editor, db):
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3747)}/pin", json={"pinned": True}))
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3747)}/pin", json={"pinned": False}))
    assert shares(db) == {3747: 33, 3748: 33, 3749: 34}
    assert not stream(db).is_dirty


def test_offer_disabled_in_kt_is_outside_100_percent(editor, db, kt_state):
    kt_state.streams[FLOW2]["offers"][2]["state"] = "disabled"  # 3749, 34%
    ok(editor.post(f"/api/campaigns/{CAMPAIGN}/fetch"))
    s = stream(db)
    assert s.total_share == 100 and shares(db) == {3747: 50, 3748: 50, 3749: 34}
    resp = editor.post(f"/api/stream-offers/{link_id(db, 3749)}/share", json={"share": 10})
    assert resp.status_code == 400 and "выключен в Keitaro" in resp.json()["error"]
    ok(editor.post(f"/api/streams/{s.id}/offers", json={"offer_id": OFFER4}))
    assert shares(db) == {3747: 34, 3748: 33, 3749: 34, OFFER4: 33}


def test_bring_back_returns_offer_to_its_place(editor, db):
    sid = stream(db).id
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3748)}/remove"))
    ok(editor.post(f"/api/streams/{sid}/push"))
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3748)}/restore"))
    assert [link.offer_id for link in stream(db).offers] == [3747, 3748, 3749]
    ok(editor.post(f"/api/streams/{sid}/push"))
    ok(editor.post(f"/api/campaigns/{CAMPAIGN}/fetch"))
    assert [link.offer_id for link in stream(db).offers][:3] == [3747, 3748, 3749]


def test_redirect_stream_shows_filter_values(editor, db):
    assert "AU" in stream(db, FLOW1).summary


def test_cancel_drops_pin_of_discarded_value(editor, db):
    sid = stream(db).id
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3747)}/share", json={"share": 40}))
    ok(editor.post(f"/api/stream-offers/{link_id(db, 3749)}/pin", json={"pinned": True}))
    ok(editor.post(f"/api/streams/{sid}/cancel"))
    pins = {link.offer_id: link.pinned for link in stream(db).offers}
    assert pins == {3747: False, 3748: False, 3749: False}
    assert shares(db) == {3747: 33, 3748: 33, 3749: 34}
