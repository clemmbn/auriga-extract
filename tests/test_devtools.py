"""
Tests for the DevTools request/response layer and token extraction.

The protocol interleaves command replies with unsolicited events on one socket.
The two behaviours that matter are that a reply is matched by id (not by
arrival order) and that events seen while waiting are kept rather than dropped
-- dropping them would discard the very requests the token is sniffed from.
"""

from __future__ import annotations

import json

import pytest

from auriga_extract.cdp import DevTools
from auriga_extract.fetch import token_from_events


class FakeSocket:
    """Stands in for a real socket; only settimeout is ever called."""

    def settimeout(self, timeout: float) -> None:
        pass


class FakeWebSocket:
    """
    Replays a scripted sequence of inbound messages.

    messages: dicts to hand back from recv(), in order. Once exhausted, recv()
    raises TimeoutError, which is how a quiet socket behaves.
    """

    def __init__(self, messages: list[dict]) -> None:
        self.inbox = list(messages)
        self.sent: list[dict] = []
        self.sock = FakeSocket()
        self.closed = False

    def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def recv(self) -> str:
        if not self.inbox:
            raise TimeoutError("no more messages")
        return json.dumps(self.inbox.pop(0))

    def close(self) -> None:
        self.closed = True


def make_devtools(messages: list[dict]) -> DevTools:
    """Build a DevTools bypassing __init__, wired to a scripted fake socket."""
    dev = DevTools.__new__(DevTools)
    dev.ws = FakeWebSocket(messages)
    dev._id = 0
    dev.events = []
    return dev


def test_call_matches_reply_by_id_and_keeps_events():
    """An event arriving before the reply must be buffered, not discarded."""
    dev = make_devtools(
        [
            {"method": "Network.requestWillBeSent", "params": {"requestId": "1"}},
            {"id": 1, "result": {"ok": True}},
        ]
    )

    result = dev.call("Network.enable")

    assert result == {"ok": True}
    assert dev.ws.sent[0]["method"] == "Network.enable"
    assert len(dev.events) == 1, "the interleaved event must survive the call"


def test_call_ignores_replies_to_other_commands():
    """A stale reply carrying a different id must not satisfy this call."""
    dev = make_devtools(
        [
            {"id": 99, "result": {"stale": True}},
            {"id": 1, "result": {"fresh": True}},
        ]
    )
    assert dev.call("Runtime.evaluate") == {"fresh": True}


def test_call_raises_on_protocol_error():
    dev = make_devtools([{"id": 1, "error": {"message": "No target with given id"}}])
    with pytest.raises(RuntimeError, match="No target with given id"):
        dev.call("Network.getResponseBody")


def test_call_times_out_when_no_reply_arrives():
    dev = make_devtools([])
    with pytest.raises(TimeoutError):
        dev.call("Network.enable", timeout=0.6)


def test_drain_collects_events_and_stops_when_quiet():
    dev = make_devtools(
        [
            {"method": "Network.responseReceived", "params": {"requestId": "a"}},
            {"method": "Network.loadingFinished", "params": {"requestId": "a"}},
        ]
    )
    dev.drain(1.0)
    assert [e["method"] for e in dev.events] == [
        "Network.responseReceived",
        "Network.loadingFinished",
    ]


def test_drain_propagates_connection_loss():
    """
    A dead socket must raise, not return quietly.

    Returning quietly is indistinguishable from an idle socket, so the caller
    would poll a corpse until its timeout instead of reconnecting.
    """

    class DeadWebSocket(FakeWebSocket):
        def recv(self):
            raise ConnectionError("WebSocket closed by peer")

    dev = make_devtools([])
    dev.ws = DeadWebSocket([])
    with pytest.raises(ConnectionError):
        dev.drain(1.0)


# --- target tracking --------------------------------------------------------


def test_connect_reconnects_when_the_tab_changes_target(monkeypatch):
    """
    A cross-origin navigation (the SSO hop) moves the tab to a new target.

    The old socket stays open and raises nothing -- it just never delivers
    another event. connect() must notice the target id changed and rebuild,
    otherwise the tool waits out its entire timeout watching a dead target.
    """
    from auriga_extract import cdp

    targets = [
        {"id": "T1", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/T1"},
        {"id": "T2", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/T2"},
    ]
    current = {"target": targets[0]}
    built: list[str] = []

    monkeypatch.setattr(cdp, "pick_target", lambda port, host: current["target"])

    def fake_devtools(ws_url, timeout=30.0):
        built.append(ws_url)
        dev = DevTools.__new__(DevTools)
        dev.ws = FakeWebSocket([{"id": 1, "result": {}}])
        dev._id = 0
        dev.events = []
        return dev

    monkeypatch.setattr(cdp, "DevTools", fake_devtools)

    session = cdp.BrowserSession(port=9222, profile=None, browser_path="x", app_host="host")

    first = session.connect()
    assert session.connect() is first, "same target must reuse the connection"
    assert len(built) == 1

    current["target"] = targets[1]
    second = session.connect()

    assert second is not first, "a new target must produce a new connection"
    assert built == [targets[0]["webSocketDebuggerUrl"], targets[1]["webSocketDebuggerUrl"]]


# --- token extraction -------------------------------------------------------


def event(url: str, headers: dict) -> dict:
    """Build a requestWillBeSent event with the given URL and headers."""
    return {
        "method": "Network.requestWillBeSent",
        "params": {"request": {"url": url, "headers": headers}},
    }


def test_token_found_on_api_request():
    events = [event("https://host/api/plannings/me", {"Authorization": "Bearer abc"})]
    assert token_from_events(events) == "Bearer abc"


def test_token_header_casing_is_ignored():
    """CDP does not normalise header names, so the match must be case-insensitive."""
    events = [event("https://host/api/x", {"authorization": "Bearer low"})]
    assert token_from_events(events) == "Bearer low"


def test_non_api_requests_are_ignored():
    """A token on an unrelated host must not be picked up."""
    events = [event("https://cdn/assets/main.js", {"Authorization": "Bearer nope"})]
    assert token_from_events(events) is None


def test_api_request_without_authorization_is_ignored():
    events = [event("https://host/api/public", {"Accept": "application/json"})]
    assert token_from_events(events) is None


def test_empty_authorization_is_ignored():
    """A blank header is not a token; treating it as one would fail much later."""
    events = [event("https://host/api/x", {"Authorization": "   "})]
    assert token_from_events(events) is None


def test_other_event_types_are_skipped():
    events = [{"method": "Network.responseReceived", "params": {"requestId": "1"}}]
    assert token_from_events(events) is None


def test_first_matching_token_wins_among_many():
    events = [
        event("https://cdn/app.js", {"Authorization": "Bearer wrong"}),
        event("https://host/api/a", {"Authorization": "Bearer right"}),
        event("https://host/api/b", {"Authorization": "Bearer later"}),
    ]
    assert token_from_events(events) == "Bearer right"
