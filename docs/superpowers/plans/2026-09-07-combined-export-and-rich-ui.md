# Combined Export, All-But Selection, and Rich UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-course `.ics` output with one combined file, add an "all but some" selection syntax to the picker, and switch all terminal output to `rich`.

**Architecture:** No new layers. `ics.py` gains a combined-calendar builder that replaces the per-course one; `select.py`'s parser gains one new branch and its rendering switches from raw `print` to a `rich.table.Table`; every module's `print()` calls become `console.print()` calls through one shared `rich.console.Console` instance in a new `console.py`.

**Tech Stack:** Python 3.11, `rich` (new), `pytest` (new, dev-only — this repo has no test suite yet), `icalendar` (existing), `uv` for environment management.

**Reference spec:** [docs/superpowers/specs/2026-09-07-combined-export-and-rich-ui-design.md](../specs/2026-09-07-combined-export-and-rich-ui-design.md)

---

### Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `rich` as a runtime dependency**

Run:
```bash
uv add rich
```
Expected: `pyproject.toml`'s `dependencies` list gains `"rich>=..."` and `uv.lock` updates.

- [ ] **Step 2: Add `pytest` as a dev dependency**

Run:
```bash
uv add --dev pytest
```
Expected: `pyproject.toml` gains a `[dependency-groups]` (or `[tool.uv]` dev-dependencies) section with `pytest`.

- [ ] **Step 3: Verify the environment installs cleanly**

Run:
```bash
uv sync
```
Expected: exits 0, no errors.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add rich and pytest dependencies"
```

---

### Task 2: Shared console module

**Files:**
- Create: `auriga_extract/console.py`

- [ ] **Step 1: Create the shared console instance**

```python
"""
Shared rich Console instance.

Every module that prints to the terminal imports this instance instead of
using bare print(), so all output goes through one stream with consistent
styling.
"""

from __future__ import annotations

from rich.console import Console

console = Console()
```

- [ ] **Step 2: Verify it imports cleanly**

Run:
```bash
uv run python -c "from auriga_extract.console import console; console.print('[green]ok[/]')"
```
Expected: prints `ok` in green, no errors.

- [ ] **Step 3: Commit**

```bash
git add auriga_extract/console.py
git commit -m "feat: add shared rich console"
```

---

### Task 3: "All but some" selection syntax

**Files:**
- Modify: `auriga_extract/select.py:21-70` (the `parse_selection` function)
- Test: `tests/test_select.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_select.py`:

```python
from auriga_extract.select import parse_selection


def test_plain_all_unaffected():
    assert parse_selection("all", 4) == [0, 1, 2, 3]


def test_plain_none_unaffected():
    assert parse_selection("none", 4) == []


def test_all_but_excludes_single_indices():
    assert parse_selection("all !3,5", 6) == [0, 1, 3, 5]


def test_all_but_excludes_a_range():
    assert parse_selection("all !2-4", 6) == [0, 4, 5]


def test_all_but_no_space_before_bang():
    assert parse_selection("all!3,5", 6) == [0, 1, 3, 5]


def test_all_but_with_tout_synonym():
    assert parse_selection("tout !1", 3) == [1, 2]


def test_all_but_empty_exclusion_raises():
    try:
        parse_selection("all !", 6)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_all_but_out_of_range_exclusion_raises():
    try:
        parse_selection("all !99", 6)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_all_but_excluding_everything_raises():
    try:
        parse_selection("all !1-6", 6)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_all_but_malformed_exclusion_raises():
    try:
        parse_selection("all !x", 6)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_unrecognized_bang_prefix_raises():
    try:
        parse_selection("foo !3", 6)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest tests/test_select.py -v
