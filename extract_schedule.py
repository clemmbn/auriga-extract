#!/usr/bin/env python3
"""
Thin CLI shim so the tool runs as the spec describes:

    uv run python extract_schedule.py --start 2026-09-01 --end 2027-06-30

All logic lives in the auriga_extract package; this file only forwards.
"""

from auriga_extract.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
