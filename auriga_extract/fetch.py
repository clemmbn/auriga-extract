"""
Data-collection layer: pull raw interventions out of the Auriga API.

The portal authenticates with a Keycloak bearer token and no cookies at all.
The token cannot be obtained by us -- it lives in the Angular app's memory, so
we watch the app's own outgoing requests over the DevTools protocol and read the
Authorization header off one of them. It is held in memory only and never
written to disk.

Once the token is known the browser is no longer needed: the API answers plain
urllib requests. That was verified on 2026-09-07 against
isaesupaero-production.np-auriga.nfrance.net -- an identical token returned the
same 15 interventions through the page's own fetch(), through urllib with
'x-scope: frontend', and through urllib without it. The earlier belief that a
WAF/TLS-fingerprinting layer rejected non-browser clients (INSTRUCTIONS.md:10)
was a hypothesis drawn from a single failed curl, and did not survive
measurement; the likeliest cause was an expired token. 'x-scope' is sent anyway,
because mirroring the SPA costs nothing.

The API accepts arbitrary startDate/endDate, so a whole year could be pulled in
one request. We still walk month by month: it bounds response size (a single
month is already ~400 KB) and gives the per-month progress output the spec asks
for.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from .cdp import TRANSIENT_ERRORS, BrowserSession, token_expiry
from .console import console

API_PATH = "/api/plannings/me"

# The UI always asks for all seven weekdays; mirroring it avoids surprising the
# backend with a parameter shape it never sees in production.
DAYS_PARAMS = [("days", str(d)) for d in range(1, 8)]

# Header the SPA sends alongside the bearer token. Measured to be optional, but
# sent regardless so our traffic stays indistinguishable from the app's.
SCOPE_HEADER = "frontend"

# How long to wait for the app to make an authenticated call we can learn the
# token from, in seconds.
TOKEN_WAIT_SECONDS = 300

# How long to stay quiet before telling the user to log in. A restored session
# usually produces a token in well under this, so the login banner never appears
# for someone who is already signed in.
QUIET_BEFORE_PROMPT_SECONDS = 15

# Delay before the first nudge, and the interval between later ones.
FIRST_NUDGE_SECONDS = 3.0
NUDGE_INTERVAL_SECONDS = 20.0


def _nudge_js(hash_route: str) -> str:
    """
    Build JavaScript that forces the SPA to refetch its planning data.

    hash_route: the app's planning route, e.g. "#/mainContent/menuEntry/227/planning".
    Returns an IIFE source string.

    A hash change re-runs the app's router WITHOUT reloading the document. That
    distinction is the whole point: a real reload during SSO would wipe a
    half-filled login form, whereas this cannot. It exists because a restored
    session can land on a view that issues no API call at all, leaving nothing
    to sniff until something provokes one.
    """
    return f"""(function () {{
  var target = {hash_route!r};
  if (location.hash === target) {{
    location.hash = '#/mainContent/welcome';
    setTimeout(function () {{ location.hash = target; }}, 250);
  }} else {{
    location.hash = target;
  }}
}})()"""


def token_from_events(events: list[dict]) -> Optional[str]:
    """
    Find a bearer token in a batch of DevTools network events.

    events: raw protocol messages, as buffered by DevTools.drain().
    Returns the Authorization header value of the first API request carrying
    one, or None. Pure: no I/O, no side effects.
    """
    for message in events:
        if message.get("method") != "Network.requestWillBeSent":
            continue
        request = message.get("params", {}).get("request") or {}
        if "/api/" not in (request.get("url") or ""):
            continue
        for name, value in (request.get("headers") or {}).items():
            # Header casing is not normalised by the protocol.
            if name.lower() == "authorization" and (value or "").strip():
                return value
    return None


def wait_for_token(
    session: BrowserSession,
    hash_route: str,
    timeout_seconds: int = TOKEN_WAIT_SECONDS,
    observer: Any = None,
) -> str:
    """
    Block until the app makes an authenticated request, then return its token.

    session: the browser session to observe.
    hash_route: planning route, used by the nudge.
    timeout_seconds: how long to wait before giving up.
    observer: optional CaptureSink; its pump() is fed the same events before
        they are discarded, so --capture records the login traffic. Passed in
        rather than subscribed, because this loop owns the event buffer and is
        the only place that knows when a connection is live.
    Returns the Authorization header value. Raises RuntimeError on timeout.
    Side effect: prints progress; may nudge the SPA's route.

    There is deliberately no "press Enter when you're logged in" prompt: login
    completion is detected rather than asserted, so the tool cannot proceed on a
    claim that turns out to be false.
    """
    console.print("Checking for an existing session...")

    started = time.time()
    deadline = started + timeout_seconds
    connected_at = 0.0
    last_nudge = 0.0
    prompted = False

    while time.time() < deadline:
        try:
            dev = session.connect()
            if not connected_at:
                connected_at = time.time()
                last_nudge = 0.0

            dev.drain(3.0)
            if observer is not None:
                # Must run before the buffer is cleared, and while the socket is
                # live: response bodies can only be fetched through it.
                observer.pump(dev)
            token = token_from_events(dev.events)
            if token:
                expires = token_expiry(token)
                suffix = f" (valid until {expires:%H:%M:%S} UTC)" if expires else ""
                console.print(f"[green]Session picked up{suffix}.[/]")
                return token
            dev.events.clear()

            # Only nudge a loaded app. While the tab is on the SSO domain we
            # stay completely out of the way -- the user may be mid-typing.
            now = time.time()
            if session.app_host in session.current_url():
                idle = now - max(connected_at, last_nudge)
                if idle > (FIRST_NUDGE_SECONDS if not last_nudge else NUDGE_INTERVAL_SECONDS):
                    session.evaluate(_nudge_js(hash_route), timeout=10)
                    last_nudge = now

        except TRANSIENT_ERRORS:
            # The tab navigated away (an SSO redirect) or was closed. Both are
            # expected during login; drop the socket and reconnect.
            session.drop()
            connected_at = 0.0
            time.sleep(1.5)

        if not prompted and time.time() - started > QUIET_BEFORE_PROMPT_SECONDS:
            console.print()
            console.rule("[bold cyan]Log in[/]", style="cyan")
            console.print("Log in to the portal in the browser window that opened.")
            console.print(
                "[dim]Nothing else is needed: the moment the portal makes its first "
                "authenticated request, this picks the session up and starts fetching.[/]"
            )
            console.print()
            prompted = True

    raise RuntimeError(
        "Never saw an authenticated request. Did you finish logging in, and is "
        "the planning view open?"
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


def _request_month(url: str, token: str, timeout: float = 60.0) -> dict[str, Any]:
    """
    Perform one planning request.

    url: fully built request URL.
    token: Authorization header value.
    timeout: seconds to wait for the response.
    Returns the decoded payload. Raises urllib.error.HTTPError on a rejection,
    so the caller can distinguish an expired token (401/403) from other faults.

    Note this uses the default opener, i.e. it honours the user's proxy
    settings -- unlike the loopback DevTools calls in cdp.py, this one goes out
    to the internet like any other request.
    """
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": token,
            "x-scope": SCOPE_HEADER,
            "Accept": "application/json, text/plain, */*",
            "accept-language": "fr",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_interventions(
    base_url: str,
    token: str,
    start: date,
    end: date,
    refresh_token: Optional[Callable[[], str]] = None,
) -> list[dict[str, Any]]:
    """
    Fetch every intervention in a date range, walking one month at a time.

    base_url: portal origin.
    token: Authorization header value.
    start, end: inclusive range.
    refresh_token: called to obtain a fresh token if one expires mid-run; when
        None, an expiry is simply reported as an error.
    Returns interventions deduplicated by id, in chronological order.
    Raises RuntimeError if the API rejects a request unrecoverably.

    Side effect: prints per-month progress, as the spec requires.

    The token refresh matters now that the login persists between runs: a run
    can begin holding a token that is already close to expiry, which was
    impossible back when every run started with a fresh manual login.
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

        payload: Optional[dict[str, Any]] = None
        for attempt in (1, 2):
            try:
                payload = _request_month(url, token)
                break
            except urllib.error.HTTPError as exc:
                body = exc.read()[:300].decode("utf-8", "replace")
                if exc.code in (401, 403) and attempt == 1 and refresh_token:
                    console.print("  [yellow]Token expired; asking the browser for a new one...[/]")
                    token = refresh_token()
                    continue
                hint = ""
                if exc.code in (401, 403):
                    hint = " -- the session token expired; re-run and log in again"
                raise RuntimeError(f"API returned HTTP {exc.code} for {label}{hint}\n{body}")
            except Exception as exc:  # noqa: BLE001 - transient network fault
                if attempt == 1:
                    console.print(f"  [yellow]{label}: {exc}; retrying...[/]")
                    time.sleep(2)
                    continue
                raise RuntimeError(f"Could not fetch {label}: {exc}")

        interventions = (payload or {}).get("interventions") or []
        # Month chunks do not overlap, but an event spanning a boundary could
        # appear twice; keying by id makes the merge idempotent regardless.
        for item in interventions:
            by_id[item.get("id")] = item
        console.print(f"  [dim]{label}:[/] [bold]{len(interventions)}[/] events")

    ordered = sorted(by_id.values(), key=lambda i: str(i.get("startDateTime") or ""))
    console.print(f"\n[green]Found [bold]{len(ordered)}[/] events in total.[/]")
    return ordered
