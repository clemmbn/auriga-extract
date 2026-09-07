"""
Interactive selection layer: show the discovered courses and let the user pick.

Deliberately dependency-free (plain input()) per the spec. The listing is
ordered by the grouping layer -- real courses first, biggest first -- and
description-only groups are shown under their own heading so it is obvious
which entries lack a pedagogical unit code.
"""

from __future__ import annotations

from collections.abc import Iterable

from rich import box
from rich.table import Table

from .console import console
from .courses import Course

# Words that select everything, in both languages the portal uses.
SELECT_ALL = {"all", "tout", "tous", "*"}
SELECT_NONE = {"none", "aucun", "rien"}


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


def _summarize(course: Course) -> str:
    """Build the second, detail line shown for one course in the listing."""
    bits: list[str] = []
    if course.has_unit:
        bits.append(course.display_code)
    if course.activity_codes:
        bits.append(",".join(course.activity_codes))

    instructors = course.instructors
    if instructors:
        shown = ", ".join(instructors[:2])
        if len(instructors) > 2:
            shown += f" +{len(instructors) - 2}"
        bits.append(shown)

    return " · ".join(bits) if bits else "(no metadata)"


def _course_table(rows: list[tuple[str, str, str, str]], title: str, title_style: str) -> Table:
    """Build one bordered rich Table for a block of course rows (# / sessions / title / details)."""
    table = Table(
        title=title,
        title_style=f"bold {title_style}",
        title_justify="left",
        header_style="bold cyan",
        box=box.ROUNDED,
        border_style=title_style,
        show_lines=False,
    )
    table.add_column("#", justify="right", style="bold cyan", no_wrap=True)
    table.add_column("Sessions", justify="right", style="magenta")
    table.add_column("Title", style="white")
    table.add_column("Details", style="dim")
    for row in rows:
        table.add_row(*row)
    return table


def render(courses: Iterable[Course]) -> None:
    """
    Print the numbered course listing as bordered rich tables.

    courses: the grouped courses, already ordered for display (real courses
    first, then description-only groups -- see courses.group_courses).
    Side effect: prints. Description-only groups get their own bordered table
    so the distinction stays visible before the user picks.
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
    console.print(_course_table(coded_rows, "COURSES FOUND", "cyan"))

    if no_unit_rows:
        console.print()
        console.print(
            _course_table(
                no_unit_rows,
                "No pedagogical unit code (one-off events, holidays, some classes)",
                "yellow",
            )
        )


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
