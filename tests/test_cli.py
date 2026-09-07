"""
Tests for CLI-level behaviour: argument handling and interrupt handling.

The Ctrl-C path is pinned here rather than exercised by signalling a live run,
because a warm run finishes in a few seconds -- any race against it is flaky and
would quietly stop testing anything the day the tool got faster.
"""

from __future__ import annotations

from datetime import date

import pytest

from auriga_extract import __version__, cli


def test_ctrl_c_reports_cancellation_instead_of_a_traceback(monkeypatch, capsys):
    """
    KeyboardInterrupt is a BaseException, so run()'s `except Exception` does not
    catch it. Without main()'s handler a Ctrl-C -- the obvious thing to press
    during a long fetch -- ends in a raw Python traceback.
    """

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run", interrupted)

    assert cli.main([]) == 130  # shell convention for "terminated by SIGINT"
    assert "Cancelled" in capsys.readouterr().out


def test_version_flag_reports_the_package_version(capsys):
    """A bug report has to be able to name the exact build it came from."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])

    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_an_inverted_date_range_is_rejected_before_a_browser_opens(capsys):
    """Launching a browser only to fail on arithmetic would waste the user's login."""
    code = cli.run(
        start=date(2027, 1, 1),
        end=date(2026, 1, 1),
        url="https://portal.test/",
        out_root=None,
        capture_dir=None,
    )

    assert code == 2
    assert "before --start" in capsys.readouterr().out
