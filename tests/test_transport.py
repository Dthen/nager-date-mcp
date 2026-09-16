"""Hermetic tests for the raw-HTTP seam (transport.py) — zero real sockets.

Every test monkeypatches `urllib.request.urlopen`; fakes never sleep
(review checklist). Runs under $PYH AND $PYO — the seam imports stdlib only.

Gate (T01): >=6 tests, all green:
  * R1 pin: fake urlopen raises TimeoutError  -> urllib.error.URLError
  * R2:     http.client.RemoteDisconnected / ConnectionResetError -> URLError
  *         OSError -> URLError
  *         HTTPError(500) -> SeamResponse.status_code==500;
            raise_for_status() -> HTTPError
  *         success body -> .json() / .text / .request.url.path exact
  *         204/empty body -> SeamResponse(204, text="") without raising
"""

import http.client
import io
import json
import urllib.error
import urllib.request

import pytest

from nager_date_mcp import transport
from nager_date_mcp.transport import SeamResponse, fetch_raw

URL = "https://date.nager.at/api/v3/PublicHolidays/2024/GB"


class FakeHttpResponse(io.BytesIO):
    """Minimal http.client.HTTPResponse stand-in (readable + getcode()).

    `read_exc` (review round-1 blocker): when set, `read()` raises it while
    urlopen itself succeeds — simulating a connection that dies after
    headers or a read timeout firing mid-body.
    """

    def __init__(self, body: bytes, status: int = 200,
                 read_exc: BaseException | None = None) -> None:
        super().__init__(body)
        self._status = status
        self._read_exc = read_exc

    def getcode(self) -> int:
        return self._status

    def read(self, *args, **kwargs):  # type: ignore[override]
        if self._read_exc is not None:
            raise self._read_exc
        return super().read(*args, **kwargs)


