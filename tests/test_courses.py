"""
Tests for the course-grouping layer.

Grouping is where the tool can be silently, invisibly wrong: a bad key merges
two unrelated things into one picker row, or splits one course into five, and
the user has no way to tell from the output that anything went astray. The
event data itself looks fine either way.

Fixtures below are hand-written miniatures of the shapes measured in a real
capture on 2026-09-07 (76 interventions, 20 of them with no pedagogical unit).
The capture itself is personal timetable data and is gitignored, so nothing
here reads from it.
"""

from __future__ import annotations

from auriga_extract.courses import Course, course_key, group_courses


def _unit(code: str, title: str) -> dict:
    """Build one interventionPedagogicalUnits entry."""
    return {"pedagogicalUnit": {"code": code, "caption": {"fr": title}}}


def _intervention(
    ident: int,
    *,
    units: list[dict] | None = None,
    description: str | None = None,
    start: str = "2026-09-17T06:30:00Z",
    instructors: list[tuple[str, str]] | None = None,
    rooms: list[str] | None = None,
    populations: list[str] | None = None,
    activity: str | None = None,
) -> dict:
    """Build one raw intervention with only the fields the grouper reads."""
    return {
        "id": ident,
        "startDateTime": start,
        "description": description,
        "interventionPedagogicalUnits": units or [],
        "interventionInstructors": [
            {"person": {"currentFirstName": first, "currentLastName": last}}
            for first, last in (instructors or [])
        ],
        "interventionResources": [
            {"resource": {"caption": {"fr": room}}} for room in (rooms or [])
        ],
        "interventionPopulations": [
            {"population": {"caption": {"fr": pop}}} for pop in (populations or [])
        ],
        "activityType": {"code": activity} if activity else None,
    }


# --------------------------------------------------------------------------
# course_key
# --------------------------------------------------------------------------


def test_single_unit_code_is_the_key():
    assert course_key(_intervention(1, units=[_unit("SD_310", "Perception")])) == "SD_310"


def test_multiple_unit_codes_sort_into_one_stable_key():
    """
    A session counting toward three parallel tracks is attended ONCE, so it must
    be one course. The API emits the same combination in different orders on
    different records, so an unsorted key would split it into several courses
    that each hold a fraction of the sessions.
    """
    forward = _intervention(1, units=[_unit("SD_310", "A"), _unit("SD_322", "B"), _unit("SD_330", "C")])
    shuffled = _intervention(2, units=[_unit("SD_330", "C"), _unit("SD_310", "A"), _unit("SD_322", "B")])

    assert course_key(forward) == course_key(shuffled) == "SD_310+SD_322+SD_330"


def test_unit_without_a_code_is_ignored():
    """A unit link with no code contributes nothing; the event falls back."""
    unit = {"pedagogicalUnit": {"caption": {"fr": "Titre sans code"}}}
    assert course_key(_intervention(1, units=[unit], description="Réunion")).startswith("sans-code:")


def test_events_with_no_unit_group_by_description():
    """
    Real recurring classes such as "3A LV1-ANGLAIS" carry no unit code at all
    (4 occurrences of exactly that in the measured capture). Dropping them, or
    splitting them per-session, would lose a genuine course.
    """
    first = _intervention(1, description="3A LV1-ANGLAIS")
    second = _intervention(2, description="3A LV1-ANGLAIS")
    assert course_key(first) == course_key(second) == "sans-code:3a lv1-anglais"


def test_description_grouping_ignores_case_and_spacing():
    spaced = _intervention(1, description="FORUM   MOBILITES")
    plain = _intervention(2, description="forum mobilites")
    assert course_key(spaced) == course_key(plain)


def test_events_with_neither_unit_nor_description_stay_separate():
    """
    The regression this file exists for. These used to share one key and fuse
    into a single "(sans titre)" course -- unrelated one-offs presented as one
    all-or-nothing row. Keying on the id keeps each its own group.
    """
    a = _intervention(1)
    b = _intervention(2)
    assert course_key(a) != course_key(b)


def test_blank_description_counts_as_no_description():
    """Whitespace-only is not a name to group on."""
    a = _intervention(1, description="   ")
    b = _intervention(2, description="")
    assert course_key(a) != course_key(b)


# --------------------------------------------------------------------------
# group_courses
# --------------------------------------------------------------------------


