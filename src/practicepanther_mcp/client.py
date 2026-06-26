"""Async HTTP client for PracticePanther.

Built on industry-leading patterns (encode/httpx, stripe-python, authlib,
boto3):

- **Shared ``httpx.AsyncClient``** with connection pooling and transport-level
  retries for transient network failures.
- **Typed exception hierarchy** with structured fields (``error_code``,
  ``request_id``, ``retry_after``). See ``exceptions.py``.
- **Application-level retry** with exponential backoff + full jitter on 429
  and 5xx responses, honoring the ``Retry-After`` header.
- **OAuth 2 authorization-code flow** with proactive token refresh and
  concurrent-safe token refresh via ``asyncio.Lock``.
- **OData pagination** via an async iterator that auto-fetches subsequent
  pages (``iter_matters()``, ``iter_time_entries()``, etc.).
- **Structured logging** via ``structlog`` — every request is logged with
  method, path, status, duration_ms, and (on error) error_code.

Docs: https://support.practicepanther.com/en/articles/479897-practicepanther-api
Swagger: https://app.practicepanther.com/swagger/ui/index
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import random
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from . import __version__
from .exceptions import (
    OAUTH_INVALID_CLIENT,
    OAUTH_INVALID_GRANT,
    OAUTH_INVALID_REFRESH,
    PracticePantherAPIError,
    PracticePantherAuthError,
    PracticePantherConnectionError,
    PracticePantherError,
    PracticePantherNotFoundError,
    PracticePantherRateLimitError,
    PracticePantherRefreshTokenExpiredError,
)

log = structlog.get_logger(__name__)


# --- Configuration constants -----------------------------------------------

DEFAULT_BASE_URL = "https://app.practicepanther.com"
DEFAULT_TIMEOUT = 30.0
TOKEN_PATH = "/oauth/token"
API_PATH_PREFIX = "/api/v2"
REFRESH_AHEAD_SECONDS = 300  # refresh 5 minutes before expiry
TOKEN_TTL_SECONDS = 50 * 60  # PP doesn't return expires_in — assume 50 min

# Connection pool sizing — httpx best practice
DEFAULT_MAX_CONNECTIONS = 100
DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 20
DEFAULT_KEEPALIVE_EXPIRY = 30.0

# Application-level retry (orthogonal to transport-level retries)
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_RETRY_DELAY = 0.5
DEFAULT_MAX_RETRY_DELAY = 30.0

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


# --- Internal helpers ------------------------------------------------------


def _retry_delay(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff with full jitter, clamped to [0.5, 30] seconds.

    ``retry_after`` (if provided) takes precedence — we honor the server's
    hint over our own backoff calculation.
    """
    if retry_after is not None:
        return min(float(retry_after), DEFAULT_MAX_RETRY_DELAY)
    delay = min(DEFAULT_BASE_RETRY_DELAY * (2 ** attempt), DEFAULT_MAX_RETRY_DELAY)
    return float(delay * random.uniform(0.5, 1.0))  # full jitter


