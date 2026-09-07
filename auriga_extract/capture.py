"""
Network-capture layer.

Records every interesting HTTP response the browser receives, writing an ordered
JSONL index plus one file per response body. This is the foundation for API
discovery (probe.py) and for the --capture debugging flag.

Traffic is observed over the Chrome DevTools Protocol: Network.requestWillBeSent
supplies the request side, Network.responseReceived the status and headers, and
Network.getResponseBody the payload, keyed by the requestId that ties them
together.

Three constraints shape the design:

1. Ordering matters more than completeness. Telling the "calendar list" call
   apart from the "event detail" call is done by correlating requests with what
   the user was doing at the time, so markers and responses share one sequence
   counter and one append-only file.

2. Nothing secret gets persisted. Request headers are written to disk and read
   back later, so credential-bearing headers are stripped. (Note the tool now
   DOES persist a browser profile between runs, reversing INSTRUCTIONS.md:19 --
   but that is a browser-managed folder, deliberately separate from these
   capture directories, which stay credential-free.)

3. Response bodies must be collected promptly. Network.getResponseBody reads
   from a per-page buffer that the browser discards on navigation, so a body
   fetched "later" is often simply gone. Hence pump(), called from the caller's
   own event loop rather than at close time.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

# Headers that may carry session material. Stripped before anything is written
# to disk, since capture directories are kept around and re-read.
REDACTED_HEADERS = {
    "cookie",
    "set-cookie",
    "authorization",
    "proxy-authorization",
    "x-auth-token",
    "x-xsrf-token",
}

# Endpoints whose RESPONSE BODIES are credentials, not data. Redacting request
# headers is not enough: the Keycloak token endpoint returns access, refresh
# and id tokens in its body, which would otherwise sit in plaintext in every
# capture directory. Nothing here is useful for understanding the timetable
# API, so it is dropped rather than stored.
CREDENTIAL_BODY_MARKERS = (
    "/protocol/openid-connect/token",
    "/protocol/openid-connect/userinfo",
    # Returns the portal's third-party Mapbox API key in plaintext. Not the
    # user's credential -- every logged-in student is served the same one -- but
    # it is still somebody's API key, and writing it into a directory that gets
    # kept and re-read would quietly break the promise made above. Nothing in it
    # helps understand the timetable API. Found while verifying a probe capture
    # on 2026-09-07.
    "/api/privateConfig",
)

# Only these resource types are recorded. The portal's data all arrives as
# XHR/fetch; recording images/fonts/css would bury the signal. Anything served
# as JSON is captured regardless of type, as a safety net for SPAs that fetch
# through mechanisms the protocol labels differently. CDP capitalises these
# ("XHR", "Fetch"), so comparisons lowercase first.
CAPTURED_RESOURCE_TYPES = {"xhr", "fetch"}

# Guard against a single pathological response filling the disk.
MAX_BODY_BYTES = 8_000_000

# Post bodies go inline in the index; keep them readable.
MAX_POST_DATA_CHARS = 4000


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """
    Copy a header mapping with credential-bearing values replaced.

    headers: raw header name -> value mapping; CDP preserves server casing, so
    the comparison lowercases each name.
    Returns a new dict; values of REDACTED_HEADERS become "<redacted>".
    """
    return {
        name: ("<redacted>" if name.lower() in REDACTED_HEADERS else value)
        for name, value in headers.items()
    }


def _describe_json(text: str) -> Optional[dict[str, Any]]:
    """
    Produce a shallow shape summary of a JSON document.

    text: the raw response body.
    Returns a small dict describing the top level (object keys, or array length
    plus the keys of the first element), or None when the body is not JSON.

    The summary exists so the end-of-run report can show what each endpoint
    returns without anyone opening the body files.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None

    if isinstance(data, dict):
        return {"type": "object", "n_keys": len(data), "keys": list(data)[:15]}

    if isinstance(data, list):
        shape: dict[str, Any] = {"type": "array", "len": len(data)}
        # An array of objects is the shape a calendar-event feed almost
        # certainly takes, so surface the element keys too -- that is what
        # identifies the endpoint we are hunting for.
        if data and isinstance(data[0], dict):
            shape["item_keys"] = list(data[0])[:25]
        return shape

    return {"type": type(data).__name__}


