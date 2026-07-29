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


async def test_cache_is_bounded_evicts_oldest(monkeypatch):
    """Filling the cache past MAX_CACHE_SIZE evicts the oldest entries."""
    import nager_date_mcp.client as client_mod

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"name": "Holiday"}])

    fake_time = {"t": 1000.0}
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: fake_time["t"])

    client = make_client(handler)
    total = client_mod.MAX_CACHE_SIZE + 50
    for i in range(total):
        fake_time["t"] += 1.0  # strictly increasing timestamps
        await client.get(f"/path/{i}", cache_ttl=3600)

    # Cache never grows beyond the bound.
    assert len(client._cache) == client_mod.MAX_CACHE_SIZE
    # Oldest entries were evicted; newest retained.
    assert "/path/0" not in client._cache
    assert f"/path/{total - 1}" in client._cache


async def test_cache_returns_copies_not_references():
    """Mutating a returned result must not corrupt the cached data."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"name": "New Year", "date": "2024-01-01"}])

    client = make_client(handler)
    first = await client.get("/NextPublicHolidays/US", cache_ttl=3600)
    # Mutate the returned data (list + inner dict), as _enrich_holidays does.
    first[0]["day_of_week"] = "MUTATED"
    first.append({"name": "INJECTED"})

    second = await client.get("/NextPublicHolidays/US", cache_ttl=3600)
    assert second == [{"name": "New Year", "date": "2024-01-01"}]
    assert "day_of_week" not in second[0]
    assert len(second) == 1


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


async def test_is_today_holiday_204_is_none():
    """204 is ambiguous (no-data OR not-a-holiday) — client reports None."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    client = make_client(handler)
    assert await client.is_today_holiday("US") is None


async def test_is_today_holiday_200_is_true():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "Independence Day"})

    client = make_client(handler)
    assert await client.is_today_holiday("us") is True


async def test_is_today_holiday_200_empty_body_is_true():
    """The real API returns 200 with an EMPTY body on a holiday — must be True."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    client = make_client(handler)
    assert await client.is_today_holiday("PE") is True


# -- 400 error message extraction ---------------------------------------------


async def test_400_json_errors_body_produces_clean_message():
    body = {"errors": {"year": ["The specified year '1975' is not supported."]}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=body)

    client = make_client(handler)
    with pytest.raises(ValueError) as exc_info:
        await client.get_holidays(1975, "US")
    msg = str(exc_info.value)
    assert "The specified year '1975' is not supported." in msg
    # Must not dump the raw JSON structure.
    assert '{"errors"' not in msg
    assert '"year"' not in msg


async def test_400_non_json_body_falls_back_to_raw_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="plain text failure")

    client = make_client(handler)
    with pytest.raises(ValueError, match="plain text failure"):
        await client.get_holidays(999, "US")
