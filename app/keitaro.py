"""Thin httpx client for the Keitaro Admin API."""

import threading
import time
from typing import Any

import httpx

from app.config import Settings


class KeitaroError(Exception):
    """Keitaro answered with an error or could not be reached."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class KeitaroClient:
    def __init__(self, http: httpx.Client, offers_ttl: float = 60.0) -> None:
        self.http = http
        self.offers_ttl = offers_ttl
        self._offers: list[dict[str, Any]] | None = None
        self._offers_at = 0.0
        # Keitaro has no campaign search: the list page filters the full list, typing re-requests it.
        self.campaigns_ttl = 15.0
        self._campaigns: list[dict[str, Any]] | None = None
        self._campaigns_at = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "KeitaroClient":
        http = httpx.Client(
            base_url=settings.keitaro_api_base,
            headers={"Api-Key": settings.keitaro_api_key, "Accept": "application/json"},
            timeout=settings.keitaro_timeout,
        )
        return cls(http, offers_ttl=settings.offers_cache_ttl)

    # --- transport --------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self.http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise KeitaroError(f"Keitaro не ответил вовремя ({self.http.base_url.host}). Попробуйте ещё раз") from exc
        except httpx.HTTPError as exc:
            raise KeitaroError(f"Keitaro недоступен: не удалось подключиться к {self.http.base_url.host}") from exc
        if resp.status_code >= 400:
            raise KeitaroError(_error_text(resp), resp.status_code)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise KeitaroError(f"Keitaro вернул не JSON ({resp.status_code})", resp.status_code) from exc

    # --- campaigns --------------------------------------------------------
    def list_campaigns(self, force: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            fresh = self._campaigns is not None and time.monotonic() - self._campaigns_at < self.campaigns_ttl
            if force or not fresh:
                self._campaigns = self._request("GET", "/campaigns")
                self._campaigns_at = time.monotonic()
            return list(self._campaigns or [])

    def get_campaign(self, campaign_id: int) -> dict[str, Any]:
        return self._request("GET", f"/campaigns/{campaign_id}")

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._campaigns = None
        return self._request("POST", "/campaigns", json=payload)

    def delete_campaign(self, campaign_id: int) -> None:
        self._campaigns = None
        self._request("DELETE", f"/campaigns/{campaign_id}")

    # --- streams ----------------------------------------------------------
    def campaign_streams(self, campaign_id: int) -> list[dict[str, Any]]:
        streams = self._request("GET", f"/campaigns/{campaign_id}/streams")
        return [s for s in streams if s.get("state") != "deleted"]

    def get_stream(self, stream_id: int) -> dict[str, Any]:
        return self._request("GET", f"/streams/{stream_id}")

    def create_stream(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/streams", json=payload)

    def update_stream(self, stream_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", f"/streams/{stream_id}", json=payload)

    # --- dictionaries -----------------------------------------------------
    def list_offers(self, force: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            fresh = self._offers is not None and time.monotonic() - self._offers_at < self.offers_ttl
            if force or not fresh:
                self._offers = self._request("GET", "/offers")
                self._offers_at = time.monotonic()
            return self._offers or []

    def offer_preview_url(self, offer: dict[str, Any]) -> str:
        """Where "(preview)" points: the offer's own URL, or its local lander on the tracker."""
        for key in ("preview_path", "action_payload"):
            value = offer.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        local = offer.get("local_path")
        if isinstance(local, str) and local:
            base = str(self.http.base_url).split("/admin_api", 1)[0].rstrip("/")
            return f"{base}{local if local.startswith('/') else '/' + local}"
        return ""

    def stream_clicks_today(self, campaign_id: int) -> dict[int, int]:
        """Clicks per stream since midnight UTC (Keitaro report API)."""
        body = {"range": {"interval": "today", "timezone": "UTC"}, "dimensions": ["stream_id"],
                "measures": ["clicks"],
                "filters": [{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}]}  # fmt: skip
        rows = self._request("POST", "/report/build", json=body).get("rows") or []
        return {int(r["stream_id"]): int(r.get("clicks") or 0) for r in rows if r.get("stream_id")}

    def invalidate_offers(self) -> None:
        with self._lock:
            self._offers = None

    def search_offers(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        q = query.strip().lower().lstrip("#")
        offers = [o for o in self.list_offers() if o.get("state", "active") != "deleted"]
        if q:
            offers = [o for o in offers if q in str(o["id"]) or q in (o.get("name") or "").lower()]
            # Exact id match first, then by name.
            offers.sort(key=lambda o: (str(o["id"]) != q, (o.get("name") or "").lower()))
        return offers[:limit]

    def get_offer(self, offer_id: int) -> dict[str, Any] | None:
        for force in (False, True):
            for offer in self.list_offers(force=force):
                if offer["id"] == offer_id:
                    return offer
        return None

    def list_domains(self) -> list[dict[str, Any]]:
        return [d for d in self._request("GET", "/domains") if d.get("state", "active") != "deleted"]

    def list_campaign_groups(self) -> list[dict[str, Any]]:
        return self._request("GET", "/groups", params={"type": "campaigns"})

    def list_traffic_sources(self) -> list[dict[str, Any]]:
        return [s for s in self._request("GET", "/traffic_sources") if s.get("state", "active") != "deleted"]


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        for key in ("error", "message", "errors"):
            if data.get(key):
                value = data[key]
                return f"Keitaro {resp.status_code}: {value if isinstance(value, str) else value!r}"
    text = resp.text.strip()[:300] or resp.reason_phrase
    return f"Keitaro {resp.status_code}: {text}"
