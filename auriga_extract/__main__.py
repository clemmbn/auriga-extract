"""
Lets the tool run as `python -m auriga_extract`, without the installed launcher.

Why this exists: pipx and uv install a small unsigned `auriga-extract.exe`
launcher on Windows. Smart App Control, AppLocker or WDAC can block that
launcher ("Une stratégie de contrôle d'application a bloqué ce fichier"),
including after an upgrade regenerates it. The Python interpreter itself is
signed and still allowed, so going through `-m` sidesteps the block entirely.

All logic lives in auriga_extract.cli; this file only forwards.
"""

from auriga_extract.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
