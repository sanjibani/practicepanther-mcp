"""Async HTTP client for PracticePanther.

Uses PracticePanther's REST + OData API with OAuth 2 authorization-code grant.
The token lifecycle:
- ``PRACTICEPANTHER_ACCESS_TOKEN`` is sent on every request as ``Bearer <token>``.
- ``PRACTICEPANTHER_REFRESH_TOKEN`` is used to mint a new access token when the
  current one expires (HTTP 401) or proactively when nearing expiry.
- ``PRACTICEPANTHER_CLIENT_ID`` + ``PRACTICEPANTHER_CLIENT_SECRET`` authenticate
  the refresh-token exchange.

Docs: https://support.practicepanther.com/en/articles/479897-practicepanther-api
Swagger: https://app.practicepanther.com/swagger/ui/index
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx


DEFAULT_BASE_URL = "https://app.practicepanther.com"
DEFAULT_TIMEOUT = 30.0
TOKEN_PATH = "/oauth/token"
API_PATH_PREFIX = "/api/v2"
REFRESH_AHEAD_SECONDS = 300  # refresh 5 min before expiry


class PracticePantherError(RuntimeError):
    """Base exception for PracticePanther client errors."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class PracticePantherAuthError(PracticePantherError):
    """Raised when credentials are missing, invalid, or unauthorized."""


class PracticePantherAPIError(PracticePantherError):
    """Raised on non-2xx API responses other than auth failures."""


