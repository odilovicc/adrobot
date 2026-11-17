from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.deps import get_kt, settings_dep, templates
from app.errors import DomainError
from app.keitaro import KeitaroClient, KeitaroError
from app.models import Campaign
from app.services import creator
from app.services.campaigns import PER_PAGE, ListQuery, build_page
from app.services.sync import import_campaign

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Kt = Annotated[KeitaroClient, Depends(get_kt)]
Conf = Annotated[Settings, Depends(settings_dep)]


@router.get("/", response_class=HTMLResponse)
def index(request: Request, kt: Kt, db: Db) -> Response:
    query = ListQuery.parse(request.query_params)
    error, campaigns = None, []
    try:
        campaigns = kt.list_campaigns(force=request.query_params.get("refresh") == "1")
    except KeitaroError as exc:
        error = exc.message
    local = db.scalars(select(Campaign)).all()
    opened = {c.keitaro_id for c in local}
    dirty = {c.keitaro_id: sum(s.is_dirty for s in c.streams) for c in local}
    page = build_page(campaigns, query, opened, dirty)
    ctx = {"page": page, "q": page.query, "error": error, "per_page": PER_PAGE}
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return templates.TemplateResponse(request, "_campaign_table.html", ctx)
    return templates.TemplateResponse(request, "index.html", ctx)


def _creator_choices(kt: KeitaroClient) -> dict[str, list[dict[str, Any]]]:
    return {
        "domains": kt.list_domains(),
        "groups": kt.list_campaign_groups(),
        "sources": kt.list_traffic_sources(),
    }


def _render_form(request: Request, kt: KeitaroClient, form: dict[str, Any], errors: dict[str, str],
                 status_code: int = 200) -> Response:  # fmt: skip
    ctx: dict[str, Any] = {"form": form, "errors": errors, "domains": [], "groups": [], "sources": []}
    try:
        ctx.update(_creator_choices(kt))
        if form.get("offer_id"):
            offer = kt.get_offer(int(form["offer_id"]))
            ctx["offer"] = offer
    except KeitaroError as exc:
        errors.setdefault("__all__", exc.message)
    return templates.TemplateResponse(request, "campaign_new.html", ctx, status_code=status_code)


@router.get("/campaigns/new", response_class=HTMLResponse)
def new_campaign(request: Request, kt: Kt, settings: Conf) -> Response:
    form = {
        "name": "", "geo": "", "offer_id": None,
        "domain_id": settings.default_domain_id, "group_id": settings.default_group_id,
        "traffic_source_id": settings.default_traffic_source_id,
    }  # fmt: skip
    return _render_form(request, kt, form, {})


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


@router.post("/campaigns/new", response_class=HTMLResponse)
def create_campaign(
    request: Request, kt: Kt, db: Db, settings: Conf,
    name: Annotated[str, Form()] = "", geo: Annotated[str, Form()] = "",
    offer_id: Annotated[str | None, Form()] = None, domain_id: Annotated[str | None, Form()] = None,
    group_id: Annotated[str | None, Form()] = None, traffic_source_id: Annotated[str | None, Form()] = None,
) -> Response:  # fmt: skip
    raw = {"name": name, "geo": geo, "offer_id": _int(offer_id), "domain_id": _int(domain_id),
           "group_id": _int(group_id), "traffic_source_id": _int(traffic_source_id)}  # fmt: skip
    try:
        campaign = creator.create_campaign(db, kt, settings, creator.CampaignForm(**raw))
    except creator.ValidationErrors as exc:
        return _render_form(request, kt, raw, dict(exc.errors), status_code=400)
    except DomainError as exc:
        return _render_form(request, kt, raw, {"__all__": exc.message}, status_code=400)
    except KeitaroError as exc:
        return _render_form(request, kt, raw, {"__all__": exc.message}, status_code=502)
    return RedirectResponse(f"/campaigns/{campaign.keitaro_id}/streams?created=1", status_code=303)


@router.get("/campaigns/{keitaro_id}/streams", response_class=HTMLResponse)
def streams_page(request: Request, keitaro_id: int, kt: Kt, db: Db, settings: Conf) -> Response:
    campaign = db.scalar(select(Campaign).where(Campaign.keitaro_id == keitaro_id))
    if campaign is None:
        try:
            campaign = import_campaign(db, kt, keitaro_id)
        except KeitaroError as exc:
            return templates.TemplateResponse(
                request, "error.html", {"error": exc.message}, status_code=502
            )
    return templates.TemplateResponse(
        request,
        "streams.html",
        {
            "campaign": campaign,
            "kt_url": f"{settings.keitaro_admin_url}#!/campaigns/{campaign.keitaro_id}",
            "created": request.query_params.get("created") == "1",
        },
    )
