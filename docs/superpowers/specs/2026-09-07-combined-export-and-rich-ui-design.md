# Combined export, "all-but" selection, and rich terminal UI

Date: 2026-09-07

## Context

The tool currently writes one `.ics` file per selected course into a per-run
subdirectory (`ics.write_courses`), the interactive picker only supports
listing explicit numbers/ranges/`all`/`none` (`select.parse_selection`), and
all terminal output goes through plain `print()`.

Three independent improvements were requested:

1. Export a single `.ics` containing all selected courses instead of one file
   per course.
2. Support "select all but some" in the picker.
3. Use `rich` for prettier, more readable terminal output.

## 1. Single combined `.ics` output

- [ics.py](../../../auriga_extract/ics.py) gains `build_combined_calendar(courses, stamp=None) -> Calendar`,
  producing one `Calendar` with a `VEVENT` for every occurrence across every
  selected course. Per-event construction (`_summary_for`, `_describe_occurrence`,
  `_rooms_for`, `parse_instant`) is reused as-is.
- The single `VTIMEZONE` is bounded by the min/max occurrence start dates
  across **all** selected courses (±365 days, same padding as today), not
  per-course.
- `write_courses` (which wrote one file per course into a per-run
  subdirectory) is replaced by `write_calendar(courses, out_root, start, end) -> Path`,
  which writes exactly one file directly into `out_root`, named
  `auriga_{start.isoformat()}_{end.isoformat()}.ics`. No run subdirectory is
  created anymore since there is only one file.
- `x-wr-calname` on the combined calendar is set to
  `f"ISAE-SUPAERO {start.isoformat()} → {end.isoformat()}"`.
- The old per-course `build_calendar`, `_unique_path`, and the per-run
  directory logic are removed — nothing else uses them.
- [cli.py](../../../auriga_extract/cli.py) calls `write_calendar` instead of
  `write_courses` and prints the single resulting path.

## 2. "All but some" selection syntax

In [select.py](../../../auriga_extract/select.py), `parse_selection(text, count)`
gains a branch checked before the existing `SELECT_ALL` exact-match check:

- If the cleaned, lowercased input matches `all` followed by whitespace and
  `!` (e.g. `all !3,5`, `all!7-9`), the substring after `!` is parsed with the
  *existing* comma/range parsing logic (shared, not duplicated) to get a set
  of excluded 1-based indices, validated against `1..count` exactly as today.
- The result is `all_indices - excluded_indices`, sorted, zero-based.
- Plain `all` (no `!`) and plain `none` keep behaving exactly as today.
- `all !` with nothing parseable after it, or a malformed exclusion list
  (bad range, non-digit, out of range), raises the same kind of `ValueError`
  with a user-facing message that the existing retry loop in `prompt()`
  already handles — no changes needed there.
- The prompt hint text in `select.prompt` is updated to mention the new
  syntax, e.g.:
  `"Numbers ('1,3,5'), ranges ('1-6'), 'all', 'all !3,5' to exclude, or 'none' to abort."`

## 3. Rich terminal output

- Add `rich` (`>=13`) to `pyproject.toml` dependencies.
- New module `auriga_extract/console.py` exporting a single shared
  `console = rich.console.Console()` instance. Every module that prints
  imports this instance instead of using bare `print()`.
- Color convention used throughout: `cyan` = section/info headers, `green` =
  success, `yellow` = warning, `red` = error, `dim` = secondary/detail text.
  Tags like `[fetch]`, `[courses]`, `[select]`, `[ics]`, `[cli]` are kept as
  a `dim` prefix for continuity with today's log style.
- [select.py](../../../auriga_extract/select.py): `render()` builds a
  `rich.table.Table` (columns: `#`, sessions count, title, details) instead
  of manual `print` formatting. The no-unit-code group still gets a distinct
  section — rendered as a second `Table` (or a section divider row) under a
  styled heading, printed after the main table so the split remains obvious.
  Prompts, parse errors, and the final "selected" confirmation list use
  styled `console.print` calls.
- [cli.py](../../../auriga_extract/cli.py): `LOGIN_BANNER` becomes a
  `rich.panel.Panel` printed via the shared console. The final summary line
  ("done — N file(s) written" etc.) is styled green/bold.
- [fetch.py](../../../auriga_extract/fetch.py) and
  [courses.py](../../../auriga_extract/courses.py): existing per-line
  progress messages keep their current content and information, only
  switching from `print(...)` to `console.print(...)` with the color
  convention above (e.g. counts highlighted in `cyan`/`green`). No progress
  bars or spinners are introduced, to keep this change simple and avoid
  interfering with the existing "poll `page.wait_for_timeout()` on the main
  thread" pattern required by Playwright's sync API (see root CLAUDE.md,
  "Non-obvious constraint").

## Out of scope

- No change to the browser/login flow, token sniffing, or grouping logic
  beyond swapping `print` for `console.print`.
- No progress bars/spinners.
- No flag to opt back into per-course files — this replaces that behavior
  entirely, per the user's explicit choice.

## Testing

There is no checked-in automated test suite in this repo today (the "76
events → 23 courses → 23 files" verification in CLAUDE.md was done ad hoc
against a mock-API capture, not via `pytest`). For this change:

- Verify `parse_selection` manually (or with a throwaway script) against:
  `"all !3,5"` excludes the right indices; exclusion syntax with an
  out-of-range or malformed index still raises `ValueError`; plain
  `all`/`none` unaffected.
- Re-run the same kind of mock-API pass described in CLAUDE.md (or a
  smaller hand-built fixture) to confirm `write_calendar` produces one
  `.ics` with every selected course's events and a single valid
  `VTIMEZONE`, and that it imports cleanly.
- Run whatever linting/type-checking is already configured for this project
  before calling the change done, per standing workflow preference.
