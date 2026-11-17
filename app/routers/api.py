"""JSON/HTML-fragment endpoints for the jQuery front-end.

Every mutation returns the re-rendered stream card; the client swaps it in.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_kt, templates
from app.errors import NotFoundError
from app.keitaro import KeitaroClient
from app.models import Campaign, Stream
from app.services import editor
from app.services.sync import import_campaign

router = APIRouter(prefix="/api")

Db = Annotated[Session, Depends(get_db)]
Kt = Annotated[KeitaroClient, Depends(get_kt)]


class AddOffer(BaseModel):
    offer_id: int


class Pin(BaseModel):
    pinned: bool


class Share(BaseModel):
    share: int


def card(request: Request, stream: Stream) -> HTMLResponse:
    return templates.TemplateResponse(request, "_stream.html", {"stream": stream})


@router.get("/offers")
def offers(kt: Kt, db: Db, q: str = "", stream_id: int | None = None) -> dict:
    """Select2 autocomplete; offers already active in ``stream_id`` are hidden."""
    exclude: set[int] = set()
    if stream_id is not None and (stream := db.get(Stream, stream_id)) is not None:
        exclude = {link.offer_id for link in stream.active_offers}
    found = [o for o in kt.search_offers(q, limit=30 + len(exclude)) if o["id"] not in exclude][:30]
    return {"results": [{"id": o["id"], "text": f"#{o['id']} {o.get('name') or ''}".strip()} for o in found]}


@router.post("/campaigns/{keitaro_id}/fetch", response_class=HTMLResponse)
def fetch(request: Request, keitaro_id: int, kt: Kt, db: Db) -> HTMLResponse:
    kt.invalidate_offers()
    campaign = import_campaign(db, kt, keitaro_id)
    return templates.TemplateResponse(request, "_streams.html", {"campaign": campaign})


@router.get("/campaigns/{keitaro_id}/streams", response_class=HTMLResponse)
def streams(request: Request, keitaro_id: int, db: Db) -> HTMLResponse:
    campaign = db.scalar(select(Campaign).where(Campaign.keitaro_id == keitaro_id))
    if campaign is None:
        raise NotFoundError("Кампания не импортирована")
    return templates.TemplateResponse(request, "_streams.html", {"campaign": campaign})


@router.post("/streams/{stream_id}/offers", response_class=HTMLResponse)
def add_offer(request: Request, stream_id: int, body: AddOffer, kt: Kt, db: Db) -> HTMLResponse:
    return card(request, editor.add_offer(db, kt, stream_id, body.offer_id))


@router.post("/streams/{stream_id}/push", response_class=HTMLResponse)
def push(request: Request, stream_id: int, kt: Kt, db: Db) -> HTMLResponse:
    return card(request, editor.push(db, kt, stream_id))


@router.post("/streams/{stream_id}/cancel", response_class=HTMLResponse)
def cancel(request: Request, stream_id: int, db: Db) -> HTMLResponse:
    return card(request, editor.cancel(db, stream_id))


@router.post("/stream-offers/{link_id}/remove", response_class=HTMLResponse)
def remove(request: Request, link_id: int, db: Db) -> HTMLResponse:
    return card(request, editor.remove_offer(db, link_id))


@router.post("/stream-offers/{link_id}/restore", response_class=HTMLResponse)
def restore(request: Request, link_id: int, db: Db) -> HTMLResponse:
    return card(request, editor.restore_offer(db, link_id))


@router.post("/stream-offers/{link_id}/pin", response_class=HTMLResponse)
def pin(request: Request, link_id: int, body: Pin, db: Db) -> HTMLResponse:
    return card(request, editor.set_pinned(db, link_id, body.pinned))


@router.post("/stream-offers/{link_id}/share", response_class=HTMLResponse)
def share(request: Request, link_id: int, body: Share, db: Db) -> HTMLResponse:
    return card(request, editor.set_share(db, link_id, body.share))
