# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-26

### Changed (breaking)

- **Exceptions moved** from `practicepanther_mcp.client` to a new
  `practicepanther_mcp.exceptions` module. Update imports:
  `from practicepanther_mcp.exceptions import PracticePantherAuthError` (or
  import from the top-level `practicepanther_mcp`).
- **`PracticePantherError` is now a base class** with `http_status`,
  `error_code`, `request_id`, `body` structured fields. Subclasses:
  `PracticePantherRefreshTokenExpiredError`, `PracticePantherNotFoundError`,
  `PracticePantherRateLimitError(retry_after=...)`, `PracticePantherConnectionError`.

### Added

- Shared `httpx.AsyncClient` with connection pooling (100 max, 20 keepalive)
  and transport-level retries — replaces per-call client construction.
- Application-level retry with exponential backoff + full jitter on 429 / 5xx,
  honoring the `Retry-After` header (RFC 7231 §7.1.3).
- Async pagination iterators: `iter_matters()`, `iter_time_entries()`,
  `iter_invoices()`, `iter_accounts()` — auto-paginate via OData `$top`/`$skip`.
- Typed OAuth 2 error-code mapping (`invalid_grant` → `PracticePantherRefreshTokenExpiredError`).
- Structured logging via `structlog` — request.start / request.end / oauth.refresh.*.
- Request ID capture from `x-request-id` / `x-amzn-requestid` headers.
- `py.typed` marker (PEP 561) — type info flows to downstream mypy.
- Pre-commit config (ruff + ruff-format + mypy + standard hooks).
- GitHub Actions CI (matrix: Python 3.10–3.13 × ubuntu/macos/windows).
- GitHub Actions publish workflow (PyPI trusted publishing, no API token).
- Dependabot config (weekly pip + GitHub Actions updates, grouped).
- CHANGELOG, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, .editorconfig, .gitattributes.
- PR template with lint/type/test checklist.

### Internal

- Tests rewritten with `respx` (industry-standard httpx mocking) — much
  cleaner than `AsyncMock` setup.
- Property-based tests via `hypothesis` for JSON serialization round-trip.
- `mypy --strict` clean across `src/`.

## [0.1.0] - 2026-06-26

### Added

- Initial release.
- 25 MCP tools covering matters (cases), client accounts, contacts, billable time entries, invoices, activities, tasks, calendar events, reference data.
- OAuth 2 authorization-code flow with `practicepanther-mcp-auth` CLI helper.
- Shared `httpx.AsyncClient` with connection pooling + transport-level retries.
- Application-level retry with exponential backoff + full jitter on 429/5xx.
- Async pagination iterators (`iter_matters()`, `iter_time_entries()`, `iter_invoices()`, `iter_accounts()`).
- Structured exception hierarchy with `http_status`, `error_code`, `request_id`, `retry_after` fields.
- Typed OAuth 2 error-code mapping (`invalid_grant` → `PracticePantherRefreshTokenExpiredError`).
- Structured logging via `structlog` (request.start / request.end / oauth.refresh.*).
- 28 tests (pytest + respx + hypothesis property tests). mypy strict clean. ruff clean.

### Security

- User-Agent header set to `practicepanther-mcp/<version>` (no PII).
- All credentials read from env vars; never logged or persisted by the package.