```
Expected: the plain `all`/`none` tests PASS (unchanged behavior), every `all !...` test FAILS (raises `ValueError: 'all !...' is not a number` or similar from the current parser, since it doesn't understand `!` yet).

- [ ] **Step 3: Implement the exclusion syntax**

Replace `auriga_extract/select.py` lines 21-70 (the current `parse_selection` function) with:

```python
def _parse_indices(text: str, count: int) -> set[int]:
    """
    Parse a comma/range list of 1-based indices, validated against count.

    text: e.g. "1,3,5" or "1-4, 7" (already lowercased).
    count: how many items are on offer.
    Returns a set of 1-based indices. Raises ValueError on anything
    unparseable or out of range. Shared by plain selections and by the
    exclusion list after "all !".
    """
    chosen: set[int] = set()
    for piece in text.replace(" ", ",").split(","):
        if not piece:
            continue

        if "-" in piece:
            low, _, high = piece.partition("-")
            if not low.isdigit() or not high.isdigit():
                raise ValueError(f"'{piece}' is not a valid range like 3-7")
            start, stop = int(low), int(high)
            if start > stop:
                start, stop = stop, start
            for number in range(start, stop + 1):
                chosen.add(number)
            continue

        if not piece.isdigit():
            raise ValueError(f"'{piece}' is not a number")
        chosen.add(int(piece))

    # Validate after expansion so "1-99" reports the bad bound, not each value.
    out_of_range = sorted(n for n in chosen if n < 1 or n > count)
    if out_of_range:
        # A fat-fingered range like "1-999" would otherwise print hundreds of
        # numbers, burying the actual message.
        shown = ", ".join(str(n) for n in out_of_range[:6])
        if len(out_of_range) > 6:
            shown += f", ... ({len(out_of_range)} values)"
        raise ValueError(f"out of range (1-{count}): {shown}")

    return chosen


def parse_selection(text: str, count: int) -> list[int]:
    """
    Turn a user's selection string into zero-based indices.

    text: raw input, e.g. "1,3,5", "1-4, 7", "all", "all !3,5", "none".
    count: how many items are on offer.
    Returns sorted unique zero-based indices.
    Raises ValueError with a user-facing message on anything unparseable or
    out of range.

    Ranges and commas are both supported because a year's timetable produces
    long lists where "1-6" is much less error-prone than typing six numbers.
    "all !<list>" selects everything except the given indices, for when most
    of the timetable is wanted and only a few entries should be dropped.
    """
    cleaned = (text or "").strip().lower()
    if not cleaned or cleaned in SELECT_NONE:
        return []
    if cleaned in SELECT_ALL:
        return list(range(count))

    if "!" in cleaned:
        head, _, remainder = cleaned.partition("!")
        if head.strip() not in SELECT_ALL:
            raise ValueError(f"'{text}' is not a valid selection")
        if not remainder.strip():
            raise ValueError("'all !' needs numbers to exclude, e.g. 'all !3,5'")
        excluded = _parse_indices(remainder, count)
        picked = sorted(set(range(1, count + 1)) - excluded)
        if not picked:
            raise ValueError("excluding everything leaves nothing selected")
        return sorted(n - 1 for n in picked)

    chosen = _parse_indices(cleaned, count)
    return sorted(n - 1 for n in chosen)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_select.py -v
```
Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add auriga_extract/select.py tests/test_select.py
git commit -m "feat: support 'all !3,5' exclusion syntax in the picker"
```

---

### Task 4: Rich-ify the picker (`select.py`)

**Files:**
- Modify: `auriga_extract/select.py` (imports, `_summarize` unchanged, `render`, `prompt`)

- [ ] **Step 1: Update imports and the prompt hint text**

At the top of `auriga_extract/select.py`, replace:

```python
from __future__ import annotations

from collections.abc import Iterable

from .courses import Course
```

with:

```python
from __future__ import annotations

from collections.abc import Iterable

from rich.table import Table

from .console import console
from .courses import Course
```

- [ ] **Step 2: Replace `render()` with a rich-table version**

Replace the current `render()` function (the one starting `def render(courses: Iterable[Course]) -> None:`) with:

