# Local dev against the compose Postgres
export DATABASE_URL := env("DATABASE_URL", "postgresql://bsdb:bsdb@127.0.0.1:5442/bsdb")

up:
    docker compose -p bugsigdb-dev up -d --build

load:
    docker compose -p bugsigdb-dev run --rm app /app/.venv/bin/python -m bugsigdb_app.load

dev:
    uv run uvicorn bugsigdb_app.app:app --reload --port 8043

test:
    uv run pytest -q
