"""
Entrypoint that wires the four layers together.

Flow: open a real browser -> wait for the user to log in -> read the bearer
token off the app's own traffic -> pull the date range from the API -> close
the browser -> group, let the user pick, write .ics files.

One deliberate design point: there is no "press Enter when you're logged in"
prompt. The tool watches for the first authenticated API request the app makes
and proceeds from there, so login completion is detected rather than asserted.
That also sidesteps a trap in Playwright's sync API, where blocking on input()
stops network events from being dispatched at all.

The browser is closed before the interactive picker, because everything needed
is already in memory by then and a stale window would only invite confusion.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from rich.panel import Panel

from .capture import CaptureSink, attach
from .console import console
from .courses import group_courses
from .fetch import TokenSniffer, fetch_interventions, wait_for_token
from .ics import write_calendar
from .probe import DEFAULT_URL, _launch_browser
from .select import prompt

# Covers the full 2026-2027 academic year, so a plain run with no
# --start/--end grabs the whole thing.
DEFAULT_START = date(2026, 9, 1)
DEFAULT_END = date(2027, 8, 31)

LOGIN_BANNER = (
    "A browser window is open on the portal.\n\n"
    "  ->  Log in as you normally would.\n\n"
    "Nothing else is needed: as soon as the portal makes its first\n"
    "authenticated request, this tool picks the session up automatically\n"
    "and starts fetching."
)


def _origin(url: str) -> str:
    """
    Reduce a page URL to its scheme://host origin.

    url: any absolute URL.
    Returns the origin with no trailing slash, for building API URLs.
    """
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def run(
    start: date,
    end: date,
    url: str,
    out_root: Path,
    channel: Optional[str],
    capture_dir: Optional[Path],
) -> int:
    """
    Execute one full extraction run.

    start, end: inclusive date range to export.
    url: portal page to open.
    out_root: parent directory for generated .ics files.
    channel: preferred browser channel, or None for bundled Chromium.
    capture_dir: when set, also record all network traffic there for debugging.
    Returns a process exit code.
    Side effects: launches a browser, reads stdin, writes .ics files.
    """
    if end < start:
        console.print(f"[red]--end ({end}) is before --start ({start})[/]")
        return 2

    sniffer = TokenSniffer()
    sink: Optional[CaptureSink] = None
    interventions: list = []

    console.rule("[bold cyan]Auriga Extract[/]", style="cyan")
    console.print()

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, channel, headless=False)
        context = browser.new_context(
            locale="fr-FR",
            timezone_id="Europe/Paris",
            no_viewport=True,
        )
        sniffer.attach(context)
        if capture_dir:
            sink = CaptureSink(capture_dir)
            attach(context, sink)

        page = context.new_page()
        try:
            console.print("Opening the timetable portal in a browser window...")
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Navigation problem ({type(exc).__name__}: {exc})[/]")
            console.print("[yellow]The window is open -- navigate to the portal manually.[/]")

        console.print()
        console.print(Panel(LOGIN_BANNER, title="AURIGA EXTRACT", border_style="cyan"))

        try:
            token = wait_for_token(page, sniffer)
            interventions = fetch_interventions(page, _origin(url), token, start, end)
        except Exception as exc:  # noqa: BLE001
            console.print(f"\n[red]Extraction failed: {exc}[/]")
            return 1
        finally:
            if sink:
                sink.close()
            try:
                browser.close()
            except Exception:  # noqa: BLE001 - user may have closed it already
                pass

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
        "--channel",
        default="chrome",
        metavar="NAME",
        help=(
            "installed browser channel to launch, e.g. chrome, msedge "
            "(default: chrome; required because the portal's WAF rejects "
            "bundled Chromium's TLS fingerprint — pass '' to try it anyway)"
        ),
    )
    parser.add_argument(
        "--capture",
        nargs="?",
        const=Path("captures"),
        type=Path,
        default=None,
        metavar="DIR",
        help="also record network traffic here (debugging)",
    )
    args = parser.parse_args(argv)

    capture_dir = None
    if args.capture:
        from datetime import datetime

        capture_dir = args.capture / datetime.now().strftime("%Y%m%d-%H%M%S")

    return run(args.start, args.end, args.url, args.out, args.channel or None, capture_dir)
