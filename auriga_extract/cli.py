"""
Entrypoint that wires the four layers together.

Flow: open a real browser -> wait for the user to log in -> read the bearer
token off the app's own traffic -> close the browser -> pull the date range
from the API over plain HTTP -> group, let the user pick, write one .ics file.

Two deliberate design points:

  - There is no "press Enter when you're logged in" prompt. The tool watches for
    the first authenticated API request the app makes and proceeds from there,
    so login completion is detected rather than asserted -- the tool can never
    charge ahead on a claim that turns out to be false.

  - The browser is closed as soon as the token is in hand, before any data is
    fetched. The API answers ordinary HTTP requests (see fetch.py), so the
    browser has no job left, and a stale window would only invite confusion.

The login persists between runs in a browser profile directory (see cdp.py),
so the common case is that no login is needed at all.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from . import __version__
from .capture import CaptureSink, attach
from .cdp import DEFAULT_PORT, BrowserSession, default_profile
from .console import console
from .courses import group_courses
from .fetch import fetch_interventions, wait_for_token
from .ics import write_calendar
from .probe import DEFAULT_URL
from .select import prompt

# Covers the full 2026-2027 academic year, so a plain run with no
# --start/--end grabs the whole thing.
DEFAULT_START = date(2026, 9, 1)
DEFAULT_END = date(2027, 8, 31)


def _origin(url: str) -> str:
    """
    Reduce a page URL to its scheme://host origin.

    url: any absolute URL.
    Returns the origin with no trailing slash, for building API URLs.
    """
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _hash_route(url: str) -> str:
    """
    Extract the SPA hash route from a portal URL.

    url: the portal page URL, e.g. ".../#/mainContent/menuEntry/227/planning".
    Returns the fragment with its leading '#', or the planning default when the
    URL carries none. Used to nudge the app into refetching.
    """
    fragment = urlsplit(url).fragment
    return f"#{fragment}" if fragment else "#/mainContent/menuEntry/227/planning"


def run(
    start: date,
    end: date,
    url: str,
    out_root: Path,
    capture_dir: Optional[Path],
    port: int = DEFAULT_PORT,
    profile: Optional[Path] = None,
    browser_path: Optional[str] = None,
    keep_browser: bool = False,
) -> int:
    """
    Execute one full extraction run.

    start, end: inclusive date range to export.
    url: portal page to open.
    out_root: parent directory for the generated .ics file.
    capture_dir: when set, also record network traffic there for debugging.
    port: browser remote-debugging port.
    profile: persistent browser profile directory, or None for the default.
    browser_path: explicit browser executable, or None to auto-detect.
    keep_browser: leave the browser window open at the end.
    Returns a process exit code.
    Side effects: launches a browser, reads stdin, writes an .ics file.
    """
    if end < start:
        console.print(f"[red]--end ({end}) is before --start ({start})[/]")
        return 2

    sink: Optional[CaptureSink] = None
    interventions: list = []

    console.rule("[bold cyan]Auriga Extract[/]", style="cyan")
    console.print()

    try:
        session = BrowserSession.open(
            url, port=port, profile=profile, browser_path=browser_path
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        return 1

    # Only true for a browser we launched ourselves; an adopted one is running
    # on whatever profile its owner gave it, so claiming otherwise would be a lie.
    if session.launched:
        console.print(f"[dim]Login is remembered in {session.profile}[/]")

    hash_route = _hash_route(url)
    try:
        if capture_dir:
            sink = CaptureSink(capture_dir)
            attach(session.connect(), sink)

        token = wait_for_token(session, hash_route, observer=sink)

        # The browser stays open through the fetch for one reason only: if the
        # token expires mid-run, this is what can get another one. With a
        # persisted login a run can start on an already-old token, which was
        # impossible when every run began with a fresh manual login.
        def refresh() -> str:
            return wait_for_token(session, hash_route, observer=sink)

        interventions = fetch_interventions(
            _origin(url), token, start, end, refresh_token=refresh
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"\n[red]Extraction failed: {exc}[/]")
        return 1
    finally:
        if sink:
            sink.close()
        # Closed before the picker: everything needed is in memory by now, and
        # a stale window would only invite confusion.
        session.close(keep_open=keep_browser)

    if not interventions:
        console.print("\n[yellow]No events in that range -- nothing to export.[/]")
        return 0

    courses = group_courses(interventions)
    selected = prompt(courses)
    if not selected:
        console.print("\n[yellow]Nothing selected; no files written.[/]")
        return 0

    path = write_calendar(selected, out_root, start, end)
    console.print()
    console.rule("[bold green]Done[/]", style="green")
    console.print(f"Wrote [bold]{path.resolve()}[/]")
    console.print("Double-click the .ics file from your file explorer to import it into your calendar.")
    console.print(
        "[dim]See [link=https://github.com/clemmbn/auriga-extract#import-into-your-calendar]"
        "this link[/link] for other import options.[/]"
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """
    Parse arguments and run. Returns a process exit code.

    argv: argument list for testing; defaults to sys.argv[1:].
    """
    parser = argparse.ArgumentParser(
        prog="auriga-extract",
        description="Export ISAE-SUPAERO timetable courses from Auriga as .ics files.",
    )
    parser.add_argument(
        # So a bug report can name the exact build it came from.
        "--version",
        action="version",
        version=f"auriga-extract {__version__}",
    )
    parser.add_argument(
        "--start",
        default=DEFAULT_START,
        type=date.fromisoformat,
        help=f"first day, YYYY-MM-DD (default: {DEFAULT_START.isoformat()})",
    )
    parser.add_argument(
        "--end",
        default=DEFAULT_END,
        type=date.fromisoformat,
        help=f"last day, YYYY-MM-DD (default: {DEFAULT_END.isoformat()})",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"portal page to open (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        # ~/Downloads is the standard user download location on macOS, Windows
        # (Path.home() resolves %USERPROFILE% there), and most Linux desktops.
        "--out",
        default=Path.home() / "Downloads",
        type=Path,
        help="output directory (default: ~/Downloads)",
    )
    parser.add_argument(
        "--browser",
        default=None,
        metavar="PATH",
        help="path to Chrome/Edge/Brave/Chromium (default: auto-detect)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        type=Path,
        metavar="DIR",
        help=(
            "browser profile directory; your login is remembered here "
            f"(default: {default_profile()})"
        ),
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        type=int,
        help=f"browser remote-debugging port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--keep-browser",
        action="store_true",
        help="leave the browser window open when the export finishes",
    )
    parser.add_argument(
        "--capture",
        nargs="?",
        const=Path("captures"),
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "record login/bootstrap network traffic here (debugging). Note the "
            "timetable itself is fetched over plain HTTP, not by the browser, "
            "so it does not appear here -- use auriga_extract.probe for that"
        ),
    )
    # Superseded by --browser when Playwright was dropped. Kept as an accepted
    # no-op because earlier READMEs documented it, so people still have it in
    # their notes; failing on it would look like the tool was broken.
    parser.add_argument("--channel", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.channel is not None:
        console.print(
            "[yellow]--channel no longer does anything; "
            "use --browser <path> to pick a browser.[/]"
        )

    capture_dir = None
    if args.capture:
        from datetime import datetime

        capture_dir = args.capture / datetime.now().strftime("%Y%m%d-%H%M%S")

    try:
        return run(
            args.start,
            args.end,
            args.url,
            args.out,
            capture_dir,
            port=args.port,
            profile=args.profile,
            browser_path=args.browser,
            keep_browser=args.keep_browser,
        )
    except KeyboardInterrupt:
        # run()'s own handler catches Exception, and KeyboardInterrupt is not one
        # -- so without this a Ctrl-C anywhere in the run (most likely during the
        # long fetch) ends in a raw traceback. run()'s finally has already closed
        # the browser by the time we get here. 130 is the shell convention for
        # "terminated by SIGINT".
        console.print("\n[yellow]Cancelled.[/]")
        return 130
