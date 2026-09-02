"""
The one endpoint that faces the internet.

Everything else in this engine is behind the home-network rule. /bridge/chat
is not: it is reachable through a tunnel, from a page anyone might open, and
it spends a Claude subscription every time it answers. So it decides for
itself who it will talk to, and those decisions are the whole of this file.

Two rules, and they pull in opposite directions:

  A stranger must not get through. Not without the access code, and not from
  an origin the owner never named.

  The owner must not be locked out of their own machine. That is not
  hypothetical - it is what happened. The page opened on the machine running
  the desk has no code to send, and an origin list written before the page
  moved refused it outright, so the one person the code was never protecting
  against was the only person it stopped.

The seam is the source address. Loopback is the owner's own browser; anything
else came in over the tunnel and is checked in full.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import server


ORIGIN = "https://rj45thompson.github.io"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    """A running engine with a known code, and a way to knock on it."""
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "DATA", tmp_path)
    monkeypatch.setitem(server.ENV, "BRIDGE_CODE", "sesame-42")
    monkeypatch.setitem(server.ENV, "BRIDGE_ORIGINS", ORIGIN)
    if hasattr(server._local, "conn"):
        del server._local.conn

    # The model is not what is under test here; the gate in front of it is.
    monkeypatch.setattr(server, "_chat_claude_cli",
                        lambda system, history, message: "answered")
    monkeypatch.setitem(server.ENV, "CHAT_PROVIDER", "claude-cli")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def ask(payload, origin=ORIGIN):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        r = urllib.request.Request(f"http://127.0.0.1:{port}/bridge/chat",
                                   data=json.dumps(payload).encode(),
                                   headers=headers, method="POST")
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    yield ask
    httpd.shutdown()
    httpd.server_close()
    if hasattr(server._local, "conn"):
        del server._local.conn


def from_the_tunnel(monkeypatch):
    """Make the next request look like it arrived from the LAN, not this box."""
    monkeypatch.setattr(server.Handler, "_client_ip", lambda self: "192.168.1.50")


MSG = [{"role": "user", "content": "hello"}]


# ── a stranger coming in over the tunnel ───────────────────────────────

def test_no_code_is_refused(bridge, monkeypatch):
    from_the_tunnel(monkeypatch)
    status, body = bridge({"messages": MSG})
    assert status == 403
    assert "access code" in body["error"]["message"].lower()


def test_a_wrong_code_is_refused(bridge, monkeypatch):
    from_the_tunnel(monkeypatch)
    status, body = bridge({"code": "hunter2", "messages": MSG})
    assert status == 403
    assert "access code" in body["error"]["message"].lower()


def test_an_unnamed_origin_is_refused_even_with_the_right_code(bridge, monkeypatch):
    from_the_tunnel(monkeypatch)
    status, body = bridge({"code": "sesame-42", "messages": MSG},
                          origin="https://somewhere-else.example")
    assert status == 403
    # The refusal names the origin and where to add it. A refusal that does
    # not say what to change is a support ticket waiting to happen.
    assert "somewhere-else.example" in body["error"]["message"]
    assert "BRIDGE_ORIGINS" in body["error"]["message"]


def test_the_right_code_from_a_named_origin_is_answered(bridge, monkeypatch):
    from_the_tunnel(monkeypatch)
    status, body = bridge({"code": "sesame-42", "messages": MSG})
    assert status == 200
    assert body["text"] == "answered"


# ── the owner, on the machine the desk is running on ───────────────────

def test_this_machine_needs_no_code(bridge):
    status, body = bridge({"messages": MSG})
    assert status == 200
    assert body["text"] == "answered"


def test_this_machine_is_not_judged_on_its_origin(bridge):
    status, body = bridge({"messages": MSG}, origin="https://anything.example")
    assert status == 200


def test_this_machine_is_answered_even_with_a_wrong_code(bridge):
    """A stale code left in a browser must not lock the owner out."""
    status, body = bridge({"code": "long-since-changed", "messages": MSG})
    assert status == 200


# ── shape checks apply to everyone ─────────────────────────────────────

def test_an_empty_conversation_is_a_bad_request_not_a_refusal(bridge):
    """400, not 403. Nothing was refused - there was nothing to answer."""
    status, body = bridge({"messages": []})
    assert status == 400
    assert "no messages" in body["error"]["message"].lower()


def test_the_body_is_read_once_and_still_has_the_messages(bridge, monkeypatch):
    """
    The gate reads the request body to check the code. _body() consumes the
    socket, so a second read returns nothing - and the messages would vanish
    between the check and the answer, every time, for tunnel traffic only.
    """
    from_the_tunnel(monkeypatch)
    status, body = bridge({"code": "sesame-42", "messages": MSG})
    assert status == 200, body
    assert body["text"] == "answered"