```python
def _course_table(rows: list[tuple[str, str, str, str]], show_header: bool = True) -> Table:
    """Build one rich Table for a block of course rows (# / sessions / title / details)."""
    table = Table(show_header=show_header, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("#", justify="right", style="bold cyan", no_wrap=True)
    table.add_column("Sessions", justify="right", style="magenta")
    table.add_column("Title", style="white")
    table.add_column("Details", style="dim")
    for row in rows:
        table.add_row(*row)
    return table


def render(courses: Iterable[Course]) -> None:
    """
    Print the numbered course listing as rich tables.

    courses: the grouped courses, already ordered for display (real courses
    first, then description-only groups -- see courses.group_courses).
    Side effect: prints. Description-only groups get their own table under a
    styled heading so the distinction stays visible before the user picks.
    """
    coded_rows: list[tuple[str, str, str, str]] = []
    no_unit_rows: list[tuple[str, str, str, str]] = []

    for index, course in enumerate(courses, start=1):
        row = (str(index), str(len(course.occurrences)), course.title[:62], _summarize(course)[:70])
        if course.has_unit:
            coded_rows.append(row)
        else:
            no_unit_rows.append(row)

    console.print()
    console.print("[bold cyan]COURSES FOUND[/]")
    console.print(_course_table(coded_rows))

    if no_unit_rows:
        console.print(
            "\n[yellow]--- no pedagogical unit code "
            "(one-off events, holidays, some classes) ---[/]"
        )
        console.print(_course_table(no_unit_rows, show_header=False))
```

- [ ] **Step 3: Replace `prompt()`'s `print` calls with `console.print`**

Replace the current `prompt()` function body with:

```python
def prompt(courses: list[Course]) -> list[Course]:
    """
    Show the listing and ask the user which courses to export.

    courses: grouped courses.
    Returns the selected subset, in listing order (possibly empty).
    Side effects: prints; reads stdin, re-prompting until the input parses.
    Treats EOF/Ctrl-C as selecting nothing rather than crashing.
    """
    if not courses:
        console.print("[yellow][select][/] nothing to choose from")
        return []

    render(courses)

    while True:
        console.print(
            "\n[bold]Which courses?[/] Numbers ('1,3,5'), ranges ('1-6'), "
            "'all', 'all !3,5' to exclude, or 'none' to abort."
        )
        try:
            raw = console.input("[bold cyan]> [/]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow][select][/] aborted")
            return []

        try:
            indices = parse_selection(raw, len(courses))
        except ValueError as exc:
            console.print(f"[red][select][/] {exc} -- try again")
            continue

        picked = [courses[i] for i in indices]
        if not picked:
            console.print("[yellow][select][/] nothing selected")
            return []

        console.print(f"[green][select][/] {len(picked)} course(s) selected:")
        for course in picked:
            console.print(f"   - {course.title[:60]} ({len(course.occurrences)} sessions)")
        return picked
```

- [ ] **Step 4: Re-run the selection tests to confirm nothing broke**

Run:
```bash
uv run pytest tests/test_select.py -v
```
Expected: all 11 tests still PASS (they only exercise `parse_selection`, unaffected by the rendering change).

- [ ] **Step 5: Manually sanity-check rendering**

Run:
```bash
uv run python -c "
from auriga_extract.courses import Course
from auriga_extract.select import render
render([
    Course(key='A', unit_codes=['A1'], title='Maths', occurrences=[{}, {}]),
    Course(key='sans-code:x', title='3A LV1-ANGLAIS', occurrences=[{}]),
])
"
```
Expected: a cyan "COURSES FOUND" table with the Maths row, then a yellow heading and a second table with the LV1-ANGLAIS row, no tracebacks.

- [ ] **Step 6: Commit**

```bash
git add auriga_extract/select.py
git commit -m "feat: render the course picker as a rich table"
```

---

### Task 5: Combined `.ics` output (`ics.py`)

**Files:**
- Modify: `auriga_extract/ics.py`
- Test: `tests/test_ics.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ics.py`:

