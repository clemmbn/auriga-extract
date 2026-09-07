"""
Tests for the month-by-month request planner.

month_chunks decides every request the tool makes. A wrong boundary here does
not raise -- it silently omits a day, or a whole month, from the export, and the
only symptom is a class the student never sees in their calendar.

(Token sniffing is exercised in test_devtools.py, alongside the DevTools event
plumbing it reads from.)
"""

from __future__ import annotations

from datetime import date

from auriga_extract.fetch import _build_url, month_chunks


def test_a_single_day_is_one_chunk():
    day = date(2026, 9, 7)
    assert month_chunks(day, day) == [(day, day)]


def test_a_whole_month_is_one_chunk():
    assert month_chunks(date(2026, 9, 1), date(2026, 9, 30)) == [
        (date(2026, 9, 1), date(2026, 9, 30))
    ]


def test_first_and_last_chunks_are_clipped_to_the_requested_range():
    """A range starting mid-month must not silently widen to the whole month."""
    chunks = month_chunks(date(2026, 9, 15), date(2026, 11, 10))

    assert chunks == [
        (date(2026, 9, 15), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 11, 1), date(2026, 11, 10)),
    ]


def test_the_default_academic_year_splits_into_twelve_chunks():
    """The shipped default range: 2026-09-01 to 2027-08-31."""
    chunks = month_chunks(date(2026, 9, 1), date(2027, 8, 31))

    assert len(chunks) == 12
    assert chunks[0] == (date(2026, 9, 1), date(2026, 9, 30))
    assert chunks[-1] == (date(2027, 8, 1), date(2027, 8, 31))


def test_chunks_are_contiguous_and_cover_the_range_exactly():
    """No gaps (a missing day loses events) and no overlaps."""
    start, end = date(2026, 9, 1), date(2027, 8, 31)
    chunks = month_chunks(start, end)

    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    for (_, previous_end), (next_start, _) in zip(chunks, chunks[1:]):
        assert (next_start - previous_end).days == 1


def test_february_boundaries_survive_leap_years():
    """
    The 'jump past the 28th, then snap to day 1' trick is the one bit of date
    arithmetic here that could plausibly be off by a day.
    """
    assert month_chunks(date(2028, 2, 1), date(2028, 2, 29))[0][1] == date(2028, 2, 29)
    assert month_chunks(date(2027, 2, 1), date(2027, 2, 28))[0][1] == date(2027, 2, 28)


def test_year_boundary_is_crossed_cleanly():
    chunks = month_chunks(date(2026, 12, 20), date(2027, 1, 10))

    assert chunks == [
        (date(2026, 12, 20), date(2026, 12, 31)),
        (date(2027, 1, 1), date(2027, 1, 10)),
    ]


def test_an_inverted_range_asks_for_nothing():
    """cli.py rejects this before we get here; the planner must not loop forever."""
    assert month_chunks(date(2026, 9, 30), date(2026, 9, 1)) == []


def test_request_url_carries_the_range_and_all_seven_weekdays():
    url = _build_url("https://portal.test", date(2026, 9, 1), date(2026, 9, 30))

    assert url.startswith("https://portal.test/api/plannings/me?")
    assert "startDate=2026-09-01" in url
    assert "endDate=2026-09-30" in url
    # The UI always asks for all seven; mirroring it keeps our traffic ordinary.
    assert url.count("days=") == 7
