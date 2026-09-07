"""
Data-collection layer: pull raw interventions out of the Auriga API.

The portal authenticates with a Keycloak bearer token and no cookies at all,
and a WAF rejects clients whose TLS fingerprint isn't a real browser. Two
consequences drive this module:

  - The token cannot be obtained by us; it lives in the Angular app's memory.
    We sniff it off the app's own outgoing requests (in memory only -- it is
    never written to disk).

  - Requests are issued with fetch() *inside the page* via page.evaluate,
    rather than through Playwright's APIRequestContext, so they leave the
    machine through Chrome's own network stack and look exactly like the app's
    traffic.

The API accepts arbitrary startDate/endDate, so a whole year could be pulled in
one request. We still walk month by month: it bounds response size (a single
month is already ~400 KB) and gives the per-month progress output the spec asks
for.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional
from urllib.parse import urlencode

from .console import console

API_PATH = "/api/plannings/me"

# The UI always asks for all seven weekdays; mirroring it avoids surprising the
# backend with a parameter shape it never sees in production.
DAYS_PARAMS = [("days", str(d)) for d in range(1, 8)]

# Header the SPA sends alongside the bearer token. The API is picky enough that
# it is not worth finding out whether it is optional.
SCOPE_HEADER = "frontend"

# How long to wait for the app to make an authenticated call we can learn the
# token from, in seconds.
TOKEN_WAIT_SECONDS = 300

# JavaScript run inside the page. Returns either the parsed body or an error
# description; throwing across the Playwright boundary loses the status code.
_FETCH_JS = """
async ([url, token, scope]) => {
  const response = await fetch(url, {
    headers: {
      'Authorization': token,
      'x-scope': scope,
      'accept': 'application/json, text/plain, */*'
    },
    credentials: 'include'
  });
  if (!response.ok) {
    return { __error: response.status, __text: (await response.text()).slice(0, 300) };
  }
  return await response.json();
}
"""


class TokenSniffer:
    """
    Watches a browser context's outgoing requests for a bearer token.

    The token is held in memory on this object only. Nothing persists it, in
    line with the spec's no-credential-storage rule.
    """

    def __init__(self) -> None:
        self.token: Optional[str] = None

    def attach(self, context: Any) -> None:
        """
        Register the request listener.

        context: a playwright.sync_api.BrowserContext.
        Side effect: adds a listener for the life of the context.
        """
        context.on("request", self._on_request)

    def _on_request(self, request: Any) -> None:
        """Record the newest Authorization header seen on an API call."""
        if "/api/" not in request.url:
            return
        # headers is a plain sync property: safe to touch inside a handler.
        token = (request.headers or {}).get("authorization")
        if not token:
            return
        is_new = token != self.token
        self.token = token
        if is_new:
            console.print("[green]Logged in.[/]")


def wait_for_token(page: Any, sniffer: TokenSniffer, timeout_seconds: int = TOKEN_WAIT_SECONDS) -> str:
    """
    Block until the app makes an authenticated request, then return its token.

    page: a Playwright Page, polled to keep the event loop dispatching.
    sniffer: the TokenSniffer attached to the same context.
    timeout_seconds: how long to wait before giving up.
    Returns the Authorization header value. Raises RuntimeError on timeout.

    Polling through Playwright (rather than sleeping) is required: the sync API
    only dispatches events while the main thread is inside a Playwright call.
    """
    console.rule("[bold cyan]Log in[/]", style="cyan")
    console.print("Waiting for you to log in...")
    waited = 0.0
    while waited < timeout_seconds:
        if sniffer.token:
            return sniffer.token
        page.wait_for_timeout(250)
        waited += 0.25
    raise RuntimeError(
        "Never saw an authenticated request. Did you finish logging in and "
        "open the planning view?"
    )


def month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """
    Split an inclusive date range into calendar-month pieces.

    start, end: inclusive bounds; end < start yields an empty list.
    Returns a list of (chunk_start, chunk_end) inclusive pairs, clipped to the
    original bounds so the first and last chunks can be partial months.
    """
    if end < start:
        return []

    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        # First day of the following month, found without calendar arithmetic
        # edge cases: jump past the 28th, then snap to day 1.
        following = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        chunk_end = min(following - timedelta(days=1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _build_url(base_url: str, start: date, end: date) -> str:
    """
    Assemble one planning request URL.

    base_url: portal origin, no trailing slash.
    start, end: inclusive range for this request.
    Returns the absolute URL.
    """
    params = DAYS_PARAMS + [
        ("startDate", start.isoformat()),
        ("endDate", end.isoformat()),
    ]
    return f"{base_url}{API_PATH}?{urlencode(params)}"


def fetch_interventions(
    page: Any, base_url: str, token: str, start: date, end: date
) -> list[dict[str, Any]]:
    """
    Fetch every intervention in a date range, walking one month at a time.

    page: Playwright Page whose origin is the portal (fetch runs inside it).
    base_url: portal origin.
    token: Authorization header value from TokenSniffer.
    start, end: inclusive range.
    Returns interventions deduplicated by id, in chronological order.
    Raises RuntimeError if the API rejects a request.

    Side effect: prints per-month progress, as the spec requires.
    """
    by_id: dict[Any, dict[str, Any]] = {}
    chunks = month_chunks(start, end)
    # %-d (no leading zero) is a glibc/macOS strftime extension; Windows'
    # C runtime rejects it with "invalid format string". %d plus lstrip
    # gets the same "7 September 2026" rendering everywhere.
    def _day_month_year(d: date) -> str:
        return f"{d.strftime('%d %B %Y').lstrip('0')}"

    span = f"{_day_month_year(start)} -> {_day_month_year(end)}"

    console.print()
    console.rule("[bold cyan]Fetching your timetable[/]", style="cyan")
    console.print(f"Date range: [bold]{span}[/]\n")

    for chunk_start, chunk_end in chunks:
        url = _build_url(base_url, chunk_start, chunk_end)
        label = chunk_start.strftime("%B %Y")
        payload = page.evaluate(_FETCH_JS, [url, token, SCOPE_HEADER])

        if isinstance(payload, dict) and "__error" in payload:
            status = payload["__error"]
            hint = ""
            if status in (401, 403):
                hint = " -- the session token likely expired; re-run and log in again"
            raise RuntimeError(
                f"API returned HTTP {status} for {label}{hint}\n{payload.get('__text', '')}"
            )

        interventions = (payload or {}).get("interventions") or []
        # Month chunks do not overlap, but an event spanning a boundary could
        # appear twice; keying by id makes the merge idempotent regardless.
        for item in interventions:
            by_id[item.get("id")] = item
        console.print(f"  [dim]{label}:[/] [bold]{len(interventions)}[/] events")

    ordered = sorted(by_id.values(), key=lambda i: str(i.get("startDateTime") or ""))
    console.print(f"\n[green]Found [bold]{len(ordered)}[/] events in total.[/]")
    return ordered
