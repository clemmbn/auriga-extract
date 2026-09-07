"""
API discovery probe for the Auriga student portal.

Why this exists: the portal's calendar endpoints, their date parameters and
their JSON schemas are undocumented, and requests replayed outside a browser
are rejected by a WAF. So rather than guessing a schema and writing a parser
against it, this tool opens a real browser, lets the user log in and drive the
UI by hand, and records every XHR/fetch that results -- annotated with markers
saying what the user was doing. Reading that capture is what tells us which
endpoint returns the event list and which returns the event detail panel.

Run it:      uv run python -m auriga_extract.probe
Re-read it:  uv run python -m auriga_extract.probe --analyze captures/<dir>

This module is throwaway-adjacent by design: once the schemas are known, the
extraction loop is written against them and only capture.py is reused.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import sync_playwright

from .capture import CaptureSink, attach

# The planning view of the ISAE-SUPAERO Auriga instance. Overridable, because
# the hash-route may well change between school years.
DEFAULT_URL = (
    "https://isaesupaero-production.np-auriga.nfrance.net"
    "/#/mainContent/menuEntry/227/planning"
)

# How long each pump iteration waits inside Playwright. Small enough that the
# console echo feels live, large enough not to spin the CPU.
PUMP_INTERVAL_MS = 200

WALKTHROUGH = """
================================ AURIGA PROBE ================================
A real Chrome window is open. Everything it fetches is being recorded.

Do these steps IN ORDER, typing a short note here BEFORE each one (the note is
written into the capture so we can tell which requests came from which action):

  1. type: login          -> then log in and reach the planning/timetable view
  2. type: month-forward  -> then click the calendar's "next month" arrow
  3. type: click-event    -> then click ONE class to open its detail panel

Type anything else at any point to drop another marker. Type 'done' when
finished; the browser closes and a summary is printed.
==============================================================================
"""


def _launch_browser(playwright: Any, channel: Optional[str], headless: bool) -> Any:
    """
    Start a browser, preferring the user's real Chrome install.

    playwright: the sync_playwright() manager.
    channel: browser channel to try first ("chrome"), or None for bundled Chromium.
    headless: whether to run without a window (only useful for testing).
    Returns a Browser. Side effect: spawns a browser process.

    Real Chrome plus the automation-flag suppression is deliberate: the portal
    already blocks non-browser clients, so there is no reason to volunteer the
    obvious automation tells. Falls back to bundled Chromium if Chrome is
    missing rather than failing the run.
    """
    args = ["--disable-blink-features=AutomationControlled"]

    if channel:
        try:
            print(f"[probe] launching browser (channel={channel}, headless={headless})")
            return playwright.chromium.launch(channel=channel, headless=headless, args=args)
        except Exception as exc:  # noqa: BLE001
            print(f"[probe] channel '{channel}' unavailable ({exc}); using bundled Chromium")

    print(f"[probe] launching bundled Chromium (headless={headless})")
    return playwright.chromium.launch(headless=headless, args=args)


def _stdin_reader(out_queue: "queue.Queue[str]") -> None:
    """
    Read lines from stdin forever, pushing them onto a queue.

    out_queue: receives each stripped line; receives "done" on EOF.
    Runs on a daemon thread. Side effect: consumes stdin.

    Reading input on a separate thread is what lets the main thread stay inside
    Playwright calls. Blocking the main thread on input() would stall
    Playwright's event dispatch, so responses would arrive in a burst after the
    user pressed Enter -- landing on the wrong side of their marker and
    destroying the correlation this whole tool exists to produce.
    """
    for line in sys.stdin:
        out_queue.put(line.strip())
    out_queue.put("done")


def _marker_loop(page: Any, sink: CaptureSink) -> None:
    """
    Interleave user markers with live Playwright event dispatch.

    page: the Playwright Page, used purely as something to poll against.
    sink: the CaptureSink receiving markers.
    Returns when the user types a quit word or the browser goes away.
    Side effects: writes markers; prints progress.
    """
    commands: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=_stdin_reader, args=(commands,), daemon=True).start()

    print(WALKTHROUGH)

    while True:
        try:
            line = commands.get_nowait()
        except queue.Empty:
            # No input pending: spend the interval inside Playwright so queued
            # network events get dispatched to the sink right now.
            try:
                page.wait_for_timeout(PUMP_INTERVAL_MS)
            except Exception as exc:  # noqa: BLE001 - page/browser closed by the user
                print(f"[probe] browser window is gone ({type(exc).__name__}); wrapping up")
                return
            continue

        if line.lower() in {"done", "quit", "exit", "q"}:
            print("[probe] finishing up")
            return

        if not line:
            print(f"[probe] {len(sink.records)} records so far")
            continue

        sink.record_marker(line)


def _group_by_endpoint(records: list[dict[str, Any]]) -> "OrderedDict[tuple[str, str], dict]":
    """
    Collapse response records into one entry per (method, path).

    records: the capture's record list.
    Returns an ordered mapping keyed by (method, path), each value holding the
    hit count, byte totals, seen query keys and a representative JSON shape.
    """
    groups: "OrderedDict[tuple[str, str], dict]" = OrderedDict()

    for rec in records:
        if rec.get("kind") != "response":
            continue

        key = (rec["method"], rec["path"])
        group = groups.setdefault(
            key,
            {"count": 0, "total_bytes": 0, "max_bytes": 0, "query_keys": set(), "shape": None, "seqs": []},
        )
        group["count"] += 1
        group["total_bytes"] += rec.get("bytes", 0)
        group["max_bytes"] = max(group["max_bytes"], rec.get("bytes", 0))
        group["query_keys"].update(rec.get("query", {}))
        group["seqs"].append(rec["seq"])
        # Keep the shape of the biggest payload: for a calendar feed that is
        # the response actually carrying the events.
        if rec.get("json_shape") and rec.get("bytes", 0) >= group["max_bytes"]:
            group["shape"] = rec["json_shape"]

    return groups


def print_summary(records: list[dict[str, Any]]) -> None:
    """
    Print the human-readable discovery report for a capture.

    records: the capture's record list (from a live run or loaded from disk).
    Side effect: prints. Two sections -- endpoints ranked by payload size
    (biggest JSON is the likeliest event feed), then a marker-by-marker
    timeline showing which requests each user action triggered.
    """
    responses = [r for r in records if r.get("kind") == "response"]
    markers = [r for r in records if r.get("kind") == "marker"]
    print("\n" + "=" * 78)
    print(f"CAPTURE SUMMARY  --  {len(responses)} responses, {len(markers)} markers")
    print("=" * 78)

    if not responses:
        print("\nNothing was captured. Did the browser reach the planning view?")
        return

    print("\n--- ENDPOINTS (largest payload first) ---\n")
    groups = _group_by_endpoint(records)
    ranked = sorted(groups.items(), key=lambda kv: kv[1]["max_bytes"], reverse=True)

    for (method, path), group in ranked:
        print(f"{method:6} {path}")
        print(
            f"       hits={group['count']}  max={group['max_bytes']}B  "
            f"total={group['total_bytes']}B  seqs={group['seqs'][:8]}"
        )
        if group["query_keys"]:
            print(f"       query params: {sorted(group['query_keys'])}")
        if group["shape"]:
            print(f"       json: {json.dumps(group['shape'], ensure_ascii=False)[:300]}")
        print()

    print("--- TIMELINE (what each action triggered) ---\n")
    current = "(before any marker)"
    bucket: list[dict[str, Any]] = []

    def flush(label: str, items: list[dict[str, Any]]) -> None:
        """Print one marker's worth of responses, biggest first."""
        print(f"  [{label}]")
        if not items:
            print("      (no responses)")
            return
        for rec in sorted(items, key=lambda r: r.get("bytes", 0), reverse=True)[:12]:
            print(f"      #{rec['seq']:04d} {rec['method']} {rec['path'][:66]} {rec.get('bytes', 0)}B")

    for rec in records:
        if rec.get("kind") == "marker":
            flush(current, bucket)
            current = rec["text"]
            bucket = []
            continue
        if rec.get("kind") == "response":
            bucket.append(rec)
    flush(current, bucket)

    print("\nBody payloads are in the capture's bodies/ directory, named by seq.")


