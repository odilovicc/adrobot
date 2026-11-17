from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import Settings, get_settings
from app.keitaro import KeitaroClient

def tracker_info(settings: Settings) -> dict[str, object]:
    """Which Keitaro the app talks to — shown in the navbar so nobody pushes to the wrong tracker."""
    url = urlsplit(settings.keitaro_url)
    is_mock = settings.keitaro_api_key == "mock-key" or url.port == 8100 or url.hostname == "mock"
    return {"host": url.netloc or settings.keitaro_url, "is_mock": is_mock, "admin_url": settings.keitaro_admin_url}


def _tracker_context(request: Request) -> dict[str, object]:
    # Resolve settings the same way routes do (honours dependency overrides in tests).
    provider = request.app.dependency_overrides.get(settings_dep, settings_dep)
    return {"tracker": tracker_info(provider()), "asset_version": _asset_version()}


STATIC = Path(__file__).parent / "static"


def _asset_version() -> str:
    """Cache-buster for app.css/app.js: changes whenever either file changes."""
    return str(max(int(f.stat().st_mtime) for f in (STATIC / "app.css", STATIC / "app.js")))


templates = Jinja2Templates(directory=Path(__file__).parent / "templates", context_processors=[_tracker_context])


@lru_cache
def _client() -> KeitaroClient:
    return KeitaroClient.from_settings(get_settings())


def get_kt() -> KeitaroClient:
    return _client()


def settings_dep() -> Settings:
    return get_settings()