def install(monkeypatch, *, exc=None, body=b"", status=200, read_exc=None):
    """Replace urllib.request.urlopen with a fake capturing the request."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        if exc is not None:
            raise exc
        return FakeHttpResponse(body, status, read_exc=read_exc)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


# -- R1 pin (REQUIRED, REFERENCE §5 client-port note) ----------------------


def test_r1_timeout_error_maps_to_urlerror(monkeypatch):
    """py3.11 TimeoutError is NOT a URLError subclass; the seam must convert
    it or a read timeout escapes the legacy `except httpx.HTTPError` fold
    shape and surfaces as -32603."""
    assert not issubclass(TimeoutError, urllib.error.URLError)  # the R1 fact
    install(monkeypatch, exc=TimeoutError("read timed out"))
    with pytest.raises(urllib.error.URLError) as ei:
        fetch_raw(URL)
    assert isinstance(ei.value.__cause__, TimeoutError)  # `from e` preserved


def test_r1_socket_timeout_is_timeouterror_alias(monkeypatch):
    # socket.timeout IS TimeoutError on py>=3.10 — pin the alias too.
    import socket

    assert socket.timeout is TimeoutError
    install(monkeypatch, exc=socket.timeout("t"))
    with pytest.raises(urllib.error.URLError):
        fetch_raw(URL)


# -- R2 protocol-error normalization (facts 2 and 4) ------------------------


@pytest.mark.parametrize(
    "exc",
    [
        http.client.RemoteDisconnected("reset by peer"),
        http.client.HTTPException("bad status line"),
        ConnectionResetError("reset by peer"),
        OSError("connection refused"),
    ],
    ids=["remote_disconnected", "http_exception", "conn_reset", "oserror"],
)
def test_r2_transport_errors_normalize_to_urlerror(monkeypatch, exc):
    install(monkeypatch, exc=exc)
    with pytest.raises(urllib.error.URLError) as ei:
        fetch_raw(URL)
    assert ei.value.__cause__ is exc


def test_httperror_subclass_of_oserror_ordering():
    # Why the HTTPError arm must precede the OSError arm: HTTPError ⊂
    # URLError ⊂ OSError. If it didn't, 4xx/5xx responses would be swallowed
    # into URLError and never reach the client's status mapper.
    assert issubclass(urllib.error.HTTPError, urllib.error.URLError)
    assert issubclass(urllib.error.URLError, OSError)


def test_http_error_response_returned_not_raised(monkeypatch):
    """5xx arrives as a readable HTTPError from urlopen; the seam returns it
    as a SeamResponse (legacy maps statuses in `_handle_response`)."""
    err = urllib.error.HTTPError(URL, 500, "Server Error", None,
                                 io.BytesIO(b'{"errors":{}}'))
    install(monkeypatch, exc=err)
    resp = fetch_raw(URL)
    assert isinstance(resp, SeamResponse)
    assert resp.status_code == 500
    assert resp.text == '{"errors":{}}'
    with pytest.raises(urllib.error.HTTPError):
        resp.raise_for_status()


# -- Read-phase normalization (review round-1 blocker) ----------------------
# urlopen succeeding but the BODY READ exploding (connection dies after
# headers; read timeout fires mid-body) must normalize identically — the
# round-1 shape read outside the arms and leaked raw http.client errors.


@pytest.mark.parametrize(
    "read_exc",
    [
        TimeoutError("read timed out mid-body"),
        http.client.RemoteDisconnected("reset by peer"),
        http.client.IncompleteRead(b"partial"),
        ConnectionResetError("reset by peer"),
        OSError("broken pipe on read"),
    ],
    ids=["timeout", "remote_disconnected", "incomplete_read",
         "conn_reset", "oserror"],
)
def test_body_read_errors_normalize_to_urlerror(monkeypatch, read_exc):
    captured = install(monkeypatch, body=b'[{"x":1}]', read_exc=read_exc)
    with pytest.raises(urllib.error.URLError) as ei:
        fetch_raw(URL)
    assert ei.value.__cause__ is read_exc  # `from e` preserved
    assert captured["timeout"] == 5.0


def test_httperror_with_exploding_body_normalizes(monkeypatch):
    """Mid-read of an ERROR response (server hangs up after a 5xx status
    line) must also fold to URLError — the HTTPError arm's read sits inside
    a nested guard reusing the same TRANSPORT_ERRORS tuple."""
    class ExplodingHTTPError(urllib.error.HTTPError):
        def read(self, *args, **kwargs):
            raise http.client.IncompleteRead(b"")

    err = ExplodingHTTPError(URL, 502, "Bad Gateway", None, None)
    install(monkeypatch, exc=err)
    with pytest.raises(urllib.error.URLError) as ei:
        fetch_raw(URL)
    assert isinstance(ei.value.__cause__, http.client.IncompleteRead)


# -- Success shape -----------------------------------------------------------


def test_success_body_fields_exact(monkeypatch):
    payload = [{"name": "New Year's Day", "localName": "Neujahr"}]
    captured = install(
        monkeypatch, body=json.dumps(payload, ensure_ascii=False).encode(), status=200
    )
    resp = fetch_raw(URL, timeout=2.5)
    assert resp.status_code == 200
    assert resp.text == json.dumps(payload, ensure_ascii=False)
    assert resp.json() == payload
    # FULL path incl. the /api/v3 prefix — the legacy 404 branch parses it.
    assert resp.request.url.path == "/api/v3/PublicHolidays/2024/GB"
    # Caller-provided timeout reaches urlopen; explicit User-Agent is sent.
    assert captured["timeout"] == 2.5
    assert captured["request"].get_header("User-agent") == transport.USER_AGENT


def test_default_timeout_is_httpx_budget(monkeypatch):
    # R2 fact 3: httpx DEFAULT_TIMEOUT_CONFIG = Timeout(timeout=5.0).
    captured = install(monkeypatch)
    fetch_raw(URL)
    assert captured["timeout"] == 5.0


def test_204_empty_body_no_raise(monkeypatch):
    """The seam owns the empty body; the client layer owns 204->None."""
    install(monkeypatch, body=b"", status=204)
    resp = fetch_raw(URL)
    assert resp.status_code == 204
    assert resp.text == ""
    resp.raise_for_status()  # 2xx must NOT raise
