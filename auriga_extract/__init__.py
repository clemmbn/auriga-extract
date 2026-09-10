"""
Auriga Extract -- pull ISAE-SUPAERO timetable events out of the Auriga student
portal and turn them into one combined .ics file.

The portal is a JavaScript single-page app whose API is guarded by a short-lived
Keycloak bearer token, held in the app's memory and sent on no cookie at all. A
browser is therefore needed to complete the SSO login and to observe the token
-- but only for that. Once the token is known the API answers ordinary HTTP
requests, so all data collection happens without a browser (measured 2026-09-07;
an earlier belief that a WAF rejected non-browser clients did not survive
testing -- see fetch.py).

Package layout mirrors the pipeline stages so any one can be debugged alone:
  - cdp.py      : browser control over the Chrome DevTools Protocol, stdlib only
  - capture.py  : network-capture layer, for debugging and discovery
  - probe.py    : discovery entrypoint, kept for when the portal changes
  - fetch.py    : token sniffing and the month-by-month API walk
  - courses.py  : groups raw interventions into courses
  - select.py   : the interactive picker
  - ics.py      : calendar generation
  - cli.py      : orchestration
"""

# Single source of truth: pyproject.toml reads this via hatchling.
__version__ = "1.1.0"
