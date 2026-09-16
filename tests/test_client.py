"""Tests for the Nager.Date API client: caching, error mapping, normalization.

T08 port of the legacy suite onto the stdlib seam (15/15 coverage kept by
name). The legacy mock transport + async client are replaced by ``FakeSeam``,
monkeypatched over ``nager_date_mcp.client.fetch_raw``, serving
SeamResponse-shaped fakes (transport.SeamResponse contract, T01). Assertions
are unchanged except where transport-shaped: sync defs, and
``urllib.error.HTTPError`` where legacy raised the transport's status error.
"""

import io
import json
import types
import urllib.error
import urllib.parse

import pytest

import nager_date_mcp.client as client_mod
from nager_date_mcp.client import NagerDateClient, _normalize_country_code


def seam_response(status=200, text="", url="https://date.nager.at/api/v3/x"):
    """Build a SeamResponse-shaped fake for the given request ``url``.

    ``request.url.path`` is derived honestly from the URL (urlsplit, keeping
    the /api/v3 prefix exactly as the real seam does) — the 404 branch parses
    it, so a fake that lied about the path would hide the prefix bug.
    ``json`` is a callable taking no args, mirroring the real SeamResponse.
    """

    def _raise_for_status():
        if status >= 400:
            raise urllib.error.HTTPError(
                url, status, "error",
                None,  # type: ignore[arg-type]  # hdrs unused by the fold
                io.BytesIO(text.encode("utf-8")),
            )

    return types.SimpleNamespace(
        status_code=status,
        text=text,
        json=lambda: json.loads(text),
        raise_for_status=_raise_for_status,
        request=types.SimpleNamespace(
            url=types.SimpleNamespace(path=urllib.parse.urlsplit(url).path)
        ),
    )


class FakeSeam:
    """Fake seam: replaces the legacy mock transport + async client.

    ``handler(url)`` returns a SeamResponse-shaped fake, or an exception
    instance which is raised so transport errors flow like the real seam.
    """

    def __init__(self, handler):
        self.handler, self.calls, self.urls = handler, 0, []

    def __call__(self, url, timeout=5.0):
        self.calls += 1
        self.urls.append(url)
        resp = self.handler(url)
        if isinstance(resp, Exception):
            raise resp
        return resp


def make_client(monkeypatch, handler) -> NagerDateClient:
    """Build a client backed by a fake seam (fetch_raw monkeypatched)."""
    monkeypatch.setattr(client_mod, "fetch_raw", FakeSeam(handler))
    return NagerDateClient()


# -- Cache behaviour -------------------------------------------------------


def test_cache_hit_avoids_second_request(monkeypatch):
    calls = {"n": 0}

    def handler(url):
        calls["n"] += 1
        return seam_response(200, json.dumps([{"name": "New Year"}]), url)

    client = make_client(monkeypatch, handler)
    first = client.get("/NextPublicHolidays/US", cache_ttl=3600)
    second = client.get("/NextPublicHolidays/US", cache_ttl=3600)

    assert first == second == [{"name": "New Year"}]
    assert calls["n"] == 1  # second call served from cache


def test_cache_miss_when_ttl_expired(monkeypatch):
    calls = {"n": 0}

    def handler(url):
        calls["n"] += 1
        return seam_response(200, json.dumps({"n": calls["n"]}), url)

    client = make_client(monkeypatch, handler)

    fake_time = {"t": 1000.0}
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: fake_time["t"])

    client.get("/x", cache_ttl=60)
    fake_time["t"] += 61  # exceed TTL
    client.get("/x", cache_ttl=60)

    assert calls["n"] == 2


def test_ttl_zero_bypasses_cache(monkeypatch):
    calls = {"n": 0}

    def handler(url):
        calls["n"] += 1
        return seam_response(200, json.dumps({"ok": True}), url)

    client = make_client(monkeypatch, handler)
    client.get("/x", cache_ttl=0)
    client.get("/x", cache_ttl=0)
    assert calls["n"] == 2


def test_cache_is_bounded_evicts_oldest(monkeypatch):
    """Filling the cache past MAX_CACHE_SIZE evicts the oldest entries."""

    def handler(url):
        return seam_response(200, json.dumps([{"name": "Holiday"}]), url)

    fake_time = {"t": 1000.0}
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: fake_time["t"])

    client = make_client(monkeypatch, handler)
    total = client_mod.MAX_CACHE_SIZE + 50
    for i in range(total):
        fake_time["t"] += 1.0  # strictly increasing timestamps
        client.get(f"/path/{i}", cache_ttl=3600)

    # Cache never grows beyond the bound.
    assert len(client._cache) == client_mod.MAX_CACHE_SIZE
    # Oldest entries were evicted; newest retained.
    assert "/path/0" not in client._cache
    assert f"/path/{total - 1}" in client._cache


