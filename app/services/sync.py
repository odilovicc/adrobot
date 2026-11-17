"""Fetch: Keitaro is the source of truth, our DB keeps drafts, pins and the archive."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.keitaro import KeitaroClient
from app.models import Campaign, Offer, OfferState, Stream, StreamOffer
from app.services.weights import Slot, WeightsError, distribute


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
    old_order = sorted(stream.offers, key=lambda link: link.position)
    links = {link.offer_id: link for link in stream.offers}
    in_kt: list[StreamOffer] = []
    for kt_offer in kt_offers:
        oid = int(kt_offer["offer_id"])
        upsert_offer(db, oid, names.get(oid))
        link = links.pop(oid, None)
        if link is None:
            link = StreamOffer(offer_id=oid, pinned=False)
            stream.offers.append(link)
        kt_share = int(kt_offer.get("share") or 0)
        # A pin is our draft concept: keep it only if Keitaro still has the pinned value,
        # otherwise the pinned number would silently change under the user.
        if link.pinned and link.share != kt_share:
            link.pinned = False
        link.state = link.published_state = OfferState.ACTIVE
        link.share = link.published_share = kt_share
        link.kt_state = kt_offer.get("state") or "active"
        if link.kt_disabled:
            link.pinned = False
        link.is_new = False
        in_kt.append(link)

    # Known to us but gone from Keitaro: drafts are dropped, published ones go to the archive.
    archived: list[StreamOffer] = []
    for link in links.values():
        if link.is_new:
            stream.offers.remove(link)
            continue
        link.state = link.published_state = OfferState.REMOVED
        link.share = link.published_share = 0
        link.pinned = False
        archived.append(link)

    for i, link in enumerate(_merge_order(in_kt, archived, old_order)):
        link.position = i
    stream.offers.sort(key=lambda link: link.position)

    # E.g. offers deleted right in Keitaro left 66%: offer the fixed split as a draft to Push.
    if stream.weighted_offers and stream.total_share != 100:
        try:
            rebalance(stream)
        except WeightsError:
            pass  # pinned sum > 100 can't happen after the pin check above; keep Keitaro's numbers


def _merge_order(in_kt: list[StreamOffer], archived: list[StreamOffer], old: list[StreamOffer]) -> list[StreamOffer]:
    """Our order for offers we already know, then offers new to us in Keitaro order;
    archived ones stay right after their old predecessor.

    Verified on a real tracker: Keitaro doesn't keep the order of ``offers`` sent in PUT,
    so its order can't be the source of truth for display or for the +1 remainder.
    """
    known = {id(link): i for i, link in enumerate(old)}
    result = sorted(in_kt, key=lambda link: known.get(id(link), len(old)))  # stable: new ones keep KT order
    archived_ids = {id(link) for link in archived}
    for idx, link in enumerate(old):
        if id(link) not in archived_ids:
            continue
        pos = 0
        for prev in reversed(old[:idx]):
            if prev in result:
                pos = result.index(prev) + 1
                break
        result.insert(pos, link)
    return result


def rebalance(stream: Stream) -> None:
    """Recompute draft shares. Raises WeightsError without touching shares on bad config.

    Offers disabled in Keitaro don't take traffic: they keep their share and stay out of the 100%.
    """
    links = [link for link in sorted(stream.offers, key=lambda link: link.position) if not link.kt_disabled]
    slots = [
        Slot(link.id or id(link), active=link.state == OfferState.ACTIVE,
             pinned_share=link.share if link.pinned else None,
             current=link.share)  # fmt: skip
        for link in links
    ]
    shares = distribute(slots)
    for link, slot in zip(links, slots, strict=True):
        link.share = shares[slot.key]


def stream_summary(kt_stream: dict[str, Any]) -> str:
    """One-line description for streams we don't edit, e.g. ``filter by country = AU, MX``."""
    filters = "; ".join(_filter_text(f) for f in kt_stream.get("filters") or []) or "None"
    action = kt_stream.get("action_type") or "—"
    payload = kt_stream.get("action_payload")
    target = f" → {payload}" if isinstance(payload, str) and payload else ""
    return f"filter by {filters}; schema {kt_stream.get('schema') or '—'}, action {action}{target}"


def _filter_text(f: dict[str, Any]) -> str:
    name = f.get("name") or "?"
    payload = f.get("payload")
    if isinstance(payload, list):
        values = ", ".join(str(v) for v in payload)
    elif payload in (None, "", {}):
        values = ""
    else:
        values = str(payload)
    op = " ≠ " if f.get("mode") == "reject" else " = "
    return f"{name}{op}{values}" if values else name


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


