"""
The chat page, served by this machine.

This is the route that cannot break, and it exists because every other one
can. A page on github.io reaching http://127.0.0.1 has to clear mixed-content
rules, a CORS preflight, an ad blocker, and - since Chrome 138 - a
local-network permission that the browser waits for silently, holding the
request with no error while the chat reports itself offline and the engine
sits here running.

Served from this engine there is nothing to clear. It is the same origin as
/bridge/chat, so the browser asks no questions, the access code is not needed,
and there is no published address to go stale.

What is pinned here is only that: the file is reachable, it arrives as HTML,
and serving it did not open a way to read the rest of the disk.
"""

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import server


PAGE = "<!doctype html><title>Muster</title><div id=shell>chat</div>"


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """An engine serving a web directory this test owns."""
    web = tmp_path / "docs"
    web.mkdir()
    (web / "muster.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not yours", encoding="utf-8")

    monkeypatch.setattr(server, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "DATA", tmp_path)
    monkeypatch.setattr(server, "WEB_DIR", web)
    monkeypatch.setattr(server, "TOKEN", "")
    monkeypatch.setattr(server, "ACCESS_PIN", "")
    if hasattr(server._local, "conn"):
        del server._local.conn

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def get(path):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    yield get
    httpd.shutdown()
    httpd.server_close()
    if hasattr(server._local, "conn"):
        del server._local.conn


def test_the_chat_page_is_served_from_this_machine(desk):
    status, body, headers = desk("/muster.html")
    assert status == 200
    assert b"Muster" in body
    assert "text/html" in headers["Content-Type"]


def test_it_is_the_same_origin_as_the_bridge(desk):
    """
    The whole point. If the page and /bridge/chat are one origin the browser
    has nothing to ask about - so /health has to answer on this origin too,
    because that is what the page probes to find the desk.
    """
    assert desk("/muster.html")[0] == 200
    assert desk("/health")[0] == 200


@pytest.mark.parametrize("path", [
    "/../secret.txt",
    "/..%2fsecret.txt",
    "/docs/../../secret.txt",
])
def test_serving_a_page_did_not_open_the_disk(desk, path):
    status, body, _ = desk(path)
    assert status != 200 or b"not yours" not in body


def test_a_page_that_is_not_there_is_not_a_crash(desk):
    status, _, _ = desk("/nothing-here.html")
    assert status == 404
