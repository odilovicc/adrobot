from alembic import command
from alembic.config import Config


def test_migrations_match_models(tmp_path, monkeypatch):
    """`alembic upgrade head` builds exactly the schema the models describe."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'm.db'}")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        command.check(cfg)  # raises if autogenerate would produce changes
    finally:
        get_settings.cache_clear()
