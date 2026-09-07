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
