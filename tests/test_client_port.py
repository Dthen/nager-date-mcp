"""Hermetic tests for the sync client port (client.py over the seam) — zero sockets.

T02 port of tests/test_client.py's cache/error semantics: every case keeps
the legacy assertion and only swaps the httpx MockTransport for a
monkeypatched ``nager_date_mcp.client.fetch_raw`` returning SeamResponse
fakes. No httpx import anywhere (review checklist); nothing sleeps.

Coverage (card verification list):
  * cache hit avoids second fetch / ttl-expiry re-fetch / cache_ttl=0 bypass
  * deep-copy contract on cached hits
  * MAX_CACHE_SIZE eviction
  * 404 "Country not found: XX" + "Not found: <path>"; 400 flattened + raw
    fallback; 500 raises HTTPError
  * is_today_holiday 200-empty -> True, 204 -> None
  * endpoint wrappers build literal base_url+path URLs with uppercase codes
  * URLError from the seam propagates UNCAUGHT (R2 facts 1-2: fold is the
    server handler's job, not the client's)
"""

import json
import urllib.error

import pytest

import nager_date_mcp.client as client_mod
from nager_date_mcp.client import BASE_URL, NagerDateClient
from nager_date_mcp.transport import SeamResponse


def seam(status: int = 200, text: str = "", path: str = "/x") -> SeamResponse:
    """Build a SeamResponse exactly as fetch_raw returns it (absolute url)."""
    return SeamResponse(status_code=status, text=text, url=BASE_URL + path)


class FakeFetch:
    """Callable seam replacement: counts calls, serves canned responses.

    Each queued item is a SeamResponse or an exception instance (raised).
    When the queue is exhausted the LAST item repeats (legacy MockTransport
    handlers were stateless per-request in the ported tests).
    """

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def __call__(self, url: str, timeout=None) -> SeamResponse:
        self.urls.append(url)
        item = self.responses[min(len(self.urls) - 1, len(self.responses) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def no_clock_drift(monkeypatch):
    """Pin client's monotonic source so TTL windows are deterministic."""
    fake = {"t": 1000.0}
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: fake["t"])
    return fake


# -- Cache behaviour (ports of test_client.py) -------------------------------


def test_cache_hit_avoids_second_request(monkeypatch, no_clock_drift):
    """Port of legacy test_cache_hit_avoids_second_request."""
    fake = FakeFetch(seam(text=json.dumps([{"name": "New Year"}])))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()

    first = client.get("/NextPublicHolidays/US", cache_ttl=3600)
    second = client.get("/NextPublicHolidays/US", cache_ttl=3600)

    assert first == second == [{"name": "New Year"}]
    assert len(fake.urls) == 1  # second call served from cache


def test_cache_miss_when_ttl_expired(monkeypatch, no_clock_drift):
    """Port of legacy test_cache_miss_when_ttl_expired."""
    fake = FakeFetch(
        seam(text=json.dumps({"n": 1})),
        seam(text=json.dumps({"n": 2})),
    )
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()

    client.get("/x", cache_ttl=60)
    no_clock_drift["t"] += 61  # exceed TTL
    client.get("/x", cache_ttl=60)

    assert len(fake.urls) == 2


def test_ttl_zero_bypasses_cache(monkeypatch, no_clock_drift):
    """Port of legacy test_ttl_zero_bypasses_cache."""
    fake = FakeFetch(seam(text=json.dumps({"ok": True})))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()

    client.get("/x", cache_ttl=0)
    client.get("/x", cache_ttl=0)
    assert len(fake.urls) == 2
    assert client._cache == {}  # nothing stored on bypass


def test_cache_is_bounded_evicts_oldest(monkeypatch):
    """Port of legacy test_cache_is_bounded_evicts_oldest (small cap)."""
    fake_time = {"t": 1000.0}
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: fake_time["t"])
    monkeypatch.setattr(client_mod, "MAX_CACHE_SIZE", 4)
    fake = FakeFetch(seam(text=json.dumps([{"name": "Holiday"}])))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()

    total = client_mod.MAX_CACHE_SIZE + 2
    for i in range(total):
        fake_time["t"] += 1.0  # strictly increasing timestamps
        client.get(f"/path/{i}", cache_ttl=3600)

    assert len(client._cache) == client_mod.MAX_CACHE_SIZE
    assert "/path/0" not in client._cache
    assert f"/path/{total - 1}" in client._cache


