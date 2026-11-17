from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OfferState(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(255))
    alias: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(default=None)

    streams: Mapped[list["Stream"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="Stream.position"
    )


class Offer(Base):
    """Cache of Keitaro offers; ``id`` is the Keitaro offer id."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(32), default="active")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(unique=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(default=0)
    type: Mapped[str] = mapped_column(String(32))
    schema: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(String(1000), default="")

    campaign: Mapped[Campaign] = relationship(back_populates="streams")
    offers: Mapped[list["StreamOffer"]] = relationship(
        back_populates="stream", cascade="all, delete-orphan", order_by="StreamOffer.position"
    )

    @property
    def accepts_offers(self) -> bool:
        return self.schema == "landings" or bool(self.offers)

    @property
    def active_offers(self) -> list["StreamOffer"]:
        return [o for o in self.offers if o.state == OfferState.ACTIVE]

    @property
    def weighted_offers(self) -> list["StreamOffer"]:
        """Offers that actually take traffic: active here and not disabled in Keitaro."""
        return [o for o in self.offers if o.is_weighted]

    @property
    def total_share(self) -> int:
        return sum(o.share for o in self.weighted_offers)

    @property
    def publish_error(self) -> str | None:
        """Why the current draft can't be pushed, or None."""
        if not self.offers:
            return None
        if not self.weighted_offers:
            return "В потоке не осталось активных офферов — Keitaro получит пустой поток. Верните хотя бы один оффер"
        if self.total_share != 100:
            return f"Сумма весов {self.total_share}% — опубликовать можно только ровно 100%"
        return None

    @property
    def is_dirty(self) -> bool:
        return any(o.is_dirty for o in self.offers)


class StreamOffer(Base):
    """Link stream ↔ offer: the working draft plus the last published state."""

    __tablename__ = "stream_offers"
    __table_args__ = (UniqueConstraint("stream_id", "offer_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"))
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    position: Mapped[int] = mapped_column(default=0)

    state: Mapped[OfferState] = mapped_column(String(16), default=OfferState.ACTIVE)
    share: Mapped[int] = mapped_column(default=0)
    pinned: Mapped[bool] = mapped_column(default=False)
    is_new: Mapped[bool] = mapped_column(default=False)

    published_state: Mapped[OfferState] = mapped_column(String(16), default=OfferState.REMOVED)
    published_share: Mapped[int] = mapped_column(default=0)
    # active/disabled as stored in Keitaro; sent back untouched on Push.
    kt_state: Mapped[str] = mapped_column(String(16), default="active")

    stream: Mapped[Stream] = relationship(back_populates="offers")
    offer: Mapped[Offer] = relationship(lazy="joined")

    @property
    def kt_disabled(self) -> bool:
        return self.kt_state == "disabled"

    @property
    def is_weighted(self) -> bool:
        return self.state == OfferState.ACTIVE and not self.kt_disabled

    @property
    def is_dirty(self) -> bool:
        return self.is_new or self.state != self.published_state or self.share != self.published_share
