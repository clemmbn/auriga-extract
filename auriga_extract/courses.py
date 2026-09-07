"""
Course-grouping layer: collapse raw interventions into the things a student
would call "a course".

The portal returns one "intervention" per session. Grouping them is not as
simple as the original brief assumed, because the live data has two wrinkles
(both measured, see CLAUDE.md):

  - About a third of events carry no pedagogical unit at all. Some are
    genuinely administrative (welcome talks, forums, holiday blocks), but some
    are real recurring classes such as "3A LV1-ANGLAIS". They are grouped by
    their description instead and flagged, so the user can decide.

  - A single session can carry up to three unit codes at once -- one shared
    class counting toward three parallel tracks. The student attends it once,
    so it becomes ONE course keyed on the combination. The API returns those
    codes in inconsistent order, so the key sorts them.

No enrichment step is needed: the list endpoint already carries instructors,
rooms and populations, so nothing here has to click through the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# Marks a group built from a description rather than a unit code.
NO_UNIT_PREFIX = "sans-code:"


def caption(node: Optional[dict[str, Any]]) -> str:
    """
    Pull a human label out of Auriga's bilingual caption objects.

    node: a dict shaped like {"fr": ..., "en": ...}, or None.
    Returns the French label, falling back to English, else "".
    French first because the timetable, rooms and populations are authored in
    French; English captions exist but are patchier.
    """
    if not node:
        return ""
    return (node.get("fr") or node.get("en") or "").strip()


def _normalize(text: str) -> str:
    """Collapse whitespace so descriptions that differ only in spacing group together."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def _unit_nodes(intervention: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the pedagogicalUnit dicts attached to an intervention (possibly empty)."""
    links = intervention.get("interventionPedagogicalUnits") or []
    return [link["pedagogicalUnit"] for link in links if link.get("pedagogicalUnit")]


def course_key(intervention: dict[str, Any]) -> str:
    """
    Compute the grouping key for one intervention.

    intervention: a raw API intervention.
    Returns a stable string key: sorted unit codes joined by "+", or a
    description-derived key prefixed with NO_UNIT_PREFIX when no unit exists.

    Sorting the codes is essential -- the API emits the same three-unit
    combination in different orders on different records, which would otherwise
    split one course into several.
    """
    codes = sorted(unit.get("code", "") for unit in _unit_nodes(intervention) if unit.get("code"))
    if codes:
        return "+".join(codes)

    description = _normalize(intervention.get("description") or "")
    return NO_UNIT_PREFIX + (description.lower() or "(sans description)")


@dataclass
class Course:
    """
    One selectable course: a group of interventions plus their shared metadata.

    key: the grouping key from course_key().
    unit_codes: pedagogical unit codes, empty for description-grouped items.
    title: human label shown in the picker and used as the ICS summary.
    occurrences: the raw interventions belonging to this course.
    """

    key: str
    unit_codes: list[str] = field(default_factory=list)
    title: str = ""
    occurrences: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_unit(self) -> bool:
        """True when this group came from real pedagogical unit codes."""
        return bool(self.unit_codes)

    @property
    def display_code(self) -> str:
        """Unit codes joined for display, or a placeholder for description groups."""
        return "+".join(self.unit_codes) if self.unit_codes else "(sans code)"

    def _collect(self, extractor) -> list[str]:
        """
        Gather a deduplicated, sorted set of labels across all occurrences.

        extractor: callable taking one intervention and returning an iterable
        of strings.
        Returns sorted unique non-empty values.
        """
        found: set[str] = set()
        for occurrence in self.occurrences:
            found.update(value for value in extractor(occurrence) if value)
        return sorted(found)

    @property
    def instructors(self) -> list[str]:
        """Every instructor seen across occurrences, as "FIRSTNAME LASTNAME"."""
        return self._collect(
            lambda i: [
                " ".join(
                    part
                    for part in (
                        (link.get("person") or {}).get("currentFirstName"),
                        (link.get("person") or {}).get("currentLastName"),
                    )
                    if part
                )
                for link in (i.get("interventionInstructors") or [])
            ]
        )

    @property
    def rooms(self) -> list[str]:
        """Every room/resource seen across occurrences."""
        return self._collect(
            lambda i: [
                caption((link.get("resource") or {}).get("caption"))
                for link in (i.get("interventionResources") or [])
            ]
        )

    @property
    def populations(self) -> list[str]:
        """Every population/filière label seen across occurrences."""
        return self._collect(
            lambda i: [
                caption((link.get("population") or {}).get("caption"))
                for link in (i.get("interventionPopulations") or [])
            ]
        )

    @property
    def activity_codes(self) -> list[str]:
        """Activity type codes present (CM, TP, BE, PRE, EX...)."""
        return self._collect(lambda i: [(i.get("activityType") or {}).get("code", "")])


def _title_for(intervention: dict[str, Any], units: list[dict[str, Any]]) -> str:
    """
    Derive a display title for a course from one of its interventions.

    intervention: a representative raw intervention.
    units: its pedagogicalUnit dicts (may be empty).
    Returns the unit caption(s) joined by " + ", else the session description,
    else a placeholder.
    """
    captions = [caption(unit.get("caption")) for unit in units]
    captions = [caption for caption in captions if caption]
    if captions:
        return " + ".join(captions)

    return _normalize(intervention.get("description") or "") or "(sans titre)"


def group_courses(interventions: list[dict[str, Any]]) -> list[Course]:
    """
    Group raw interventions into Course objects.

    interventions: raw API interventions, any order.
    Returns courses sorted for the picker: real (unit-coded) courses first,
    then the most frequent, then alphabetically -- so the classes that dominate
    a timetable appear at the top and one-off admin items sink to the bottom.
    Side effect: prints a short breakdown.
    """
    grouped: dict[str, Course] = {}

    for intervention in interventions:
        key = course_key(intervention)
        course = grouped.get(key)
        if course is None:
            units = _unit_nodes(intervention)
            course = Course(
                key=key,
                unit_codes=sorted(u.get("code", "") for u in units if u.get("code")),
                title=_title_for(intervention, units),
                )
            grouped[key] = course
        course.occurrences.append(intervention)

    courses = sorted(
        grouped.values(),
        key=lambda c: (not c.has_unit, -len(c.occurrences), c.title.lower()),
    )

    # Keep each course's sessions chronological so the ICS and the counts read
    # in a sensible order.
    for course in courses:
        course.occurrences.sort(key=lambda i: str(i.get("startDateTime") or ""))

    n_coded = sum(1 for c in courses if c.has_unit)
    print(
        f"[courses] {len(interventions)} events -> {len(courses)} groups "
        f"({n_coded} with a unit code, {len(courses) - n_coded} without)"
    )
    return courses
