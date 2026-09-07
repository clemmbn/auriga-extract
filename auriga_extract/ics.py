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

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, Timezone

from .console import console
from .courses import Course, caption

SCHOOL_TZ = ZoneInfo("Europe/Paris")
TZID = "Europe/Paris"

# Namespace for UIDs. Any stable domain works; this one identifies the source.
UID_DOMAIN = "auriga.isae-supaero"

PRODID = "-//Auriga Extract//Timetable to ICS//FR"


def parse_instant(value: Optional[str]) -> Optional[datetime]:
    """
    Parse an Auriga UTC timestamp into an aware Europe/Paris datetime.

    value: an ISO-8601 string ending in "Z", or None.
    Returns a timezone-aware datetime in Europe/Paris, or None if unparseable.

    Python 3.11's fromisoformat understands the trailing "Z" directly, so no
    manual suffix surgery is needed.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    # A timestamp without an offset would otherwise be treated as naive local
    # time; the API always sends UTC, so say so explicitly.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(SCHOOL_TZ)


def _describe_occurrence(course: Course, intervention: dict[str, Any]) -> str:
    """
    Build the DESCRIPTION body for one session.

    course: the parent course (used for unit codes).
    intervention: the raw API record for this session.
    Returns a newline-separated block holding the metadata that has no
    dedicated ICS field.
    """
    lines: list[str] = []

    activity = intervention.get("activityType") or {}
    if caption(activity.get("caption")):
        lines.append(f"Type : {caption(activity.get('caption'))}")

    topic = (intervention.get("description") or "").strip()
    if topic:
        lines.append(f"Séance : {topic}")

    instructors = [
        " ".join(
            part
            for part in (
                (link.get("person") or {}).get("currentFirstName"),
                (link.get("person") or {}).get("currentLastName"),
            )
            if part
        )
        for link in (intervention.get("interventionInstructors") or [])
    ]
    instructors = [name for name in instructors if name]
    if instructors:
        lines.append(f"Intervenant(s) : {', '.join(instructors)}")

    populations = [
        caption((link.get("population") or {}).get("caption"))
        for link in (intervention.get("interventionPopulations") or [])
    ]
    populations = [p for p in populations if p]
    if populations:
        lines.append(f"Population(s) : {', '.join(populations)}")

    if course.unit_codes:
        lines.append(f"Unité(s) pédagogique(s) : {', '.join(course.unit_codes)}")

    if intervention.get("isExam"):
        lines.append("** Examen **")

    return "\n".join(lines)


def _rooms_for(intervention: dict[str, Any]) -> str:
    """Join this session's room captions into a LOCATION value."""
    rooms = [
        caption((link.get("resource") or {}).get("caption"))
        for link in (intervention.get("interventionResources") or [])
    ]
    return ", ".join(room for room in rooms if room)


def _summary_for(course: Course, intervention: dict[str, Any]) -> str:
    """
    Build the event title: activity type code, then the course title.

    Prefixing with CM/TP/BE/EX means a glance at the calendar says what kind of
    session it is, which is the distinction that changes how you prepare.
    """
    code = (intervention.get("activityType") or {}).get("code")
    return f"{code} · {course.title}" if code else course.title


def _add_course_events(calendar: Calendar, course: Course, dtstamp: datetime) -> int:
    """
    Add every occurrence of one course as a VEVENT on an existing Calendar.

    calendar: the in-progress combined Calendar to append to.
    course: the course whose occurrences become events.
    dtstamp: DTSTAMP value shared by every event in this export run.
    Returns how many events were actually added, which is not necessarily
    len(course.occurrences) -- see below.
    Side effect: mutates calendar. Skips occurrences with no usable
    start/end instant, printing why.
    """
    written = 0
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
        written += 1

    return written


def build_combined_calendar(
    courses: list[Course], start: date, end: date, stamp: Optional[datetime] = None
) -> tuple[Calendar, int]:
    """
    Render every selected course into one icalendar Calendar.

    courses: the selected courses, in the order they should be added.
    start, end: the requested export range; used for the calendar's display
    name and to pad the VTIMEZONE bounds if no occurrence has usable dates.
    stamp: DTSTAMP value; defaults to now (injectable for deterministic tests).
    Returns (calendar, n_events): one Calendar containing a single VTIMEZONE
    plus one VEVENT per occurrence, across all courses, that has a usable start
    and end -- and the count of those events. The count is returned rather than
    inferred from the courses, because occurrences with unusable dates are
    skipped and reporting them as written would overstate the file's contents.
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
    n_events = sum(_add_course_events(calendar, course, dtstamp) for course in courses)

    return calendar, n_events


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
    calendar, n_events = build_combined_calendar(courses, start, end)
    path.write_bytes(calendar.to_ical())

    console.print(
        f"[dim][ics][/] wrote [green]{path.name}[/] "
        f"({len(courses)} course(s), {n_events} session(s))"
    )
    return path