```python
from datetime import date, datetime, timezone
from pathlib import Path

from auriga_extract.courses import Course
from auriga_extract.ics import build_combined_calendar, write_calendar


def _intervention(id_: int, start: str, end: str) -> dict:
    return {
        "id": id_,
        "startDateTime": start,
        "endDateTime": end,
        "activityType": {"code": "CM", "caption": {"fr": "Cours magistral"}},
        "interventionInstructors": [],
        "interventionResources": [],
        "interventionPopulations": [],
    }


def _course(key: str, code: str, title: str, occurrences: list[dict]) -> Course:
    return Course(key=key, unit_codes=[code], title=title, occurrences=occurrences)


def test_build_combined_calendar_includes_every_course():
    course_a = _course(
        "A", "A1", "Maths",
        [_intervention(1, "2026-09-17T06:30:00Z", "2026-09-17T08:00:00Z")],
    )
    course_b = _course(
        "B", "B1", "Physique",
        [_intervention(2, "2026-09-18T06:30:00Z", "2026-09-18T08:00:00Z")],
    )

    calendar = build_combined_calendar(
        [course_a, course_b],
        date(2026, 9, 1),
        date(2026, 9, 30),
        stamp=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
    )

    events = calendar.walk("VEVENT")
    assert len(events) == 2
    uids = {str(event["uid"]) for event in events}
    assert uids == {
        "auriga-1@auriga.isae-supaero",
        "auriga-2@auriga.isae-supaero",
    }

    timezones = calendar.walk("VTIMEZONE")
    assert len(timezones) == 1


def test_build_combined_calendar_skips_unusable_dates():
    course = _course(
        "A", "A1", "Maths",
        [
            _intervention(1, "2026-09-17T06:30:00Z", "2026-09-17T08:00:00Z"),
            {"id": 2, "startDateTime": None, "endDateTime": None},
        ],
    )

    calendar = build_combined_calendar([course], date(2026, 9, 1), date(2026, 9, 30))

    events = calendar.walk("VEVENT")
    assert len(events) == 1


def test_write_calendar_writes_one_file(tmp_path: Path):
    course = _course(
        "A", "A1", "Maths",
        [_intervention(1, "2026-09-17T06:30:00Z", "2026-09-17T08:00:00Z")],
    )

    path = write_calendar([course], tmp_path, date(2026, 9, 1), date(2026, 9, 30))

    assert path == tmp_path / "auriga_2026-09-01_2026-09-30.ics"
    assert path.exists()
    contents = path.read_text()
    assert contents.count("BEGIN:VEVENT") == 1
    assert contents.count("BEGIN:VTIMEZONE") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
uv run pytest tests/test_ics.py -v
```
Expected: FAIL with `ImportError: cannot import name 'build_combined_calendar'` (neither function exists yet).

- [ ] **Step 3: Replace the per-course builder and writer with combined versions**

In `auriga_extract/ics.py`, replace the entire block from `def build_calendar(course: Course, ...` (starts at line 152) through the end of `write_courses` (ends at line 246, the final `return written`) with:

