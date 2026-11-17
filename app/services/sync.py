"""Fetch: Keitaro is the source of truth, our DB keeps drafts, pins and the archive."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.keitaro import KeitaroClient
from app.models import Campaign, Offer, OfferState, Stream, StreamOffer


def import_campaign(db: Session, kt: KeitaroClient, keitaro_id: int) -> Campaign:
    """Create or refresh a campaign with all its streams. Discards unpublished drafts."""
    kt_campaign = kt.get_campaign(keitaro_id)
    kt_streams = kt.campaign_streams(keitaro_id)

    campaign = db.scalar(select(Campaign).where(Campaign.keitaro_id == keitaro_id))
    if campaign is None:
        campaign = Campaign(keitaro_id=keitaro_id, name="", alias="")
        db.add(campaign)
    campaign.name = kt_campaign.get("name") or f"#{keitaro_id}"
    campaign.alias = kt_campaign.get("alias") or ""
    campaign.fetched_at = datetime.now(UTC).replace(tzinfo=None)

    names = _offer_names(kt, kt_streams)
    by_kid = {s.keitaro_id: s for s in campaign.streams}
    seen: set[int] = set()
    for kt_stream in kt_streams:
        stream = by_kid.get(kt_stream["id"])
        if stream is None:
            stream = Stream(keitaro_id=kt_stream["id"])
            campaign.streams.append(stream)
        seen.add(kt_stream["id"])
        apply_kt_stream(db, stream, kt_stream, names)

    for kid, stream in by_kid.items():
        if kid not in seen:  # deleted in Keitaro
            campaign.streams.remove(stream)

    db.commit()
    db.refresh(campaign)
    return campaign


def apply_kt_stream(db: Session, stream: Stream, kt_stream: dict[str, Any], names: dict[int, str]) -> None:
    """Make ``kt_stream`` the new published state of ``stream``."""
    stream.name = kt_stream.get("name") or f"Stream #{kt_stream['id']}"
    stream.position = kt_stream.get("position") or 0
    stream.type = kt_stream.get("type") or ""
    stream.schema = kt_stream.get("schema") or ""
    stream.state = kt_stream.get("state") or "active"
    stream.summary = stream_summary(kt_stream)

    kt_offers = kt_stream.get("offers") or []
    links = {link.offer_id: link for link in stream.offers}
    position = 0
    for kt_offer in kt_offers:
        oid = int(kt_offer["offer_id"])
        upsert_offer(db, oid, names.get(oid))
        link = links.pop(oid, None)
        if link is None:
            link = StreamOffer(offer_id=oid, pinned=False)
            stream.offers.append(link)
        link.position = position
        position += 1
        link.state = link.published_state = OfferState.ACTIVE
        link.share = link.published_share = int(kt_offer.get("share") or 0)
        link.kt_state = kt_offer.get("state") or "active"
        link.is_new = False

    # Known to us but gone from Keitaro: drafts are dropped, published ones go to the archive.
    for link in sorted(links.values(), key=lambda link: link.position):
        if link.is_new:
            stream.offers.remove(link)
            continue
        link.position = position
        position += 1
        link.state = link.published_state = OfferState.REMOVED
        link.share = link.published_share = 0
        link.pinned = False
    stream.offers.sort(key=lambda link: link.position)


def stream_summary(kt_stream: dict[str, Any]) -> str:
    """One-line description for streams we don't edit (filters are not parsed)."""
    filters = ", ".join(f.get("name", "?") for f in kt_stream.get("filters") or []) or "None"
    action = kt_stream.get("action_type") or "—"
    payload = kt_stream.get("action_payload")
    target = f" → {payload}" if isinstance(payload, str) and payload else ""
    return f"filter by {filters}; schema {kt_stream.get('schema') or '—'}, action {action}{target}"


def _offer_names(kt: KeitaroClient, kt_streams: list[dict[str, Any]]) -> dict[int, str]:
    if not any(s.get("offers") for s in kt_streams):
        return {}
    return {o["id"]: o.get("name") or f"Offer #{o['id']}" for o in kt.list_offers()}


def upsert_offer(db: Session, offer_id: int, name: str | None, state: str = "active") -> Offer:
    offer = db.get(Offer, offer_id)
    if offer is None:
        offer = Offer(id=offer_id, name=name or f"Offer #{offer_id}", state=state)
        db.add(offer)
        db.flush()
    elif name and offer.name != name:
        offer.name = name
    return offer


