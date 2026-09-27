# Contributing to TokenRouter

Thank you for considering a contribution!

## Development Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -e ".[dev]"
```

Create local config:

```bash
cp .env.example .env
cp config.example.yaml config.yaml
# edit .env and config.yaml
```

## Running

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload
```

Health check:

```bash
curl http://127.0.0.1:8787/health
```

## Tests & Lint

```bash
pytest
ruff check .
ruff format --check .
```

Auto-fix and format:

```bash
ruff check --fix .
ruff format .
```

Each test gets its own temporary SQLite accounting database
(`TOKENROUTER_DB_PATH`), so local usage accounting never leaks between tests and
no real `data/tokenrouter.db` is touched. Tests mock Clean APIs; never use real
API keys.

## Project Principles

- Clean APIs is the only upstream provider. Do not add other providers.
- Do not scrape the Clean APIs dashboard or bypass limits/billing/ToS.
- Use legitimate API keys via environment variables.
- Never log or expose API keys; mask them in diagnostics.
- Never silently substitute a model when the client explicitly requests one.
- Keep the app local-first and single-user; do not add Redis/Postgres/React/Docker orchestration without discussion.
- Keep `app/upstream/` isolated so other providers could be added later — but do not implement them now.

## Milestones

Work milestone-by-milestone. Do not proceed if the current milestone's tests or lint fail.
See `docs/architecture.md` for the milestone plan.

## Pull Requests

- Keep functions small and typed.
- Prefer dependency injection.
- Add tests for new behavior (mocked upstream, no real API keys).
- Update `docs/` when behavior changes.
