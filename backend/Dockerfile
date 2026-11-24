FROM python:3.12.16-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_NO_MANAGED_PYTHON=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
RUN useradd --system --uid 10001 inboxpilot && mkdir data && chown inboxpilot data
COPY alembic.ini ./
COPY migrations migrations
COPY app app
USER inboxpilot
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