```python
def _add_course_events(calendar: Calendar, course: Course, dtstamp: datetime) -> None:
    """
    Add every occurrence of one course as a VEVENT on an existing Calendar.

    calendar: the in-progress combined Calendar to append to.
    course: the course whose occurrences become events.
    dtstamp: DTSTAMP value shared by every event in this export run.
    Side effect: mutates calendar. Skips occurrences with no usable
    start/end instant, printing why.
    """
    for intervention in course.occurrences:
        start = parse_instant(intervention.get("startDateTime"))
        end = parse_instant(intervention.get("endDateTime"))
        if not start or not end:
            console.print(
                f"[yellow][ics][/] skipping session {intervention.get('id')}: unusable dates"
            )
            continue

        event = Event()
        event.add("uid", f"auriga-{intervention.get('id')}@{UID_DOMAIN}")
        event.add("summary", _summary_for(course, intervention))
        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("dtstamp", dtstamp)

        location = _rooms_for(intervention)
        if location:
            event.add("location", location)

        description = _describe_occurrence(course, intervention)
        if description:
            event.add("description", description)

        calendar.add_component(event)


def build_combined_calendar(
    courses: list[Course], start: date, end: date, stamp: Optional[datetime] = None
) -> Calendar:
    """
    Render every selected course into one icalendar Calendar.

    courses: the selected courses, in the order they should be added.
    start, end: the requested export range; used for the calendar's display
    name and to pad the VTIMEZONE bounds if no occurrence has usable dates.
    stamp: DTSTAMP value; defaults to now (injectable for deterministic tests).
    Returns one Calendar containing a single VTIMEZONE plus one VEVENT per
    occurrence, across all courses, that has a usable start and end.
    """
    calendar = Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    # Apple Calendar shows this as the imported calendar's name.
    calendar.add("x-wr-calname", f"ISAE-SUPAERO {start.isoformat()} → {end.isoformat()}")
    calendar.add("x-wr-timezone", TZID)

    all_starts = [
        parsed
        for course in courses
        for parsed in (parse_instant(i.get("startDateTime")) for i in course.occurrences)
        if parsed
    ]
    # Bound the VTIMEZONE to the data's own span (padded a year each way)
    # rather than emitting decades of DST transitions. Fall back to the
    # requested range if every occurrence turned out to be unusable.
    tz_first = (min(all_starts).date() if all_starts else start) - timedelta(days=365)
    tz_last = (max(all_starts).date() if all_starts else end) + timedelta(days=365)
    calendar.add_component(Timezone.from_tzinfo(SCHOOL_TZ, TZID, tz_first, tz_last))

    dtstamp = stamp or datetime.now(tz=ZoneInfo("UTC"))
    for course in courses:
        _add_course_events(calendar, course, dtstamp)

    return calendar


def write_calendar(courses: list[Course], out_root: Path, start: date, end: date) -> Path:
    """
    Write every selected course into one combined .ics file.

    courses: the selected courses.
    out_root: directory the file is written into (created if missing).
    start, end: the requested range, used for the filename and calendar name.
    Returns the written path. Side effects: creates out_root, writes one
    file, prints progress.
    """
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / f"auriga_{start.isoformat()}_{end.isoformat()}.ics"
    calendar = build_combined_calendar(courses, start, end)
    path.write_bytes(calendar.to_ical())

    total_sessions = sum(len(course.occurrences) for course in courses)
    console.print(
        f"[dim][ics][/] wrote [green]{path.name}[/] "
        f"({len(courses)} course(s), {total_sessions} session(s))"
    )
    return path
```

- [ ] **Step 4: Remove the now-unused `sanitize_filename` function**

In `auriga_extract/ics.py`, delete the `sanitize_filename` function (lines 65-78, the one starting `def sanitize_filename(name: str, fallback: str = "cours") -> str:`) and the `_UNSAFE_FILENAME` regex constant just above it (line 38, `_UNSAFE_FILENAME = re.compile(...)`) — the new filename is built only from ISO dates, which are already filesystem-safe. Also remove the now-unused `import re` and `import unicodedata` lines at the top of the file if nothing else in the file uses them (check with `grep -n "re\.\|unicodedata\." auriga_extract/ics.py` after deleting — the docstring `Turn a course code...` etc. goes with the function).

- [ ] **Step 5: Update imports at the top of `ics.py`**

Replace:

```python
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, Timezone

from .courses import Course, caption
```

with:

```python
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, Timezone

from .console import console
from .courses import Course, caption
```

(Only remove `import re` / `import unicodedata` if Step 4's grep confirmed they're unused elsewhere in the file.)

- [ ] **Step 6: Update the module docstring**

Replace the file's top docstring (lines 1-14):

```python
"""
ICS generation layer: one .ics file per selected course.
...
"""
```

with:

```python
"""
ICS generation layer: one combined .ics file for every selected course.

Timezone handling is the part most likely to go subtly wrong, so it is worth
being explicit. The API returns instants in UTC (`2026-09-17T06:30:00Z`), while
the school thinks in Europe/Paris. We convert to Europe/Paris and emit local
times with a TZID, bundling a single real VTIMEZONE component covering every
selected course's own date span. That is what makes the file self-contained
and unambiguous across the March/October DST switches -- a TZID without a
VTIMEZONE is technically invalid and some clients silently guess.

UIDs are derived from the portal's own intervention id, so re-importing an
updated export updates events in place instead of duplicating them.
"""
```

- [ ] **Step 7: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_ics.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add auriga_extract/ics.py tests/test_ics.py
git commit -m "feat: write one combined .ics instead of one per course"
```

---

### Task 6: Wire `cli.py` to the combined writer and rich banner

**Files:**
- Modify: `auriga_extract/cli.py`

- [ ] **Step 1: Update imports**

Replace:

```python
from playwright.sync_api import sync_playwright

