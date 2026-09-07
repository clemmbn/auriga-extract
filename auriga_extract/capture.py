"""
Network-capture layer.

Records every interesting HTTP response a Playwright browser context receives,
writing an ordered JSONL index plus one file per response body. This is the
foundation both for API discovery (probe.py) and, later, for the real
extraction loop -- which will reuse the exact same listener to harvest calendar
JSON while driving the UI.

Two constraints shape the design:

1. Ordering matters more than completeness. Telling the "calendar list" call
   apart from the "event detail" call is done by correlating requests with what
   the user was doing at the time, so markers and responses share one sequence
   counter and one append-only file.

2. Nothing secret gets persisted. Request headers are written to disk and read
   back later, so credential-bearing headers are stripped. The project spec
   forbids storing credentials or sessions between runs.
"""

from __future__ import annotations

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
)

# Only these resource types are recorded. The portal's data all arrives as
# XHR/fetch; recording images/fonts/css would bury the signal. Anything served
# as JSON is captured regardless of type, as a safety net for SPAs that fetch
# through mechanisms Playwright labels differently.
CAPTURED_RESOURCE_TYPES = {"xhr", "fetch"}

# Guard against a single pathological response filling the disk.
MAX_BODY_BYTES = 8_000_000

# Post bodies go inline in the index; keep them readable.
MAX_POST_DATA_CHARS = 4000


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """
    Copy a header mapping with credential-bearing values replaced.

    headers: raw header name -> value mapping from Playwright (names lowercased).
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

    def record_response(self, response: Any) -> None:
        """
        Record one Playwright Response if it looks like application data.

        response: a playwright.sync_api.Response, delivered by the context's
        "response" event.
        Returns nothing. Side effects: may write a body file plus an index
        record, and prints a one-line summary so the run is visibly working.

        Never raises: a probe that dies on one odd response (a redirect with no
        body, a connection reset mid-session) loses the whole session's data.
        """
        try:
            self._record_response_inner(response)
        except Exception as exc:  # noqa: BLE001 - capture must never break the run
            print(f"[capture] !! failed to record a response: {type(exc).__name__}: {exc}")

    def _record_response_inner(self, response: Any) -> None:
        """Body of record_response; see that method. Split out to keep it flat."""
        request = response.request
        resource_type = request.resource_type
        content_type = (response.headers or {}).get("content-type", "")

        # Early return on the overwhelming majority: static assets.
        is_json = "json" in content_type.lower()
        if resource_type not in CAPTURED_RESOURCE_TYPES and not is_json:
            return

        seq = self._next_seq()
        parsed = urlparse(response.url)

        body_text: Optional[str] = None
        body_error: Optional[str] = None
        n_bytes = 0

        if any(marker in response.url for marker in CREDENTIAL_BODY_MARKERS):
            self._write_credential_placeholder(seq, request, response, parsed)
            return

        try:
            raw = response.body()
            n_bytes = len(raw)
            if n_bytes > MAX_BODY_BYTES:
                body_error = f"body too large ({n_bytes} bytes), not stored"
            else:
                body_text = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - redirects/empty bodies raise here
            body_error = f"{type(exc).__name__}: {exc}"

        body_file: Optional[str] = None
        json_shape: Optional[dict[str, Any]] = None
        if body_text is not None:
            json_shape = _describe_json(body_text)
            suffix = "json" if json_shape else "txt"
            body_path = self.bodies_dir / f"{seq:04d}.{suffix}"
            body_path.write_text(body_text, encoding="utf-8")
            body_file = str(body_path.relative_to(self.root))

        post_data = request.post_data
        if post_data and len(post_data) > MAX_POST_DATA_CHARS:
            post_data = post_data[:MAX_POST_DATA_CHARS] + "...<truncated>"

        self._write(
            {
                "seq": seq,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "kind": "response",
                "method": request.method,
                "url": response.url,
                "path": parsed.path,
                "fragment": parsed.fragment,
                "query": {k: v for k, v in parse_qs(parsed.query).items()},
                "status": response.status,
                "resource_type": resource_type,
                "content_type": content_type,
                "bytes": n_bytes,
                "post_data": post_data,
                "request_headers": _redact_headers(request.headers or {}),
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
            f"[capture] #{seq:04d} {request.method} {response.status} "
            f"{parsed.path[:70]} {n_bytes}B{shape_note}"
        )

    def _write_credential_placeholder(self, seq, request, response, parsed) -> None:
        """
        Record that a credential endpoint was called, without its body.

        seq: sequence number already allocated for this response.
        request/response: the Playwright objects.
        parsed: the urlparse result for the response URL.
        Side effect: writes an index record whose body is deliberately absent.
        """
        self._write(
            {
                "seq": seq,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "kind": "response",
                "method": request.method,
                "url": response.url,
                "path": parsed.path,
                "status": response.status,
                "resource_type": request.resource_type,
                "content_type": (response.headers or {}).get("content-type", ""),
                "bytes": 0,
                "post_data": None,
                "request_headers": {},
                "body_file": None,
                "body_error": "<not stored: credential-bearing endpoint>",
                "json_shape": None,
            }
        )
        print(f"[capture] #{seq:04d} {request.method} {parsed.path[:60]} <body not stored: credentials>")

    def close(self) -> None:
        """Flush and close the index file. Safe to call twice."""
        if self._index_fh and not self._index_fh.closed:
            self._index_fh.close()
        print(f"[capture] closed; {len(self.records)} records in {self.root}")


def attach(context: Any, sink: CaptureSink) -> None:
    """
    Wire a Playwright browser context up to a sink.

    context: a playwright.sync_api.BrowserContext.
    sink: the CaptureSink receiving records.
    Side effect: registers event listeners for the life of the context.

    Listening at context level (rather than per page) means popups and tabs
    opened during SSO are covered without extra bookkeeping, since they share
    the context.

    Caveat for callers: Playwright's sync API only dispatches these events
    while the main thread is inside a Playwright call. A caller that blocks on
    input() will see events pile up and arrive late, out of order relative to
    markers -- see probe.py for the pump-and-poll pattern that avoids this.
    """
    context.on("response", sink.record_response)
    context.on(
        "page",
        lambda page: print(f"[capture] new page/tab opened: {page.url[:90]}"),
    )
    print("[capture] listeners attached to browser context")
