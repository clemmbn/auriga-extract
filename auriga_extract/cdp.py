"""
Browser-control layer, built on the Chrome DevTools Protocol with nothing but
the standard library.

Purpose
-------
Drive a locally installed Chromium-family browser (Chrome, Edge, Brave,
Chromium) so the user can complete the school's SSO login by hand, and watch the
resulting network traffic long enough to learn the app's bearer token. Once the
token is known, no browser is needed at all -- the API answers plain HTTP
requests (measured 2026-09-07; see fetch.py).

This module replaces Playwright. That dependency cost 136 MB of wheel plus up to
552 MB of downloaded browsers, for a job that amounts to "launch a browser,
subscribe to Network events, read one header". The DevTools protocol is a
WebSocket carrying JSON, so a ~120-line WebSocket client covers it.

Main responsibilities
---------------------
  - WebSocket   : RFC 6455 client, only the parts DevTools actually uses.
  - DevTools    : request/response correlation plus event buffering.
  - Browser discovery and launch, with a PERSISTENT profile directory.
  - BrowserSession: the reconnect-tolerant handle both cli.py and probe.py use.

Non-obvious constraints
-----------------------
1. The DevTools endpoint is on 127.0.0.1 and must NEVER go through a system or
   corporate proxy, or the connection silently fails. Hence a dedicated opener
   built with an empty ProxyHandler.

2. Every SSO redirect navigates the tab, which tears down the WebSocket. Any
   loop that waits for the login MUST expect to reconnect repeatedly, and must
   re-issue Network.enable each time -- the subscription dies with the socket.

3. While the user may be typing on the login form, the page must never be
   reloaded. Re-entering the SPA's hash route forces it to refetch without
   touching the document, so a half-filled form survives.

4. The profile directory persists the login between runs. This intentionally
   reverses INSTRUCTIONS.md:19 ("no session persistence across runs"); see
   README for where the folder lives and how to sign out.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .console import console

# Default port for Chrome's DevTools listener. Overridable because a second
# copy of this tool (or an unrelated debugger) may already hold it.
DEFAULT_PORT = 9222

# Exceptions that all mean the same thing here: the tab went away mid-flight,
# almost always because an SSO redirect navigated it. Every one is recoverable
# by reconnecting, so they are caught as a group rather than individually.
TRANSIENT_ERRORS = (
    ConnectionError,
    OSError,
    RuntimeError,
    TimeoutError,
    ValueError,
    KeyError,
)


class WebSocket:
    """
    Minimal RFC 6455 client -- just enough to speak the DevTools protocol.

    Implements the opening handshake, client-side masking, the three payload
    length forms, fragmentation reassembly and ping/pong. Deliberately omits
    everything DevTools never sends: extensions, compression, binary frames.
    """

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        """
        Open a connection and complete the WebSocket handshake.

        url: a ws:// endpoint, as handed out by Chrome's /json listing.
        timeout: socket timeout in seconds, applied to the handshake and reads.
        Raises ValueError for a non-ws scheme, ConnectionError if the upgrade
        is refused. Side effect: opens a TCP socket held until close().
        """
        parts = urllib.parse.urlparse(url)
        if parts.scheme != "ws":
            raise ValueError(f"only ws:// is supported, got {url!r}")
        host = parts.hostname or "localhost"
        port = parts.port or 80
        path = parts.path + (f"?{parts.query}" if parts.query else "")

        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)

        # The key is not a security measure in a loopback context, but the
        # server rejects the handshake without a well-formed one.
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )

        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("DevTools closed during the WebSocket handshake")
            buffer += chunk
        status_line = buffer.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            raise ConnectionError(f"WebSocket upgrade refused: {status_line!r}")

        # The server may have packed the first frames into the same TCP segment
        # as the handshake response; keep them for the frame reader.
        self._rest = buffer.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        """
        Read exactly n bytes, drawing on leftovers before touching the socket.

        n: byte count required by the caller.
        Returns exactly n bytes. Raises ConnectionError if the peer closes
        first. Side effect: advances the internal leftover buffer.
        """
        out, self._rest = self._rest[:n], self._rest[n:]
        while len(out) < n:
            chunk = self.sock.recv(max(4096, n - len(out)))
            if not chunk:
                raise ConnectionError("WebSocket closed by peer")
            needed = n - len(out)
            out += chunk[:needed]
            # Anything past what this call needs belongs to the next frame.
            self._rest = chunk[needed:]
        return out

    def _read_frame(self) -> tuple[bool, int, bytes]:
        """
        Read one frame off the wire.

        Returns (fin, opcode, payload). Raises ConnectionError on a dead peer.
        """
        b1, b2 = self._read(2)
        fin, opcode = bool(b1 & 0x80), b1 & 0x0F
        masked, length = bool(b2 & 0x80), b2 & 0x7F
        # 126 and 127 are escapes meaning "the real length follows", in 2 or 8
        # network-order bytes respectively.
        if length == 126:
            (length,) = struct.unpack("!H", self._read(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._read(8))
        mask = self._read(4) if masked else None
        payload = self._read(length) if length else b""
        if mask:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return fin, opcode, payload

    def send(self, text: str) -> None:
        """
        Send one text message as a single masked frame.

        text: the JSON command to transmit.
        Side effect: writes to the socket. Clients MUST mask; servers must not.
        """
        data = text.encode()
        header = bytearray([0x81])  # FIN set, opcode 1 (text)
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(
            bytes(header) + bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        )

    def recv(self) -> str:
        """
        Return the next complete text message.

        Reassembles fragmented messages and answers pings inline, so callers
        only ever see application data. Raises ConnectionError when the peer
        sends a close frame or drops. Side effect: may write a pong.
        """
        chunks: list[bytes] = []
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == 0x8:  # close
                raise ConnectionError("WebSocket closed by peer")
            if opcode == 0x9:  # ping -> pong, with an empty masked payload
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode == 0xA:  # pong, nothing to do
                continue
            chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")

    def close(self) -> None:
        """Close the socket, ignoring an already-dead peer."""
        try:
            self.sock.close()
        except OSError:
            pass


class DevTools:
    """
    A DevTools protocol session over one WebSocket.

    Correlates replies with commands by the integer id every message carries,
    and buffers the unsolicited events that arrive in between (which is where
    the interesting data -- the outgoing requests -- actually lives).
    """

    def __init__(self, ws_url: str, timeout: float = 30.0) -> None:
        """
        ws_url: webSocketDebuggerUrl for a page or browser target.
        timeout: socket timeout for the underlying connection.
        Side effect: opens the WebSocket immediately.
        """
        self.ws = WebSocket(ws_url, timeout=timeout)
        self._id = 0
        self.events: list[dict] = []

    def call(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
        """
        Invoke one DevTools method and wait for its reply.

        method: protocol method name, e.g. "Network.enable".
        params: method arguments, or None.
        timeout: seconds to wait for the matching reply.
        Returns the "result" object. Raises RuntimeError if the browser reports
        an error, TimeoutError if no reply arrives.
        Side effect: any events seen while waiting are appended to self.events
        rather than discarded -- dropping them would lose the very requests we
        are trying to observe.
        """
        self._id += 1
        message_id = self._id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))

        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.sock.settimeout(max(0.5, deadline - time.time()))
            try:
                message = json.loads(self.ws.recv())
            except (socket.timeout, TimeoutError):
                continue
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error'].get('message')}")
                return message.get("result", {})
            if "method" in message:
                self.events.append(message)
        raise TimeoutError(f"no reply to {method}")

    def drain(self, seconds: float) -> None:
        """
        Collect incoming events for a while, ignoring replies.

        seconds: how long to keep reading.
        Side effect: appends to self.events. Returns quietly when the socket
        merely goes idle; raises ConnectionError when the peer has gone.

        The distinction matters: a dead connection that returned quietly would
        be indistinguishable from a quiet one, and the caller would poll a
        corpse until its timeout expired instead of reconnecting.
        """
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.ws.sock.settimeout(max(0.2, deadline - time.time()))
            try:
                message = json.loads(self.ws.recv())
            except (socket.timeout, TimeoutError):
                return
            if "method" in message:
                self.events.append(message)

    def close(self) -> None:
        """Close the underlying socket."""
        self.ws.close()


# The DevTools endpoint is always on loopback. Routing it through a configured
# system/corporate proxy is a classic silent failure (very common on managed
# Windows machines), so these requests get an opener with proxies disabled.
# Calls to the Auriga API itself deliberately keep normal proxy handling.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def devtools_json(port: int, path: str = "/json") -> Any:
    """
    Query Chrome's DevTools HTTP listener.

    port: the remote-debugging port.
    path: "/json" for the target list, "/json/version" for browser info.
    Returns the decoded JSON. Raises on connection failure.
    """
    with _LOCAL_OPENER.open(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return json.load(response)


def port_alive(port: int) -> bool:
    """
    Report whether a DevTools listener is answering on a port.

    port: the port to test. Returns True if it responds, False on any failure.
    """
    try:
        devtools_json(port, "/json/version")
        return True
    except Exception:  # noqa: BLE001 - any failure means "not usable"
        return False


def _is_windows() -> bool:
    """
    Report whether this is Windows.

    Returns True on any Windows build. Deliberately reads sys.platform rather
    than os.name: os.name is what pathlib consults to decide between PosixPath
    and WindowsPath, so overriding it in a test breaks path handling process-
    wide. sys.platform is inert by comparison, which keeps these branches
    testable from any machine.
    """
    return sys.platform.startswith("win")


def default_profile() -> Path:
    """
    Per-OS directory for the persistent browser profile.

    Returns the path. Nothing is created here; _spawn does that.

    This folder is what makes the login stick between runs. It is separate from
    the user's everyday browser profile, so this tool never touches their normal
    session and the two can run side by side.
    """
    if _is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "auriga-extract" / "chrome-profile"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "auriga-extract" / "chrome-profile"
    return Path.home() / ".config" / "auriga-extract" / "chrome-profile"


# Purely regenerable Chrome/Chromium state: rendering caches, component-
# updater downloads, and local metrics buffers. None of these hold anything
# the portal login depends on (that's Default/Cookies, Default/Local Storage,
# Default/Network -- deliberately left alone). Paths are relative to the
# profile root; a leading "Default/" reaches into the profile's only
# (default) Chrome profile.
_PURGEABLE_PROFILE_PATHS = [
    "BrowserMetrics",
    "component_crx_cache",
    "optimization_guide_model_store",
    "WasmTtsEngine",
    "Safe Browsing",
    "OnDeviceHeadSuggestModel",
    "GraphiteDawnCache",
    "ActorSafetyLists",
    "ZxcvbnData",
    "CertificateRevocation",
    "OptimizationHints",
    "Subresource Filter",
    "PKIMetadata",
    "Crowd Deny",
    "segmentation_platform",
    "SafetyTips",
    "OptimizationGuideModelsManifest",
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/DawnWebGPUCache",
    "Default/DawnGraphiteCache",
]


def purge_profile_caches(profile: Path) -> None:
    """
    Delete regenerable Chrome cache/telemetry directories from a profile.

    profile: the persistent profile directory (see default_profile()).
    Side effect: removes disk cache, component-updater downloads, and metrics
    buffers under `profile`; every one of them is rebuilt on next launch, so
    this only costs a slightly slower first paint next run, not the login.

    Called after every run, not just ones needing a fresh login: BrowserMetrics
    gets a new 4MB file on every single launch, warm or cold (measured: 6
    launches -> 24MB, never reclaimed on its own since metrics reporting is
    disabled and nothing consumes the old files), so without this it grows
    without bound regardless of how often the SSO session itself expires.
    """
    for rel in _PURGEABLE_PROFILE_PATHS:
        shutil.rmtree(profile / rel, ignore_errors=True)


def find_chromium() -> str:
    """
    Locate a Chromium-family browser.

    Returns an executable path. Raises RuntimeError with actionable advice when
    nothing is found.

    Searches PATH first (cheap, and respects a user's deliberate choice), then
    the standard install locations, which is where these browsers actually live
    on Windows and macOS since neither puts them on PATH. Firefox is not a
    candidate: it does not speak the DevTools protocol.
    """
    if _is_windows():
        names = ["chrome", "msedge", "brave", "chromium"]
    elif sys.platform == "darwin":
        names = ["chromium", "google-chrome", "chrome", "brave"]
    else:
        names = [
            "chromium",
            "chromium-browser",
            "google-chrome-stable",
            "google-chrome",
            "chrome",
            "brave",
            "microsoft-edge",
        ]
    for name in names:
        found = shutil.which(name)
        if found:
            return found

    candidates: list[str] = []
    if _is_windows():
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env)
            if not root:
                continue
            candidates += [
                os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(root, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                os.path.join(root, "Chromium", "Application", "chrome.exe"),
            ]
    elif sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
    for path in candidates:
        if os.path.isfile(path):
            return path

    raise RuntimeError(
        "No Chrome, Edge, Brave or Chromium installation found.\n"
        "Install one of them, or point at it directly:\n"
        '  --browser "/path/to/chrome"'
    )


def list_pages(port: int) -> list[dict]:
    """
    List debuggable page targets.

    port: the remote-debugging port.
    Returns the page targets that expose a WebSocket URL; empty on any failure.
    """
    try:
        targets = devtools_json(port)
    except Exception:  # noqa: BLE001 - treated as "nothing available yet"
        return []
    return [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]


def pick_target(port: int, app_host: str) -> dict:
    """
    Choose the tab to observe.

    port: the remote-debugging port.
    app_host: hostname of the portal, preferred when visible.
    Returns a target dict. Raises ConnectionError when no tab exists.

    Falling back to "any tab" is essential rather than sloppy: during SSO the
    tab sits on the identity provider's domain, so insisting on the app host
    would blind us for exactly the stretch we need to watch.
    """
    pages = list_pages(port)
    if not pages:
        raise ConnectionError("no debuggable tab available")
    for target in pages:
        if app_host in (target.get("url") or ""):
            return target
    return pages[0]


def token_expiry(token: str) -> Optional[datetime]:
    """
    Read the expiry out of a JWT bearer token.

    token: the Authorization header value, with or without the "Bearer " prefix.
    Returns an aware UTC datetime, or None if the token is not a readable JWT.
    The payload is only base64-decoded, never verified -- this is for a friendly
    log line, not a security decision.
    """
    try:
        payload = token.split(" ")[-1].split(".")[1]
        payload += "=" * (-len(payload) % 4)  # restore stripped base64 padding
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.fromtimestamp(claims["exp"], timezone.utc)
    except Exception:  # noqa: BLE001 - opaque tokens are fine, just unreadable
        return None


def _spawn(browser_path: str, port: int, profile: Path, url: str) -> subprocess.Popen:
    """
    Start the browser process.

    browser_path: executable to run.
    port: remote-debugging port to open.
    profile: persistent user-data directory (created if absent).
    url: page to open on startup.
    Returns the process handle. Side effect: spawns a browser window.
    """
    profile.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if _is_windows():
        # Keeps Ctrl-C in this terminal from also killing the browser.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen(
            [
                browser_path,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                # Without this the persistent profile is nearly pointless. The
                # portal's identity cookies (KEYCLOAK_IDENTITY, AUTH_SESSION_ID,
                # and the Shibboleth IdP's shib_idp_session behind it) are all
                # SESSION cookies. Chrome writes them to disk but purges them on
                # the next startup unless that startup is a session restore, so
                # every run would otherwise begin logged out even though the
                # profile itself persisted perfectly.
                "--restore-last-session",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=Translate,OptimizationHints,OptimizationHintsFetching,"
                "OptimizationTargetPrediction,OptimizationGuideModelDownloading",
                # This profile only needs to hold session cookies. Left at Chrome's
                # defaults, every fresh --user-data-dir independently downloads the
                # component-updater suite (Safe Browsing lists, on-device ML models,
                # a WASM TTS engine) -- 200MB+ of unrelated end-user features that
                # measured out to over 20x the size of the actual cookie/session
                # data (Default/) they were sitting next to.
                "--disable-component-update",
                "--disable-sync",
                "--disable-background-networking",
                # --metrics-recording-only is NOT what it sounds like: it skips the
                # UMA consent dialog for automation but still writes local metrics
                # logs. --disable-metrics is the flag that actually stops
                # BrowserMetrics/ from growing.
                "--disable-metrics",
                url,
            ],
            **kwargs,
        )
    except OSError as exc:
        # Almost always a hand-typed --browser path that does not exist, or one
        # pointing at something that is not executable.
        raise RuntimeError(
            f"Could not start the browser at {browser_path}\n  {exc}\n"
            "Check the path passed to --browser, or omit it to auto-detect."
        ) from exc


@dataclass
class BrowserSession:
    """
    A launched browser plus a reconnect-tolerant DevTools connection.

    port: the remote-debugging port in use.
    profile: persistent profile directory (this is what remembers the login).
    browser_path: the executable that was launched.
    app_host: portal hostname, used to prefer the right tab.
    proc: the process handle, or None when an already-running browser was reused.

    Side effects: owns a browser process and a socket until close() is called.
    """

    port: int
    profile: Path
    browser_path: str
    app_host: str
    proc: Optional[subprocess.Popen] = None
    # False when we adopted a browser that was already listening on the port.
    # close() consults this: shutting down a window the user opened for their
    # own work would be a rude surprise, and it is not ours to close.
    launched: bool = True
    _dev: Optional[DevTools] = field(default=None, repr=False)
    # Which DevTools target _dev is attached to, so a tab that navigates to a
    # new target can be noticed. See connect().
    _target_id: Optional[str] = field(default=None, repr=False)

    @classmethod
    def open(
        cls,
        url: str,
        *,
        port: int = DEFAULT_PORT,
        profile: Optional[Path] = None,
        browser_path: Optional[str] = None,
    ) -> "BrowserSession":
        """
        Launch (or adopt) a browser sitting on url.

        url: page to open; also supplies the app hostname.
        port: remote-debugging port.
        profile: persistent profile directory; defaults to default_profile().
        browser_path: explicit executable, or None to auto-detect.
        Returns a ready session. Raises RuntimeError with actionable text when
        no browser exists, the profile is already in use, or the port never
        opens. Side effect: may spawn a browser window, or navigate an existing
        one to url.
        """
        profile = profile or default_profile()
        app_host = urllib.parse.urlsplit(url).netloc

        # An already-listening port means a previous run left a browser open (or
        # the user started one). Reusing it is both faster and avoids the
        # profile-lock collision a second launch would hit.
        if port_alive(port):
            console.print(f"[dim]Reusing the browser already open on port {port}.[/]")
            session = cls(
                port=port,
                profile=profile,
                browser_path="(reused)",
                app_host=app_host,
                launched=False,
            )
            # An adopted browser is sitting wherever its owner left it, which is
            # usually NOT the portal. Nothing downstream would recover from that:
            # wait_for_token only nudges once the tab is already on the app host,
            # so a tab parked elsewhere means no nudge, no API call, no token --
            # and a silent wait until the full timeout expires.
            try:
                session.navigate(url)
            except Exception as exc:  # noqa: BLE001 - advisory, not fatal
                console.print(
                    f"[yellow]Could not steer that browser to the portal ({exc}).\n"
                    f"Open {url} in it yourself, or re-run with --port <other-port>.[/]"
                )
            return session

        browser_path = browser_path or find_chromium()
        console.print(f"[dim]Launching {os.path.basename(browser_path)}... This window will close on its own.[/]")
        proc = _spawn(browser_path, port, profile, url)

        # Chrome opens the debugging port a little after the process starts;
        # poll rather than guessing a sleep duration.
        for _ in range(80):
            if port_alive(port):
                return cls(
                    port=port,
                    profile=profile,
                    browser_path=browser_path,
                    app_host=app_host,
                    proc=proc,
                )
            if proc.poll() is not None:
                # Exiting instantly almost always means another browser instance
                # already holds this profile directory.
                raise RuntimeError(
                    f"The browser closed immediately.\n"
                    f"Its profile is probably already in use: {profile}\n"
                    "Close that browser window, or pass --profile <other-folder>."
                )
            time.sleep(0.5)

        # Leaving this process running would be worse than the failure itself:
        # it keeps the profile directory locked, so every later run trips the
        # "profile is already in use" path above and the tool looks permanently
        # broken until the user hunts down a stray window.
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001 - already gone
            pass
        raise RuntimeError(
            f"The browser never opened port {port}.\nTry again with --port <other-port>."
        )

    def connect(self) -> DevTools:
        """
        Return a live DevTools connection, (re)connecting if needed.

        Returns the session's DevTools. Raises ConnectionError when no tab is
        available. Side effects: may open a socket; re-issues Network.enable on
        every fresh connection -- the subscription belongs to the socket, so a
        reconnect without it would produce a permanently empty event stream.

        The target id is re-checked on every call, and that check is
        load-bearing. A cross-origin navigation (which is exactly what the SSO
        hop is) moves the tab to a NEW DevTools target. The old socket stays
        open and raises nothing; it simply never delivers another event. Without
        this comparison the tool waits out its whole timeout watching a target
        the user long since navigated away from.
        """
        target = pick_target(self.port, self.app_host)
        if self._dev is not None and target.get("id") != self._target_id:
            self.drop()
        if self._dev is None:
            self._dev = DevTools(target["webSocketDebuggerUrl"], timeout=15)
            self._dev.call("Network.enable", timeout=15)
            self._target_id = target.get("id")
        return self._dev

    def drop(self) -> None:
        """
        Discard the current connection so the next connect() makes a new one.

        Side effect: closes the socket. Used after a navigation kills it.
        """
        if self._dev is not None:
            self._dev.close()
            self._dev = None
        self._target_id = None

    def current_url(self) -> str:
        """Return the observed tab's URL, or "" if it cannot be read."""
        try:
            return pick_target(self.port, self.app_host).get("url") or ""
        except Exception:  # noqa: BLE001 - the tab may be mid-navigation
            return ""

    def navigate(self, url: str, timeout: float = 30.0) -> None:
        """
        Point the observed tab at a URL.

        url: absolute page URL to load.
        timeout: seconds to wait for the protocol reply.
        Side effect: navigates the tab, which tears down the DevTools socket --
        so the connection is dropped afterwards and the next connect() rebuilds
        it against whatever target the navigation produced.
        Raises on protocol errors or when no tab is available.
        """
        self.connect().call("Page.navigate", {"url": url}, timeout=timeout)
        self.drop()

    def evaluate(self, expression: str, timeout: float = 30.0) -> Any:
        """
        Run JavaScript in the observed tab.

        expression: source to evaluate.
        timeout: seconds to wait for the result.
        Returns the value by value (not as a remote handle). Raises on protocol
        errors or a dead connection.
        """
        result = self.connect().call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        return (result.get("result") or {}).get("value")

    def close(self, keep_open: bool = False) -> None:
        """
        Shut the browser down.

        keep_open: leave the window running (useful when debugging).
        Side effect: terminates the browser process.

        Browser.close over the protocol rather than proc.terminate(): on Windows
        Chrome re-spawns itself into a new process, so killing the handle we
        launched leaves an orphan window and a locked profile behind.
        """
        self.drop()
        if keep_open:
            return
        if not self.launched:
            # Adopted from an already-listening port, so it belongs to whoever
            # started it -- possibly the user, mid-task in their own window.
            console.print("[dim]Leaving the browser open; this run did not start it.[/]")
            return
        try:
            endpoint = devtools_json(self.port, "/json/version")["webSocketDebuggerUrl"]
            ws = WebSocket(endpoint, timeout=5)
            ws.send(json.dumps({"id": 1, "method": "Browser.close", "params": {}}))
            time.sleep(0.5)
            ws.close()
            return
        except Exception:  # noqa: BLE001 - fall through to the blunt instrument
            pass
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:  # noqa: BLE001 - already gone
                pass

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
