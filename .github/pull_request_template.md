## What

<!-- One-paragraph summary of what changed. -->

## Why

<!-- The motivation. Link to issue if applicable. Reference the user-facing behavior change. -->

## How tested

- [ ] `pytest` passes locally
- [ ] `ruff check src tests` is clean
- [ ] `ruff format --check src tests` is clean
- [ ] `mypy src` is clean (strict)
- [ ] Manual smoke test against real PracticePanther sandbox (if user-facing change)

## Checklist

- [ ] Bumped version in `src/practicepanther_mcp/__init__.py` if user-facing change
- [ ] Added entry to `CHANGELOG.md` under `[Unreleased]`
- [ ] Updated `README.md` if API/CLI/auth changed
- [ ] No secrets in code (only env-var references)
- [ ] Tests cover both happy path AND error paths (auth errors, rate limits, network errors)