from .capture import CaptureSink, attach
from .courses import group_courses
from .fetch import TokenSniffer, fetch_interventions, wait_for_token
from .ics import write_courses
from .probe import DEFAULT_URL, _launch_browser
from .select import prompt
```

with:

```python
from playwright.sync_api import sync_playwright
from rich.panel import Panel

from .capture import CaptureSink, attach
from .console import console
from .courses import group_courses
from .fetch import TokenSniffer, fetch_interventions, wait_for_token
from .ics import write_calendar
from .probe import DEFAULT_URL, _launch_browser
from .select import prompt
```

- [ ] **Step 2: Replace the plain-text banner with a rich Panel**

Replace:

```python
LOGIN_BANNER = """
=============================== AURIGA EXTRACT ===============================
A browser window is open on the portal.

  ->  Log in as you normally would.

Nothing else is needed: as soon as the portal makes its first authenticated
request, this tool picks the session up automatically and starts fetching.
==============================================================================
"""
```

with:

```python
LOGIN_BANNER = (
    "A browser window is open on the portal.\n\n"
    "  ->  Log in as you normally would.\n\n"
    "Nothing else is needed: as soon as the portal makes its first\n"
    "authenticated request, this tool picks the session up automatically\n"
    "and starts fetching."
)
```

- [ ] **Step 3: Replace `print` calls in `run()` with styled `console.print`/`Panel`**

Replace:

```python
        try:
            print(f"[cli] opening {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            print(f"[cli] navigation problem ({type(exc).__name__}: {exc})")
            print("[cli] the window is open -- navigate to the portal manually")

        print(LOGIN_BANNER)
```

with:

```python
        try:
            console.print(f"[dim][cli][/] opening {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow][cli][/] navigation problem ({type(exc).__name__}: {exc})")
            console.print("[yellow][cli][/] the window is open -- navigate to the portal manually")

        console.print(Panel(LOGIN_BANNER, title="AURIGA EXTRACT", border_style="cyan"))
```

Replace:

```python
        try:
            token = wait_for_token(page, sniffer)
            interventions = fetch_interventions(page, _origin(url), token, start, end)
        except Exception as exc:  # noqa: BLE001
            print(f"[cli] extraction failed: {exc}")
            return 1
```

with:

```python
        try:
            token = wait_for_token(page, sniffer)
            interventions = fetch_interventions(page, _origin(url), token, start, end)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red][cli][/] extraction failed: {exc}")
            return 1
```

Replace:

```python
    if not interventions:
        print("[cli] no events in that range -- nothing to export")
        return 0

    courses = group_courses(interventions)
    selected = prompt(courses)
    if not selected:
        print("[cli] nothing selected; no files written")
        return 0

    paths = write_courses(selected, out_root, start, end)
    print(f"\n[cli] done -- {len(paths)} file(s) written")
    print("[cli] double-click any .ics to import it into Apple Calendar")
    return 0
```

with:

```python
    if not interventions:
        console.print("[yellow][cli][/] no events in that range -- nothing to export")
        return 0

    courses = group_courses(interventions)
    selected = prompt(courses)
    if not selected:
        console.print("[yellow][cli][/] nothing selected; no files written")
        return 0

    path = write_calendar(selected, out_root, start, end)
    console.print(f"\n[green][cli][/] done -- wrote [bold]{path.name}[/]")
    console.print("[dim][cli][/] double-click the .ics to import it into Apple Calendar")
    return 0
```

- [ ] **Step 4: Update the module docstring's "then group, let the user pick, write .ics files" line if it references per-course output**

The current docstring already says "group, let the user pick, write .ics files" (line 9), which is still accurate for the combined file — no change needed here.

- [ ] **Step 5: Sanity-check the module imports and parses**

Run:
```bash
uv run python -c "import auriga_extract.cli"
```
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add auriga_extract/cli.py
git commit -m "feat: wire cli to the combined .ics writer and rich banner"
```

---

### Task 7: Style `fetch.py` output

