"""Tests for the Nager.Date API client: caching, error mapping, normalization."""

import httpx
import pytest

from nager_date_mcp.client import NagerDateClient, _normalize_country_code


def make_client(handler) -> NagerDateClient:
    """Build a client backed by an httpx.MockTransport."""
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="https://date.nager.at/api/v3", transport=transport
    )
    return NagerDateClient(client=http)


# -- Cache behaviour -------------------------------------------------------


async def test_cache_hit_avoids_second_request():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=[{"name": "New Year"}])

    client = make_client(handler)
    first = await client.get("/NextPublicHolidays/US", cache_ttl=3600)
    second = await client.get("/NextPublicHolidays/US", cache_ttl=3600)

    assert first == second == [{"name": "New Year"}]
    assert calls["n"] == 1  # second call served from cache


async def test_cache_miss_when_ttl_expired(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"n": calls["n"]})

    client = make_client(handler)

    import nager_date_mcp.client as client_mod

    fake_time = {"t": 1000.0}
    monkeypatch.setattr(
        client_mod.time, "monotonic", lambda: fake_time["t"]
    )

    await client.get("/x", cache_ttl=60)
    fake_time["t"] += 61  # exceed TTL
    await client.get("/x", cache_ttl=60)

    assert calls["n"] == 2


async def test_ttl_zero_bypasses_cache():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    await client.get("/x", cache_ttl=0)
    await client.get("/x", cache_ttl=0)
    assert calls["n"] == 2


# -- Error mapping ---------------------------------------------------------


async def test_404_maps_to_country_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = make_client(handler)
    with pytest.raises(ValueError, match="Country not found: ZZ"):
        await client.get_holidays(2024, "ZZ")


async def test_400_maps_to_bad_request_with_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Invalid year supplied")

    client = make_client(handler)
    with pytest.raises(ValueError, match="Invalid year supplied"):
        await client.get_holidays(999, "US")


async def test_500_raises_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.get("/x", cache_ttl=0)


# -- Country code normalization -------------------------------------------


def test_normalize_country_code():
    assert _normalize_country_code("us") == "US"
    assert _normalize_country_code(" gb ") == "GB"
    with pytest.raises(ValueError):
        _normalize_country_code("")


async def test_endpoint_uses_uppercase_code_in_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=[])

    client = make_client(handler)
    await client.get_holidays(2024, "us")
    assert seen["path"].endswith("/PublicHolidays/2024/US")


# -- is_today_holiday 204 handling ----------------------------------------


async def test_is_today_holiday_204_is_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    client = make_client(handler)
    assert await client.is_today_holiday("US") is False


async def test_is_today_holiday_200_is_true():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "Independence Day"})

    client = make_client(handler)
    assert await client.is_today_holiday("us") is True
