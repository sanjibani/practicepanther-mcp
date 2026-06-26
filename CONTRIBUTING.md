# Contributing

Thanks for your interest in improving practicepanther-mcp.

## Development setup

```bash
git clone https://github.com/sanjibani/practicepanther-mcp
cd practicepanther-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install         # (optional) install git hooks
```

## Running tests

```bash
pytest                     # run all unit tests
pytest --cov=practicepanther_mcp --cov-report=term-missing   # with coverage
```

Tests use `respx` to mock httpx and `hypothesis` for property-based tests. No live API
calls happen in the default test suite.

## Linting & typing

```bash
ruff format src tests      # auto-format
ruff check src tests       # lint
mypy src                   # strict type check
```

The CI pipeline runs all three on Python 3.10–3.13 across ubuntu/macos/windows.

## Adding a new MCP tool

1. Add the API method to `client.py` (one resource group at a time).
2. Add the `@mcp.tool()` wrapper to `server.py` with a rich docstring
   (what / when / example).
3. Add at least one test in `tests/test_smoke.py` covering:
   - Happy path (respx mock returning expected JSON)
   - One error path (401, 404, 429, 500 — pick the most likely for this endpoint)
4. Update `README.md` Tools table.
5. Bump `__version__` in `src/practicepanther_mcp/__init__.py`.
6. Add a CHANGELOG entry under `[Unreleased]`.

## Pull request process

1. Open an issue first if the change is non-trivial (new feature, breaking change).
2. Fork the repo, create a feature branch.
3. Make your changes + tests. Run `ruff format`, `ruff check`, `mypy src`, `pytest`.
4. Push the branch and open a PR using the PR template.
5. CI must pass (lint + matrix of Python versions × OS).
6. A maintainer will review within ~2 business days.

## Release process

1. Update `CHANGELOG.md` — move entries from `[Unreleased]` to a new `[X.Y.Z]` section.
2. Bump `__version__` in `src/practicepanther_mcp/__init__.py`.
3. Commit + tag: `git tag vX.Y.Z && git push --tags`.
4. GitHub Actions `publish.yml` builds the wheel/sdist and uploads to PyPI
   via trusted publishing.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be kind,
assume good faith, focus on the technical merits.
