"""Raw-HTTP transport seam for the Nager.Date API — stdlib only (REFERENCE §1, D5).

This module is the single network-touching seam of the package: `fetch_raw`
is the ONLY function that opens a socket (T01 grep gate). The legacy async
httpx client (`client.py`) keeps its own transport until T02 re-points it at
this seam; nothing here imports httpx or fastmcp — the seam uses only the
standard library (`urllib.request`, `urllib.error`, `http.client`, `socket`,
`json`), so its tests are interpreter-independent.

Port provenance (REFERENCE §5 client-port note C-1; R2 facts from _chain.md,
all re-verifiable via `git show pre-migration/20260914:src/nager_date_mcp/...`):

* Fact 1 — the legacy client had NO retry ladder and NO transport-level
  `except` at all: the only `except` in client.py was
  `except (ValueError, TypeError)` around `response.json()` inside
  `_extract_error_message` (tag client.py l.44), and every
  `await self._client.get(path)` was unguarded. There was no ladder to feed;
  the seam normalization below IS the parity (R2 fact 4).
* Fact 2 — every transport error folded at the SERVER layer: all 7 tool
  handlers carry `except httpx.HTTPError: return f"Error: API request
  failed — {e}"` (tag server.py l.124-126 … l.350-352), and
  `httpx.TimeoutException ⊂ httpx.HTTPError` (verified `issubclass(...) ==
  True` in $PYO), so timeouts folded exactly like connect errors. Legacy
  tests assert only the `startswith("Error: API request failed")` prefix —
  the post-migration message *suffix* may differ ("urlopen error ..." vs the
  httpx repr); the fold SHAPE must not change.
* Fact 3 — the legacy timeout budget was httpx's implicit default:
  `DEFAULT_TIMEOUT_CONFIG = Timeout(timeout=5.0)` (httpx 0.28.1, verified in
  $PYO); no explicit timeout existed anywhere. `fetch_raw` keeps 5.0 s.
"""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Explicit User-Agent: bare-urllib requests occasionally draw Cloudflare
# "403 error code: 1010" on this API (seam fact, _chain.md), while
# `python-httpx/0.28.1` and the default-UA request on /api/v3 got 200. The
# value follows the fleet precedent shape (`crtsh-mcp`,
# src/crtsh_mcp/client.py:58). The live smoke in T10/T11 is the arbiter; the
# UA is NOT part of the frozen surface, so it is recorded here in code.
USER_AGENT = "nager-date-mcp/0.3.0"

# R2 fact 3: legacy httpx DEFAULT_TIMEOUT_CONFIG = Timeout(timeout=5.0) —
# the seam must carry the same 5.0 s default to preserve the budget.
DEFAULT_TIMEOUT = 5.0

# The ONE transport-error tuple (review checklist: never split across arms),
# reused verbatim at every network-touching site in fetch_raw below so no
# site can drift. R1 pin (REQUIRED, REFERENCE §5 client-port note): py3.11
# `TimeoutError` is NOT a `urllib.error.URLError` subclass (`socket.timeout
# is TimeoutError`), while httpx's TimeoutException WAS an httpx.HTTPError —
# without conversion a read timeout would escape the legacy
# `except httpx.HTTPError` fold (fact 2) and surface as JSON-RPC -32603
# instead of friendly error text. R2 fact 4: http.client.HTTPException
# protocol errors and socket resets are normalized into URLError at THIS
# single seam — including errors raised while reading the BODY (connection
# dies after headers, mid-body timeout), not just from urlopen itself — so
# nothing but URLError/HTTPError can ever escape fetch_raw.
TRANSPORT_ERRORS = (TimeoutError, socket.timeout, http.client.HTTPException,
                    ConnectionResetError, OSError)


class _SeamUrl:
    """httpx `request.url` stand-in: `.path` is what the client parses."""

    __slots__ = ("path",)

    def __init__(self, path: str) -> None:
        self.path = path


class _SeamRequest:
    """httpx `response.request` stand-in exposing `.url.path`."""

    __slots__ = ("url",)

    def __init__(self, url: str) -> None:
        # Full path INCLUDING the /api/v3 prefix (and any query string):
        # the legacy `_handle_response` 404 branch picks the first 2-char
        # ALPHA segment of `response.request.url.path` (so "v3" is excluded
        # by isalpha()) to build "Country not found: ZZ" — seam fact,
        # verified live against the tag.
        self.url = _SeamUrl(urllib.parse.urlsplit(url).path)