**Files:**
- Modify: `auriga_extract/fetch.py`

- [ ] **Step 1: Add the console import**

Replace:

```python
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional
from urllib.parse import urlencode
```

with:

```python
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional
from urllib.parse import urlencode

from .console import console
```

- [ ] **Step 2: Style the token-capture message**

Replace:

```python
        if is_new:
            print("[fetch] captured a fresh bearer token from the app's own traffic")
```

with:

```python
        if is_new:
            console.print("[green][fetch][/] captured a fresh bearer token from the app's own traffic")
```

- [ ] **Step 3: Style the wait message**

Replace:

```python
    print("[fetch] waiting for the portal to make an authenticated request...")
```

with:

```python
    console.print("[cyan][fetch][/] waiting for the portal to make an authenticated request...")
```

- [ ] **Step 4: Style the per-run and per-month progress messages**

Replace:

```python
    by_id: dict[Any, dict[str, Any]] = {}
    chunks = month_chunks(start, end)
    print(f"[fetch] {len(chunks)} month(s) to fetch, {start} -> {end}")
```

with:

```python
    by_id: dict[Any, dict[str, Any]] = {}
    chunks = month_chunks(start, end)
    console.print(f"[cyan][fetch][/] [bold]{len(chunks)}[/] month(s) to fetch, {start} -> {end}")
```

Replace:

```python
        interventions = (payload or {}).get("interventions") or []
        # Month chunks do not overlap, but an event spanning a boundary could
        # appear twice; keying by id makes the merge idempotent regardless.
        for item in interventions:
            by_id[item.get("id")] = item
        print(f"[fetch] {label}: {len(interventions)} events (running total {len(by_id)})")

    ordered = sorted(by_id.values(), key=lambda i: str(i.get("startDateTime") or ""))
    print(f"[fetch] collected {len(ordered)} unique events")
    return ordered
```

with:

```python
        interventions = (payload or {}).get("interventions") or []
        # Month chunks do not overlap, but an event spanning a boundary could
        # appear twice; keying by id makes the merge idempotent regardless.
        for item in interventions:
            by_id[item.get("id")] = item
        console.print(
            f"[dim][fetch][/] {label}: [bold]{len(interventions)}[/] events "
            f"(running total {len(by_id)})"
        )

    ordered = sorted(by_id.values(), key=lambda i: str(i.get("startDateTime") or ""))
    console.print(f"[green][fetch][/] collected [bold]{len(ordered)}[/] unique events")
    return ordered
```

- [ ] **Step 5: Sanity-check the module imports and parses**

Run:
```bash
uv run python -c "import auriga_extract.fetch"
```
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add auriga_extract/fetch.py
git commit -m "style: switch fetch.py progress output to rich"
```

---

### Task 8: Style `courses.py` output

**Files:**
- Modify: `auriga_extract/courses.py`

- [ ] **Step 1: Add the console import**

Replace:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional
```

with:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .console import console
```

- [ ] **Step 2: Style the grouping-summary message**

Replace:

```python
    n_coded = sum(1 for c in courses if c.has_unit)
    print(
        f"[courses] {len(interventions)} events -> {len(courses)} groups "
        f"({n_coded} with a unit code, {len(courses) - n_coded} without)"
    )
    return courses
```

with:

```python
    n_coded = sum(1 for c in courses if c.has_unit)
    console.print(
        f"[cyan][courses][/] {len(interventions)} events -> "
        f"[bold]{len(courses)}[/] groups "
        f"({n_coded} with a unit code, {len(courses) - n_coded} without)"
    )
    return courses
```

- [ ] **Step 3: Sanity-check the module imports and parses**

Run:
```bash
uv run python -c "import auriga_extract.courses"
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add auriga_extract/courses.py
git commit -m "style: switch courses.py summary output to rich"
```

---

### Task 9: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run:
```bash
uv run pytest -v
```
Expected: all tests across `tests/test_select.py` and `tests/test_ics.py` PASS.

- [ ] **Step 2: Run whatever type checker/linter is already configured**

Run:
```bash
grep -E "mypy|ruff|pylint|flake8" pyproject.toml
```
If a tool is configured, run it (e.g. `uv run ruff check .`, `uv run mypy .`) and fix any new issues introduced by this change. If nothing is configured, skip — do not add a new tool as part of this change.

- [ ] **Step 3: Manually verify the combined export end-to-end with synthetic data**

Run:
```bash
uv run python -c "
from datetime import date
from pathlib import Path
from auriga_extract.courses import Course
from auriga_extract.ics import write_calendar

