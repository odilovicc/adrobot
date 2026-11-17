.PHONY: install migrate dev app mock test probe probe-write e2e-real

install:
	uv sync

migrate:
	uv run alembic upgrade head

app: migrate
	uv run uvicorn app.main:app --reload --port 8000

mock:
	uv run uvicorn mock_keitaro.app:app --reload --port 8100

# App + mock Keitaro together; Ctrl+C stops both.
dev: install migrate
	@trap 'kill 0' INT TERM; \
	uv run uvicorn mock_keitaro.app:app --port 8100 & \
	uv run uvicorn app.main:app --reload --port 8000 & \
	wait

test:
	uv run pytest -q

probe:
	uv run python scripts/probe_keitaro.py

probe-write:
	uv run python scripts/probe_keitaro.py --write

# Video scenario on the real tracker: temporary adrobot-probe-e2e-* campaign, deleted at the end.
e2e-real:
	uv run python scripts/e2e_real.py
