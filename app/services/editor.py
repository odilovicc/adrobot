"""Draft editing of offers inside a stream and publishing it to Keitaro."""

from sqlalchemy.orm import Session

from app.errors import ConflictError, DomainError, NotFoundError
from app.keitaro import KeitaroClient
from app.models import OfferState, Stream, StreamOffer
from app.services.sync import apply_kt_stream, upsert_offer
from app.services.sync import rebalance as _rebalance
from app.services.weights import WeightsError


KT_DISABLED = "Оффер выключен в Keitaro и не получает трафик — включите его в трекере и сделайте Fetch"


def get_stream(db: Session, stream_id: int) -> Stream:
    stream = db.get(Stream, stream_id)
    if stream is None:
        raise NotFoundError("Поток не найден — сделайте Fetch")
    return stream


def get_link(db: Session, link_id: int) -> StreamOffer:
    link = db.get(StreamOffer, link_id)
    if link is None:
        raise NotFoundError("Оффер в потоке не найден — обновите страницу")
    return link


def rebalance(stream: Stream) -> None:
    try:
        _rebalance(stream)
    except WeightsError as exc:
        raise DomainError(str(exc)) from exc


def _commit(db: Session, stream: Stream) -> Stream:
    """Rebalance and commit, or roll back everything if the result is invalid."""
    try:
        db.flush()
        rebalance(stream)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(stream)
    return stream


def add_offer(db: Session, kt: KeitaroClient, stream_id: int, offer_id: int) -> Stream:
    stream = get_stream(db, stream_id)
    if not stream.accepts_offers:
        raise DomainError("В этот поток нельзя добавить оффер: у него схема «%s», а не landings" % stream.schema)

    existing = next((link for link in stream.offers if link.offer_id == offer_id), None)
    if existing is not None:
        if existing.state == OfferState.ACTIVE:
            raise DomainError("Этот оффер уже в потоке")
        return restore_offer(db, existing.id)

    kt_offer = kt.get_offer(offer_id)
    if kt_offer is None:
        raise DomainError(f"Оффер #{offer_id} не найден в Keitaro")
    upsert_offer(db, offer_id, kt_offer.get("name"), kt_offer.get("state") or "active",
                 preview=kt.offer_preview_url(kt_offer))

    position = max((link.position for link in stream.offers), default=-1) + 1
    stream.offers.append(
        StreamOffer(offer_id=offer_id, position=position, state=OfferState.ACTIVE, is_new=True,
                    published_state=OfferState.REMOVED, published_share=0)  # fmt: skip
    )
    return _commit(db, stream)


def remove_offer(db: Session, link_id: int) -> Stream:
    link = get_link(db, link_id)
    stream = link.stream
    if link.is_new:
        stream.offers.remove(link)
    else:
        link.state = OfferState.REMOVED
        link.share = 0
        link.pinned = False
    return _commit(db, stream)


def restore_offer(db: Session, link_id: int) -> Stream:
    link = get_link(db, link_id)
    if link.state == OfferState.ACTIVE:
        raise DomainError("Оффер и так активен")
    link.state = OfferState.ACTIVE
    link.pinned = False
    return _commit(db, link.stream)


def set_pinned(db: Session, link_id: int, pinned: bool) -> Stream:
    link = get_link(db, link_id)
    if link.state != OfferState.ACTIVE:
        raise DomainError("Нельзя закрепить вес удалённого оффера")
    if link.kt_disabled:
        raise DomainError(KT_DISABLED)
    link.pinned = pinned
    return _commit(db, link.stream)


def set_share(db: Session, link_id: int, share: int) -> Stream:
    link = get_link(db, link_id)
    if link.state != OfferState.ACTIVE:
        raise DomainError("Нельзя задать вес удалённому офферу")
    if link.kt_disabled:
        raise DomainError(KT_DISABLED)
    if not 0 <= share <= 100:
        raise DomainError("Вес должен быть целым числом от 0 до 100")
    link.share = share
    link.pinned = True
    return _commit(db, link.stream)


def cancel(db: Session, stream_id: int) -> Stream:
    stream = get_stream(db, stream_id)
    for link in list(stream.offers):
        if link.is_new:
            stream.offers.remove(link)
        else:
            if link.pinned and link.share != link.published_share:
                link.pinned = False  # the pin belonged to the discarded draft value
            link.state = link.published_state
            link.share = link.published_share
            if link.state != OfferState.ACTIVE:
                link.pinned = False
    db.commit()
    db.refresh(stream)
    return stream


def push(db: Session, kt: KeitaroClient, stream_id: int) -> Stream:
    stream = get_stream(db, stream_id)
    if error := stream.publish_error:
        raise DomainError(error)
    active = sorted(stream.active_offers, key=lambda link: link.position)

    # Don't overwrite changes made in Keitaro after our last Fetch.
    current = kt.get_stream(stream.keitaro_id)
    in_kt = {(int(o["offer_id"]), int(o.get("share") or 0)) for o in current.get("offers") or []}
    published = {
        (link.offer_id, link.published_share)
        for link in stream.offers
        if link.published_state == OfferState.ACTIVE and not link.is_new
    }
    if in_kt != published:
        raise ConflictError("Поток изменён в Keitaro после последнего Fetch. Ничего не отправлено. "
            "Нажмите Fetch, чтобы загрузить свежее состояние (ваш черновик в этом потоке будет сброшен), и повторите правки")

    payload = {"offers": [{"offer_id": link.offer_id, "share": link.share, "state": link.kt_state}
                          for link in active]}  # fmt: skip
    kt.update_stream(stream.keitaro_id, payload)
    fresh = kt.get_stream(stream.keitaro_id)
    names = {link.offer_id: link.offer.name for link in stream.offers}
    apply_kt_stream(db, stream, fresh, names)
    db.commit()
    db.refresh(stream)
    return stream
