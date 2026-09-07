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