class PracticePantherClient:
    """Async client for PracticePanther's REST + OData API.

    OAuth 2 authorization-code flow. Credentials:
    - ``access_token``   — short-lived bearer token (typically 1 hour)
    - ``refresh_token``  — long-lived (60 days) token to mint new access tokens
    - ``client_id``      — your registered OAuth app client id
    - ``client_secret``  — your registered OAuth app client secret

    Set the ``PRACTICEPANTHER_*`` env vars or pass them to the constructor.
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
    ) -> None:
        access_token = access_token or os.environ.get("PRACTICEPANTHER_ACCESS_TOKEN")
        refresh_token = refresh_token or os.environ.get("PRACTICEPANTHER_REFRESH_TOKEN")
        client_id = client_id or os.environ.get("PRACTICEPANTHER_CLIENT_ID")
        client_secret = client_secret or os.environ.get("PRACTICEPANTHER_CLIENT_SECRET")

        if not all([access_token, refresh_token, client_id, client_secret]):
            raise PracticePantherAuthError(
                "PracticePanther credentials missing. Set PRACTICEPANTHER_ACCESS_TOKEN, "
                "PRACTICEPANTHER_REFRESH_TOKEN, PRACTICEPANTHER_CLIENT_ID, and "
                "PRACTICEPANTHER_CLIENT_SECRET — or run `practicepanther-mcp-auth` to obtain them."
            )

        self._access_token: str = access_token
        self._refresh_token: str = refresh_token
        self._client_id: str = client_id
        self._client_secret: str = client_secret
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

        # Token cache — protected by lock for concurrent refresh.
        # PracticePanther doesn't return an expiry in the token response, so we
        # default to 50 minutes (typical OAuth access-token lifetime) and rely
        # on 401-driven refresh as the source of truth.
        self._token_expires_at: float = time.monotonic() + 50 * 60
        self._token_lock = asyncio.Lock()

    async def _refresh_tokens(self) -> None:
        """Exchange refresh_token for a fresh access_token (+ rotated refresh_token)."""
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.post(
                f"{self._base_url}{TOKEN_PATH}",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        if response.status_code != 200:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise PracticePantherAuthError(
                f"PracticePanther refresh_token exchange failed (HTTP {response.status_code}): {body}",
                status_code=response.status_code,
                body=body,
            )
        data = response.json()
        new_access = data.get("access_token")
        new_refresh = data.get("refresh_token", self._refresh_token)
        if not new_access:
            raise PracticePantherAuthError(f"No access_token in refresh response: {data}")
        self._access_token = new_access
        self._refresh_token = new_refresh
        self._token_expires_at = time.monotonic() + 50 * 60 - REFRESH_AHEAD_SECONDS

    async def _get_token(self) -> str:
        """Return a valid access token, refreshing if expired. Concurrent-safe."""
        async with self._token_lock:
            if time.monotonic() >= self._token_expires_at:
                await self._refresh_tokens()
            return self._access_token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        odata: dict[str, Any] | None = None,
    ) -> Any:
        """Issue an authenticated request against the PracticePanther API.

        ``odata`` (if provided) is merged into the query string with the ``$`` prefix
        that OData requires (e.g. ``{"filter": "x eq 1", "orderby": "name desc"}``).
        """
        url = f"{self._base_url}{API_PATH_PREFIX}{path}"

        # Build query string: caller params + OData params (with $ prefix)
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
        query_string = urlencode(all_params, doseq=True)
        if query_string:
            url = f"{url}?{query_string}"

        for attempt in range(2):
            token = await self._get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            if json is not None:
                headers["Content-Type"] = "application/json"
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.request(method, url, json=json, headers=headers)
            if response.status_code == 401 and attempt == 0:
                # Force token refresh on next iteration
                async with self._token_lock:
                    self._token_expires_at = 0.0
                continue
            break

        if response.status_code == 401:
            raise PracticePantherAuthError(
                "PracticePanther rejected the bearer token (HTTP 401). "
                "Refresh failed or refresh_token expired (60-day TTL).",
                401,
            )
        if response.status_code == 403:
            raise PracticePantherAuthError(
                "PracticePanther denied access (HTTP 403). Check your app scopes.",
                403,
            )
        if response.status_code == 429:
            raise PracticePantherAPIError(
                "PracticePanther rate limit hit (HTTP 429). Slow down.",
                429,
            )
        if not 200 <= response.status_code < 300:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise PracticePantherAPIError(
                f"PracticePanther returned HTTP {response.status_code}",
                status_code=response.status_code,
                body=body,
            )

        text = response.text
        if not text:
            return None
        try:
            return response.json()
        except ValueError:
            return text

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
        filter: str | None = None,
        select: str | None = None,
    ) -> list[dict[str, Any]]:
        """List/search matters (cases). Supports OData ``$filter``, ``$orderby``,
        ``$select`` for rich queries."""
        odata: dict[str, Any] = {"top": top, "skip": skip}
        if orderby:
            odata["orderby"] = orderby
        if filter:
            odata["filter"] = filter
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

    async def get_matter(self, matter_id: int) -> dict[str, Any]:
        """Fetch a single matter (case) with full detail."""
        return await self._request("GET", f"/matters/{matter_id}")

    async def create_matter(self, matter: dict[str, Any]) -> dict[str, Any]:
        """Open a new matter."""
        return await self._request("POST", "/matters", json=matter)

    async def update_matter(self, matter_id: int, updates: dict[str, Any]) -> dict[str, Any]:
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
        filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """List/search client accounts."""
        odata: dict[str, Any] = {"top": top, "skip": skip}
        if orderby:
            odata["orderby"] = orderby
        if filter:
            odata["filter"] = filter
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        if email:
            params["email"] = email
        return await self._request("GET", "/accounts", params=params, odata=odata)

    async def get_account(self, account_id: int) -> dict[str, Any]:
        """Fetch a single client account."""
        return await self._request("GET", f"/accounts/{account_id}")

    async def create_account(self, account: dict[str, Any]) -> dict[str, Any]:
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
    ) -> list[dict[str, Any]]:
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

    async def create_contact(self, contact: dict[str, Any]) -> dict[str, Any]:
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
    ) -> list[dict[str, Any]]:
        """List time entries. The big one — captures billable hours across the firm.

        ``start_date``/``end_date`` are ISO-8601 strings (e.g. ``"2026-01-01"``).
        ``billable`` filters to billable or non-billable entries only.
        """
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

    async def get_time_entry(self, time_entry_id: int) -> dict[str, Any]:
        """Fetch a single time entry."""
        return await self._request("GET", f"/timeentries/{time_entry_id}")

    async def create_time_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Log a billable (or non-billable) time entry.

        Minimum required fields: ``matterId``, ``userId``, ``date``, ``hours``,
        ``description``. For billable entries also include ``rate`` and ``billable: true``.
        """
        return await self._request("POST", "/timeentries", json=entry)

    async def update_time_entry(self, time_entry_id: int, updates: dict[str, Any]) -> dict[str, Any]:
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
    ) -> list[dict[str, Any]]:
        """List invoices. ``status`` typically: ``draft``, ``sent``, ``paid``,
        ``partially_paid``, ``void``, ``overdue``."""
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

    async def get_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Fetch a single invoice with line items."""
        return await self._request("GET", f"/invoices/{invoice_id}")

    async def create_invoice(self, invoice: dict[str, Any]) -> dict[str, Any]:
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
    ) -> list[dict[str, Any]]:
        """List activities (calls, emails, meetings, etc.) on matters/accounts."""
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

    async def create_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
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
    ) -> list[dict[str, Any]]:
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

    async def create_task(self, task: dict[str, Any]) -> dict[str, Any]:
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
    ) -> list[dict[str, Any]]:
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

    async def create_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Create a new calendar event."""
        return await self._request("POST", "/calendarevents", json=event)

    # ----- Users (firm staff) ------------------------------------------------

    async def list_users(self) -> list[dict[str, Any]]:
        """List firm users (attorneys, paralegals, staff)."""
        return await self._request("GET", "/users")

    # ----- Reference data ----------------------------------------------------

    async def list_practice_areas(self) -> list[dict[str, Any]]:
        """List practice areas defined for the firm."""
        return await self._request("GET", "/practiceareas")

    async def list_expense_categories(self) -> list[dict[str, Any]]:
        """List expense categories."""
        return await self._request("GET", "/expensecategories")
