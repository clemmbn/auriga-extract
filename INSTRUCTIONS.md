# Brief: School Timetable to ICS Extractor (Browser Automation)

## Goal

Build a Python CLI tool that logs into a school's web-based timetable portal (ISAE-SUPAERO, likely a custom student portal wrapping an ADE Campus-style planning engine), extracts class events over a user-specified date range, lets the user interactively pick which courses to keep, and generates one `.ics` file per selected course.

## Context (what we already know)

- The timetable page is a JavaScript single-page app. Saving the page as static HTML does not capture the events, because they are loaded asynchronously via API calls (XHR/fetch).
- Direct `curl` calls to the underlying API endpoint (copied from DevTools "Copy as cURL") are rejected, most likely by a WAF/anti-bot layer (TLS fingerprinting, not just missing headers/cookies). This means the extraction must go through a real, automated browser, not raw HTTP requests.
- The API endpoint returns event data as JSON when the calendar is browsed.
- Clicking on an individual event in the UI opens a detail panel showing richer metadata: teaching unit code(s) ("Unité(s) pédagogique(s)", e.g. `2627_FIG_3A_S5_FI_SD_310 - Fondamentaux de la décision`), population(s) (e.g. `2627_FIG_3A_FI_SD - Filière Sciences de la Décision`), instructor(s) ("Intervenants", e.g. `BENJAMIN - BOBBIA`), and resource/room ("Ressource(s)", e.g. `05_118_AMPHI3 - AMPHI 3 - 192 - 1`). This detail is very likely also fetched via its own API call when the event is clicked, and should be intercepted the same way as the main calendar data rather than scraped from the DOM.
- We do not yet have the exact API URL(s), request parameters, or JSON schema. Claude Code should discover these by instrumenting Playwright's network listeners while driving the real login/navigation flow, rather than assuming a fixed schema up front. Treat the exact endpoint shapes as unknowns to be confirmed empirically in the first working session, not as fixed requirements.

## Authentication

- Launch the browser in headed (visible) mode.
- Navigate to the portal's login page and then pause, prompting the user in the terminal to complete login manually (SSO or credentials, whatever the school uses) and press Enter once they've reached the planning page.
- No credential storage, no session persistence across runs. Every run starts with a fresh manual login. Keep this simple: just wait for explicit user confirmation before continuing.

## Course/date selection workflow

1. **Date range input**: the user passes a start date and end date as CLI arguments (e.g. `--start 2026-09-01 --end 2027-06-30`).
2. **Data collection**: drive the calendar UI (or call the discovered API directly with date parameters, if that turns out to be reliable and doesn't trigger the WAF when done through the browser context) to cover the full requested range. If the UI only loads one month/week at a time, iterate month by month (or week by week) programmatically, using Playwright to click "next" and intercepting each resulting network response.
3. **Deduplication into "courses"**: many calendar entries will be recurring occurrences of the same course (same teaching unit code + instructor + room combination, different dates/times). Group raw events into unique courses using the teaching unit code as the primary key (fall back to title + instructor if the code is missing on some entries).
4. **Enrichment**: for each unique course (not each occurrence), open/click one representative event to capture the detail panel data (teaching unit code, population, instructor, room). Apply this enriched metadata to all occurrences of that course. Avoid clicking every single occurrence if the metadata is identical across them, for efficiency.
5. **Interactive selection**: print a numbered list of all unique courses found, showing at least: teaching unit code, human-readable title, instructor, and number of occurrences in the selected date range. Let the user select which ones to keep by entering numbers (comma-separated, ranges allowed, or an "all" option).
6. **ICS generation**: for each selected course, generate one `.ics` file containing all of its occurrences within the requested date range.

## ICS output spec

- One file per course, named predictably from the teaching unit code (sanitized for filesystem use), e.g. `2627_FIG_3A_S5_FI_SD_310.ics`.
- Each event (`VEVENT`) should include:
  - `SUMMARY`: course title (teaching unit label)
  - `DTSTART` / `DTEND`: with correct date and time, timezone `Europe/Paris`
  - `LOCATION`: room/resource
  - `DESCRIPTION`: instructor name(s), population/filière, and any other relevant metadata not already in a dedicated field
  - `UID`: stable and unique per occurrence, so re-imports don't duplicate events in the user's calendar app
- Use the `icalendar` Python library for correct RFC 5545 formatting rather than hand-writing ICS text.
- Output files go into a local `output/` directory, one subfolder per run (e.g. named by date range) to avoid overwriting previous exports.

## Non-functional requirements

- Target platform: macOS (the user is fully in the Apple ecosystem; output `.ics` files should be double-click importable into Apple Calendar without issues, so double-check timezone handling in particular).
- Handle pagination/navigation failures gracefully (timeouts, unexpected page states) with clear error messages rather than silent failures.
- Log progress to the terminal as it works through the date range (e.g. "Fetching October 2026... 42 events found").
- Code should be reasonably modular: a network-capture/data-collection layer, a course-grouping/enrichment layer, an interactive-selection layer, and an ICS-generation layer, so any one part can be debugged or adjusted independently once the real API shapes are known.

## Suggested tech stack

- Python 3, managed as a `uv` project (`uv init`, dependencies added via `uv add`, run via `uv run`)
- Playwright (Python) for browser automation and network interception, run headed. Remember that `playwright install` (browser binaries) still needs to be run once inside the `uv` environment.
- `icalendar` for ICS generation
- Standard library `argparse` for CLI args, plain `input()` for the interactive course picker (no need for extra dependencies there)

## First working session: investigation steps

Since the exact API endpoints and JSON/DOM structures for both the calendar data and the event detail panel are not yet confirmed, the first implementation pass should:

1. Open the portal in a Playwright-controlled browser, pause for manual login.
2. Attach a response listener logging every XHR/fetch response URL and a snippet of its body while manually browsing a month and clicking one event in the detail panel.
3. Identify which response corresponds to the calendar/event list and which corresponds to the detail panel, and inspect their exact JSON schema.
4. Only then implement the full automated extraction loop against those confirmed shapes.

## Deliverable

A `uv`-managed project (`pyproject.toml` + `uv.lock`), with a main entry point runnable as, for example:

```
uv run extract_schedule.py --start 2026-09-01 --end 2027-06-30
```

which walks the user through manual login, data collection, course selection, and ends with one `.ics` file per selected course saved locally.
