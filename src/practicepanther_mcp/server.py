"""PracticePanther MCP server.

Exposes PracticePanther's REST + OData API as MCP tools so Claude / Cursor /
any MCP client can read matters (cases), client accounts, contacts, time
entries (billable hours), invoices, activities, tasks, calendar events — and
create new records.

Quick start:
    pip install -e .
    practicepanther-mcp-auth --client-id <id> --client-secret <secret>
        # opens browser, completes OAuth, prints env vars to set
    export PRACTICEPANTHER_ACCESS_TOKEN=...
    export PRACTICEPANTHER_REFRESH_TOKEN=...
    export PRACTICEPANTHER_CLIENT_ID=...
    export PRACTICEPANTHER_CLIENT_SECRET=...
    practicepanther_mcp
"""
from __future__ import annotations

import json
import sys
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from .client import PracticePantherClient
from .exceptions import (
    PracticePantherAPIError,
    PracticePantherAuthError,
    PracticePantherConnectionError,
    PracticePantherError,
    PracticePantherNotFoundError,
    PracticePantherRateLimitError,
    PracticePantherRefreshTokenExpiredError,
)

log = structlog.get_logger(__name__)


def _format_error(e: Exception) -> str:
    if isinstance(e, PracticePantherRefreshTokenExpiredError):
        return (
            "PracticePanther refresh token expired or revoked. Run "
            "`practicepanther-mcp-auth --client-id <id> --client-secret <secret>` "
            "to obtain a new one."
        )
    if isinstance(e, PracticePantherAuthError):
        return (
            "Authentication failed against PracticePanther. Run "
            "`practicepanther-mcp-auth` to refresh your tokens, or check "
            "PRACTICEPANTHER_ACCESS_TOKEN / PRACTICEPANTHER_REFRESH_TOKEN / "
            "PRACTICEPANTHER_CLIENT_ID / PRACTICEPANTHER_CLIENT_SECRET."
        )
    if isinstance(e, PracticePantherNotFoundError):
        return f"Resource not found: {e}"
    if isinstance(e, PracticePantherRateLimitError):
        wait = f" Retry in {e.retry_after}s." if e.retry_after else ""
        return f"PracticePanther rate limit hit.{wait} Slow down."
    if isinstance(e, PracticePantherConnectionError):
        return f"Network failure talking to PracticePanther: {e}"
    if isinstance(e, PracticePantherAPIError):
        request_id = f" (request_id: {e.request_id})" if e.request_id else ""
        return f"PracticePanther API error (HTTP {e.http_status}){request_id}: {e}"
    if isinstance(e, PracticePantherError):
        return f"PracticePanther error: {e}"
    return f"Unexpected error: {e!r}"


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


mcp = FastMCP(
    "practicepanther_mcp",
    instructions=(
        "Tools for PracticePanther — cloud-based legal practice management. "
        "Read and create matters (cases), client accounts, contacts, time "
        "entries (billable hours), invoices, activities, tasks, calendar "
        "events, and reference data. OAuth 2 authorization-code grant; access "
        "tokens are cached in-process and auto-refreshed from a 60-day refresh "
        "token before each expiry."
    ),
)


def _client() -> PracticePantherClient:
    return PracticePantherClient()


# ----- Matter tools ---------------------------------------------------------