class SeamResponse:
    """Read-only view of a seam GET, shaped to the httpx.Response contract
    the legacy client relies on: `status_code`, `text`, `.json()`,
    `raise_for_status()`, `request.url.path`.
    """

    __slots__ = ("status_code", "text", "request")

    def __init__(self, status_code: int, text: str, url: str) -> None:
        self.status_code = status_code
        self.text = text
        self.request = _SeamRequest(url)

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        """Raise `urllib.error.HTTPError` on 4xx/5xx.

        Parity role of `httpx.HTTPStatusError` (fact 2's fold catches
        httpx.HTTPError; the stdlib analogue class escaping to the fold may
        differ in name but must be an HTTP-status failure — R2 fact 4
        suffix latitude). Legacy `_handle_response` only calls this after
        its 204/404/400 branches, so in practice it fires on 5xx.
        """
        if self.status_code >= 400:
            raise urllib.error.HTTPError(
                url=self.request.url.path,
                code=self.status_code,
                msg="",
                hdrs=None,  # type: ignore[arg-type]
                fp=None,    # type: ignore[arg-type]
            )


def fetch_raw(url: str, timeout: float = DEFAULT_TIMEOUT) -> SeamResponse:
    """GET absolute `url`; return a `SeamResponse`. The package's ONLY
    network-touching function (grep gate).

    URL contract: callers build `url` by LITERAL `base_url + path`
    concatenation (e.g. ``BASE_URL + "/PublicHolidays/2024/GB"``).
    WHY not urljoin: the legacy httpx client merged
    `base_url="https://date.nager.at/api/v3"` with relative paths KEEPING the
    `/api/v3` prefix (MockTransport probe verified the real request URL
    `https://date.nager.at/api/v3/PublicHolidays/2024/GB`);
    `urllib.parse.urljoin(base, "/PublicHolidays/...")` treats the leading
    slash as absolute-from-root and DROPS `/api/v3` → 403/404s. No urljoin
    on the request URL anywhere in this module.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        # The body read happens INSIDE the try (review round-1 fix): a
        # connection that dies after headers, or a read timeout that fires
        # mid-body, raises http.client protocol errors / TimeoutError from
        # `resp.read()` — sites the round-1 shape left outside the arms,
        # leaking non-URLErrors past the fold. `with` guarantees the socket
        # closes on every exit path (success or normalized error).
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read()
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        # urlopen raises HTTPError for 4xx/5xx, and HTTPError IS a readable
        # response object (status + body). Legacy control flow maps statuses
        # in the client's `_handle_response` (204→None, 404→ValueError,
        # 400→ValueError, else raise_for_status), NOT at the transport — so
        # return it as a SeamResponse and leave status mapping to the
        # client, matching legacy exactly. This arm is ordered BEFORE the
        # OSError tuple arm deliberately: HTTPError ⊂ URLError ⊂ OSError, so
        # a later OSError catch would swallow every 4xx/5xx response.
        #
        # The body read is raised FROM an except arm, so the outer arms
        # cannot re-catch it (Python does not re-enter the same try) — hence
        # the nested guard reusing THE SAME tuple: an error mid-read of an
        # error response (server hangs up after a 5xx status line) must
        # normalize to URLError exactly like the success-path read.
        try:
            body = exc.read()
        except TRANSPORT_ERRORS as e:
            raise urllib.error.URLError(e) from e
        status = exc.code
    except TRANSPORT_ERRORS as e:
        # R1 + R2 fact 4 normalization — see TRANSPORT_ERRORS for the full
        # pinned rationale. `from e` preserves the cause; the tuple is ONE
        # shared constant, never split across arms (a split arm would
        # swallow `from e` or shadow HTTPError).
        raise urllib.error.URLError(e) from e
    # 204/empty body decodes cleanly to text="" — the seam owns the empty
    # body, the client layer owns the 204→None mapping (tests pin it).
    return SeamResponse(status_code=status, text=body.decode("utf-8"), url=url)
