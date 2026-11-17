from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keitaro_url: str = "http://127.0.0.1:8100"
    keitaro_api_key: str = "mock-key"
    keitaro_timeout: float = 15.0
    database_url: str = "sqlite:///./adrobot.db"

    default_domain_id: int | None = None
    default_group_id: int | None = None
    default_traffic_source_id: int | None = None

    geo_redirect_url: str = "https://google.com"
    offers_cache_ttl: float = 60.0

    @property
    def keitaro_api_base(self) -> str:
        return self.keitaro_url.rstrip("/") + "/admin_api/v1"

    @property
    def keitaro_admin_url(self) -> str:
        return self.keitaro_url.rstrip("/") + "/admin/"


@lru_cache
def get_settings() -> Settings:
    return Settings()
