"""
Interactive selection layer: show the discovered courses and let the user pick.

Deliberately dependency-free (plain input()) per the spec. The listing is
ordered by the grouping layer -- real courses first, biggest first -- and
description-only groups are shown under their own heading so it is obvious
which entries lack a pedagogical unit code.
"""

from __future__ import annotations

from collections.abc import Iterable

from .courses import Course

# Words that select everything, in both languages the portal uses.
SELECT_ALL = {"all", "tout", "tous", "*"}
SELECT_NONE = {"none", "aucun", "rien"}


def parse_selection(text: str, count: int) -> list[int]:
    """
    Turn a user's selection string into zero-based indices.

    text: raw input, e.g. "1,3,5", "1-4, 7", "all", "none".
    count: how many items are on offer.
    Returns sorted unique zero-based indices.
    Raises ValueError with a user-facing message on anything unparseable or
    out of range.

    Ranges and commas are both supported because a year's timetable produces
    long lists where "1-6" is much less error-prone than typing six numbers.
    """
    cleaned = (text or "").strip().lower()
    if not cleaned or cleaned in SELECT_NONE:
        return []
    if cleaned in SELECT_ALL:
        return list(range(count))

    chosen: set[int] = set()
    for piece in cleaned.replace(" ", ",").split(","):
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


def render(courses: Iterable[Course]) -> None:
    """
    Print the numbered course listing.

    courses: the grouped courses, already ordered for display.
    Side effect: prints. Groups without a unit code get a heading so the
    distinction is visible before the user picks.
    """
    print()
    print("=" * 78)
    print("COURSES FOUND")
    print("=" * 78)

    heading_shown = False
    for index, course in enumerate(courses, start=1):
        if not course.has_unit and not heading_shown:
            print("\n--- no pedagogical unit code (one-off events, holidays, some classes) ---")
            heading_shown = True
        print(f"{index:3}. {len(course.occurrences):3}x  {course.title[:62]}")
        print(f"        {_summarize(course)[:70]}")


def prompt(courses: list[Course]) -> list[Course]:
    """
    Show the listing and ask the user which courses to export.

    courses: grouped courses.
    Returns the selected subset, in listing order (possibly empty).
    Side effects: prints; reads stdin, re-prompting until the input parses.
    Treats EOF/Ctrl-C as selecting nothing rather than crashing.
    """
    if not courses:
        print("[select] nothing to choose from")
        return []

    render(courses)

    while True:
        print(
            "\nWhich courses? Numbers ('1,3,5'), ranges ('1-6'), "
            "'all', or 'none' to abort."
        )
        try:
            raw = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\n[select] aborted")
            return []

        try:
            indices = parse_selection(raw, len(courses))
        except ValueError as exc:
            print(f"[select] {exc} -- try again")
            continue

        picked = [courses[i] for i in indices]
        if not picked:
            print("[select] nothing selected")
            return []

        print(f"[select] {len(picked)} course(s) selected:")
        for course in picked:
            print(f"   - {course.title[:60]} ({len(course.occurrences)} sessions)")
        return picked
