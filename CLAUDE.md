# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Working end to end. `uv run python extract_schedule.py --start … --end …` opens a browser, waits for manual login, fetches the range, and writes one combined `.ics` for all selected courses.

Verified against a mock API replaying real captured payloads: 76 events → 23 courses grouped correctly, all UIDs unique, every event round-tripping to the exact instant the API reported. Combined single-file writing verified separately (see docs/superpowers/plans/2026-09-07-combined-export-and-rich-ui.md, Task 9). Not yet run end to end against the live portal with a real login.

## What this tool does (per INSTRUCTIONS.md)

A Python CLI that logs into ISAE-SUPAERO's web-based timetable portal (an ADE Campus-style planning SPA), extracts class events over a user-specified date range via Playwright network interception, lets the user interactively pick which courses to keep, and generates one combined `.ics` file for all selected courses.

Key constraints from the brief:
- The portal's API is protected by what appears to be a WAF/TLS-fingerprinting layer — raw `curl`/HTTP requests get rejected even with correct headers/cookies copied from DevTools. **All data collection must go through a real Playwright-driven browser**, not direct HTTP calls.
- Auth is manual: launch headed, navigate to login, pause and wait for the user to log in and press Enter in the terminal — no credential storage or session persistence across runs.
- The exact API endpoint(s) and JSON schema for calendar data and the event detail panel are *not yet known* and must be discovered empirically first (see "Investigation-first workflow" below) — do not assume a fixed schema.
- Courses are deduplicated from raw calendar occurrences using teaching unit code as the primary key (fallback: title + instructor). Enrichment (population, instructor, room) comes from clicking one representative occurrence's detail panel per course, not every occurrence.
- ICS generation must use the `icalendar` library (not hand-rolled ICS text), with `Europe/Paris` timezone handling correct enough for Apple Calendar double-click import.

### Modules

- `capture.py` — records network traffic to a capture directory (used by the probe, and by `--capture` for debugging).
- `probe.py` — API-discovery session; how the endpoints below were found. Keep it: it is the tool to reach for if the portal changes.
- `fetch.py` — token sniffing plus the in-page fetch loop, month by month.
- `courses.py` — groups interventions into courses.
- `select.py` — the interactive picker (`1,3`, `1-6`, `all`, `none`).
- `ics.py` — calendar generation.
- `cli.py` — orchestration; `extract_schedule.py` at the repo root is a thin shim over it.

## The Auriga API (confirmed from a live capture, 2026-09-07)

Base: `https://isaesupaero-production.np-auriga.nfrance.net`

**Event feed — this is the only endpoint the extractor needs:**

```
GET /api/plannings/me?days=1&days=2&...&days=7&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
```

`startDate`/`endDate` are free-form, so any range can be requested directly — no need to click through the UI month by month. Returns `{"interventions": [...], "unavailabilities": [...]}`. The extractor iterates a month at a time anyway, to bound payload size and give per-month progress output.

**Auth:** Keycloak bearer token in an `Authorization` header, plus `x-scope: frontend`. **No cookies are used at all.** The token must be sniffed from a live request at runtime and kept in memory only — never written to disk.

Requests must be issued from *inside* the page (`page.evaluate` + `fetch`) rather than through Playwright's `APIRequestContext`, so they carry the real browser's TLS fingerprint. The WAF rejects clients that don't.

**The detail-panel endpoint (`/api/menuEntries/227/interventions/<id>`) is NOT needed.** The list response already contains the pedagogical unit, instructors, populations and resources. The only fields unique to the detail call are `accountedDuration`, `breakDuration`, `additionalRemarks`, `project`, `site`, `standardTimeslot` and `objectSharingDomains` — none of which we use. This removes the "click a representative event to enrich" step the original brief assumed.

### Intervention shape (what matters)

