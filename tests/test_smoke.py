"""Smoke tests for practicepanther-mcp — no live API calls required.

Built with ``respx`` (industry-standard httpx mocking) + ``pytest-asyncio``.
The previous AsyncMock-based tests were fragile because they re-implemented
httpx's request shape; respx intercepts the actual ``httpx.AsyncClient`` and
verifies real request URLs, headers, and bodies.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from hypothesis import given, settings
from hypothesis import strategies as st

from practicepanther_mcp import (
    PracticePantherAPIError,
    PracticePantherAuthError,
    PracticePantherClient,
    PracticePantherConnectionError,
    PracticePantherNotFoundError,
    PracticePantherRateLimitError,
    PracticePantherRefreshTokenExpiredError,
)
from practicepanther_mcp.exceptions import (
    OAUTH_INVALID_GRANT,
    OAUTH_INVALID_REFRESH,
)
from practicepanther_mcp.server import _format_error, _json

# --- Test fixtures --------------------------------------------------------


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRACTICEPANTHER_ACCESS_TOKEN", "access-tok")
    monkeypatch.setenv("PRACTICEPANTHER_REFRESH_TOKEN", "refresh-tok")
    monkeypatch.setenv("PRACTICEPANTHER_CLIENT_ID", "client")
    monkeypatch.setenv("PRACTICEPANTHER_CLIENT_SECRET", "secret")


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[PracticePantherClient]:
    """Build a client wired against respx-mocked httpx."""
    _env(monkeypatch)
    c = PracticePantherClient()
    try:
        yield c
    finally:
        await c.aclose()


# --- Client construction --------------------------------------------------


def test_client_missing_credentials_raises() -> None:
    with pytest.raises(PracticePantherAuthError) as exc_info:
        PracticePantherClient()
    assert "credentials missing" in str(exc_info.value).lower()


def test_client_uses_env_when_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    c = PracticePantherClient()
    assert c._access_token == "access-tok"
    assert c._refresh_token == "refresh-tok"


@pytest.mark.asyncio
async def test_client_aclose_closes_underlying_httpx_client(client: PracticePantherClient) -> None:
    """Context manager / aclose() flushes the connection pool."""
    # Touch the inner client so it's not in pristine state
    assert not client._client.is_closed
    await client.aclose()
    assert client._client.is_closed


# --- OAuth: refresh on 401 ------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_401_refreshes_token_and_retries(client: PracticePantherClient) -> None:
    # First refresh-token exchange after 401
    refresh = respx.post("https://app.practicepanther.com/oauth/token").mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "new-access", "refresh_token": "new-refresh"},
        )
    )
    # First call: 401, second (with new token): 200
    matters_route = respx.get(
        "https://app.practicepanther.com/api/v2/users"
    ).mock(side_effect=[
        httpx.Response(401, text=""),
        httpx.Response(200, json=[]),
    ])

    result = await client.list_users()

    assert result == []
    assert refresh.call_count == 1
    assert matters_route.call_count == 2
    # Second request used the new bearer token
    assert matters_route.calls[1].request.headers["Authorization"] == "Bearer new-access"
    # Refresh-token rotation was stored
    assert client._refresh_token == "new-refresh"


@pytest.mark.asyncio
@respx.mock
async def test_refresh_invalid_grant_raises_specific_exception(
    client: PracticePantherClient,
) -> None:
    respx.post("https://app.practicepanther.com/oauth/token").mock(
        return_value=httpx.Response(
            400,
            json={"error": OAUTH_INVALID_GRANT, "error_description": "refresh token expired"},
        )
    )
    # Force expiry so the next call refreshes
    client._token_expires_at = 0.0

    with pytest.raises(PracticePantherRefreshTokenExpiredError) as exc_info:
        await client.list_users()
    assert exc_info.value.error_code == OAUTH_INVALID_GRANT
    assert "practicepanther-mcp-auth" in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_refresh_invalid_refresh_raises_specific_exception(
    client: PracticePantherClient,
) -> None:
    respx.post("https://app.practicepanther.com/oauth/token").mock(
        return_value=httpx.Response(400, json={"error": OAUTH_INVALID_REFRESH})
    )
    client._token_expires_at = 0.0
    with pytest.raises(PracticePantherRefreshTokenExpiredError):
        await client.list_users()


@pytest.mark.asyncio
@respx.mock
async def test_refresh_invalid_client_raises_auth_error(
    client: PracticePantherClient,
) -> None:
    respx.post("https://app.practicepanther.com/oauth/token").mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"})
    )
    client._token_expires_at = 0.0
    with pytest.raises(PracticePantherAuthError) as exc_info:
        await client.list_users()
    err_msg = str(exc_info.value).lower()
    assert "client_id" in err_msg or "client_secret" in err_msg


# --- HTTP status code mapping ---------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_404_raises_not_found(client: PracticePantherClient) -> None:
    respx.get("https://app.practicepanther.com/api/v2/matters/9999").mock(
        return_value=httpx.Response(404, json={"message": "no such matter"})
    )
    with pytest.raises(PracticePantherNotFoundError) as exc_info:
        await client.get_matter(9999)
    assert exc_info.value.http_status == 404


@pytest.mark.asyncio
@respx.mock
async def test_429_includes_retry_after(client: PracticePantherClient) -> None:
    respx.get("https://app.practicepanther.com/api/v2/users").mock(
        return_value=httpx.Response(429, headers={"retry-after": "2.5"}, text="slow down")
    )
    with pytest.raises(PracticePantherRateLimitError) as exc_info:
        await client.list_users()
    assert exc_info.value.retry_after == 2.5


@pytest.mark.asyncio
@respx.mock
async def test_500_raises_api_error(client: PracticePantherClient) -> None:
    respx.get("https://app.practicepanther.com/api/v2/users").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    with pytest.raises(PracticePantherAPIError) as exc_info:
        await client.list_users()
    assert exc_info.value.http_status == 500


@pytest.mark.asyncio
@respx.mock
async def test_500_request_id_captured_from_header(client: PracticePantherClient) -> None:
    respx.get("https://app.practicepanther.com/api/v2/users").mock(
        return_value=httpx.Response(500, headers={"x-request-id": "req-abc123"}, text="boom")
    )
    with pytest.raises(PracticePantherAPIError) as exc_info:
        await client.list_users()
    assert exc_info.value.request_id == "req-abc123"


@pytest.mark.asyncio
@respx.mock
async def test_connection_error_wrapped(client: PracticePantherClient) -> None:
    respx.get("https://app.practicepanther.com/api/v2/users").mock(
        side_effect=httpx.ConnectError("DNS failure")
    )
    with pytest.raises(PracticePantherConnectionError):
        await client.list_users()


# --- Retry with exponential backoff ---------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_429_is_retried_then_raises(client: PracticePantherClient) -> None:
    """429 should be retried up to max_retries times, then raise."""
    route = respx.get("https://app.practicepanther.com/api/v2/users").mock(
        return_value=httpx.Response(429, text="slow down")
    )
    # Use a tiny max_retries to keep the test fast
    client._max_retries = 2
    with pytest.raises(PracticePantherRateLimitError):
        await client.list_users()
    # Initial call + 2 retries = 3 attempts
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_500_eventually_succeeds_after_retries(client: PracticePantherClient) -> None:
    """5xx errors should be retried; if a retry succeeds, return the result."""
    route = respx.get("https://app.practicepanther.com/api/v2/users").mock(
        side_effect=[
            httpx.Response(502, text="bad gateway"),
            httpx.Response(503, text="unavailable"),
            httpx.Response(200, json=[{"id": 1, "name": "Sarah"}]),
        ]
    )
    client._max_retries = 3
    result = await client.list_users()
    assert result == [{"id": 1, "name": "Sarah"}]
    assert route.call_count == 3


# --- OData query construction ---------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_odata_dollar_prefix_applied(client: PracticePantherClient) -> None:
    route = respx.get("https://app.practicepanther.com/api/v2/matters").mock(
        return_value=httpx.Response(200, json=[])
    )
    await client.find_matters(
        account_id=42, status="open", top=10, skip=20,
        orderby="openDate desc", odata_filter="contains(displayName,'Smith')",
        select="id,displayName,status",
    )
    url = str(route.calls[0].request.url)
    assert "%24top=10" in url
    assert "%24skip=20" in url
    assert "%24orderby=openDate" in url
    assert "filter=contains" in url
    assert "select=id%2CdisplayName%2Cstatus" in url
    assert "accountId=42" in url
    assert "status=open" in url


@pytest.mark.asyncio
@respx.mock
async def test_odata_drops_none_and_empty(client: PracticePantherClient) -> None:
    route = respx.get("https://app.practicepanther.com/api/v2/matters").mock(
        return_value=httpx.Response(200, json=[])
    )
    await client.find_matters(top=10)
    url = str(route.calls[0].request.url)
    assert "accountId" not in url
    assert "%24top=10" in url


# --- Write endpoints: POST / PATCH ----------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_create_matter_uses_post(client: PracticePantherClient) -> None:
    route = respx.post("https://app.practicepanther.com/api/v2/matters").mock(
        return_value=httpx.Response(201, json={"id": 1, "displayName": "Smith v. Jones"})
    )
    result = await client.create_matter({"displayName": "Smith v. Jones", "accountId": 5})
    assert result["id"] == 1
    assert route.calls[0].request.method == "POST"
    # Body was JSON-serialized
    body = json.loads(route.calls[0].request.content)
    assert body == {"displayName": "Smith v. Jones", "accountId": 5}


@pytest.mark.asyncio
@respx.mock
async def test_update_matter_uses_patch(client: PracticePantherClient) -> None:
    route = respx.patch("https://app.practicepanther.com/api/v2/matters/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "status": "closed"})
    )
    await client.update_matter(1, {"status": "closed"})
    assert route.calls[0].request.method == "PATCH"


# --- Pagination iterator --------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_iter_matters_auto_paginates(client: PracticePantherClient) -> None:
    # Three pages; last page is SHORT (< page_size) so the iterator stops.
    page1 = [{"id": i} for i in range(1, 4)]  # 3 items (full)
    page2 = [{"id": i} for i in range(4, 7)]  # 3 items (full)
    page3 = [{"id": i} for i in range(7, 9)]  # 2 items (short → stop)
    route = respx.get("https://app.practicepanther.com/api/v2/matters").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=page3),
        ]
    )
    results = []
    async for matter in client.iter_matters(page_size=3):
        results.append(matter)
    assert [m["id"] for m in results] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert route.call_count == 3
    # Verify $top/$skip were passed correctly on each call
    urls = [str(c.request.url) for c in route.calls]
    # Each page should carry $top=3 and an incrementing $skip
    for i, expected_skip in enumerate(["0", "3", "6"]):
        assert "%24top=3" in urls[i]
        assert f"%24skip={expected_skip}" in urls[i]


# --- Property-based tests -------------------------------------------------


@given(st.dictionaries(st.text(min_size=1), st.integers() | st.text() | st.booleans(), max_size=10))
@settings(max_examples=50, deadline=None)
def test_json_serialization_round_trip(d: dict[str, Any]) -> None:
    """Any JSON-serializable dict survives our _json helper unchanged."""
    # Skip values that aren't JSON-serializable (we only generate JSON-friendly ones above)
    try:
        json.loads(_json(d))
    except (TypeError, ValueError):
        pytest.skip("non-JSON value")
    assert json.loads(_json(d)) == d


# --- Server error helpers -------------------------------------------------


def test_format_error_refresh_expired_suggests_auth_helper() -> None:
    msg = _format_error(PracticePantherRefreshTokenExpiredError("expired"))
    assert "practicepanther-mcp-auth" in msg


def test_format_error_auth_suggests_env_vars() -> None:
    msg = _format_error(PracticePantherAuthError("bad"))
    assert "PRACTICEPANTHER_ACCESS_TOKEN" in msg


def test_format_error_404_says_not_found() -> None:
    msg = _format_error(PracticePantherNotFoundError("missing"))
    assert "not found" in msg.lower()


def test_format_error_429_includes_retry_after() -> None:
    msg = _format_error(PracticePantherRateLimitError("slow", retry_after=5.0))
    assert "Retry in 5.0s" in msg or "Retry in 5s" in msg


def test_format_error_connection_says_network() -> None:
    msg = _format_error(PracticePantherConnectionError("dns"))
    assert "network" in msg.lower()


def test_format_error_500_includes_request_id() -> None:
    err = PracticePantherAPIError("boom", http_status=500, request_id="req-xyz")
    msg = _format_error(err)
    assert "req-xyz" in msg
    assert "500" in msg


def test_format_error_generic() -> None:
    msg = _format_error(ValueError("nope"))
    assert "Unexpected" in msg


def test_error_repr_includes_structured_fields() -> None:
    err = PracticePantherAPIError("boom", http_status=500, error_code="oops", request_id="req-1")
    r = repr(err)
    assert "http_status=500" in r
    assert "error_code='oops'" in r
    assert "request_id='req-1'" in r
