from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import Settings, get_settings
from app.keitaro import KeitaroClient

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@lru_cache
def _client() -> KeitaroClient:
    return KeitaroClient.from_settings(get_settings())


def get_kt() -> KeitaroClient:
    return _client()


def settings_dep() -> Settings:
    return get_settings()
