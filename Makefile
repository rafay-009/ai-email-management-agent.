.PHONY: setup migrate backend frontend check browser compose
setup:
	cd backend && uv sync --locked --python 3.12
	cd frontend && npm ci
migrate:
	cd backend && uv run --locked alembic upgrade head
backend:
	cd backend && uv run --locked uvicorn app.main:app --no-access-log
frontend:
	cd frontend && npm run dev
check:
	cd backend && uv run --locked ruff check . && uv run --locked ruff format --check .
	cd backend && uv run --locked python -m pytest -q
	cd frontend && npm run lint && npm run typecheck && npm run build
browser:
	cd frontend && npm test
compose:
	docker compose up --build