class PracticePantherClient:
    """Async client for PracticePanther's REST + OData API.

    OAuth 2 authorization-code flow. Credentials:
    - ``access_token``   — short-lived bearer token (~50 min)
    - ``refresh_token``  — long-lived (60 days) token to mint new access tokens
    - ``client_id``      — your registered OAuth app client id
    - ``client_secret``  — your registered OAuth app client secret

    Set the ``PRACTICEPANTHER_*`` env vars or pass them to the constructor.

    Use as an async context manager to ensure the underlying httpx client's
    connection pool is cleanly closed:

        async with PracticePantherClient() as client:
            async for matter in client.iter_matters(status="open"):
                ...
    """

    def __init__(
        self,
        access_token: str | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        access_token = access_token or os.environ.get("PRACTICEPANTHER_ACCESS_TOKEN")
        refresh_token = refresh_token or os.environ.get("PRACTICEPANTHER_REFRESH_TOKEN")
        client_id = client_id or os.environ.get("PRACTICEPANTHER_CLIENT_ID")
        client_secret = client_secret or os.environ.get("PRACTICEPANTHER_CLIENT_SECRET")

        if not all([access_token, refresh_token, client_id, client_secret]):
            raise PracticePantherAuthError(
                "PracticePanther credentials missing. Set PRACTICEPANTHER_ACCESS_TOKEN, "
                "PRACTICEPANTHER_REFRESH_TOKEN, PRACTICEPANTHER_CLIENT_ID, and "
                "PRACTICEPANTHER_CLIENT_SECRET — or run `practicepanther-mcp-auth` "
                "to obtain them."
            )

        assert access_token is not None
        assert refresh_token is not None
        assert client_id is not None
        assert client_secret is not None
        self._access_token: str = access_token
        self._refresh_token: str = refresh_token
        self._client_id: str = client_id
        self._client_secret: str = client_secret
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries

        # Token cache — protected by lock for concurrent refresh.
        self._token_expires_at: float = time.monotonic() + TOKEN_TTL_SECONDS
        self._token_lock = asyncio.Lock()

        # Build shared httpx.AsyncClient with pooling + transport retries.
        transport = httpx.AsyncHTTPTransport(retries=3)
        limits = httpx.Limits(
            max_connections=DEFAULT_MAX_CONNECTIONS,
            max_keepalive_connections=DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=DEFAULT_KEEPALIVE_EXPIRY,
        )
        timeout_obj = httpx.Timeout(
            timeout,
            connect=10.0,
            read=timeout,
            write=10.0,
            pool=5.0,
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_obj,
            limits=limits,
            transport=transport,
            headers={
                "User-Agent": f"practicepanther-mcp/{__version__}",
                "Accept": "application/json",
            },
            follow_redirects=False,
        )

    # --- Context manager ------------------------------------------------------

    async def __aenter__(self) -> PracticePantherClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Flush keepalive connections and release the httpx client."""
        await self._client.aclose()

    # --- Token lifecycle ------------------------------------------------------

    async def _refresh_tokens(self) -> None:
        """Exchange refresh_token for a fresh access_token (+ rotated refresh_token).

        Maps OAuth 2 error codes to typed exceptions so callers can branch on
        cause rather than parsing message text.
        """
        log.info("oauth.refresh.start")
        try:
            response = await self._client.post(
                TOKEN_PATH,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        except httpx.HTTPError as exc:
            log.error("oauth.refresh.connection_error", error=str(exc))
            raise PracticePantherConnectionError(
                f"Network failure during PracticePanther token refresh: {exc}",
            ) from exc

        if response.status_code != 200:
            error_code: str | None = None
            error_description: str | None = None
            try:
                data = response.json()
                error_code = data.get("error")
                error_description = data.get("error_description")
            except ValueError:
                data = response.text
            log.error(
                "oauth.refresh.api_error",
                http_status=response.status_code,
                error_code=error_code,
                error_description=error_description,
            )
            if error_code in (OAUTH_INVALID_GRANT, OAUTH_INVALID_REFRESH):
                raise PracticePantherRefreshTokenExpiredError(
                    "PracticePanther refresh_token expired or revoked. "
                    "Re-run `practicepanther-mcp-auth` to obtain a new one.",
                    http_status=response.status_code,
                    error_code=error_code,
                    body=data,
                ) from None
            if error_code == OAUTH_INVALID_CLIENT:
                raise PracticePantherAuthError(
                    "PracticePanther rejected client_id/client_secret. "
                    "Check PRACTICEPANTHER_CLIENT_ID and PRACTICEPANTHER_CLIENT_SECRET.",
                    http_status=response.status_code,
                    error_code=error_code,
                    body=data,
                ) from None
            raise PracticePantherAuthError(
                f"PracticePanther token refresh failed (HTTP {response.status_code}): "
                f"{error_description or data}",
                http_status=response.status_code,
                error_code=error_code,
                body=data,
            ) from None

        data = response.json()
        new_access = data.get("access_token")
        new_refresh = data.get("refresh_token", self._refresh_token)
        if not new_access:
            log.error("oauth.refresh.missing_access_token", response_keys=list(data.keys()))
            raise PracticePantherAuthError(
                f"No access_token in PracticePanther refresh response: {data}",
                http_status=200,
                body=data,
            )
        self._access_token = new_access
        self._refresh_token = new_refresh
        self._token_expires_at = time.monotonic() + TOKEN_TTL_SECONDS - REFRESH_AHEAD_SECONDS
        log.info("oauth.refresh.success", expires_in_seconds=int(TOKEN_TTL_SECONDS))

    async def _get_token(self) -> str:
        """Return a valid access token, refreshing if expired. Concurrent-safe."""
        if time.monotonic() < self._token_expires_at:
            return self._access_token
        async with self._token_lock:
            if time.monotonic() >= self._token_expires_at:
                await self._refresh_tokens()
            return self._access_token

    # --- Request execution ----------------------------------------------------

    def _build_url(
        self,
        path: str,
        params: dict[str, Any] | None,
        odata: dict[str, Any] | None,
    ) -> str:
        """Compose full URL with regular params + OData params (with $ prefix)."""
        all_params: list[tuple[str, str]] = []
        if params:
            for k, v in params.items():
                if v is None:
                    continue
                all_params.append((k, str(v)))
        if odata:
            for k, v in odata.items():
                if v is None or v == "":
                    continue
                key = k if k.startswith("$") else f"${k}"
                all_params.append((key, str(v)))
        query = urlencode(all_params, doseq=True)
        base = f"{API_PATH_PREFIX}{path}"
        return f"{base}?{query}" if query else base

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map a non-2xx response to the most specific typed exception."""
        request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("x-amzn-requestid")
            or response.headers.get("request-id")
        )
        try:
            data = response.json()
        except ValueError:
            data = response.text

        if response.status_code == 404:
            raise PracticePantherNotFoundError(
                f"PracticePanther resource not found: {response.url}",
                http_status=404,
                request_id=request_id,
                body=data,
            )
        if response.status_code in (401, 403):
            raise PracticePantherAuthError(
                f"PracticePanther rejected the request (HTTP {response.status_code}). "
                "If 401: refresh_token may have expired — run `practicepanther-mcp-auth`.",
                http_status=response.status_code,
                request_id=request_id,
                body=data,
            )
        if response.status_code == 429:
            retry_after: float | None = None
            with contextlib.suppress(ValueError):
                ra_header = response.headers.get("retry-after")
                if ra_header:
                    retry_after = float(ra_header)
            raise PracticePantherRateLimitError(
                "PracticePanther rate limit hit (HTTP 429). Slow down.",
                retry_after=retry_after,
                request_id=request_id,
                body=data,
            )
        if 500 <= response.status_code < 600:
            raise PracticePantherAPIError(
                f"PracticePanther server error (HTTP {response.status_code})",
                http_status=response.status_code,
                request_id=request_id,
                body=data,
            )
        raise PracticePantherAPIError(
            f"PracticePanther returned HTTP {response.status_code}",
            http_status=response.status_code,
            request_id=request_id,
            body=data,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        odata: dict[str, Any] | None = None,
    ) -> Any:
        """Issue an authenticated request with retry + 401-triggered token refresh."""
        url_path = self._build_url(path, params, odata)
        full_url = f"{self._base_url}{url_path}"

        last_exc: PracticePantherError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                token = await self._get_token()
            except PracticePantherRefreshTokenExpiredError:
                raise

            headers = {"Authorization": f"Bearer {token}"}
            if json is not None:
                headers["Content-Type"] = "application/json"

            log.info("request.start", method=method, path=url_path, attempt=attempt)
            t0 = time.monotonic()
            try:
                response = await self._client.request(
                    method, full_url, json=json, headers=headers
                )
            except httpx.HTTPError as exc:
                duration_ms = (time.monotonic() - t0) * 1000
                log.warning(
                    "request.connection_error",
                    method=method,
                    path=url_path,
                    error=str(exc),
                    duration_ms=round(duration_ms, 1),
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                raise PracticePantherConnectionError(
                    f"Network failure calling PracticePanther {method} {url_path}: {exc}",
                ) from exc

            duration_ms = (time.monotonic() - t0) * 1000
            log.info(
                "request.end",
                method=method,
                path=url_path,
                status=response.status_code,
                duration_ms=round(duration_ms, 1),
            )

            if response.status_code == 401 and attempt == 0:
                log.warning("request.401_forcing_token_refresh", path=url_path)
                self._token_expires_at = 0.0
                continue

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                retry_after: float | None = None
                with contextlib.suppress(ValueError):
                    ra_header = response.headers.get("retry-after")
                    if ra_header:
                        retry_after = float(ra_header)
                delay = _retry_delay(attempt, retry_after)
                log.warning(
                    "request.retry",
                    method=method,
                    path=url_path,
                    status=response.status_code,
                    attempt=attempt,
                    delay=round(delay, 2),
                )
                await asyncio.sleep(delay)
                continue

            if 200 <= response.status_code < 300:
                text = response.text
                if not text:
                    return None
                try:
                    return response.json()
                except ValueError:
                    return text

            try:
                self._raise_for_status(response)
            except PracticePantherRateLimitError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = _retry_delay(attempt, exc.retry_after)
                    log.warning("request.retry_after_429", delay=round(delay, 2))
                    await asyncio.sleep(delay)
                    continue
                raise
            except (PracticePantherAPIError, PracticePantherAuthError,
                    PracticePantherNotFoundError):
                raise
            except PracticePantherError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    # ----- Pagination helpers ------------------------------------------------

    async def _iter_pages(
        self,
        method_path: str,
        *,
        item_key: str | None = None,
        page_size: int = 100,
        odata_filter: str | None = None,
        odata_orderby: str | None = None,
        select: str | None = None,
        **params: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async iterator over paginated list endpoints.

        PracticePanther uses OData-style ``$top``/``$skip`` pagination.
        Yields individual items until a short page is returned.
        """
        skip = 0
        while True:
            page = await self._request(
                "GET",
                method_path,
                params=params,
                odata={
                    "top": page_size,
                    "skip": skip,
                    "filter": odata_filter,
                    "orderby": odata_orderby,
                    "select": select,
                },
            )
            if not page:
                return
            items = page if isinstance(page, list) else page.get(item_key or "items", page)
            for item in items:
                yield item
            if len(items) < page_size:
                return
            skip += page_size

    # ----- Matters (cases) ----------------------------------------------------

    async def find_matters(
        self,
        *,
        account_id: int | None = None,
        status: str | None = None,
        practice_area_id: int | None = None,
        responsible_attorney_id: int | None = None,
        top: int = 50,
        skip: int = 0,
        orderby: str | None = None,
        odata_filter: str | None = None,
        select: str | None = None,
    ) -> Any:
        """List/search matters (cases)."""
        odata: dict[str, Any] = {"top": top, "skip": skip}
        if orderby:
            odata["orderby"] = orderby
        if odata_filter:
            odata["filter"] = odata_filter
        if select:
            odata["select"] = select
        params: dict[str, Any] = {}
        if account_id is not None:
            params["accountId"] = account_id
        if status:
            params["status"] = status
        if practice_area_id is not None:
            params["practiceAreaId"] = practice_area_id
        if responsible_attorney_id is not None:
            params["responsibleAttorneyId"] = responsible_attorney_id
        return await self._request("GET", "/matters", params=params, odata=odata)

    async def iter_matters(
        self,
        *,
        account_id: int | None = None,
        status: str | None = None,
        practice_area_id: int | None = None,
        responsible_attorney_id: int | None = None,
        page_size: int = 100,
        odata_filter: str | None = None,
        odata_orderby: str | None = None,
        select: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterate over all matters matching filters, auto-paginating via OData."""
        params: dict[str, Any] = {}
        if account_id is not None:
            params["accountId"] = account_id
        if status:
            params["status"] = status
        if practice_area_id is not None:
            params["practiceAreaId"] = practice_area_id
        if responsible_attorney_id is not None:
            params["responsibleAttorneyId"] = responsible_attorney_id
        async for matter in self._iter_pages(
            "/matters",
            page_size=page_size,
            odata_filter=odata_filter,
            odata_orderby=odata_orderby,
            select=select,
            **params,
        ):
            yield matter

    async def get_matter(self, matter_id: int) -> Any:
        """Fetch a single matter (case) with full detail."""
        return await self._request("GET", f"/matters/{matter_id}")

    async def create_matter(self, matter: dict[str, Any]) -> Any:
        """Open a new matter."""
        return await self._request("POST", "/matters", json=matter)

    async def update_matter(self, matter_id: int, updates: dict[str, Any]) -> Any:
        """Patch fields on a matter."""
        return await self._request("PATCH", f"/matters/{matter_id}", json=updates)

    # ----- Accounts (clients) ------------------------------------------------

    async def find_accounts(
        self,
        *,
        name: str | None = None,
        email: str | None = None,
        top: int = 50,
        skip: int = 0,
        orderby: str | None = None,
        odata_filter: str | None = None,
    ) -> Any:
        """List/search client accounts."""
        odata: dict[str, Any] = {"top": top, "skip": skip}
        if orderby:
            odata["orderby"] = orderby
        if odata_filter:
            odata["filter"] = odata_filter
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        if email:
            params["email"] = email
        return await self._request("GET", "/accounts", params=params, odata=odata)

    async def iter_accounts(
        self,
        *,
        name: str | None = None,
        email: str | None = None,
        page_size: int = 100,
        odata_filter: str | None = None,
        odata_orderby: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterate over all client accounts matching filters, auto-paginating."""
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        if email:
            params["email"] = email
        async for account in self._iter_pages(
            "/accounts",
            page_size=page_size,
            odata_filter=odata_filter,
            odata_orderby=odata_orderby,
            **params,
        ):
            yield account

    async def get_account(self, account_id: int) -> Any:
        """Fetch a single client account."""
        return await self._request("GET", f"/accounts/{account_id}")

    async def create_account(self, account: dict[str, Any]) -> Any:
        """Create a new client account."""
        return await self._request("POST", "/accounts", json=account)

    # ----- Contacts -----------------------------------------------------------

    async def find_contacts(
        self,
        *,
        account_id: int | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        top: int = 50,
        skip: int = 0,
    ) -> Any:
        """List contacts (associated people on an account)."""
        params: dict[str, Any] = {}
        if account_id is not None:
            params["accountId"] = account_id
        if first_name:
            params["firstName"] = first_name
        if last_name:
            params["lastName"] = last_name
        if email:
            params["email"] = email
        return await self._request(
            "GET", "/contacts", params=params, odata={"top": top, "skip": skip}
        )

    async def create_contact(self, contact: dict[str, Any]) -> Any:
        """Create a new contact."""
        return await self._request("POST", "/contacts", json=contact)

    # ----- TimeEntries (billable hours) --------------------------------------

    async def find_time_entries(
        self,
        *,
        matter_id: int | None = None,
        user_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        billable: bool | None = None,
        top: int = 50,
        skip: int = 0,
        orderby: str | None = None,
    ) -> Any:
        """List time entries."""
        params: dict[str, Any] = {}
        if matter_id is not None:
            params["matterId"] = matter_id
        if user_id is not None:
            params["userId"] = user_id
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        if billable is not None:
            params["billable"] = "true" if billable else "false"
        odata: dict[str, Any] = {"top": top, "skip": skip}
        if orderby:
            odata["orderby"] = orderby
        return await self._request("GET", "/timeentries", params=params, odata=odata)

    async def iter_time_entries(
        self,
        *,
        matter_id: int | None = None,
        user_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        billable: bool | None = None,
        page_size: int = 100,
        odata_orderby: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterate over all matching time entries, auto-paginating."""
        params: dict[str, Any] = {}
        if matter_id is not None:
            params["matterId"] = matter_id
        if user_id is not None:
            params["userId"] = user_id
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        if billable is not None:
            params["billable"] = "true" if billable else "false"
        async for entry in self._iter_pages(
            "/timeentries",
            page_size=page_size,
            odata_orderby=odata_orderby,
            **params,
        ):
            yield entry

    async def get_time_entry(self, time_entry_id: int) -> Any:
        """Fetch a single time entry."""
        return await self._request("GET", f"/timeentries/{time_entry_id}")

    async def create_time_entry(self, entry: dict[str, Any]) -> Any:
        """Log a billable (or non-billable) time entry."""
        return await self._request("POST", "/timeentries", json=entry)

    async def update_time_entry(self, time_entry_id: int, updates: dict[str, Any]) -> Any:
        """Patch fields on a time entry."""
        return await self._request("PATCH", f"/timeentries/{time_entry_id}", json=updates)

    # ----- Invoices ----------------------------------------------------------

    async def find_invoices(
        self,
        *,
        account_id: int | None = None,
        matter_id: int | None = None,
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        top: int = 50,
        skip: int = 0,
        orderby: str | None = None,
    ) -> Any:
        """List invoices."""
        params: dict[str, Any] = {}
        if account_id is not None:
            params["accountId"] = account_id
        if matter_id is not None:
            params["matterId"] = matter_id
        if status:
            params["status"] = status
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        odata: dict[str, Any] = {"top": top, "skip": skip}
        if orderby:
            odata["orderby"] = orderby
        return await self._request("GET", "/invoices", params=params, odata=odata)

    async def iter_invoices(
        self,
        *,
        account_id: int | None = None,
        matter_id: int | None = None,
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page_size: int = 100,
        odata_orderby: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterate over all matching invoices, auto-paginating."""
        params: dict[str, Any] = {}
        if account_id is not None:
            params["accountId"] = account_id
        if matter_id is not None:
            params["matterId"] = matter_id
        if status:
            params["status"] = status
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        async for invoice in self._iter_pages(
            "/invoices",
            page_size=page_size,
            odata_orderby=odata_orderby,
            **params,
        ):
            yield invoice

    async def get_invoice(self, invoice_id: int) -> Any:
        """Fetch a single invoice with line items."""
        return await self._request("GET", f"/invoices/{invoice_id}")

    async def create_invoice(self, invoice: dict[str, Any]) -> Any:
        """Create a new invoice."""
        return await self._request("POST", "/invoices", json=invoice)

    # ----- Activities / Tasks / Notes ----------------------------------------

    async def find_activities(
        self,
        *,
        matter_id: int | None = None,
        account_id: int | None = None,
        activity_type: str | None = None,
        top: int = 50,
        skip: int = 0,
    ) -> Any:
        """List activities (calls, emails, meetings) on matters/accounts."""
        params: dict[str, Any] = {}
        if matter_id is not None:
            params["matterId"] = matter_id
        if account_id is not None:
            params["accountId"] = account_id
        if activity_type:
            params["type"] = activity_type
        return await self._request(
            "GET", "/activities", params=params, odata={"top": top, "skip": skip}
        )

    async def create_activity(self, activity: dict[str, Any]) -> Any:
        """Log a new activity (call, email, meeting) against a matter."""
        return await self._request("POST", "/activities", json=activity)

    async def find_tasks(
        self,
        *,
        matter_id: int | None = None,
        assignee_id: int | None = None,
        status: str | None = None,
        top: int = 50,
        skip: int = 0,
    ) -> Any:
        """List tasks (to-dos)."""
        params: dict[str, Any] = {}
        if matter_id is not None:
            params["matterId"] = matter_id
        if assignee_id is not None:
            params["assigneeId"] = assignee_id
        if status:
            params["status"] = status
        return await self._request(
            "GET", "/tasks", params=params, odata={"top": top, "skip": skip}
        )

    async def create_task(self, task: dict[str, Any]) -> Any:
        """Create a new task."""
        return await self._request("POST", "/tasks", json=task)

    # ----- Calendar events ---------------------------------------------------

    async def find_events(
        self,
        *,
        user_id: int | None = None,
        matter_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        top: int = 50,
        skip: int = 0,
    ) -> Any:
        """List calendar events."""
        params: dict[str, Any] = {}
        if user_id is not None:
            params["userId"] = user_id
        if matter_id is not None:
            params["matterId"] = matter_id
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        return await self._request(
            "GET", "/calendarevents", params=params, odata={"top": top, "skip": skip}
        )

    async def create_event(self, event: dict[str, Any]) -> Any:
        """Create a new calendar event."""
        return await self._request("POST", "/calendarevents", json=event)

    # ----- Users (firm staff) ------------------------------------------------

    async def list_users(self) -> Any:
        """List firm users (attorneys, paralegals, staff)."""
        return await self._request("GET", "/users")

    # ----- Reference data ----------------------------------------------------

    async def list_practice_areas(self) -> Any:
        """List practice areas defined for the firm."""
        return await self._request("GET", "/practiceareas")

    async def list_expense_categories(self) -> Any:
        """List expense categories."""
        return await self._request("GET", "/expensecategories")
