"""Check assumptions about the real Keitaro Admin API before trusting the mock.

    uv run python scripts/probe_keitaro.py            # read-only checks
    uv run python scripts/probe_keitaro.py --write    # + temporary adrobot-probe-* campaign, deleted at the end

Never prints the API key.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.keitaro import KeitaroClient, KeitaroError  # noqa: E402
from app.services.creator import generate_alias  # noqa: E402

VOLATILE = {"updated_at", "offers", "created_at"}


def show(title: str, value: object) -> None:
    print(f"\n== {title}\n{json.dumps(value, ensure_ascii=False, indent=2, default=str)[:3000]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="create, PUT and delete a temporary campaign")
    args = parser.parse_args()

    settings = get_settings()
    kt = KeitaroClient.from_settings(settings)
    print(f"Keitaro: {settings.keitaro_api_base}")

    domains, groups, sources = kt.list_domains(), kt.list_campaign_groups(), kt.list_traffic_sources()
    offers = kt.list_offers()
    campaigns = kt.list_campaigns()
    show("counts", {"domains": len(domains), "groups": len(groups), "traffic_sources": len(sources),
                    "offers": len(offers), "campaigns": len(campaigns)})  # fmt: skip
    show("domains", [{k: d.get(k) for k in ("id", "name", "state")} for d in domains[:10]])
    show("groups", groups[:10])
    show("traffic_sources", [{k: s.get(k) for k in ("id", "name", "state")} for s in sources[:10]])
    show("offer sample", offers[:1])
    try:
        show("streams_actions", kt._request("GET", "/streams_actions"))
    except KeitaroError as exc:
        print("streams_actions:", exc.message)
    for camp in campaigns[:3]:
        streams = kt.campaign_streams(camp["id"])
        show(f"campaign #{camp['id']} keys", sorted(camp))
        if streams:
            show(f"campaign #{camp['id']} first stream", streams[0])
            break

    if not args.write:
        return 0
    if len(offers) < 2:
        print("Need at least 2 offers for the PUT check")
        return 1

    payload = {"name": f"adrobot-probe-{generate_alias()}", "alias": generate_alias(), "type": "position",
               "state": "active", "cost_type": "CPC", "cost_value": 0, "cost_auto": True}  # fmt: skip
    if domains:
        payload["domain_id"] = settings.default_domain_id or domains[0]["id"]
    if groups:
        payload["group_id"] = str(settings.default_group_id or groups[0]["id"])
    if sources:
        payload["traffic_source_id"] = settings.default_traffic_source_id or sources[0]["id"]
    camp = kt.create_campaign(payload)
    show("created campaign", camp)
    try:
        geo = kt.create_stream({"campaign_id": camp["id"], "type": "regular", "name": "probe geo", "position": 1,
                                "state": "active", "schema": "redirect", "action_type": "http",
                                "action_payload": "https://google.com",
                                "filters": [{"name": "country", "mode": "accept", "payload": ["AU", "MX"]}]})  # fmt: skip
        show("geo stream", geo)
        o1, o2 = offers[0]["id"], offers[1]["id"]
        st = kt.create_stream({"campaign_id": camp["id"], "type": "regular", "name": "probe offers",
                               "position": 2, "state": "active", "schema": "landings", "action_type": "http",
                               "filters": [], "offers": [{"offer_id": o1, "share": 50, "state": "active"},
                                                         {"offer_id": o2, "share": 50, "state": "active"}]})  # fmt: skip
        before = kt.get_stream(st["id"])
        show("offers stream (GET)", before)

        kt.update_stream(st["id"], {"offers": [{"offer_id": o2, "share": 100, "state": "active"}]})
        after = kt.get_stream(st["id"])
        changed = {k: (before.get(k), after.get(k)) for k in set(before) | set(after)
                   if k not in VOLATILE and before.get(k) != after.get(k)}  # fmt: skip
        show("PUT {offers} → other fields changed", changed or "none")
        show("PUT {offers} → offers now", [(o["offer_id"], o["share"], o.get("state")) for o in after["offers"]])

        # Does Keitaro validate the sum, and how does it treat disabled offers?
        for label, offers_body in (
            ("active 50 + disabled 50", [{"offer_id": o1, "share": 50, "state": "active"},
                                         {"offer_id": o2, "share": 50, "state": "disabled"}]),
            ("active 100 + disabled 40", [{"offer_id": o1, "share": 100, "state": "active"},
                                          {"offer_id": o2, "share": 40, "state": "disabled"}]),
            ("active 60 + active 30 (sum 90)", [{"offer_id": o1, "share": 60, "state": "active"},
                                                {"offer_id": o2, "share": 30, "state": "active"}]),
            ("empty list", []),
        ):  # fmt: skip
            try:
                kt.update_stream(st["id"], {"offers": offers_body})
                now = kt.get_stream(st["id"])["offers"]
                show(f"PUT {label} → accepted", [(o["offer_id"], o["share"], o.get("state")) for o in now])
            except KeitaroError as exc:
                show(f"PUT {label} → rejected", {"status": exc.status_code, "error": exc.message})

        geo_after = kt.get_stream(geo["id"])
        show("geo stream filters after", geo_after.get("filters"))
        show("campaign streams list", [(s["id"], s["name"], s.get("state")) for s in kt.campaign_streams(camp["id"])])
    finally:
        kt.delete_campaign(camp["id"])
        print(f"\nDeleted probe campaign #{camp['id']}")
        try:
            show("campaign after delete", {k: v for k, v in kt.get_campaign(camp["id"]).items() if k in ("id", "state")})
        except KeitaroError as exc:
            print("GET after delete:", exc.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
