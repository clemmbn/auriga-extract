# auriga-extract

<img width="1582" height="1035" alt="Capture d’écran 2026-09-10 à 09 20 07" src="https://github.com/user-attachments/assets/98b5bb5d-b3c3-4f8f-9691-181708d27aeb" />


Exports your ISAE-SUPAERO timetable from the Auriga portal into one `.ics`
file you can import into Apple Calendar, Google Calendar, Outlook, etc.

It opens a real browser, waits for you to log in normally, then reads your
schedule from the portal's own traffic. **Credentials are never stored or
seen by this tool**, you log in on the portal's own page. Your login is
remembered between runs, so after the first time it usually exports without
asking you to log in at all.

## Requirements

- **Git** — [git-scm.com/downloads](https://git-scm.com/downloads) (macOS
  offers to install it the first time it's needed).
- **A Chromium-family browser you already have** — Chrome, Edge, Brave or
  Chromium. It's found automatically. Firefox won't work: it doesn't speak the
  DevTools protocol this uses.

No Python install needed: [uv](https://docs.astral.sh/uv/) brings its own.
Nothing else is downloaded — no browser install step, only two small Python
packages.

## Install

Install uv, **restart your terminal completely** (the installer changes your
PATH; only a fresh terminal sees it), then install the tool:

```bash
# uv on macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
# uv on Windows (PowerShell)
winget install --id=astral-sh.uv -e
```

```bash
# the tool, same everywhere
uv tool install git+https://github.com/clemmbn/auriga-extract.git
```

`auriga-extract` is now a command available from any directory. To work on the
code instead, clone the repo and run `uv tool install --editable .` inside it.

<details>
<summary><strong>Never used a terminal before?</strong></summary>

A terminal is just a window where you type commands and press Enter. Paste each
command below one at a time, press Enter, and wait for it to finish before the
next one.

**On macOS:**

1. Open **Terminal** — press `Cmd+Space`, type `Terminal`, press Enter.
2. Paste `git --version`. If Git is missing, macOS offers to install the
   "Command Line Tools": click **Install** and wait for it to finish.
3. Paste the macOS uv line from above.
4. **Quit Terminal completely** (`Cmd+Q`, not just close the window) and
   reopen it. Easy to skip, but required.
5. Paste the `uv tool install ...` line.
6. Paste `auriga-extract --help`. A list of options means you're done — skip to
   **Usage**.

**On Windows:**

1. Install [Git for Windows](https://git-scm.com/downloads/win) — the default
   options are fine.
2. Open **PowerShell** — press the Windows key, type `PowerShell`, press Enter.
3. Paste the Windows uv line from above. If `winget` isn't recognised, use
   this instead:

   ```bash
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

4. **Close PowerShell completely** and reopen it. Easy to skip, but required.
5. Paste the `uv tool install ...` line.
6. Paste `auriga-extract --help`. A list of options means you're done — skip to
   **Usage**.

If a step prints an error, copy the exact text and check **Troubleshooting**
below, or ask whoever sent you this tool.

</details>

<details>
<summary><strong>Using conda?</strong></summary>

Nothing special: uv is a standalone executable with its own Python, so it never
touches your conda environments. You don't need `conda activate` to run the
tool, and it keeps working if you remove conda.

Avoid two things:

- **`pip install --user pipx`.** `--user` writes to a folder that *every* conda
  environment on the same Python minor version reads, so pipx and its
  dependencies show up in `pip list` and `conda env export` for all of them.
- **`pip install` or `conda install` of this tool into an env.** Conda and pip
  keep separate records and can overwrite each other's files.

**Already ran `pip install --user pipx`?** Your environments are fine: there is
one copy of those packages, not one per env. `python -m pip list --user` shows
what's in that shared folder. To clear it:

```bash
pipx uninstall auriga-extract
python -m pip uninstall pipx argcomplete userpath filelock platformdirs colorama click
```

pip prints each path before asking `y/n`. If one points inside an env
(`.../envs/<name>/lib/...`), answer **`n`** for it — `click` and `colorama` are
common dependencies and may legitimately live there. Then install with uv as
above.

</details>

<details>
<summary><strong>Prefer pipx?</strong></summary>

```bash
python -m pip install --user pipx   # skip if you have pipx; not for conda users
python -m pipx ensurepath           # then restart your terminal
pipx install git+https://github.com/clemmbn/auriga-extract.git
```

Conda users: install pipx into its own env instead —
`conda create -n tools -c conda-forge pipx`, then `conda activate tools` (only
needed to upgrade or uninstall). Update with `pipx upgrade auriga-extract`, or
`pipx reinstall auriga-extract` to force a fresh fetch.

</details>

### Updating the tool

```bash
uv tool upgrade auriga-extract
```

If it says it's up to date but you know a new version is out, force a fresh
fetch:

```bash
uv tool install --reinstall git+https://github.com/clemmbn/auriga-extract.git
```

To remove the tool: `uv tool uninstall auriga-extract`.

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

The `.ics` is a plain file, so your calendar won't auto-update: re-import after
re-running an export. In every app below, import into a **new, separate
calendar** (e.g. "Supaero") rather than your main one, so it's easy to
show/hide or wipe and redo.

- **Google Calendar** — Settings (gear icon) → **Import & export** → select
  the file → choose your Supaero calendar → **Import**. Full steps:
  [support.google.com/calendar/answer/37118](https://support.google.com/calendar/answer/37118).
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

## Re-exporting after a schedule change

Re-run `auriga-extract` and re-import the new file. Every event has a stable ID
(`auriga-<id>@auriga.isae-supaero`) taken from the portal's own intervention ID,
which survives a reschedule. Apple Calendar and Google Calendar match on it and
update the event in place instead of duplicating it. Outlook doesn't, so clear
out the old batch first (see above).

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
- **It asks you to log in every time:** more than half a day since the last
  login is expected — the school's sign-on session has expired. On back-to-back
  runs, check you're not passing a different `--profile` each time, and that the
  folder listed above is writable.
- **`auriga-extract: command not found` after install:** restart your terminal
  so the PATH change takes effect. If it still fails, run `uv tool update-shell`
  and restart again (with pipx: `pipx ensurepath`).
- **Windows: "Une stratégie de contrôle d'application a bloqué ce fichier"**
  (or "An Application Control policy has blocked this file"): Windows is
  refusing the small unsigned `auriga-extract.exe` launcher that uv and pipx
  create. This is usually **Smart App Control**, or a policy on a school/work
  PC, and can start after an update because every upgrade creates a brand-new
  launcher. The tool itself is fine: run it through its own Python, which
  Windows doesn't block. In PowerShell:

  ```bash
  & "$(uv tool dir)\auriga-extract\Scripts\python.exe" -m auriga_extract
  ```

  Installed with pipx? Use this instead:

  ```bash
  & "$(pipx environment --value PIPX_LOCAL_VENVS)\auriga-extract\Scripts\python.exe" -m auriga_extract
  ```

  All the usual options work after it (`--start`, `--out`, ...).

## License

MIT — see [LICENSE](LICENSE). Not affiliated with or endorsed by ISAE-SUPAERO
or the Auriga portal's vendor; it reads your own timetable, with your own
login, the same way the portal's web page does.
