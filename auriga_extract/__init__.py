"""
Auriga Extract — pull ISAE-SUPAERO timetable events out of the Auriga student
portal and turn them into per-course .ics files.

The portal is a JavaScript single-page app sitting behind a WAF that rejects
replayed HTTP requests (curl with copied cookies/headers gets blocked, most
likely on TLS/browser fingerprinting). Every byte therefore has to be obtained
through a real, automated browser rather than a plain HTTP client.

Package layout mirrors the pipeline stages so any one can be debugged alone:
  - capture.py  : network-capture layer (Playwright response recording)
  - probe.py    : discovery entrypoint used to reverse-engineer the portal API

Later stages (course grouping/enrichment, interactive selection, ICS writing)
are deliberately not implemented yet: the portal's endpoints and JSON schemas
are unknown, and must be confirmed from a real capture before anything parses
them.
"""

__version__ = "0.1.0"
