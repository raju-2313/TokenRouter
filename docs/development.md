# Development

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload
curl http://127.0.0.1:8787/health
```

## Checks

```bash
pytest
ruff check .
ruff format --check .
```

Fix:

```bash
ruff check --fix .
ruff format .
```

## Conventions

- Small, typed functions.
- Dependency injection where useful.
- No unnecessary abstractions.
- Tests mock Clean APIs; never require real keys.
- Keep the layering intact: routes → service → routing engine → budget manager →
  upstream client. The routing engine does no HTTP and no SQL.
- Never log or store API keys; use `app/core/obs.py` for structured metadata.
