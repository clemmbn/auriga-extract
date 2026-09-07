# auriga-extract

Exports your ISAE-SUPAERO timetable from the Auriga portal into one `.ics`
file you can import into Apple Calendar, Google Calendar, Outlook, etc.

It opens a real browser, waits for you to log in normally, then reads your
schedule from the portal's own traffic. **Credentials are never stored or
seen by this tool**, you log in on the portal's own page.

Your login is remembered between runs, so after the first time it usually
exports without asking you to log in at all.

## Requirements

- **Python 3.11+** — [python.org/downloads](https://www.python.org/downloads/)
  (Windows: tick "Add python.exe to PATH" during install).
- **A Chromium-family browser you already have** — Chrome, Edge, Brave or
  Chromium. It's found automatically. Firefox won't work: it doesn't speak the
  DevTools protocol this uses.

Nothing else is downloaded — no browser install step, and only two small Python
packages.

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
   git clone https://github.com/clemmbn/auriga-extract.git
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
   git clone https://github.com/clemmbn/auriga-extract.git
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
auriga-extract
```

This exports the full 2026-2027 academic year (2026-09-01 to 2027-08-31) by
default. Pass `--start`/`--end` to export a different range.

1. **Log in** in the browser window that opens — no key to press, the tool
   detects your session on its own. **On later runs you're usually already
   logged in and this step is skipped entirely.**
2. **Wait** while it fetches your schedule, month by month.
3. **Pick courses** to keep from the table shown. Press **Enter** to take
   everything, or type numbers (`1,3`), ranges (`1-6`), `all !3,5` to keep
   everything except a few, or `none` to abort. Course-less one-offs
   (holidays, admin notices, language classes...) are listed separately so
   nothing gets lost.
4. **Get the file** — one combined `.ics`, written to `~/Downloads` by
   default (`--out <dir>` to change it), path printed at the end.

### Your login is remembered

The browser profile used for this lives in its own folder, separate from your
everyday browser:

| OS | Folder |
|---|---|
| macOS | `~/Library/Application Support/auriga-extract/chrome-profile` |
| Windows | `%LOCALAPPDATA%\auriga-extract\chrome-profile` |
| Linux | `~/.config/auriga-extract/chrome-profile` |

It holds your portal session, exactly like a normal browser profile does.
**To sign out, delete that folder** — the next run will ask you to log in
again. Use `--profile <dir>` to keep it somewhere else.

The school's sign-on session doesn't last forever (roughly half a day), so
you'll be asked to log in again now and then. Re-running the same day
normally won't ask.

Every event has a stable ID, so re-exporting and re-importing later updates
existing events instead of duplicating them — support varies by app, see
below.

### All options

```
--version        print the version and exit
--start START    first day, YYYY-MM-DD (default: 2026-09-01)
--end END        last day, YYYY-MM-DD (default: 2027-08-31)
--out OUT        output directory (default: ~/Downloads)
--url URL        portal page to open (default: the Supaero planning page)
--browser PATH   path to Chrome/Edge/Brave/Chromium (default: auto-detect)
--profile DIR    browser profile directory; your login is remembered here
--port PORT      browser remote-debugging port (default: 9222)
--keep-browser   leave the browser window open when the export finishes
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
  prompt. If it doesn't work, watch [this video](https://www.youtube.com/watch?v=xEaamiZDWuo).

## Updating after a schedule change

Just re-run the same command and re-import the new file:

```bash
auriga-extract
```

This works because every event gets a stable ID derived from the portal's
own intervention ID (`auriga-<id>@auriga.isae-supaero`), not a random one
generated per export. When a class is rescheduled, the portal keeps the
same intervention ID, so the new export produces an event with the same ID
but updated time/room/etc. A calendar app that supports it (Apple Calendar,
Google Calendar) matches on that ID and updates the existing event in
place instead of adding a duplicate. Outlook doesn't do ID matching on
import, so there you still need to clear out the old batch first (see
above).

## Troubleshooting

- **"No Chrome, Edge, Brave or Chromium installation found":** install one of
  them, or point at it directly with `--browser "/path/to/chrome"`.
- **"The browser closed immediately... profile is probably already in use":**
  a browser window is already running on that profile. Close it, or use
  `--profile <other-folder>`.
- **"The browser never opened port 9222":** something else is using the port.
  Retry with `--port 9333`.
- **"Reusing the browser already open on port 9222":** a browser (probably one
  this tool left open) is already listening there. It gets pointed at the
  portal and left running at the end, since it isn't ours to close. Use
  `--port 9333` if you'd rather have a fresh window.
- **"Never saw an authenticated request":** the login didn't finish, or the
  planning view never opened. Re-run and complete the login in the window.
- **It asks you to log in every time:** if it's been more than half a day
  since the last login, that's expected — the school's sign-on session has
  expired. If it happens on back-to-back runs, check you're not passing a
  different `--profile` each time, and that the folder listed above is
  writable.
- **`auriga-extract: command not found` after install:** restart your
  terminal so `pipx ensurepath`'s PATH change takes effect.

## License

MIT — see [LICENSE](LICENSE). Not affiliated with or endorsed by ISAE-SUPAERO
or the Auriga portal's vendor; it reads your own timetable, with your own
login, the same way the portal's web page does.
