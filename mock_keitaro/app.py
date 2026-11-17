"""In-memory mock of the Keitaro Admin API (only what Adrobot uses).

Run: ``uvicorn mock_keitaro.app:app --port 8100``. Same paths as the real
tracker (``/admin_api/v1/...``), ``Api-Key`` auth, seed data mirroring the demo
video. ``/mock`` is a tiny admin page to edit streams "directly in Keitaro".
"""

import copy
import html
import itertools
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

API_KEY = os.environ.get("MOCK_API_KEY", "mock-key")

STREAM_FIELDS = {
    "campaign_id", "type", "name", "position", "weight", "action_options", "comments", "state",
    "action_type", "action_payload", "schema", "collect_clicks", "filter_or", "filters", "triggers",
    "landings", "offers",
}  # fmt: skip


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class KeitaroError(HTTPException):
    pass


class MockState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._ids = itertools.count(1000)
        self.campaigns: dict[int, dict[str, Any]] = {}
        self.streams: dict[int, dict[str, Any]] = {}
        self.offers: dict[int, dict[str, Any]] = {}
        self.domains = [{"id": 1, "name": "track.example.com", "state": "active"},
                        {"id": 2, "name": "go.example.org", "state": "active"}]  # fmt: skip
        self.groups = [{"id": 1, "name": "Tests", "position": 1, "type": "campaigns"},
                       {"id": 2, "name": "Nutra", "position": 2, "type": "campaigns"}]  # fmt: skip
        self.traffic_sources = [{"id": 1, "name": "Facebook", "state": "active"},
                                {"id": 2, "name": "Propeller Ads", "state": "active"}]  # fmt: skip
        # Failure injection for tests: number of next POST /streams calls to fail.
        self.fail_stream_creates = 0
        self.requests: list[tuple[str, str]] = []
        self._seed()
        self.seed_ids = set(self.campaigns)

    def next_id(self) -> int:
        return next(self._ids)

    def _seed(self) -> None:
        names = ["Keto Burn AU", "Slim Fit MX", "Joint Pro RO", "Cardio Plus", "Vision Max",
                 "Hair Grow", "Prostatin", "Diabetix", "Varicose Off", "Detox Tea", "Libido Max",
                 "Smart Watch X"]  # fmt: skip
        for i, name in enumerate(names):
            oid = 3747 + i
            self.offers[oid] = {"id": oid, "name": name, "state": "active", "group_id": 1,
                                "action_type": "http", "action_payload": f"https://offers.example/{oid}",
                                "preview_path": f"https://offers.example/{oid}", "country": []}  # fmt: skip
        camp = self.add_campaign({"name": "Demo campaign", "alias": "dEm0Cmp", "type": "position",
                                  "domain_id": 1, "group_id": 1, "traffic_source_id": 1}, cid=2)  # fmt: skip
        self.add_stream({"campaign_id": camp["id"], "type": "regular", "name": "Flow 1", "position": 1,
                         "schema": "redirect", "action_type": "http", "action_payload": "https://google.com",
                         "filters": [{"name": "country", "mode": "accept", "payload": ["AU"]}]}, sid=564220)  # fmt: skip
        self.add_stream({"campaign_id": camp["id"], "type": "regular", "name": "Flow 2", "position": 2,
                         "schema": "landings", "action_type": "http", "filters": [],
                         "offers": [{"offer_id": 3747, "share": 33}, {"offer_id": 3748, "share": 33},
                                    {"offer_id": 3749, "share": 34}]}, sid=564221)  # fmt: skip
        self._seed_demo_campaigns()

    def _seed_demo_campaigns(self) -> None:
        """Enough campaigns to make search, filters and pagination on the list page meaningful."""
        self.groups.append({"id": 3, "name": "Gambling", "position": 3, "type": "campaigns"})
        brands = ["Keto", "Slim", "Joint", "Cardio", "Vision", "Hair", "Detox", "Libido", "Smart"]
        geos = [["AU"], ["MX"], ["RO", "BG"], ["DE", "AT"], ["PL"], ["IT"], ["ES", "PT"], ["FR"], ["TR"]]
        for i in range(36):
            brand, geo = brands[i % len(brands)], geos[i % len(geos)]
            camp = self.add_campaign({
                "name": f"{brand} {'/'.join(geo)} {'FB' if i % 3 else 'PA'} #{i + 1}",
                "alias": f"demo{i:03d}", "state": "disabled" if i % 7 == 6 else "active",
                "domain_id": 1 + i % 2, "group_id": [1, 2, 2, 3][i % 4], "traffic_source_id": 1 + i % 2,
            })  # fmt: skip
            camp["created_at"] = f"2026-{1 + i % 11:02d}-{1 + (i * 7) % 27:02d} {8 + i % 12:02d}:{(i * 13) % 60:02d}:00"
            self.add_stream({"campaign_id": camp["id"], "type": "regular", "name": "Geo → redirect",
                             "position": 1, "schema": "redirect", "action_type": "http",
                             "action_payload": "https://google.com",
                             "filters": [{"name": "country", "mode": "accept", "payload": geo}]})  # fmt: skip
            offers = [3747 + (i + k) % 12 for k in range(1 + i % 3)]
            base, rem = divmod(100, len(offers))
            self.add_stream({"campaign_id": camp["id"], "type": "regular", "name": "Offers", "position": 2,
                             "schema": "landings", "action_type": "http", "filters": [],
                             "offers": [{"offer_id": o, "share": base + (k < rem)} for k, o in enumerate(offers)]})  # fmt: skip

    # --- entities ---------------------------------------------------------
    def add_campaign(self, body: dict[str, Any], cid: int | None = None) -> dict[str, Any]:
        cid = cid or self.next_id()
        camp = {"id": cid, "alias": body["alias"], "name": body["name"], "type": body.get("type", "position"),
                "state": body.get("state", "active"), "cost_type": body.get("cost_type", "CPC"),
                "cost_value": body.get("cost_value", 0), "cost_currency": body.get("cost_currency", "USD"),
                "cost_auto": body.get("cost_auto", False), "domain_id": body.get("domain_id"),
                "group_id": _int_or_none(body.get("group_id")), "traffic_source_id": body.get("traffic_source_id"),
                "position": len(self.campaigns) + 1, "created_at": _now(), "updated_at": _now()}  # fmt: skip
        self.campaigns[cid] = camp
        return camp

    def view_campaign(self, camp: dict[str, Any]) -> dict[str, Any]:
        """Real Keitaro also returns group and domain names (probe 2026-11-17)."""
        group = next((g["name"] for g in self.groups if g["id"] == camp.get("group_id")), "")
        domain = next((d["name"] for d in self.domains if d["id"] == camp.get("domain_id")), "")
        return {**camp, "group": group, "domain": f"https://{domain}/" if domain else ""}

    def add_stream(self, body: dict[str, Any], sid: int | None = None) -> dict[str, Any]:
        sid = sid or self.next_id()
        stream = {"id": sid, "type": "regular", "name": "", "position": 1, "weight": 0, "state": "active",
                  "action_type": "http", "action_payload": None, "action_options": None, "schema": "landings",
                  "collect_clicks": True, "filter_or": False, "comments": None, "triggers": [], "landings": []}  # fmt: skip
        stream.update({k: v for k, v in body.items() if k in STREAM_FIELDS and k not in ("filters", "offers")})
        stream["id"] = sid
        stream["filters"] = self._filters(sid, body.get("filters") or [])
        stream["offers"] = self._offers(sid, body.get("offers") or [])
        stream["updated_at"] = _now()
        self.streams[sid] = stream
        return stream

    def update_stream(self, sid: int, body: dict[str, Any]) -> dict[str, Any]:
        stream = self.streams[sid]
        for key, value in body.items():
            if key == "filters":
                stream["filters"] = self._filters(sid, value or [])
            elif key == "offers":
                # Real Keitaro doesn't keep the order sent in PUT (probe 2026-11-17: sent [a, b], got [b, a]).
                stream["offers"] = list(reversed(self._offers(sid, value or [])))
            elif key in STREAM_FIELDS:
                stream[key] = value
        stream["updated_at"] = _now()
        return stream

    def _filters(self, sid: int, filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for f in filters:
            if not f.get("name") or f.get("mode") not in ("accept", "reject"):
                raise KeitaroError(400, {"error": "Filter requires name and mode accept|reject"})
            out.append({"id": self.next_id(), "stream_id": sid, "name": f["name"], "mode": f["mode"],
                        "payload": f.get("payload")})  # fmt: skip
        return out

    def _offers(self, sid: int, offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out, seen = [], set()
        for o in offers:
            oid = o.get("offer_id")
            if oid not in self.offers:
                raise KeitaroError(400, {"error": f"Offer #{oid} not found"})
            if oid in seen:
                raise KeitaroError(400, {"error": f"Offer #{oid} is duplicated"})
            share = o.get("share")
            if not isinstance(share, int) or not 0 <= share <= 100:
                raise KeitaroError(400, {"error": f"Invalid share for offer #{oid}"})
            seen.add(oid)
            out.append({"id": self.next_id(), "stream_id": sid, "offer_id": oid, "share": share,
                        "state": o.get("state", "active"), "created_at": _now(), "updated_at": _now()})  # fmt: skip
        return out


def _int_or_none(value: Any) -> int | None:
    return None if value in (None, "") else int(value)


state = MockState()
api = APIRouter(prefix="/admin_api/v1")


def check_key(api_key: str | None = Header(default=None, alias="Api-Key")) -> None:
    if api_key != API_KEY:
        raise KeitaroError(401, {"error": "Unauthorized"})


def _get(collection: dict[int, dict[str, Any]], key: int, what: str) -> dict[str, Any]:
    if key not in collection:
        raise KeitaroError(404, {"error": f"{what} not found"})
    return collection[key]


@api.get("/campaigns")
def list_campaigns(offset: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
    items = [state.view_campaign(c) for c in state.campaigns.values() if c["state"] != "deleted"]
    return items[offset : offset + limit if limit else None]


@api.post("/campaigns")
def create_campaign(body: dict[str, Any]) -> dict[str, Any]:
    if not body.get("name") or not body.get("alias"):
        raise KeitaroError(400, {"error": "name and alias are required"})
    if any(c["alias"] == body["alias"] for c in state.campaigns.values()):
        raise KeitaroError(400, {"error": "Alias already exists"})
    for field, items in (("domain_id", state.domains), ("traffic_source_id", state.traffic_sources),
                         ("group_id", state.groups)):  # fmt: skip
        value = _int_or_none(body.get(field))
        if value is not None and value not in {i["id"] for i in items}:
            raise KeitaroError(400, {"error": f"Invalid {field}"})
    return state.view_campaign(state.add_campaign(body))


@api.get("/campaigns/{cid}")
def get_campaign(cid: int) -> dict[str, Any]:
    return state.view_campaign(_get(state.campaigns, cid, "Campaign"))


@api.delete("/campaigns/{cid}")
def delete_campaign(cid: int) -> dict[str, Any]:
    camp = _get(state.campaigns, cid, "Campaign")
    del state.campaigns[cid]
    for sid in [s for s, v in state.streams.items() if v["campaign_id"] == cid]:
        del state.streams[sid]
    return camp


@api.get("/campaigns/{cid}/streams")
def campaign_streams(cid: int) -> list[dict[str, Any]]:
    _get(state.campaigns, cid, "Campaign")
    streams = [s for s in state.streams.values() if s["campaign_id"] == cid and s["state"] != "deleted"]
    return sorted(streams, key=lambda s: s["position"])


@api.post("/streams")
def create_stream(body: dict[str, Any]) -> dict[str, Any]:
    missing = [f for f in ("campaign_id", "schema", "type", "name", "action_type") if not body.get(f)]
    if missing:
        raise KeitaroError(400, {"error": f"Required: {', '.join(missing)}"})
    _get(state.campaigns, body["campaign_id"], "Campaign")
    if state.fail_stream_creates:
        state.fail_stream_creates -= 1
        raise KeitaroError(500, {"error": "Injected failure: cannot create stream"})
    return state.add_stream(body)


@api.get("/streams/{sid}")
def get_stream(sid: int) -> dict[str, Any]:
    return _get(state.streams, sid, "Stream")


@api.put("/streams/{sid}")
def put_stream(sid: int, body: dict[str, Any]) -> dict[str, Any]:
    _get(state.streams, sid, "Stream")
    return state.update_stream(sid, body)


@api.delete("/streams/{sid}")
def delete_stream(sid: int) -> dict[str, Any]:
    stream = _get(state.streams, sid, "Stream")
    stream["state"] = "deleted"
    return stream


@api.get("/offers")
def list_offers() -> list[dict[str, Any]]:
    return list(state.offers.values())


@api.get("/domains")
def list_domains() -> list[dict[str, Any]]:
    return state.domains


@api.get("/groups")
def list_groups(type: str | None = None) -> list[dict[str, Any]]:  # noqa: A002
    if not type:
        raise KeitaroError(400, {"error": "type is required"})
    return [g for g in state.groups if g["type"] == type]


@api.get("/traffic_sources")
def list_traffic_sources() -> list[dict[str, Any]]:
    return state.traffic_sources


@api.get("/streams_actions")
def stream_actions() -> list[dict[str, Any]]:
    return [{"key": "http", "name": "HTTP redirect", "type": "redirect"},
            {"key": "meta", "name": "Meta redirect", "type": "redirect"}]  # fmt: skip


app = FastAPI(title="Mock Keitaro Admin API")


@app.middleware("http")
async def _log(request: Request, call_next):  # noqa: ANN001, ANN202
    state.requests.append((request.method, request.url.path))
    return await call_next(request)


@app.exception_handler(KeitaroError)
def _keitaro_error(_: Request, exc: KeitaroError) -> JSONResponse:
    return JSONResponse(exc.detail, status_code=exc.status_code)


app.include_router(api, dependencies=[Depends(check_key)])


# --- "Directly in Keitaro" admin page ---------------------------------------
@app.get("/mock", response_class=HTMLResponse)
def mock_admin() -> str:
    rows = []
    for camp in state.campaigns.values():
        rows.append(f"<h2>Campaign #{camp['id']} {html.escape(camp['name'])}</h2>")
        for s in campaign_streams(camp["id"]):
            rows.append(f"<h3>Stream #{s['id']} {html.escape(s['name'])} ({s['schema']})</h3><ul>")
            for o in s["offers"]:
                name = html.escape(state.offers[o["offer_id"]]["name"])
                rows.append(
                    f"<li>#{o['offer_id']} {name} — {o['share']}% "
                    f"<form method=post action=/mock/streams/{s['id']}/offers/{o['offer_id']}/delete "
                    f"style=display:inline><button>Удалить оффер в KT</button></form></li>"
                )
            rows.append("</ul>")
    return (
        "<!doctype html><meta charset=utf-8><title>Mock Keitaro</title>"
        "<style>body{font-family:system-ui;max-width:900px;margin:2rem auto}</style>"
        "<h1>Mock Keitaro admin</h1><form method=post action=/mock/reset><button>Reset seed</button></form>"
        + "".join(rows)
    )


@app.get("/admin/", response_class=HTMLResponse)
def mock_view_in_kt() -> RedirectResponse:
    return RedirectResponse("/mock")


@app.post("/mock/streams/{sid}/offers/{oid}/delete")
def mock_delete_offer(sid: int, oid: int) -> RedirectResponse:
    stream = _get(state.streams, sid, "Stream")
    stream["offers"] = [o for o in stream["offers"] if o["offer_id"] != oid]
    return RedirectResponse("/mock", status_code=303)


@app.post("/mock/reset")
def mock_reset() -> RedirectResponse:
    state.reset()
    return RedirectResponse("/mock", status_code=303)


def snapshot() -> dict[str, Any]:
    """Deep copy of the state, handy for assertions in tests."""
    return copy.deepcopy({"campaigns": state.campaigns, "streams": state.streams})
