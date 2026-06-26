"""PracticePanther exceptions — typed hierarchy with structured context.

Pattern: stripe-python / boto3 — every error carries structured fields
(http_status, error_code, request_id) so callers can branch on cause, not
just message text. The base class is never raised directly.
"""
from __future__ import annotations

from typing import Any


class PracticePantherError(Exception):
    """Base exception for all PracticePanther client errors.

    Subclasses set a default ``http_status`` and may add their own structured
    fields. Never raise this directly — raise the most specific subclass.
    """

    http_status: int | None = None

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        error_code: str | None = None,
        request_id: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status if http_status is not None else self.http_status
        self.error_code = error_code
        self.request_id = request_id
        self.body = body

    def __repr__(self) -> str:
        parts = [f"http_status={self.http_status!r}"]
        if self.error_code:
            parts.append(f"error_code={self.error_code!r}")
        if self.request_id:
            parts.append(f"request_id={self.request_id!r}")
        return f"{type(self).__name__}({', '.join(parts)})"


class PracticePantherAuthError(PracticePantherError):
    """401 (bad/expired token) or 403 (insufficient scope). Run
    ``practicepanther-mcp-auth`` to refresh credentials."""

    http_status = 401


class PracticePantherRefreshTokenExpiredError(PracticePantherAuthError):
    """The 60-day refresh_token itself expired or was revoked. User must
    re-run the OAuth flow — automatic refresh cannot recover."""

    http_status = 401


class PracticePantherNotFoundError(PracticePantherError):
    """404 — resource doesn't exist or the user doesn't have access."""

    http_status = 404


class PracticePantherRateLimitError(PracticePantherError):
    """429 — rate limit hit. Includes ``retry_after`` if the server sent one."""

    http_status = 429

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class PracticePantherAPIError(PracticePantherError):
    """5xx or other non-2xx response that wasn't caught by a more specific
    exception. Caller may retry after a backoff."""

    http_status = 500


class PracticePantherConnectionError(PracticePantherError):
    """Network-level failure (DNS, TCP, TLS). Distinct from HTTP errors so
    callers can retry transient connectivity issues."""

    http_status = None


# OAuth 2 RFC 6749 error codes we handle specially (mapped to typed exceptions).
# Centralized here so callers/tests can reference them without importing client.
OAUTH_INVALID_GRANT = "invalid_grant"
OAUTH_INVALID_REFRESH = "invalid_refresh"
OAUTH_INVALID_CLIENT = "invalid_client"
