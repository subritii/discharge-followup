# Discharge follow-up pipeline. Default command = demo mode: saved AI drafts, Salesforce
# dry run, no secrets needed. Full mode passes .env at runtime (docker-compose.yml);
# no secret is ever copied into the image (see .dockerignore).
FROM python:3.11.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first (cached layer); includes the spaCy model used by Presidio.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .

RUN useradd --create-home --uid 1000 app && mkdir -p data && chown app:app data
USER app

CMD ["python", "run.py", "--dry-run", "--offline", "--drafts", "demo/drafts"]
