"""Campaign creator: name + geo + offer → ready-to-run campaign with two streams."""

import gettext
import re
import secrets
import string
from dataclasses import dataclass, field
from functools import cache

import pycountry
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import DomainError
from app.keitaro import KeitaroClient, KeitaroError
from app.models import Campaign
from app.services.sync import import_campaign

ALIAS_ALPHABET = string.ascii_letters + string.digits
ALIAS_ATTEMPTS = 3
ISO_ALPHA2 = frozenset(c.alpha_2 for c in pycountry.countries)


@dataclass(frozen=True)
class Country:
    code: str
    name: str  # Russian, shown to the user
    name_en: str  # searchable too


@cache
def country_choices() -> tuple[Country, ...]:
    """All ISO 3166-1 countries with Russian names, sorted by name (for the creator's country picker)."""
    ru = gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["ru"], fallback=True)
    items = []
    for c in pycountry.countries:
        en = getattr(c, "common_name", None) or c.name
        items.append(Country(c.alpha_2, ru.gettext(en), en))
    return tuple(sorted(items, key=lambda c: c.name.replace("Ё", "Е").casefold()))


class ValidationErrors(DomainError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(errors.values()))
        self.errors = errors


@dataclass
class CampaignForm:
    name: str = ""
    geo: str = ""
    offer_id: int | None = None
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None
    countries: list[str] = field(default_factory=list)


def generate_alias(length: int = 7) -> str:
    return "".join(secrets.choice(ALIAS_ALPHABET) for _ in range(length))


def parse_geo(raw: str) -> list[str]:
    """``"mx, AU;ro"`` → ``["MX", "AU", "RO"]``; raises DomainError on unknown codes."""
    codes: list[str] = []
    bad: list[str] = []
    for token in re.split(r"[\s,;]+", raw.strip()):
        if not token:
            continue
        code = token.upper()
        if code not in ISO_ALPHA2:
            bad.append(token)
        elif code not in codes:
            codes.append(code)
    if bad:
        raise DomainError(f"Неизвестные коды стран: {', '.join(bad)} — нужен ISO 3166-1 alpha-2, например MX,AU,RO")
    if not codes:
        raise DomainError("Укажите хотя бы одну страну, например MX,AU,RO")
    return codes


def validate(form: CampaignForm, kt: KeitaroClient) -> CampaignForm:
    """Check everything before any write to Keitaro."""
    errors: dict[str, str] = {}
    form.name = form.name.strip()
    if not form.name:
        errors["name"] = "Название кампании не может быть пустым"
    elif len(form.name) > 255:
        errors["name"] = "Название длиннее 255 символов"

    try:
        form.countries = parse_geo(form.geo)
    except DomainError as exc:
        errors["geo"] = exc.message

    if form.offer_id is None:
        errors["offer_id"] = "Выберите оффер"
    elif kt.get_offer(form.offer_id) is None:
        errors["offer_id"] = f"Оффер #{form.offer_id} не найден в Keitaro"

    choices = {
        "domain_id": ("домен", kt.list_domains),
        "group_id": ("группу", kt.list_campaign_groups),
        "traffic_source_id": ("источник", kt.list_traffic_sources),
    }
    for attr, (label, loader) in choices.items():
        value = getattr(form, attr)
        if value is None:
            errors[attr] = f"Выберите {label}"
        elif value not in {item["id"] for item in loader()}:
            errors[attr] = f"Выбранный {label} не найден в Keitaro"

    if errors:
        raise ValidationErrors(errors)
    return form


def create_campaign(db: Session, kt: KeitaroClient, settings: Settings, form: CampaignForm) -> Campaign:
    form = validate(form, kt)
    kt_campaign = _create_with_unique_alias(kt, form)
    cid = kt_campaign["id"]
    try:
        kt.create_stream({
            "campaign_id": cid, "type": "regular", "name": f"Geo {','.join(form.countries)} → redirect",
            "position": 1, "state": "active", "schema": "redirect", "action_type": "http",
            "action_payload": settings.geo_redirect_url,
            "filters": [{"name": "country", "mode": "accept", "payload": form.countries}],
        })  # fmt: skip
        kt.create_stream({
            "campaign_id": cid, "type": "regular", "name": "Offers", "position": 2, "state": "active",
            "schema": "landings", "action_type": "http", "filters": [],
            "offers": [{"offer_id": form.offer_id, "share": 100, "state": "active"}],
        })  # fmt: skip
    except KeitaroError as exc:
        _rollback(kt, cid, exc)
    return import_campaign(db, kt, cid)


def _create_with_unique_alias(kt: KeitaroClient, form: CampaignForm) -> dict:
    payload = {
        "name": form.name, "type": "position", "state": "active",
        "domain_id": form.domain_id, "group_id": str(form.group_id), "traffic_source_id": form.traffic_source_id,
        "cost_type": "CPC", "cost_value": 0, "cost_auto": True,
    }  # fmt: skip
    for attempt in range(ALIAS_ATTEMPTS):
        try:
            return kt.create_campaign({**payload, "alias": generate_alias()})
        except KeitaroError as exc:
            if "alias" not in exc.message.lower() or attempt == ALIAS_ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")


def _rollback(kt: KeitaroClient, campaign_id: int, cause: KeitaroError) -> None:
    try:
        kt.delete_campaign(campaign_id)
    except KeitaroError as exc:
        raise KeitaroError(
            f"Не удалось создать поток ({cause.message}) и не удалось удалить кампанию #{campaign_id}: "
            f"{exc.message}. Удалите её вручную в Keitaro.",
            cause.status_code,
        ) from cause
    raise KeitaroError(f"Не удалось создать поток, кампания откатана: {cause.message}", cause.status_code) from cause
