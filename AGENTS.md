# Repository Guidelines

## Project Structure & Module Organization

`recruit_server/` contains the standalone Python 3.10+ service. Keep HTTP routing and authentication in `api.py`, Oracle transport in `oracle.py`, recruiting operations in `recruiting.py`, and attachment handling in `resumes.py`. Supporting modules handle configuration, caching, identifiers, lookups, and errors. Keep business logic outside HTTP handlers and avoid dependencies on parent projects.

`tests/` contains offline tests and shared fixtures in `support.py`. `docs/` documents the API contract, configuration, and Oracle endpoint mappings. There is no frontend or asset build pipeline.

## Build, Test, and Development Commands

- `python3 -m venv .venv && source .venv/bin/activate`: create and activate a local environment.
- `python -m pip install -e '.[test]'`: install the package and HTTP test dependencies in editable mode.
- `cp .env.example .env` and `chmod 600 .env`: prepare private local configuration; fill in the required credentials.
- `set -a; source ./.env; set +a`: explicitly export configuration into your shell.
- `python -m recruit_server`: start the single-process server at `127.0.0.1:8010`; `fusion-recruit-api` is the equivalent installed command.
- `python -m unittest discover -s tests -t . -v`: run the complete offline suite.

## Coding Style & Naming Conventions

Use four-space indentation, snake_case for modules/functions/variables, PascalCase for classes, and UPPER_CASE for constants. Follow nearby import grouping and concise docstrings. Preserve Oracle field names in response data. No formatter or linter is configured in `pyproject.toml`; keep changes consistent with existing code.

## Testing Guidelines

Use `unittest`, `unittest.mock`, and Starlette `TestClient`. Name files `test_*.py` and methods `test_<behavior>`. Reuse synthetic records and mocked HTTP helpers from `tests/support.py`; tests must not require credentials or contact Oracle. Cover changed behavior and relevant failures, particularly authentication, pagination, cache invalidation, URL validation, and download cleanup. No numeric coverage threshold is configured.

## Commit & Pull Request Guidelines

Git history is unavailable in this checkout, so no established commit convention can be verified. Use concise, imperative subjects. Describe behavior changes, link relevant issues, report test results, and update affected documentation in pull requests.

## Security & Configuration

Read configuration only from explicit environment variables. Never commit credentials, real candidate records, or resumes. Preserve TLS verification, sanitized errors, bounded downloads, and disabled automatic write retries. Journey updates require explicit configuration; keep them disabled by default.
