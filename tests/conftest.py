import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base, get_db, make_engine
from app.deps import get_kt, settings_dep
from app.keitaro import KeitaroClient
from app.main import create_app
from mock_keitaro import app as mock


@pytest.fixture
def kt_state():
    mock.state.reset()
    return mock.state


@pytest.fixture
def kt(kt_state) -> KeitaroClient:
    http = TestClient(mock.app, base_url="http://kt/admin_api/v1", headers={"Api-Key": mock.API_KEY})
    return KeitaroClient(http, offers_ttl=0)


@pytest.fixture
def db():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, keitaro_url="http://kt", database_url="sqlite://",
                    default_domain_id=1, default_group_id=1, default_traffic_source_id=1)  # fmt: skip


@pytest.fixture
def client(db, kt, settings):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_kt] = lambda: kt
    app.dependency_overrides[settings_dep] = lambda: settings
    with TestClient(app) as c:
        yield c