@mcp.tool()
async def find_matters(
    account_id: int | None = None,
    status: str | None = None,
    practice_area_id: int | None = None,
    responsible_attorney_id: int | None = None,
    top: int = 50,
    skip: int = 0,
    orderby: str | None = None,
    odata_filter: str | None = None,
    select: str | None = None,
) -> str:
    """Search matters (cases). Optional OData ``orderby``, ``filter``, ``select``
    let you write rich queries (e.g. ``orderby="openDate desc"``,
    ``odata_filter="contains(displayName,'Smith')"``)."""
    try:
        return _json(await _client().find_matters(
            account_id=account_id, status=status,
            practice_area_id=practice_area_id,
            responsible_attorney_id=responsible_attorney_id,
            top=top, skip=skip, orderby=orderby,
            odata_filter=odata_filter, select=select,
        ))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def get_matter(matter_id: int) -> str:
    """Fetch a single matter with full detail (parties, practice area, status, etc.)."""
    try:
        return _json(await _client().get_matter(matter_id))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def create_matter(matter_json: str) -> str:
    """Open a new matter. ``matter_json`` is a JSON object string. Minimum
    required fields: ``displayName``, ``accountId``, ``practiceAreaId``,
    ``responsibleAttorneyId``, ``openDate``."""
    try:
        data = json.loads(matter_json)
        if not isinstance(data, dict):
            return "matter_json must decode to a JSON object."
        return _json(await _client().create_matter(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in matter_json: {e}"
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def update_matter(matter_id: int, updates_json: str) -> str:
    """Patch fields on a matter. ``updates_json`` is a JSON object with only the
    fields you want to change (e.g. ``'{"status":"closed"}'``)."""
    try:
        updates = json.loads(updates_json)
        if not isinstance(updates, dict):
            return "updates_json must decode to a JSON object."
        return _json(await _client().update_matter(matter_id, updates))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in updates_json: {e}"
    except PracticePantherError as e:
        return _format_error(e)


# ----- Account (client) tools ----------------------------------------------


@mcp.tool()
async def find_accounts(
    name: str | None = None,
    email: str | None = None,
    top: int = 50,
    skip: int = 0,
    orderby: str | None = None,
    odata_filter: str | None = None,
) -> str:
    """Search client accounts by name or email."""
    try:
        return _json(await _client().find_accounts(
            name=name, email=email, top=top, skip=skip,
            orderby=orderby, odata_filter=odata_filter,
        ))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def get_account(account_id: int) -> str:
    """Fetch a single client account with full detail."""
    try:
        return _json(await _client().get_account(account_id))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def create_account(account_json: str) -> str:
    """Create a new client account. ``account_json`` is a JSON object string.
    Minimum required: ``displayName``."""
    try:
        data = json.loads(account_json)
        if not isinstance(data, dict):
            return "account_json must decode to a JSON object."
        return _json(await _client().create_account(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in account_json: {e}"
    except PracticePantherError as e:
        return _format_error(e)


# ----- Contact tools --------------------------------------------------------


@mcp.tool()
async def find_contacts(
    account_id: int | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    top: int = 50,
    skip: int = 0,
) -> str:
    """List contacts associated with accounts (e.g. co-counsel, witnesses)."""
    try:
        return _json(await _client().find_contacts(
            account_id=account_id, first_name=first_name, last_name=last_name,
            email=email, top=top, skip=skip,
        ))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def create_contact(contact_json: str) -> str:
    """Create a new contact. ``contact_json`` is a JSON object string."""
    try:
        data = json.loads(contact_json)
        if not isinstance(data, dict):
            return "contact_json must decode to a JSON object."
        return _json(await _client().create_contact(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in contact_json: {e}"
    except PracticePantherError as e:
        return _format_error(e)


# ----- Time-entry tools (the killer feature) -------------------------------


@mcp.tool()
async def find_time_entries(
    matter_id: int | None = None,
    user_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    billable: bool | None = None,
    top: int = 50,
    skip: int = 0,
    orderby: str | None = None,
) -> str:
    """List time entries (billable hours). Use ``start_date`` + ``end_date`` for
    a date range (ISO-8601, e.g. ``"2026-01-01"``), ``matter_id`` for a single
    case, ``user_id`` for a single attorney, ``billable`` to filter."""
    try:
        return _json(await _client().find_time_entries(
            matter_id=matter_id, user_id=user_id,
            start_date=start_date, end_date=end_date, billable=billable,
            top=top, skip=skip, orderby=orderby,
        ))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def get_time_entry(time_entry_id: int) -> str:
    """Fetch a single time entry."""
    try:
        return _json(await _client().get_time_entry(time_entry_id))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def create_time_entry(entry_json: str) -> str:
    """Log a billable or non-billable time entry. ``entry_json`` is a JSON object
    string. Required: ``matterId``, ``userId``, ``date`` (YYYY-MM-DD),
    ``hours``, ``description``. For billable entries also include ``rate`` and
    ``billable: true``."""
    try:
        data = json.loads(entry_json)
        if not isinstance(data, dict):
            return "entry_json must decode to a JSON object."
        return _json(await _client().create_time_entry(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in entry_json: {e}"
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def update_time_entry(time_entry_id: int, updates_json: str) -> str:
    """Patch fields on a time entry (e.g. add narrative, adjust hours, mark billed)."""
    try:
        updates = json.loads(updates_json)
        if not isinstance(updates, dict):
            return "updates_json must decode to a JSON object."
        return _json(await _client().update_time_entry(time_entry_id, updates))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in updates_json: {e}"
    except PracticePantherError as e:
        return _format_error(e)


# ----- Invoice tools --------------------------------------------------------


@mcp.tool()
async def find_invoices(
    account_id: int | None = None,
    matter_id: int | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    top: int = 50,
    skip: int = 0,
    orderby: str | None = None,
) -> str:
    """List invoices. Filter by ``status`` (``draft``, ``sent``, ``paid``,
    ``partially_paid``, ``void``, ``overdue``) or by matter/account/date range."""
    try:
        return _json(await _client().find_invoices(
            account_id=account_id, matter_id=matter_id, status=status,
            start_date=start_date, end_date=end_date,
            top=top, skip=skip, orderby=orderby,
        ))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def get_invoice(invoice_id: int) -> str:
    """Fetch a single invoice with line items."""
    try:
        return _json(await _client().get_invoice(invoice_id))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def create_invoice(invoice_json: str) -> str:
    """Create a new invoice. ``invoice_json`` is a JSON object string."""
    try:
        data = json.loads(invoice_json)
        if not isinstance(data, dict):
            return "invoice_json must decode to a JSON object."
        return _json(await _client().create_invoice(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in invoice_json: {e}"
    except PracticePantherError as e:
        return _format_error(e)


# ----- Activity / task / event tools ---------------------------------------


@mcp.tool()
async def find_activities(
    matter_id: int | None = None,
    account_id: int | None = None,
    activity_type: str | None = None,
    top: int = 50,
    skip: int = 0,
) -> str:
    """List activities (calls, emails, meetings) on matters/accounts."""
    try:
        return _json(await _client().find_activities(
            matter_id=matter_id, account_id=account_id,
            activity_type=activity_type, top=top, skip=skip,
        ))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def create_activity(activity_json: str) -> str:
    """Log a new activity (call, email, meeting) against a matter."""
    try:
        data = json.loads(activity_json)
        if not isinstance(data, dict):
            return "activity_json must decode to a JSON object."
        return _json(await _client().create_activity(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in activity_json: {e}"
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def find_tasks(
    matter_id: int | None = None,
    assignee_id: int | None = None,
    status: str | None = None,
    top: int = 50,
    skip: int = 0,
) -> str:
    """List tasks (to-dos) on matters."""
    try:
        return _json(await _client().find_tasks(
            matter_id=matter_id, assignee_id=assignee_id,
            status=status, top=top, skip=skip,
        ))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def create_task(task_json: str) -> str:
    """Create a new task. ``task_json`` is a JSON object string."""
    try:
        data = json.loads(task_json)
        if not isinstance(data, dict):
            return "task_json must decode to a JSON object."
        return _json(await _client().create_task(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in task_json: {e}"
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def find_events(
    user_id: int | None = None,
    matter_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    top: int = 50,
    skip: int = 0,
) -> str:
    """List calendar events (hearings, deadlines, meetings)."""
    try:
        return _json(await _client().find_events(
            user_id=user_id, matter_id=matter_id,
            start_date=start_date, end_date=end_date,
            top=top, skip=skip,
        ))
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def create_event(event_json: str) -> str:
    """Create a new calendar event. ``event_json`` is a JSON object string."""
    try:
        data = json.loads(event_json)
        if not isinstance(data, dict):
            return "event_json must decode to a JSON object."
        return _json(await _client().create_event(data))
    except json.JSONDecodeError as e:
        return f"Invalid JSON in event_json: {e}"
    except PracticePantherError as e:
        return _format_error(e)


# ----- Reference / diagnostic -----------------------------------------------


@mcp.tool()
async def list_users() -> str:
    """List firm users (attorneys, paralegals, staff). Use to look up IDs for
    ``responsible_attorney_id``, ``user_id``, ``assignee_id`` in other tools."""
    try:
        return _json(await _client().list_users())
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def list_practice_areas() -> str:
    """List practice areas defined for the firm (e.g. Family Law, Personal Injury)."""
    try:
        return _json(await _client().list_practice_areas())
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def list_expense_categories() -> str:
    """List expense categories (Filing Fees, Travel, Copying, etc.)."""
    try:
        return _json(await _client().list_expense_categories())
    except PracticePantherError as e:
        return _format_error(e)


@mcp.tool()
async def health_check() -> str:
    """Verify credentials by listing firm users. If this works, all other tools
    should work too."""
    try:
        await _client().list_users()
        return _json({"status": "ok"})
    except PracticePantherError as e:
        return _format_error(e)


def main() -> None:
    try:
        mcp.run()
    except PracticePantherAuthError as e:
        log.error("server.auth_failed_on_start", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