- `startDateTime` / `endDateTime` — **UTC, with a `Z` suffix** (e.g. `2026-09-17T06:30:00Z` is 08:30 Paris). Convert to `Europe/Paris`. Each intervention also carries a `timezone` object naming `Europe/Paris`.
- `interventionPedagogicalUnits[].pedagogicalUnit` — `code` (e.g. `2627_FIG_3A_S5_DA_SA_302`) and `caption.fr` (the human course title). **Often absent**, and there can be up to 3.
- `interventionInstructors[].person` — `currentFirstName` / `currentLastName`. Often absent.
- `interventionResources[].resource` — `caption.fr` is the room. Up to 14 per event.
- `interventionPopulations[].population` — `code` and `caption.fr` (the filière).
- `activityType` — `code` (`CM` lecture, `PC`/`TP`/`BE` classes, `PRE` presentation, `EX` exam, `EVENEMENT`) and `caption.fr`.
- `description` — the session topic (e.g. "Modèle de caméra"); frequently `null` on real courses.
- `isExam`, `participations`, `interventionPrograms` — present in the list, unused so far.

### Grouping reality (measured over Sep+Oct 2026: 76 interventions → 23 groups)

- Roughly **a third of events have no pedagogical unit**: one-off admin items (welcome talks, forums, "Assistante sociale"), holidays ("VACANCES TOUSSAINT"), and some real classes ("3A LV1-ANGLAIS"). These must fall back to grouping by `description`.
- Some sessions carry **3 unit codes at once** (e.g. `SD_310+SD_322+SD_330`) — one shared session counting toward three parallel tracks. The code order varies between records, so any key built from them must sort first.

## Product decisions (settled with the user, 2026-09-07)

- **Sessions carrying several unit codes become ONE course**, keyed on the sorted combination. A session counting toward three tracks is attended once; emitting it per-unit would put duplicate events in the calendar at the same slot.
- **Event titles are `CM · Perception et navigation`** — activity type code, then course title. Session topic and instructors go in the description.
- **Non-course items are listed, not auto-included or dropped.** They appear in the picker under their own heading. Dropping them would lose real classes like "3A LV1-ANGLAIS", which has no unit code.

## Timezone handling

The API sends UTC; the school thinks in Europe/Paris. Output converts to Europe/Paris and emits local times with `TZID`, bundling a real `VTIMEZONE` (via `icalendar.Timezone.from_tzinfo`, bounded to the export range ±1 year). A `TZID` without a `VTIMEZONE` is invalid and some clients silently guess. Verified correct across both DST switches.

UIDs are `auriga-<intervention id>@auriga.isae-supaero`, so re-importing an updated export updates events in place instead of duplicating them.

## Commands

`uv` manages the environment (Python >=3.11); `playwright` and `icalendar` are installed.

```bash
uv sync
```

Export a date range (opens real Chrome; log in when it appears — no keypress needed, the tool detects the session automatically):

```bash
uv run python extract_schedule.py --start 2026-09-01 --end 2027-06-30
```

Useful flags: `--out` (default `~/Downloads`), `--url`, `--channel ""` to force bundled Chromium, `--capture` to also record traffic for debugging.

Run a fresh API-discovery session, if the portal changes:

```bash
uv run python -m auriga_extract.probe
```

Re-print the report for an existing capture, without a browser:

```bash
uv run python -m auriga_extract.probe --analyze captures/<timestamp>
```

If Playwright browsers go missing:

```bash
uv run playwright install chromium
```

## Capture format

`captures/<timestamp>/index.jsonl` is an ordered stream of two record kinds: `response` (method, url, path, query, status, byte count, redacted request headers, a shallow `json_shape`, and a pointer to the body file) and `marker` (a note the user typed describing what they were about to do). `bodies/<seq>.<ext>` holds full payloads. Captures hold personal timetable data and are gitignored.

**Non-obvious constraint:** Playwright's sync API only dispatches events while the main thread is inside a Playwright call. Blocking on `input()` stalls dispatch, so responses arrive in a burst afterwards and land on the wrong side of their marker — destroying the correlation the capture exists to provide. `probe.py` therefore reads stdin on a background thread and pumps `page.wait_for_timeout()` on the main thread. Preserve that pattern in any interactive browser code.

## Non-functional notes

- Target platform is macOS; `.ics` output must import cleanly into Apple Calendar.
- Log progress to the terminal as extraction proceeds (e.g. per-month event counts) — failures (timeouts, unexpected page states) should surface as clear errors, not silent no-ops.