def test_cache_returns_copies_not_references(monkeypatch, no_clock_drift):
    """Port of legacy test_cache_returns_copies_not_references."""
    payload = [{"name": "New Year", "date": "2024-01-01"}]
    fake = FakeFetch(seam(text=json.dumps(payload)))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()

    first = client.get("/NextPublicHolidays/US", cache_ttl=3600)
    # Mutate the returned data (list + inner dict), as _enrich_holidays does.
    first[0]["day_of_week"] = "MUTATED"
    first.append({"name": "INJECTED"})

    second = client.get("/NextPublicHolidays/US", cache_ttl=3600)
    assert second == payload
    assert "day_of_week" not in second[0]
    assert len(second) == 1
    assert len(fake.urls) == 1  # still served from the pristine cache entry


def test_cache_hit_return_mutation_does_not_poison_cache(monkeypatch, no_clock_drift):
    """T08-review follow-up (mutation-tested): the CACHE-HIT branch must
    deepcopy, not return the stored object. The legacy test above only
    mutates a FRESH return; with ``return data`` on the hit path that
    mutation reaches straight into the cache entry (the real server's
    _enrich_holidays mutates returned values in place). Here the mutation
    is applied to a value that already came out of the cache, then the
    cache is read a third time — it must still be pristine."""
    payload = {"country": "US", "holidays": [{"name": "New Year"}]}
    fake = FakeFetch(seam(text=json.dumps(payload)))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()

    client.get("/CountryInfo/US", cache_ttl=3600)          # prime (fresh path)
    hit = client.get("/CountryInfo/US", cache_ttl=3600)    # cache-hit path
    assert len(fake.urls) == 1                             # hit proven
    # Mutate the hit-returned dict AND its nested list, in place.
    hit["holidays"].append({"name": "INJECTED"})
    hit["holidays"][0]["day_of_week"] = "MUTATED"
    hit["poisoned"] = True

    again = client.get("/CountryInfo/US", cache_ttl=3600)  # second hit
    assert again == {"country": "US", "holidays": [{"name": "New Year"}]}
    assert "poisoned" not in again
    assert len(again["holidays"]) == 1
    assert "day_of_week" not in again["holidays"][0]
    assert len(fake.urls) == 1  # still served from the un-poisoned cache entry


# -- URL construction ---------------------------------------------------------


def test_fetch_url_is_literal_concat_keepin_api_prefix(monkeypatch, no_clock_drift):
    """Seam fact: base_url + path LITERALLY — /api/v3 must survive (urljoin
    would drop it and 404). Port of legacy endpoint-uses-uppercase-code."""
    fake = FakeFetch(seam(text="[]"))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()

    client.get_holidays(2024, "us")

    assert fake.urls == ["https://date.nager.at/api/v3/PublicHolidays/2024/US"]


def test_cache_key_includes_query_string(monkeypatch, no_clock_drift):
    """Seam fact: cache key = path incl. query; different queries are hits."""
    fake = FakeFetch(seam(text="[]"))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()

    client.get("/NextPublicHolidays/US?foo=1", cache_ttl=3600)
    client.get("/NextPublicHolidays/US?foo=1", cache_ttl=3600)
    client.get("/NextPublicHolidays/US?foo=2", cache_ttl=3600)

    assert len(fake.urls) == 2
    assert set(client._cache) == {"/NextPublicHolidays/US?foo=1",
                                  "/NextPublicHolidays/US?foo=2"}


# -- Error mapping (ports of test_client.py) ----------------------------------


def test_404_maps_to_country_not_found(monkeypatch, no_clock_drift):
    fake = FakeFetch(seam(status=404, text="not found",
                           path="/CountryInfo/ZZ"))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    with pytest.raises(ValueError, match="Country not found: ZZ"):
        client.get_country_info("ZZ")


def test_404_without_code_maps_to_not_found_path(monkeypatch, no_clock_drift):
    fake = FakeFetch(seam(status=404, text="not found", path="/Nope"))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    with pytest.raises(ValueError, match=r"Not found: /api/v3/Nope"):
        client.get("/Nope", cache_ttl=0)


def test_400_maps_to_bad_request_with_detail(monkeypatch, no_clock_drift):
    fake = FakeFetch(seam(status=400, text="Invalid year supplied"))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    with pytest.raises(ValueError, match="Invalid year supplied"):
        client.get_holidays(999, "US")