def load_records(capture_dir: Path) -> list[dict[str, Any]]:
    """
    Read a previously written capture index.

    capture_dir: a directory containing index.jsonl.
    Returns the record list in file order. Raises SystemExit with a clear
    message if the index is missing.
    """
    index_path = capture_dir / "index.jsonl"
    if not index_path.exists():
        raise SystemExit(f"No index.jsonl in {capture_dir}")

    records = []
    with index_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    print(f"[probe] loaded {len(records)} records from {index_path}")
    return records


def run_probe(url: str, out_root: Path, channel: Optional[str], headless: bool) -> Path:
    """
    Drive one interactive discovery session end to end.

    url: page to open before handing control to the user.
    out_root: parent directory for capture folders; a timestamped subfolder is created.
    channel: preferred browser channel, or None for bundled Chromium.
    headless: run without a window (testing only -- manual login needs a window).
    Returns the capture directory. Side effects: launches a browser, writes the
    capture, prints a summary.
    """
    capture_dir = out_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    sink = CaptureSink(capture_dir)

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, channel, headless)
        # French locale and Paris timezone so any server-side date formatting
        # matches what the timetable is meant to express; no_viewport lets the
        # page use the real window size, which matters for a calendar grid.
        context = browser.new_context(
            locale="fr-FR",
            timezone_id="Europe/Paris",
            no_viewport=True,
        )
        attach(context, sink)
        page = context.new_page()

        try:
            print(f"[probe] navigating to {url}")
            # domcontentloaded, not networkidle: this is an SPA that may poll,
            # so waiting for network silence can hang until timeout.
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            print(f"[probe] initial navigation problem ({type(exc).__name__}: {exc})")
            print("[probe] the window is still open -- navigate manually if needed")

        try:
            _marker_loop(page, sink)
        except KeyboardInterrupt:
            print("\n[probe] interrupted")
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001 - already gone if the user closed it
                pass
            sink.close()

    print_summary(sink.records)
    return capture_dir


def main(argv: Optional[list[str]] = None) -> int:
    """
    CLI entrypoint. Returns a process exit code.

    argv: argument list for testing; defaults to sys.argv[1:].
    """
    parser = argparse.ArgumentParser(
        prog="auriga-probe",
        description="Record the Auriga portal's network traffic to discover its API.",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="page to open (default: planning view)")
    parser.add_argument("--out", default="captures", type=Path, help="where capture folders go")
    parser.add_argument(
        "--channel",
        default="chrome",
        help="browser channel; pass empty string to force bundled Chromium",
    )
    parser.add_argument("--headless", action="store_true", help="no window (testing only)")
    parser.add_argument(
        "--analyze",
        type=Path,
        metavar="CAPTURE_DIR",
        help="skip the browser; re-print the summary for an existing capture",
    )
    args = parser.parse_args(argv)

    if args.analyze:
        print_summary(load_records(args.analyze))
        return 0

    capture_dir = run_probe(args.url, args.out, args.channel or None, args.headless)
    print(f"\n[probe] capture saved to: {capture_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