def test_cache_returns_copies_not_references(monkeypatch):
    """Mutating a returned result must not corrupt the cached data."""

    def handler(url):
        return seam_response(
            200, json.dumps([{"name": "New Year", "date": "2024-01-01"}]), url
        )

    client = make_client(monkeypatch, handler)
    first = client.get("/NextPublicHolidays/US", cache_ttl=3600)
    # Mutate the returned data (list + inner dict), as _enrich_holidays does.
    first[0]["day_of_week"] = "MUTATED"
    first.append({"name": "INJECTED"})

    second = client.get("/NextPublicHolidays/US", cache_ttl=3600)
    assert second == [{"name": "New Year", "date": "2024-01-01"}]
    assert "day_of_week" not in second[0]
    assert len(second) == 1


# -- Error mapping ---------------------------------------------------------


def test_404_maps_to_country_not_found(monkeypatch):
    def handler(url):
        return seam_response(404, "not found", url)

    client = make_client(monkeypatch, handler)
    with pytest.raises(ValueError, match="Country not found: ZZ"):
        client.get_holidays(2024, "ZZ")


def test_400_maps_to_bad_request_with_detail(monkeypatch):
    def handler(url):
        return seam_response(400, "Invalid year supplied", url)

    client = make_client(monkeypatch, handler)
    with pytest.raises(ValueError, match="Invalid year supplied"):
        client.get_holidays(999, "US")


def test_500_raises_http_error(monkeypatch):
    """Legacy raised the transport's HTTPStatusError; the seam's
    raise_for_status analogue is urllib.error.HTTPError."""

    def handler(url):
        return seam_response(500, "boom", url)

    client = make_client(monkeypatch, handler)
    with pytest.raises(urllib.error.HTTPError):
        client.get("/x", cache_ttl=0)


# -- Country code normalization -------------------------------------------


def test_normalize_country_code():
    assert _normalize_country_code("us") == "US"
    assert _normalize_country_code(" gb ") == "GB"
    with pytest.raises(ValueError):
        _normalize_country_code("")


def test_endpoint_uses_uppercase_code_in_path(monkeypatch):
    seen = {}

    def handler(url):
        seen["path"] = urllib.parse.urlsplit(url).path
        return seam_response(200, json.dumps([]), url)

    client = make_client(monkeypatch, handler)
    client.get_holidays(2024, "us")
    assert seen["path"].endswith("/PublicHolidays/2024/US")


# -- is_today_holiday 204 handling ----------------------------------------


def test_is_today_holiday_204_is_none(monkeypatch):
    """204 is ambiguous (no-data OR not-a-holiday) — client reports None."""

    def handler(url):
        return seam_response(204, "", url)

    client = make_client(monkeypatch, handler)
    assert client.is_today_holiday("US") is None


def test_is_today_holiday_200_is_true(monkeypatch):
    def handler(url):
        return seam_response(200, json.dumps({"name": "Independence Day"}), url)

    client = make_client(monkeypatch, handler)
    assert client.is_today_holiday("us") is True


def test_is_today_holiday_200_empty_body_is_true(monkeypatch):
    """The real API returns 200 with an EMPTY body on a holiday — must be True."""

    def handler(url):
        return seam_response(200, "", url)

    client = make_client(monkeypatch, handler)
    assert client.is_today_holiday("PE") is True


# -- 400 error message extraction ---------------------------------------------


def test_400_json_errors_body_produces_clean_message(monkeypatch):
    body = {"errors": {"year": ["The specified year '1975' is not supported."]}}

    def handler(url):
        return seam_response(400, json.dumps(body), url)

    client = make_client(monkeypatch, handler)
    with pytest.raises(ValueError) as exc_info:
        client.get_holidays(1975, "US")
    msg = str(exc_info.value)
    assert "The specified year '1975' is not supported." in msg
    # Must not dump the raw JSON structure.
    assert '{"errors"' not in msg
    assert '"year"' not in msg


def test_400_non_json_body_falls_back_to_raw_text(monkeypatch):
    def handler(url):
        return seam_response(400, "plain text failure", url)

    client = make_client(monkeypatch, handler)
    with pytest.raises(ValueError, match="plain text failure"):
        client.get_holidays(999, "US")