def test_400_json_errors_body_produces_clean_message(monkeypatch, no_clock_drift):
    """Port of legacy test_400_json_errors_body_produces_clean_message."""
    body = {"errors": {"year": ["The specified year '1975' is not supported."]}}
    fake = FakeFetch(seam(status=400, text=json.dumps(body)))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    with pytest.raises(ValueError) as exc_info:
        client.get_holidays(1975, "US")
    msg = str(exc_info.value)
    assert "The specified year '1975' is not supported." in msg
    # Must not dump the raw JSON structure.
    assert '{"errors"' not in msg
    assert '"year"' not in msg


def test_400_non_json_body_falls_back_to_raw_text(monkeypatch, no_clock_drift):
    """Port of legacy test_400_non_json_body_falls_back_to_raw_text."""
    fake = FakeFetch(seam(status=400, text="plain text failure"))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    with pytest.raises(ValueError, match="plain text failure"):
        client.get_holidays(999, "US")


def test_500_raises_http_error(monkeypatch, no_clock_drift):
    """Port of legacy test_500_raises_http_error: httpx.HTTPStatusError ->
    urllib.error.HTTPError (the seam's raise_for_status analogue)."""
    fake = FakeFetch(seam(status=500, text="boom"))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    with pytest.raises(urllib.error.HTTPError):
        client.get("/x", cache_ttl=0)


# -- is_today_holiday ----------------------------------------------------------


def test_is_today_holiday_204_is_none(monkeypatch):
    """204 is ambiguous (no-data OR not-a-holiday) — client reports None."""
    fake = FakeFetch(seam(status=204, text=""))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    assert client.is_today_holiday("US") is None


def test_is_today_holiday_200_is_true(monkeypatch):
    fake = FakeFetch(seam(status=200, text=json.dumps({"name": "x"})))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    assert client.is_today_holiday("us") is True


def test_is_today_holiday_200_empty_body_is_true(monkeypatch):
    """The real API returns 200 with an EMPTY body on a holiday — must be
    True, and the body must NEVER be parsed (endpoint quirk)."""
    fake = FakeFetch(seam(status=200, text=""))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    assert client.is_today_holiday("PE") is True


def test_is_today_holiday_query_and_bypass(monkeypatch, no_clock_drift):
    """offset/countyCode ride the query string; the call bypasses the cache."""
    fake = FakeFetch(seam(status=204, text=""))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()

    client.is_today_holiday("GB", offset=0)
    assert fake.urls[-1] == BASE_URL + "/IsTodayPublicHoliday/GB?offset=0"
    client.is_today_holiday("GB", county_code="GB-ENG", offset=1)
    assert fake.urls[-1] == (
        BASE_URL + "/IsTodayPublicHoliday/GB?countyCode=GB-ENG&offset=1")
    assert client._cache == {}  # volatile endpoint never touches the cache


# -- Transport-error propagation (R2 facts 1-2) ---------------------------------


def test_urlerror_propagates_uncaught_from_client(monkeypatch):
    """fetch_raw raising URLError must escape the client UNCAUGHT: the
    legacy client had zero transport-level except (fact 1); the friendly
    `Error: API request failed —` fold is the SERVER handler's job (fact 2)."""
    err = urllib.error.URLError("connection refused")
    fake = FakeFetch(err)
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    with pytest.raises(urllib.error.URLError) as ei:
        client.get_next_holidays("US")
    assert ei.value is err


def test_timeout_already_normalized_at_seam(monkeypatch):
    """R1 corollary: the client relies on the seam converting TimeoutError
    to URLError; whatever URLError reaches the handler folds identically."""
    fake = FakeFetch(urllib.error.URLError(TimeoutError("read timed out")))
    monkeypatch.setattr(client_mod, "fetch_raw", fake)
    client = NagerDateClient()
    with pytest.raises(urllib.error.URLError):
        client.get_country_info("DE")


# -- Construction surface ---------------------------------------------------------


def test_no_httpx_or_async_leftovers():
    """The ported module must be sync stdlib over the seam only."""
    import pathlib

    src = pathlib.Path(client_mod.__file__).read_text(encoding="utf-8")
    assert "httpx" not in src
    assert "async" not in src  # no async def / coroutines / comments left
    init = NagerDateClient.__init__
    assert "client" not in getattr(init, "__annotations__", {})
    assert not hasattr(NagerDateClient, "aclose")
    assert not hasattr(NagerDateClient, "__aenter__")
    assert not hasattr(NagerDateClient, "__aexit__")


def test_default_ttl_and_base_url_kept():
    client = NagerDateClient()
    assert client.base_url == BASE_URL
    assert client.default_ttl == client_mod.TTL_HOUR
