# auriga-extract

Exports your ISAE-SUPAERO timetable from the Auriga portal into one `.ics`
file you can import into Apple Calendar, Google Calendar, Outlook, etc.

It opens a real browser, waits for you to log in normally, then reads your
schedule from the portal's own traffic. **Credentials are never stored or
seen by this tool**, you log in on the portal's own page.

## Requirements

- **Python 3.11+** — [python.org/downloads](https://www.python.org/downloads/)
  (Windows: tick "Add python.exe to PATH" during install).
- **Google Chrome** — the portal blocks the headless browser most automation
  tools use, so this drives your real Chrome instead.

## Install

<details>
<summary><strong>Never used a terminal before?</strong></summary>

A terminal is just a window where you type commands and press Enter. Type or
paste each command below one at a time, press Enter, and wait for it to
finish before the next one.

**On macOS:**

1. Open **Terminal** — press `Cmd+Space`, type `Terminal`, press Enter.
2. Check if Git is installed: paste `git --version` and press Enter. If
   nothing is installed, macOS pops up a dialog to install the "Command Line
   Tools" — click **Install** and wait for it to finish, then continue.
3. Paste each line below one at a time, pressing Enter after each, waiting
   for it to finish:

   ```bash
   python3 -m pip install --user pipx
   python3 -m pipx ensurepath
   ```

4. **Quit Terminal completely** (`Cmd+Q`, not just close the window) and
   reopen it — this step is easy to skip but required.
5. Paste, one line at a time:

   ```bash
   git clone https://github.com/<your-org>/auriga-extract.git
   cd auriga-extract
   pipx install .
   ```

6. Check it worked: paste `auriga-extract --help`. If you see a list of
   options rather than an error, you're done — skip to **Usage** below.

**On Windows:**

1. Install [Python](https://www.python.org/downloads/) if you haven't —
   during install, **tick "Add python.exe to PATH"** at the bottom of the
   first screen, this is easy to miss.
2. Install [Git for Windows](https://git-scm.com/downloads/win) if you
   haven't — the default options in the installer are fine.
3. Open **PowerShell** — press the Windows key, type `PowerShell`, press
   Enter.
4. Paste each line below one at a time, pressing Enter after each:

   ```bash
   python -m pip install --user pipx
   python -m pipx ensurepath
   ```

5. **Close the PowerShell window completely** and reopen it — this step is
   easy to skip but required.
6. Paste, one line at a time:

   ```bash
   git clone https://github.com/<your-org>/auriga-extract.git
   cd auriga-extract
   pipx install .
   ```

7. Check it worked: paste `auriga-extract --help`. If you see a list of
   options rather than an error, you're done — skip to **Usage** below.

If any step prints an error, copy the exact text and check the
**Troubleshooting** section at the bottom of this page, or ask whoever sent
you this tool.

</details>

<details>
<summary><strong>Using conda?</strong></summary>

> **⚠️ Don't `pip install` or `conda install` this tool directly into a conda
> environment.** Mixing conda and pip installs of the same packages in one
> environment can leave you with two incompatible versions fighting over the
> same files (conflicting binaries, broken imports) — annoying to debug and
> easy to avoid.

Use **pipx** for this tool even if you have conda installed — pipx creates
its own isolated environment, completely separate from any conda env, so
there's no interaction at all:

```bash
conda deactivate          # make sure no conda env is active
python -m pip install --user pipx
python -m pipx ensurepath
git clone https://github.com/clemmbn/auriga-extract.git
cd auriga-extract
pipx install .
```

`auriga-extract` is then available from any terminal, conda-activated or
not.

If you'd rather install it inside a conda env anyway (e.g. to match a course
setup), keep it consistent: create a dedicated env, then install *only* with
`pip` inside it — never both:

```bash
conda create -n auriga python=3.11 -y
conda activate auriga
pip install -e .
```

You'll then need `conda activate auriga` every time before running
`auriga-extract`, which is the main reason pipx is recommended above.
</details>

Works the same on macOS, Windows, and Linux.

```bash
python -m pip install --user pipx   # one-time, skip if you have pipx
python -m pipx ensurepath           # then restart your terminal

git clone https://github.com/clemmbn/auriga-extract.git
cd auriga-extract
pipx install .
```

`auriga-extract` is now a command available from any directory.
Already use [uv](https://docs.astral.sh/uv/)?
`uv tool install .` works the same way and is a bit faster.

To update after pulling new changes: `git pull && pipx install --force .`

## Usage

```bash
auriga-extract --start 2026-09-01 --end 2027-06-30
```

1. **Log in** in the Chrome window that opens — no key to press, the tool
   detects your session on its own and closes the browser once picked up.
2. **Wait** while it fetches your schedule, month by month.
3. **Pick courses** to keep from the table shown (`1,3`, `1-6`, `all`,
   `none`). Course-less one-offs (holidays, admin notices, language
   classes...) are listed separately so nothing gets lost.
4. **Get the file** — one combined `.ics`, written to `~/Downloads` by
   default (`--out <dir>` to change it), path printed at the end.

Every event has a stable ID, so re-exporting and re-importing later updates
existing events instead of duplicating them — support varies by app, see
below.

### All options

```
--start START    first day, YYYY-MM-DD (required)
--end END        last day, YYYY-MM-DD (required)
--out OUT        output directory (default: ~/Downloads)
--url URL        portal page to open (default: the Supaero planning page)
--channel NAME   installed browser channel to launch, e.g. chrome, msedge
                 (default: chrome; pass '' to try the bundled browser instead —
                 this usually fails, since the portal blocks it)
--capture [DIR]  also record raw network traffic there (debugging only)
```

## Import into your calendar

The `.ics` is a plain file so your calendar won't
auto-update. Re-import after re-running an export. In every app below,
import into a **new, separate calendar** (e.g. "Supaero") rather than your
main one, so it's easy to show/hide or wipe and redo.

- **Google Calendar** — Settings (gear icon) → **Import & export** → select
  the file → choose your Supaero calendar → **Import**. Full steps:
  [support.google.com/calendar/answer/37118](https://support.google.com/calendar/answer/37118).
  Guest lists/conferencing links aren't carried over on import (irrelevant
  here — class events have neither).
- **Apple Calendar (macOS)** — double-click the file, pick the target
  calendar in the dialog, **OK**. Re-imports update matching events in
  place (stable ID).
- **Outlook (desktop/web)** — desktop: **File → Open & Export →
  Import/Export**; web: **Settings → Calendar → Import calendar**. Either
  way, choose **Import an iCalendar (.ics) file** and pick the target
  calendar. Outlook always adds new events rather than updating by ID — on
  re-import, delete the previous batch first (easy if it's its own
  calendar) to avoid duplicates.
- **iPhone/iPad without a Mac** — send yourself the file (email, AirDrop,
  Files) and tap it; iOS opens it in Calendar with an "Add to Calendar"
  prompt.

## Troubleshooting

- **Browser fails to launch ("Couldn't start chrome"):** install Google
  Chrome, or pass `--channel msedge` to use Edge instead.
- **`auriga-extract: command not found` after install:** restart your
  terminal so `pipx ensurepath`'s PATH change takes effect.
