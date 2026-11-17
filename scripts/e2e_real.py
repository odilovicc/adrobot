"""Video scenario against the real Keitaro on a throwaway adrobot-probe-e2e-* campaign (deleted at the end).

Run: make e2e-real (needs a real tracker in .env and DEFAULT_*_ID set).
"""

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, make_engine  # noqa: E402
from app.keitaro import KeitaroClient  # noqa: E402
from app.services import creator, editor  # noqa: E402
from app.services.sync import import_campaign  # noqa: E402

settings = get_settings()
kt = KeitaroClient.from_settings(settings)
engine = make_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
db = sessionmaker(engine)()

offers = [o["id"] for o in kt.list_offers() if o.get("state") == "active"][:5]
o1, o2, o3, o4 = offers[:4]


def kt_offers(sid):
    return sorted((o["offer_id"], o["share"]) for o in kt.get_stream(sid)["offers"])


def ui(stream):
    return [(lk.offer_id, lk.share, lk.state, "pin" if lk.pinned else "") for lk in stream.offers]


form = creator.CampaignForm(name=f"adrobot-probe-e2e-{secrets.token_hex(3)}", geo="au, mx", offer_id=o1,
                            domain_id=settings.default_domain_id, group_id=settings.default_group_id,
                            traffic_source_id=settings.default_traffic_source_id)
form = creator.validate(form, kt)
camp = creator.create_campaign(db, kt, settings, form)
print("created", camp.keitaro_id, camp.name)
try:
    geo, st = sorted(camp.streams, key=lambda s: s.position)
    print("geo summary:", geo.summary)
    sid, kid = st.id, st.keitaro_id
    print("start KT", kt_offers(kid))

    for o in (o2, o3):
        editor.add_offer(db, kt, sid, o)
    s = editor.push(db, kt, sid)
    print("3 offers pushed:", kt_offers(kid), "dirty", s.is_dirty)

    s = editor.add_offer(db, kt, sid, o4)
    print("add 4th (draft):", ui(s), "KT", kt_offers(kid))
    s = editor.push(db, kt, sid)
    assert sorted(sh for _, sh in kt_offers(kid)) == [25, 25, 25, 25]
    print("push 4x25 OK; order", [lk.offer_id for lk in s.offers])

    link = next(lk for lk in s.offers if lk.offer_id == o2)
    s = editor.remove_offer(db, link.id)
    s = editor.push(db, kt, sid)
    print("remove+push:", kt_offers(kid))
    assert o2 not in dict(kt_offers(kid))

    s = editor.restore_offer(db, link.id)
    s = editor.push(db, kt, sid)
    print("bring back+push:", kt_offers(kid), "order", [lk.offer_id for lk in s.offers])
    assert [lk.offer_id for lk in s.offers] == [o1, o2, o3, o4]

    # 4:11 delete two offers directly in Keitaro, then Fetch
    keep = [o for o in kt.get_stream(kid)["offers"] if o["offer_id"] not in (o2, o3)]
    kt.update_stream(kid, {"offers": [{"offer_id": o["offer_id"], "share": o["share"], "state": o["state"]} for o in keep]})
    import_campaign(db, kt, camp.keitaro_id)
    s = editor.get_stream(db, sid)
    print("after KT delete + Fetch:", ui(s), "dirty", s.is_dirty)

    l1 = next(lk for lk in s.offers if lk.offer_id == o1)
    editor.set_share(db, l1.id, 25)
    s = editor.restore_offer(db, next(lk for lk in s.offers if lk.offer_id == o2).id)
    print("pin 25 + bring back:", ui(s))
    s = editor.push(db, kt, sid)
    print("pushed:", kt_offers(kid))
    assert sorted(sh for _, sh in kt_offers(kid)) == [25, 37, 38]

    # geo stream must be untouched by all those PUTs
    g = kt.get_stream(geo.keitaro_id)
    print("geo after:", g["action_type"], g["action_payload"], [(f["name"], f["payload"]) for f in g["filters"]])
    print("E2E OK")
finally:
    kt.delete_campaign(camp.keitaro_id)
    print("deleted campaign", camp.keitaro_id)
