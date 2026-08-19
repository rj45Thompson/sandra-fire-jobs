"""
Shared fixtures.

Every test runs against a throwaway SQLite file, never Sandra's real
data/muster.db. server.py resolves DB_PATH once at import and caches one
connection per thread on a threading.local, so both have to be redirected
together or the tests quietly read the real database instead.
"""

import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import server  # noqa: E402


def make_docx(path: Path, body: str) -> Path:
    """A real .docx - a zip with word/document.xml - not a text file renamed."""
    paras = "".join(
        f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in body.strip().split("\n")
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paras}</w:body></w:document>"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("word/document.xml", doc)
    return path


RESUME_BODY = """Sandra Thompson
Onoway, Alberta  T0E 1V0
sandra.test@example.com
780-555-0142

Paid-on-call firefighter with Lac Ste. Anne County.
NFPA 1001 Level I complete. Standard First Aid and CPR-C current.
Class 5 driver's licence.
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point server.py at an empty database for the duration of one test."""
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "DATA", tmp_path)
    if hasattr(server._local, "conn"):
        del server._local.conn
    conn = server.db()
    yield conn
    conn.close()
    if hasattr(server._local, "conn"):
        del server._local.conn


@pytest.fixture
def resume(tmp_path):
    return make_docx(tmp_path / "resume.docx", RESUME_BODY)


class Client:
    """Tiny HTTP client for talking to a test engine."""

    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def _do(self, path, data=None, method=None, cookie=None, accept=None):
        import urllib.error
        import urllib.request

        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = f"muster_device={cookie}"
        if accept:
            headers["Accept"] = accept
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
            method=method or ("POST" if data is not None else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def get(self, path, **kw):
        s, b, _ = self._do(path, **kw)
        return s, _json(b)

    def post(self, path, payload=None, **kw):
        s, b, _ = self._do(path, data=payload if payload is not None else {}, **kw)
        return s, _json(b)

    def raw(self, path, **kw):
        return self._do(path, **kw)


def _json(b):
    try:
        return json.loads(b.decode())
    except (ValueError, UnicodeDecodeError):
        return b


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """
    A real engine on an ephemeral port, wired to throwaway everything.

    Every outside dependency is cut here rather than per-test: no test may
    reach Gmail, spawn the assistant, drive a browser or touch the real
    database. Anything a test wants to exercise, it opts back in to
    explicitly by patching that one seam.
    """
    import threading
    from http.server import ThreadingHTTPServer

    monkeypatch.setattr(server, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "DATA", tmp_path)
    monkeypatch.setattr(server, "RESUME_DIR", tmp_path / "resumes")
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path / "documents")
    monkeypatch.setattr(server, "TOKEN", "")
    monkeypatch.setattr(server, "ACCESS_PIN", "")
    monkeypatch.setattr(server, "_PIN_TRIES", {})
    monkeypatch.setattr(server, "ENV", dict(server.ENV))
    server.RESUME_DIR.mkdir(parents=True, exist_ok=True)
    server.DOCS_DIR.mkdir(parents=True, exist_ok=True)

    def no_assistant(*a, **k):
        raise AssertionError(
            "a test reached the real assistant - patch _chat_claude_cli in the test")
    monkeypatch.setattr(server, "_chat_claude_cli", no_assistant)

    if hasattr(server._local, "conn"):
        del server._local.conn

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    yield Client(httpd.server_address[1])

    httpd.shutdown()
    httpd.server_close()
    if hasattr(server._local, "conn"):
        del server._local.conn
