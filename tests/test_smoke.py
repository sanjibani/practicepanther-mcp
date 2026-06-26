"""Smoke tests for PracticePanther MCP MCP — no live API calls required."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from practicepanther_mcp import PracticepantherAPIError, PracticepantherAuthError, PracticepantherClient
from practicepanther_mcp.server import _format_error, _json


# ----- Client construction --------------------------------------------------


def test_client_missing_credentials_raises() -> None:
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(PracticepantherAuthError):
            PracticepantherClient()


def test_client_uses_env_when_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRACTICEPANTHER_USERNAME", "u")
    monkeypatch.setenv("PRACTICEPANTHER_PASSWORD", "p")
    client = PracticepantherClient()
    assert client._basic_auth


# ----- Async request mock ---------------------------------------------------


@pytest.mark.asyncio
async def test_401_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRACTICEPANTHER_USERNAME", "u")
    monkeypatch.setenv("PRACTICEPANTHER_PASSWORD", "p")
    fake_response = AsyncMock()
    fake_response.status_code = 401
    fake_response.text = ""
    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=fake_response)
    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        client = PracticepantherClient()
        with pytest.raises(PracticepantherAuthError):
            await client._request("GET", "/whatever")


@pytest.mark.asyncio
async def test_500_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRACTICEPANTHER_USERNAME", "u")
    monkeypatch.setenv("PRACTICEPANTHER_PASSWORD", "p")
    fake_response = AsyncMock()
    fake_response.status_code = 500
    fake_response.text = "kaboom"
    fake_response.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
    fake_http = AsyncMock()
    fake_http.__aenter__ = AsyncMock(return_value=fake_http)
    fake_http.__aexit__ = AsyncMock(return_value=None)
    fake_http.request = AsyncMock(return_value=fake_response)
    with patch("practicepanther_mcp.client.httpx.AsyncClient", return_value=fake_http):
        client = PracticepantherClient()
        with pytest.raises(PracticepantherAPIError):
            await client._request("GET", "/whatever")


# ----- Server helpers -------------------------------------------------------


def test_format_error_auth() -> None:
    msg = _format_error(PracticepantherAuthError("nope", 401))
    assert "Authentication" in msg


def test_format_error_generic() -> None:
    msg = _format_error(ValueError("nope"))
    assert "Unexpected" in msg


def test_json_serializes() -> None:
    out = _json({"a": 1, "b": [1, 2]})
    parsed = json.loads(out)
    assert parsed == {"a": 1, "b": [1, 2]}