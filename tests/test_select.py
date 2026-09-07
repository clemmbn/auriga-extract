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
