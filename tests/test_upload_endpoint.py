"""
The real HTTP endpoints the merged Profile & documents section calls.

Runs an actual server on an ephemeral port against a throwaway database,
so these exercise routing, JSON encoding and status codes rather than
calling the handler methods directly. The chat provider is mocked: no test
should ever spawn the Claude CLI or reach the network.
"""

import base64
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from conftest import RESUME_BODY, make_docx

import server


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A live engine on a random free port, isolated from the real one."""
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "DATA", tmp_path)
    monkeypatch.setattr(server, "RESUME_DIR", tmp_path / "resumes")
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path / "documents")
    monkeypatch.setattr(server, "TOKEN", "")
    server.RESUME_DIR.mkdir(parents=True, exist_ok=True)
    server.DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # never invoke the real assistant from a test
    monkeypatch.setattr(server, "_chat_claude_cli",
                        lambda *a, **k: "mocked reply")

    if hasattr(server._local, "conn"):
        del server._local.conn

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    class Client:
        base = f"http://127.0.0.1:{port}"

        def get(self, path):
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                return r.status, json.loads(r.read().decode())

        def post(self, path, payload):
            req = urllib.request.Request(
                self.base + path,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST")
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode())

    yield Client()
    httpd.shutdown()
    httpd.server_close()
    if hasattr(server._local, "conn"):
        del server._local.conn


def b64_docx(tmp_path, body=RESUME_BODY):
    path = make_docx(tmp_path / "upload.docx", body)
    return base64.b64encode(path.read_bytes()).decode()


def test_health_is_reachable(api):
    status, body = api.get("/health")
    assert status == 200
    assert body["ok"] is True


def test_gaps_start_empty_and_report_the_full_total(api):
    status, body = api.get("/profile/gaps")
    assert status == 200
    assert body["complete"] == 0
    assert body["total"] == len(server.REQUIRED_FIELDS)
    assert len(body["gaps"]) == body["total"]


def test_uploading_a_resume_fills_the_needs_list(api, tmp_path):
    """The whole point of the merged section, end to end over HTTP."""
    status, body = api.post("/upload", {
        "kind": "resumes",
        "filename": "resume.docx",
        "content_b64": b64_docx(tmp_path),
    })
    assert status == 200
    assert body["ok"] is True
    assert body["lifted_fields"] > 0

    status, gaps = api.get("/profile/gaps")
    assert gaps["complete"] == body["lifted_fields"]
    assert len(gaps["gaps"]) == gaps["total"] - gaps["complete"]

    have = {h["key"]: h["value"] for h in gaps["have"]}
    assert have["first_name"] == "Sandra"
    assert have["city"] == "Onoway"


def test_uploading_a_resume_also_records_certifications(api, tmp_path):
    status, body = api.post("/upload", {
        "kind": "resumes",
        "filename": "resume.docx",
        "content_b64": b64_docx(tmp_path),
    })
    assert body["lifted_certs"] > 0

    status, certs = api.get("/certs")
    assert status == 200
    assert len(certs) == body["lifted_certs"]
    assert all(c["source"] == "resume" for c in certs)


def test_a_resume_that_cannot_be_parsed_still_uploads(api):
    """
    The regression that took the server down: the file is written and the
    row committed before parsing, so a parse failure used to break the
    response after the upload had already half-happened. It must now come
    back as a clean success with nothing lifted.
    """
    status, body = api.post("/upload", {
        "kind": "resumes",
        "filename": "broken.docx",
        "content_b64": base64.b64encode(b"not a zip at all").decode(),
    })
    assert status == 200
    assert body["ok"] is True
    assert body["lifted_certs"] == 0
    assert body["lifted_fields"] == 0

    # and the engine is still answering afterwards
    assert api.get("/health")[1]["ok"] is True


def test_supporting_documents_do_not_touch_the_profile(api, tmp_path):
    """Only a résumé fills needs - a random attachment must not."""
    api.post("/upload", {
        "kind": "documents",
        "filename": "cert.docx",
        "content_b64": b64_docx(tmp_path),
    })
    _, gaps = api.get("/profile/gaps")
    assert gaps["complete"] == 0


def test_bad_base64_is_rejected_without_killing_the_server(api):
    status, body = api.post("/upload", {
        "kind": "resumes",
        "filename": "x.docx",
        "content_b64": "!!!! not base64 !!!!",
    })
    assert status == 400
    assert "error" in body
    assert api.get("/health")[1]["ok"] is True


def test_profile_chat_records_what_she_says(api, monkeypatch):
    """
    The other half of filling needs: anything the résumé missed gets
    answered in chat. The model is mocked, so this tests our parsing and
    persistence of its reply, not the model.
    """
    monkeypatch.setattr(
        server, "_chat_claude_cli",
        lambda *a, **k: 'Recorded that for you.\n{"set": {"city": "Onoway", '
                        '"province": "Alberta"}}')

    status, body = api.post("/profile/chat", {"message": "I live in Onoway, Alberta"})
    assert status == 200

    _, gaps = api.get("/profile/gaps")
    have = {h["key"]: h["value"] for h in gaps["have"]}
    assert have["city"] == "Onoway"
    assert have["province"] == "Alberta"


def test_profile_chat_ignores_keys_that_are_not_real_fields(api, monkeypatch):
    """A reply naming a field we do not track must not create one."""
    monkeypatch.setattr(
        server, "_chat_claude_cli",
        lambda *a, **k: 'Done.\n{"set": {"favourite_colour": "pink", "city": "Onoway"}}')

    api.post("/profile/chat", {"message": "my favourite colour is pink"})

    _, gaps = api.get("/profile/gaps")
    have = {h["key"] for h in gaps["have"]}
    assert "city" in have
    assert "favourite_colour" not in have