courses = [
    Course(key='A', unit_codes=['A1'], title='Maths',
           occurrences=[{'id': 1, 'startDateTime': '2026-09-17T06:30:00Z',
                         'endDateTime': '2026-09-17T08:00:00Z',
                         'activityType': {'code': 'CM', 'caption': {'fr': 'Cours magistral'}},
                         'interventionInstructors': [], 'interventionResources': [],
                         'interventionPopulations': []}]),
    Course(key='B', unit_codes=['B1'], title='Physique',
           occurrences=[{'id': 2, 'startDateTime': '2026-09-18T06:30:00Z',
                         'endDateTime': '2026-09-18T08:00:00Z',
                         'activityType': {'code': 'TP', 'caption': {'fr': 'Travaux pratiques'}},
                         'interventionInstructors': [], 'interventionResources': [],
                         'interventionPopulations': []}]),
]
path = write_calendar(courses, Path('/tmp/auriga_verify'), date(2026, 9, 1), date(2026, 9, 30))
print(path.read_text())
"
```
Expected: one file written under `/tmp/auriga_verify/`, printed contents show exactly one `VTIMEZONE` and two `VEVENT` blocks (Maths and Physique), each with correct `TZID=Europe/Paris` local times (08:30 and 08:30 respectively, since both are 06:30 UTC).

- [ ] **Step 4: Manually verify the "all but" picker syntax interactively**

Run:
```bash
uv run python -c "
from auriga_extract.courses import Course
from auriga_extract.select import prompt

courses = [Course(key=str(i), unit_codes=[f'U{i}'], title=f'Course {i}', occurrences=[{}]) for i in range(1, 6)]
picked = prompt(courses)
print('picked:', [c.title for c in picked])
"
```
At the prompt, type `all !2,4` and press Enter.
Expected: the rich table renders all 5 courses; after input, output shows `picked: ['Course 1', 'Course 3', 'Course 5']`.

- [ ] **Step 5: Clean up the manual-verification temp file**

```bash
rm -rf /tmp/auriga_verify
```

- [ ] **Step 6: Update CLAUDE.md's stale per-course-file claims**

In the root `CLAUDE.md`, replace:

```
Working end to end. `uv run python extract_schedule.py --start … --end …` opens a browser, waits for manual login, fetches the range, and writes one `.ics` per selected course.
```

with:

```
Working end to end. `uv run python extract_schedule.py --start … --end …` opens a browser, waits for manual login, fetches the range, and writes one combined `.ics` for all selected courses.
```

Replace:

```
Verified against a mock API replaying real captured payloads: 76 events → 23 courses → 23 files, all UIDs unique, every event round-tripping to the exact instant the API reported. Not yet run end to end against the live portal with a real login.
```

with:

```
Verified against a mock API replaying real captured payloads: 76 events → 23 courses grouped correctly, all UIDs unique, every event round-tripping to the exact instant the API reported. Combined single-file writing verified separately (see docs/superpowers/plans/2026-09-07-combined-export-and-rich-ui.md, Task 9). Not yet run end to end against the live portal with a real login.
```

Replace:

```
A Python CLI that logs into ISAE-SUPAERO's web-based timetable portal (an ADE Campus-style planning SPA), extracts class events over a user-specified date range via Playwright network interception, lets the user interactively pick which courses to keep, and generates one `.ics` file per selected course.
```

with:

```
A Python CLI that logs into ISAE-SUPAERO's web-based timetable portal (an ADE Campus-style planning SPA), extracts class events over a user-specified date range via Playwright network interception, lets the user interactively pick which courses to keep, and generates one combined `.ics` file for all selected courses.
```

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: reflect combined .ics output in CLAUDE.md"
```
