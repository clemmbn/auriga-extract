"""
ICS generation layer: one .ics file per selected course.

Timezone handling is the part most likely to go subtly wrong, so it is worth
being explicit. The API returns instants in UTC (`2026-09-17T06:30:00Z`), while
the school thinks in Europe/Paris. We convert to Europe/Paris and emit local
times with a TZID, bundling a real VTIMEZONE component covering the exported
range. That is what makes the file self-contained and unambiguous across the
March/October DST switches -- a TZID without a VTIMEZONE is technically invalid
and some clients silently guess.

UIDs are derived from the portal's own intervention id, so re-importing an
updated export updates events in place instead of duplicating them.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, Timezone

from .courses import Course, caption

SCHOOL_TZ = ZoneInfo("Europe/Paris")
TZID = "Europe/Paris"

# Namespace for UIDs. Any stable domain works; this one identifies the source.
UID_DOMAIN = "auriga.isae-supaero"

PRODID = "-//Auriga Extract//Timetable to ICS//FR"

# Filesystem-hostile characters, plus anything non-printable.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._+-]+")


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


def sanitize_filename(name: str, fallback: str = "cours") -> str:
    """
    Turn a course code or title into a safe file stem.

    name: the raw code or title.
    fallback: used when nothing usable survives sanitizing.
    Returns a trimmed, filesystem-safe stem (no extension), max 90 chars.
    """
    # Transliterate accents first (é -> e) instead of letting them be replaced
    # by underscores, which turned "Présentation" into "Pr_sentation".
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = _UNSAFE_FILENAME.sub("_", ascii_only.strip()).strip("._")
    return (cleaned[:90] or fallback)


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


def build_calendar(course: Course, stamp: Optional[datetime] = None) -> Calendar:
    """
    Render one course as an icalendar Calendar.

    course: the course to export, with its occurrences.
    stamp: DTSTAMP value; defaults to now (injectable for deterministic tests).
    Returns a Calendar containing a VTIMEZONE plus one VEVENT per occurrence
    that has a usable start and end.
    """
    calendar = Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    # Apple Calendar shows this as the imported calendar's name.
    calendar.add("x-wr-calname", course.title)
    calendar.add("x-wr-timezone", TZID)

    starts = [parse_instant(i.get("startDateTime")) for i in course.occurrences]
    starts = [s for s in starts if s]
    if starts:
        # Bound the VTIMEZONE to the data's own span (padded a year each way)
        # rather than emitting decades of DST transitions.
        first = min(starts).date() - timedelta(days=365)
        last = max(starts).date() + timedelta(days=365)
        calendar.add_component(Timezone.from_tzinfo(SCHOOL_TZ, TZID, first, last))

    dtstamp = stamp or datetime.now(tz=ZoneInfo("UTC"))

    for intervention in course.occurrences:
        start = parse_instant(intervention.get("startDateTime"))
        end = parse_instant(intervention.get("endDateTime"))
        if not start or not end:
            print(f"[ics] skipping session {intervention.get('id')}: unusable dates")
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

    return calendar


def _unique_path(directory: Path, stem: str) -> Path:
    """
    Pick a non-colliding .ics path inside a directory.

    Two description-grouped courses can sanitize to the same stem, so a numeric
    suffix is appended rather than silently overwriting an earlier export.
    """
    candidate = directory / f"{stem}.ics"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}.ics"
        counter += 1
    return candidate


def write_courses(courses: list[Course], out_root: Path, start: date, end: date) -> list[Path]:
    """
    Write one .ics per course into a per-run directory.

    courses: the selected courses.
    out_root: parent output directory.
    start, end: the requested range, used to name the run folder.
    Returns the written paths. Side effects: creates directories, writes files,
    prints progress.
    """
    run_dir = out_root / f"{start.isoformat()}_{end.isoformat()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[ics] writing to {run_dir}")

    written: list[Path] = []
    for course in courses:
        # Unit code makes the best filename; description groups fall back to
        # their title, which is all the identity they have.
        stem = sanitize_filename(course.display_code if course.has_unit else course.title)
        path = _unique_path(run_dir, stem)
        path.write_bytes(build_calendar(course).to_ical())
        written.append(path)
        print(f"[ics] {path.name}  ({len(course.occurrences)} sessions)")

    return written
