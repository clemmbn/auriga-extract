"""
Tests for the hand-rolled WebSocket client in cdp.py.

This is the riskiest code in the project: a framing bug would show up as a
mysterious hang or a truncated message during login, far from its cause. The
tests drive a real socketpair rather than a mock, so the actual byte-level
parsing is what gets exercised.

Frames are written from the "server" side, which per RFC 6455 means UNMASKED --
only clients mask. The masking path is covered separately by test_send_masks.
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from auriga_extract.cdp import WebSocket

# Opcodes used below.
TEXT, CLOSE, PING = 0x1, 0x8, 0x9
CONTINUATION = 0x0


def server_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """
    Build one unmasked server->client frame.

    opcode: WebSocket opcode.
    payload: raw frame payload.
    fin: whether this frame completes the message.
    Returns the encoded frame.
    """
    header = bytearray([(0x80 if fin else 0x00) | opcode])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 1 << 16:
        header.append(126)
        header += struct.pack("!H", n)
    else:
        header.append(127)
        header += struct.pack("!Q", n)
    return bytes(header) + payload


@pytest.fixture
def ws_pair():
    """
    Yield (WebSocket, server_socket) sharing a socketpair.

    The client is built without __init__ so no handshake is attempted; only the
    two attributes the frame reader touches are set.
    """
    client_sock, server_sock = socket.socketpair()
    client_sock.settimeout(5)
    server_sock.settimeout(5)

    ws = WebSocket.__new__(WebSocket)
    ws.sock = client_sock
    ws._rest = b""

    yield ws, server_sock

    client_sock.close()
    server_sock.close()


def send_from_server(server_sock: socket.socket, data: bytes) -> threading.Thread:
    """
    Write to the server end on a thread.

    Needed because a socketpair's buffer is only a few KB: a large payload
    written inline would block before the client ever started reading.
    Returns the started thread so the caller can join it.
    """
    thread = threading.Thread(target=server_sock.sendall, args=(data,), daemon=True)
    thread.start()
    return thread


def test_small_text_message(ws_pair):
    ws, server = ws_pair
    send_from_server(server, server_frame(TEXT, b'{"id":1}'))
    assert ws.recv() == '{"id":1}'


def test_medium_payload_uses_two_byte_length(ws_pair):
    """A 200-byte payload crosses into the 126 escape form."""
    ws, server = ws_pair
    payload = b"x" * 200
    send_from_server(server, server_frame(TEXT, payload))
    assert ws.recv() == payload.decode()


def test_large_payload_uses_eight_byte_length(ws_pair):
    """A 70000-byte payload crosses into the 127 escape form."""
    ws, server = ws_pair
    payload = b"y" * 70_000
    thread = send_from_server(server, server_frame(TEXT, payload))
    assert ws.recv() == payload.decode()
    thread.join(timeout=5)


def test_fragmented_message_is_reassembled(ws_pair):
    ws, server = ws_pair
    data = server_frame(TEXT, b"hel", fin=False) + server_frame(CONTINUATION, b"lo", fin=True)
    send_from_server(server, data)
    assert ws.recv() == "hello"


def test_ping_is_answered_and_hidden_from_caller(ws_pair):
    """A ping must produce a pong and must not surface as a message."""
    ws, server = ws_pair
    send_from_server(server, server_frame(PING, b"") + server_frame(TEXT, b"after-ping"))

    assert ws.recv() == "after-ping"

    # The pong: FIN + opcode 0xA, masked (client frames always are), no payload.
    pong = server.recv(16)
    assert pong[0] == 0x8A
    assert pong[1] & 0x80, "client frames must set the mask bit"
    assert pong[1] & 0x7F == 0


def test_close_frame_raises(ws_pair):
    ws, server = ws_pair
    send_from_server(server, server_frame(CLOSE, b""))
    with pytest.raises(ConnectionError):
        ws.recv()


def test_peer_hangup_raises(ws_pair):
    ws, server = ws_pair
    server.close()
    with pytest.raises(ConnectionError):
        ws.recv()


def test_send_masks_and_round_trips(ws_pair):
    """What send() writes must be a well-formed masked frame the server can decode."""
    ws, server = ws_pair
    ws.send("hello server")

    header = server.recv(2)
    assert header[0] == 0x81, "FIN + text opcode"
    assert header[1] & 0x80, "client frames must be masked"
    length = header[1] & 0x7F
    assert length == len("hello server")

    mask = server.recv(4)
    masked = server.recv(length)
    unmasked = bytes(c ^ mask[i % 4] for i, c in enumerate(masked))
    assert unmasked.decode() == "hello server"


def test_leftover_bytes_are_not_lost(ws_pair):
    """
    Two frames arriving in one TCP segment must both be readable.

    This is the bug that leftover buffering exists to prevent: reading frame one
    pulls frame two's bytes off the socket too, and dropping them would silently
    lose messages.
    """
    ws, server = ws_pair
    send_from_server(server, server_frame(TEXT, b"first") + server_frame(TEXT, b"second"))
    assert ws.recv() == "first"
    assert ws.recv() == "second"