@dataclass
class CaptureSink:
    """
    Owns one capture directory and the ordered record stream inside it.

    root: directory to create; receives index.jsonl and a bodies/ subdirectory.

    Side effects: creates directories and holds an open append-mode file handle
    until close() is called. Every record is flushed immediately so a capture
    survives the process being killed mid-session.
    """

    root: Path
    records: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0
    _index_fh: Any = None
    # requestId -> the request half of an exchange, kept until the response
    # arrives. CDP splits one HTTP exchange across several events, and only the
    # requestId links them.
    _pending: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.bodies_dir = self.root / "bodies"
        self.bodies_dir.mkdir(parents=True, exist_ok=True)
        self._index_fh = (self.root / "index.jsonl").open("a", encoding="utf-8")
        print(f"[capture] writing to {self.root}")

    def _write(self, record: dict[str, Any]) -> None:
        """
        Append one record to the index and to the in-memory list.

        record: any JSON-serialisable mapping; gets seq/ts stamped by callers.
        Side effect: writes a line to index.jsonl and flushes.
        """
        self.records.append(record)
        self._index_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._index_fh.flush()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def record_marker(self, text: str) -> None:
        """
        Insert a user-typed annotation into the stream.

        text: free-form note describing what the user is about to do.
        Side effect: writes a record; prints confirmation.

        Markers are the whole reason this capture is interpretable -- they let
        us attribute a burst of requests to "navigated a month" or "clicked an
        event" after the fact.
        """
        self._write(
            {
                "seq": self._next_seq(),
                "ts": datetime.now().isoformat(timespec="seconds"),
                "kind": "marker",
                "text": text,
            }
        )
        print(f"[capture] >>> marker: {text}")

    def pump(self, devtools: Any) -> None:
        """
        Turn buffered DevTools events into capture records.

        devtools: a cdp.DevTools whose .events list is read (not cleared -- the
        caller owns that, since it may also be scanning them for a token).
        Side effects: writes records and body files; prints one line per
        recorded response.

        Must be called from the caller's polling loop, promptly and often:
        response bodies live in a buffer the browser drops on navigation.

        Never raises. A probe that dies on one odd response (a redirect with no
        body, a connection reset mid-session) loses the whole session's data.
        """
        for message in list(devtools.events):
            try:
                self._handle_event(devtools, message)
            except Exception as exc:  # noqa: BLE001 - capture must never break the run
                print(f"[capture] !! failed to record a response: {type(exc).__name__}: {exc}")

    def _handle_event(self, devtools: Any, message: dict[str, Any]) -> None:
        """
        Dispatch one protocol event.

        devtools: connection used to fetch response bodies.
        message: a raw CDP event.
        Side effect: updates pending state or writes a record.
        """
        method = message.get("method")
        params = message.get("params") or {}
        request_id = params.get("requestId")

        if method == "Network.requestWillBeSent" and request_id:
            request = params.get("request") or {}
            self._pending[request_id] = {
                "method": request.get("method", ""),
                "headers": request.get("headers") or {},
                "post_data": request.get("postData"),
                "resource_type": params.get("type", ""),
            }
            return

        if method == "Network.responseReceived" and request_id:
            # Merge rather than replace: the request half was stored earlier and
            # carries the headers and post body we still need.
            entry = self._pending.setdefault(request_id, {})
            entry["response"] = params.get("response") or {}
            entry["resource_type"] = params.get("type", entry.get("resource_type", ""))
            return

        # loadingFinished is the earliest point at which the body is complete.
        if method in ("Network.loadingFinished", "Network.loadingFailed") and request_id:
            entry = self._pending.pop(request_id, None)
            if entry and entry.get("response"):
                self._record_exchange(devtools, request_id, entry)

    def _record_exchange(self, devtools: Any, request_id: str, entry: dict[str, Any]) -> None:
        """
        Write one completed request/response pair.

        devtools: connection used for Network.getResponseBody.
        request_id: the CDP requestId, needed to fetch the body.
        entry: the merged request/response state collected by _handle_event.
        Side effects: may write a body file plus an index record; prints a
        one-line summary so the run is visibly working.
        """
        response = entry["response"]
        url = response.get("url", "")
        content_type = (response.get("headers") or {}).get("content-type", "")
        # CDP header names preserve server casing; normalise for the lookup.
        if not content_type:
            for name, value in (response.get("headers") or {}).items():
                if name.lower() == "content-type":
                    content_type = value
                    break
        resource_type = (entry.get("resource_type") or "").lower()

        # Early return on the overwhelming majority: static assets.
        is_json = "json" in content_type.lower()
        if resource_type not in CAPTURED_RESOURCE_TYPES and not is_json:
            return

        seq = self._next_seq()
        parsed = urlparse(url)
        method = entry.get("method", "")
        status = response.get("status", 0)

        if any(marker in url for marker in CREDENTIAL_BODY_MARKERS):
            self._write_credential_placeholder(seq, entry, url, parsed, content_type)
            return

        body_text: Optional[str] = None
        body_error: Optional[str] = None
        n_bytes = 0
        try:
            result = devtools.call(
                "Network.getResponseBody", {"requestId": request_id}, timeout=10
            )
            raw_body = result.get("body", "")
            raw = (
                base64.b64decode(raw_body)
                if result.get("base64Encoded")
                else raw_body.encode("utf-8", "replace")
            )
            n_bytes = len(raw)
            if n_bytes > MAX_BODY_BYTES:
                body_error = f"body too large ({n_bytes} bytes), not stored"
            else:
                body_text = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - redirects/evicted bodies land here
            body_error = f"{type(exc).__name__}: {exc}"

        body_file: Optional[str] = None
        json_shape: Optional[dict[str, Any]] = None
        if body_text is not None:
            json_shape = _describe_json(body_text)
            suffix = "json" if json_shape else "txt"
            body_path = self.bodies_dir / f"{seq:04d}.{suffix}"
            body_path.write_text(body_text, encoding="utf-8")
            body_file = str(body_path.relative_to(self.root))

        post_data = entry.get("post_data")
        if post_data and len(post_data) > MAX_POST_DATA_CHARS:
            post_data = post_data[:MAX_POST_DATA_CHARS] + "...<truncated>"

        self._write(
            {
                "seq": seq,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "kind": "response",
                "method": method,
                "url": url,
                "path": parsed.path,
                "fragment": parsed.fragment,
                "query": {k: v for k, v in parse_qs(parsed.query).items()},
                "status": status,
                "resource_type": resource_type,
                "content_type": content_type,
                "bytes": n_bytes,
                "post_data": post_data,
                "request_headers": _redact_headers(entry.get("headers") or {}),
                "body_file": body_file,
                "body_error": body_error,
                "json_shape": json_shape,
            }
        )

        shape_note = ""
        if json_shape:
            if json_shape.get("type") == "array":
                shape_note = f" array[{json_shape['len']}]"
            elif json_shape.get("type") == "object":
                shape_note = f" object({json_shape['n_keys']} keys)"
        print(
            f"[capture] #{seq:04d} {method} {status} "
            f"{parsed.path[:70]} {n_bytes}B{shape_note}"
        )

    def _write_credential_placeholder(
        self,
        seq: int,
        entry: dict[str, Any],
        url: str,
        parsed: Any,
        content_type: str,
    ) -> None:
        """
        Record that a credential endpoint was called, without its body.

        seq: sequence number already allocated for this response.
        entry: the merged request/response state.
        url: the full response URL.
        parsed: the urlparse result for that URL.
        content_type: the response content type.
        Side effect: writes an index record whose body is deliberately absent.
        """
        method = entry.get("method", "")
        self._write(
            {
                "seq": seq,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "kind": "response",
                "method": method,
                "url": url,
                "path": parsed.path,
                "status": (entry.get("response") or {}).get("status", 0),
                "resource_type": entry.get("resource_type", ""),
                "content_type": content_type,
                "bytes": 0,
                "post_data": None,
                "request_headers": {},
                "body_file": None,
                "body_error": "<not stored: credential-bearing endpoint>",
                "json_shape": None,
            }
        )
        print(f"[capture] #{seq:04d} {method} {parsed.path[:60]} <body not stored: credentials>")

    def close(self) -> None:
        """Flush and close the index file. Safe to call twice."""
        if self._index_fh and not self._index_fh.closed:
            self._index_fh.close()
        print(f"[capture] closed; {len(self.records)} records in {self.root}")


def attach(devtools: Any, sink: CaptureSink) -> None:
    """
    Subscribe a DevTools connection to the network domain.

    devtools: a cdp.DevTools connection.
    sink: the CaptureSink that will receive records via its pump().
    Side effect: enables the Network domain on the browser side.

    This only turns the event stream on. Nothing is recorded until the caller
    starts calling sink.pump(devtools) from its own loop -- an explicit pull
    rather than a callback, because bodies must be fetched with a live
    connection and the caller is the one who knows when it has one.

    Note the subscription belongs to the socket: after any reconnect, this must
    be called again. cdp.BrowserSession.connect() handles that for its own
    connections.
    """
    devtools.call("Network.enable", timeout=15)
    print("[capture] network events enabled")
