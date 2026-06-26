"""Smoke tests for practicepanther-mcp — no live API calls required."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from practicepanther_mcp import (
    PracticePantherAPIError,
    PracticePantherAuthError,
    PracticePantherClient,
)
from practicepanther_mcp.server import _format_error, _json


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRACTICEPANTHER_ACCESS_TOKEN", "access-tok")
    monkeypatch.setenv("PRACTICEPANTHER_REFRESH_TOKEN", "refresh-tok")
    monkeypatch.setenv("PRACTICEPANTHER_CLIENT_ID", "client")
    monkeypatch.setenv("PRACTICEPANTHER_CLIENT_SECRET", "secret")


# ----- Client construction -------------------------------------------------


def test_client_missing_credentials_raises() -> None:
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(PracticePantherAuthError):
            PracticePantherClient()


def test_client_uses_env_when_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = PracticePantherClient()
    assert client._access_token == "access-tok"
    assert client._refresh_token == "refresh-tok"
    assert client._client_id == "client"


# ----- Token refresh + retry -----------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = PracticePantherClient()

    # First token refresh after 401
    refresh_resp = AsyncMock()
    refresh_resp.status_code = 200
    refresh_resp.json = lambda: {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
    }
    refresh_resp.text = ""

    # First call: 401, second call (with new token): 200
    bad = AsyncMock()
    bad.status_code = 401
    bad.text = ""
    ok = AsyncMock()
    ok.status_code = 200
    ok.text = "[]"
    ok.json = lambda: []

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.post = AsyncMock(return_value=refresh_resp)
    fake_http.request = AsyncMock(side_effect=[bad, ok])

    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        result = await client.list_users()
        assert result == []
        # Token refreshed exactly once after 401
        assert fake_http.post.call_count == 1
        # After refresh, the second request used the new bearer token
        second_call = fake_http.request.call_args_list[1]
        assert second_call.kwargs["headers"]["Authorization"] == "Bearer new-access"
        # New refresh token was stored
        assert client._refresh_token == "new-refresh"


@pytest.mark.asyncio
async def test_refresh_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = PracticePantherClient()
    # Force expiry so the next call refreshes
    client._token_expires_at = 0.0

    refresh_resp = AsyncMock()
    refresh_resp.status_code = 400
    refresh_resp.json = lambda: {"error": "invalid_grant"}
    refresh_resp.text = '{"error":"invalid_grant"}'

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.post = AsyncMock(return_value=refresh_resp)

    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        with pytest.raises(PracticePantherAuthError):
            await client.list_users()


# ----- Request error handling ----------------------------------------------


@pytest.mark.asyncio
async def test_403_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = PracticePantherClient()

    forbidden = AsyncMock()
    forbidden.status_code = 403
    forbidden.text = "forbidden"
    forbidden.json = lambda: (_ for _ in ()).throw(ValueError("not json"))

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=forbidden)

    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        with pytest.raises(PracticePantherAuthError):
            await client.list_users()


@pytest.mark.asyncio
async def test_429_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = PracticePantherClient()

    limited = AsyncMock()
    limited.status_code = 429
    limited.text = "rate limited"
    limited.json = lambda: (_ for _ in ()).throw(ValueError("not json"))

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=limited)

    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        with pytest.raises(PracticePantherAPIError):
            await client.list_users()


@pytest.mark.asyncio
async def test_500_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = PracticePantherClient()

    boom = AsyncMock()
    boom.status_code = 500
    boom.text = "internal error"
    boom.json = lambda: {"message": "internal error"}

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=boom)

    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        with pytest.raises(PracticePantherAPIError) as exc_info:
            await client.list_users()
        assert exc_info.value.status_code == 500


# ----- OData query construction -------------------------------------------


@pytest.mark.asyncio
async def test_odata_params_become_dollar_prefixed(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = PracticePantherClient()

    ok = AsyncMock()
    ok.status_code = 200
    ok.text = "[]"
    ok.json = lambda: []

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=ok)

    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        await client.find_matters(
            account_id=42, status="open", top=10, skip=20,
            orderby="openDate desc", filter="contains(displayName,'Smith')",
            select="id,displayName,status",
        )
        url = fake_http.request.call_args.args[1]
        # OData keys should carry the $ prefix
        assert "%24top=10" in url
        assert "%24skip=20" in url
        assert "orderby=openDate" in url and ("desc" in url)
        assert "filter=contains" in url
        assert "select=id%2CdisplayName%2Cstatus" in url
        # Regular params (no $) stay as-is
        assert "accountId=42" in url
        assert "status=open" in url


@pytest.mark.asyncio
async def test_odata_drops_none_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = PracticePantherClient()

    ok = AsyncMock()
    ok.status_code = 200
    ok.text = "[]"
    ok.json = lambda: []

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=ok)

    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        await client.find_matters(top=10)
        url = fake_http.request.call_args.args[1]
        # None values must NOT appear in the query string
        assert "accountId" not in url
        assert "status" not in url
        assert "orderby" not in url.lower().replace("%24orderby", "")
        # But top/skip do
        assert "%24top=10" in url


# ----- Write endpoints issue POST/PATCH -----------------------------------


@pytest.mark.asyncio
async def test_create_matter_uses_post(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = PracticePantherClient()

    created = AsyncMock()
    created.status_code = 201
    created.text = '{"id": 1, "displayName": "Smith v. Jones"}'
    created.json = lambda: {"id": 1, "displayName": "Smith v. Jones"}

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=created)

    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        result = await client.create_matter({"displayName": "Smith v. Jones", "accountId": 5})
        assert result["id"] == 1
        method, url = fake_http.request.call_args.args[:2]
        assert method == "POST"
        assert url.endswith("/api/v2/matters")


@pytest.mark.asyncio
async def test_update_matter_uses_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    client = PracticePantherClient()

    patched = AsyncMock()
    patched.status_code = 200
    patched.text = '{"id": 1, "status": "closed"}'
    patched.json = lambda: {"id": 1, "status": "closed"}

    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=patched)

    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        await client.update_matter(1, {"status": "closed"})
        method, url = fake_http.request.call_args.args[:2]
        assert method == "PATCH"
        assert url.endswith("/api/v2/matters/1")


# ----- Server helpers ------------------------------------------------------


def test_format_error_auth() -> None:
    msg = _format_error(PracticePantherAuthError("nope"))
    assert "Authentication failed" in msg
    assert "practicepanther-mcp-auth" in msg


def test_format_error_api() -> None:
    msg = _format_error(PracticePantherAPIError("kaboom", 500, "body"))
    assert "API error" in msg
    assert "500" in msg


def test_format_error_generic() -> None:
    msg = _format_error(ValueError("nope"))
    assert "Unexpected" in msg


def test_json_serializes() -> None:
    assert json.loads(_json({"a": 1, "b": ["x", "y"]})) == {"a": 1, "b": ["x", "y"]}
