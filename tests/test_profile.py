"""
Tests for browser discovery and the persistent profile location.

These paths are the only thing standing between a student and a "nothing
happens" failure on a machine unlike the developer's, and two of the three
branches can never run on the developer's own OS -- so they are pinned here.
"""

from __future__ import annotations

import pytest

from auriga_extract import cdp


def test_profile_on_macos(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    path = cdp.default_profile()
    assert path.parts[-3:] == ("Application Support", "auriga-extract", "chrome-profile")


def test_profile_on_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", "/fake/AppData/Local")
    path = cdp.default_profile()
    assert path.parts[-2:] == ("auriga-extract", "chrome-profile")
    assert "Local" in str(path)


def test_profile_on_windows_without_localappdata(monkeypatch):
    """Falls back to the home directory rather than crashing on a bare env."""
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    path = cdp.default_profile()
    assert path.parts[-2:] == ("auriga-extract", "chrome-profile")


def test_profile_on_linux(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    path = cdp.default_profile()
    assert path.parts[-3:] == (".config", "auriga-extract", "chrome-profile")


def test_find_chromium_prefers_path(monkeypatch):
    """PATH wins over the well-known install locations."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        cdp.shutil, "which", lambda name: "/usr/bin/chromium" if name == "chromium" else None
    )
    assert cdp.find_chromium() == "/usr/bin/chromium"


def test_find_chromium_falls_back_to_known_locations(monkeypatch):
    """Nothing on PATH is the normal case on macOS, where apps live in /Applications."""
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(cdp.shutil, "which", lambda name: None)
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    monkeypatch.setattr(cdp.os.path, "isfile", lambda path: path == chrome)
    assert cdp.find_chromium() == chrome


def test_find_chromium_raises_actionable_error(monkeypatch):
    """With no browser anywhere, the message must name the escape hatch."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(cdp.shutil, "which", lambda name: None)
    monkeypatch.setattr(cdp.os.path, "isfile", lambda path: False)
    with pytest.raises(RuntimeError, match="--browser"):
        cdp.find_chromium()


def test_spawn_requests_session_restore(monkeypatch, tmp_path):
    """
    --restore-last-session must be passed, or the persistent profile is moot.

    The portal's identity cookies (KEYCLOAK_IDENTITY, AUTH_SESSION_ID, and the
    Shibboleth IdP's shib_idp_session) are all SESSION cookies. Chrome writes
    them to disk but purges them at the next startup unless that startup is a
    session restore -- so without this flag every run starts logged out even
    though the profile persisted perfectly. Verified against the live portal.
    """
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return object()

    monkeypatch.setattr(cdp.subprocess, "Popen", fake_popen)
    cdp._spawn("/bin/chrome", 9222, tmp_path / "profile", "https://example.test/#/planning")

    assert "--restore-last-session" in captured["argv"]
    assert f"--user-data-dir={tmp_path / 'profile'}" in captured["argv"]
    assert "--remote-debugging-port=9222" in captured["argv"]


def test_spawn_reports_a_bad_browser_path(monkeypatch, tmp_path):
    """A hand-typed --browser path must not surface as a raw OSError traceback."""

    def fake_popen(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(cdp.subprocess, "Popen", fake_popen)
    with pytest.raises(RuntimeError, match="--browser"):
        cdp._spawn("/nonexistent/chrome", 9222, tmp_path / "p", "https://example.test")


def test_token_expiry_reads_jwt_claim():
    """exp is read for a friendly log line; the token is never verified."""
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps({"exp": 1_800_000_000}).encode()).rstrip(b"=")
    token = f"Bearer header.{payload.decode()}.signature"
    expiry = cdp.token_expiry(token)
    assert expiry is not None
    assert expiry.year == 2027


def test_token_expiry_tolerates_opaque_tokens():
    """An unreadable token is fine -- it just means no expiry line is printed."""
    assert cdp.token_expiry("Bearer not-a-jwt") is None


# --------------------------------------------------------------------------
# Browser lifecycle: launching, adopting, and shutting down
#
# Both branches below are failure modes that cascade. A browser left running
# holds the profile lock, so one bad run turns into a tool that appears
# permanently broken; a browser closed that we never opened destroys work the
# user was doing in their own window.
# --------------------------------------------------------------------------


class _FakeProc:
    """Stands in for subprocess.Popen: never exits, records terminate()."""

    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


def test_open_terminates_the_browser_when_the_port_never_opens(monkeypatch, tmp_path):
    """
    A browser that starts but never opens its debugging port must be killed.

    Leaving it running is worse than the failure: it keeps the profile
    directory locked, so every later run trips the "profile is already in use"
    path instead, and the tool looks broken until the user finds a stray window.
    """
    proc = _FakeProc()
    monkeypatch.setattr(cdp, "_spawn", lambda *args, **kwargs: proc)
    monkeypatch.setattr(cdp, "port_alive", lambda port: False)
    monkeypatch.setattr(cdp.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="never opened port"):
        cdp.BrowserSession.open(
            "https://portal.test/#/planning",
            profile=tmp_path / "profile",
            browser_path="/bin/chrome",
        )

    assert proc.terminated


def test_adopting_a_running_browser_steers_it_to_the_portal(monkeypatch, tmp_path):
    """
    A browser already on the port is sitting wherever its owner left it.

    Nothing downstream recovers from that: wait_for_token only nudges once the
    tab is on the app host, so a tab parked elsewhere yields no API call, no
    token, and a silent wait until the full 300s timeout expires.
    """
    navigated = []
    monkeypatch.setattr(cdp, "port_alive", lambda port: True)
    monkeypatch.setattr(
        cdp.BrowserSession, "navigate", lambda self, url, **kwargs: navigated.append(url)
    )

    session = cdp.BrowserSession.open(
        "https://portal.test/#/planning", profile=tmp_path / "profile"
    )

    assert navigated == ["https://portal.test/#/planning"]
    assert session.launched is False


def test_adopting_a_browser_that_cannot_be_steered_is_not_fatal(monkeypatch, tmp_path):
    """Advisory, not fatal -- the user can still navigate the tab by hand."""

    def boom(self, url, **kwargs):
        raise RuntimeError("no debuggable tab available")

    monkeypatch.setattr(cdp, "port_alive", lambda port: True)
    monkeypatch.setattr(cdp.BrowserSession, "navigate", boom)

    session = cdp.BrowserSession.open("https://portal.test/", profile=tmp_path / "p")
    assert session.launched is False


def test_close_leaves_an_adopted_browser_running(monkeypatch, tmp_path):
    """Closing a window the user opened for their own work would be destructive."""
    closed = []
    monkeypatch.setattr(cdp, "port_alive", lambda port: True)
    monkeypatch.setattr(cdp.BrowserSession, "navigate", lambda self, url, **kwargs: None)
    monkeypatch.setattr(
        cdp, "devtools_json", lambda port, path="/json": closed.append(path) or {}
    )

    session = cdp.BrowserSession.open("https://portal.test/", profile=tmp_path / "p")
    session.close()

    assert closed == []


def test_close_shuts_down_a_browser_we_launched(monkeypatch, tmp_path):
    """The normal path still closes the window this run opened."""
    proc = _FakeProc()
    alive = iter([False, True])
    monkeypatch.setattr(cdp, "_spawn", lambda *args, **kwargs: proc)
    monkeypatch.setattr(cdp, "port_alive", lambda port: next(alive, True))

    session = cdp.BrowserSession.open(
        "https://portal.test/", profile=tmp_path / "p", browser_path="/bin/chrome"
    )
    assert session.launched is True

    asked = []
    monkeypatch.setattr(
        cdp,
        "devtools_json",
        lambda port, path="/json": asked.append(path) or {"webSocketDebuggerUrl": "ws://x/1"},
    )
    monkeypatch.setattr(cdp, "WebSocket", lambda url, timeout=5: _FakeWebSocket())
    monkeypatch.setattr(cdp.time, "sleep", lambda seconds: None)
    session.close()

    assert "/json/version" in asked


class _FakeWebSocket:
    """Accepts the Browser.close command without touching a real socket."""

    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)

    def close(self):
        pass