def test_occurrences_collect_under_one_course():
    interventions = [
        _intervention(1, units=[_unit("A1", "Maths")], start="2026-09-18T06:30:00Z"),
        _intervention(2, units=[_unit("A1", "Maths")], start="2026-09-17T06:30:00Z"),
    ]
    courses = group_courses(interventions)

    assert len(courses) == 1
    assert len(courses[0].occurrences) == 2
    # Sessions are re-sorted chronologically regardless of arrival order.
    assert [i["id"] for i in courses[0].occurrences] == [2, 1]


def test_coded_courses_sort_ahead_of_uncoded_ones():
    """One-off admin items must sink below real classes in the picker."""
    interventions = [
        _intervention(1, description="VACANCES TOUSSAINT"),
        _intervention(2, units=[_unit("A1", "Maths")]),
    ]
    courses = group_courses(interventions)

    assert [c.has_unit for c in courses] == [True, False]


def test_busier_courses_sort_first():
    interventions = [
        _intervention(1, units=[_unit("A1", "Rare")]),
        _intervention(2, units=[_unit("B1", "Frequent")]),
        _intervention(3, units=[_unit("B1", "Frequent")]),
    ]
    courses = group_courses(interventions)

    assert [c.title for c in courses] == ["Frequent", "Rare"]


def test_title_comes_from_the_unit_caption():
    courses = group_courses([_intervention(1, units=[_unit("A1", "Perception et navigation")])])
    assert courses[0].title == "Perception et navigation"


def test_title_joins_several_unit_captions():
    courses = group_courses([_intervention(1, units=[_unit("A1", "Maths"), _unit("B1", "Physique")])])
    assert courses[0].title == "Maths + Physique"


def test_title_falls_back_to_description_then_placeholder():
    by_description = group_courses([_intervention(1, description="FORUM MOBILITES")])
    assert by_description[0].title == "FORUM MOBILITES"

    nameless = group_courses([_intervention(2)])
    assert nameless[0].title == "(sans titre)"


def test_display_code_marks_uncoded_groups():
    coded = group_courses([_intervention(1, units=[_unit("A1", "Maths"), _unit("B1", "Physique")])])
    assert coded[0].display_code == "A1+B1"

    uncoded = group_courses([_intervention(2, description="Assistante sociale")])
    assert uncoded[0].display_code == "(sans code)"


# --------------------------------------------------------------------------
# Course metadata roll-ups
# --------------------------------------------------------------------------


def test_metadata_is_deduplicated_across_occurrences():
    """
    Every occurrence repeats the same instructor/room/population, so the picker
    would show the same name a dozen times without the dedup.
    """
    interventions = [
        _intervention(
            n,
            units=[_unit("A1", "Maths")],
            instructors=[("Marie", "Curie")],
            rooms=["61.101"],
            populations=["3A"],
            activity="CM",
        )
        for n in (1, 2, 3)
    ]
    course = group_courses(interventions)[0]

    assert course.instructors == ["Marie Curie"]
    assert course.rooms == ["61.101"]
    assert course.populations == ["3A"]
    assert course.activity_codes == ["CM"]


def test_metadata_unions_differing_occurrences():
    interventions = [
        _intervention(1, units=[_unit("A1", "Maths")], rooms=["61.101"], activity="CM"),
        _intervention(2, units=[_unit("A1", "Maths")], rooms=["61.102", "61.101"], activity="TP"),
    ]
    course = group_courses(interventions)[0]

    assert course.rooms == ["61.101", "61.102"]
    assert course.activity_codes == ["CM", "TP"]


def test_partial_instructor_names_do_not_leave_stray_spaces():
    """Either name half can be absent; the join must not produce " Curie"."""
    intervention = _intervention(1, units=[_unit("A1", "Maths")])
    intervention["interventionInstructors"] = [
        {"person": {"currentFirstName": None, "currentLastName": "Curie"}},
        {"person": {"currentFirstName": "Marie", "currentLastName": None}},
    ]
    course = group_courses([intervention])[0]

    assert course.instructors == ["Curie", "Marie"]


def test_missing_metadata_blocks_are_tolerated():
    """The API omits these keys entirely on sparse records."""
    course = Course(key="A1", unit_codes=["A1"], title="Maths", occurrences=[{"id": 1}])

    assert course.instructors == []
    assert course.rooms == []
    assert course.populations == []
    assert course.activity_codes == []


def test_no_interventions_yields_no_courses():
    assert group_courses([]) == []
