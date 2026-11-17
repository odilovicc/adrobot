# Notes for AI agents

- Run `make test` after every change; all tests run against the in-memory mock, no network.
- Business logic lives in `app/services/*`; routers only parse input and render.
- `app/services/weights.py` is pure — keep it free of DB/HTTP; every rule change needs a test there.
- The mock (`mock_keitaro/app.py`) must mirror verified real Keitaro behaviour (see `scripts/probe_keitaro.py`
  and README). Change it only together with evidence from the real API.
- Never hardcode the Keitaro URL or API key; never print the key. Config comes from `.env` via `app/config.py`.
- Schema changes: edit `app/models.py`, then `uv run alembic revision --autogenerate -m "..."`;
  `tests/test_migrations.py` fails if models and migrations diverge.
- UI: no `alert()`/`confirm()` — use toasts and Bootstrap modals (`app/static/app.js`).
