"""
The connectors, and what happens when their credentials are wrong.

Every one of these talks to something outside the app - Gmail over IMAP,
the assistant over a subprocess or HTTP - and every one of them can fail
in a way that is not a crash: a rejected password, a service that is not
running, a reply that never arrives. Those are the interesting cases,
because a connector that fails loudly is fine and a connector that fails
quietly costs Sandra a job.

Nothing here touches the real Gmail or the real assistant. The fixture
refuses outright if a test tries.
"""

import imaplib
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import email_client
import server


# ── Gmail: the credential itself ──────────────────────────────────────

def test_a_missing_password_is_caught_before_dialling_gmail(monkeypatch):
    def never(*a, **k):
        pytest.fail("should not have opened a connection with no password")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", never)

    ok, msg = email_client.test_login("sandra@gmail.com", "")
    assert ok is False
    assert "app password" in msg.lower()


def test_a_missing_address_is_caught_too(monkeypatch):
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *a, **k: pytest.fail("no"))
    ok, msg = email_client.test_login("", "abcd efgh ijkl mnop")
    assert ok is False


def test_a_normal_password_instead_of_an_app_password_says_so(monkeypatch):
    """
    The single most likely mistake, and the error Gmail gives back is
    useless on its own. She must be told what kind of password she needs.
    """
    class Rejects:
        def __init__(self, host): pass
        def login(self, a, p):
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", Rejects)

    ok, msg = email_client.test_login("sandra@gmail.com", "hunter2")
    assert ok is False
    assert "16-character" in msg
    assert "App Password" in msg
    assert "2-Step" in msg


def test_gmail_unreachable_is_reported_as_a_network_problem(monkeypatch):
    class Down:
        def __init__(self, host):
            raise OSError("getaddrinfo failed")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", Down)

    ok, msg = email_client.test_login("sandra@gmail.com", "abcd efgh ijkl mnop")
    assert ok is False
    assert "Could not reach Gmail" in msg


def test_a_good_app_password_connects(monkeypatch):
    calls = {}

    class Works:
        def __init__(self, host): calls["host"] = host
        def login(self, a, p): calls["login"] = (a, p)
        def select(self, box): calls["select"] = box
        def logout(self): calls["logout"] = True
    monkeypatch.setattr(imaplib, "IMAP4_SSL", Works)

    ok, msg = email_client.test_login("sandra@gmail.com", "abcd efgh ijkl mnop")
    assert ok is True
    # the spaces Google shows in the app password must be stripped for login
    assert calls["login"] == ("sandra@gmail.com", "abcdefghijklmnop")
    assert calls["logout"] is True


# ── Gmail: connecting through the app ─────────────────────────────────

def test_a_rejected_password_is_never_saved(engine, monkeypatch, tmp_path):
    """A bad credential must not be written to .env and then look connected."""
    monkeypatch.setattr(email_client, "test_login",
                        lambda a, p: (False, "Gmail rejected that."))
    env_file = tmp_path / ".env"
    env_file.write_text("API_PORT=8770\n", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)

    status, body = engine.post("/email/connect",
                               {"address": "sandra@gmail.com", "app_password": "wrong"})
    assert body["ok"] is False
    assert "GMAIL_APP_PASSWORD" not in env_file.read_text(encoding="utf-8")

    _, st = engine.get("/email/status")
    assert not st.get("connected")


def test_a_good_password_is_saved_and_shows_as_connected(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(email_client, "test_login", lambda a, p: (True, "Connected to Gmail."))
    env_file = tmp_path / ".env"
    env_file.write_text("API_PORT=8770\n", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)

    status, body = engine.post("/email/connect",
                               {"address": "sandra@gmail.com",
                                "app_password": "abcd efgh ijkl mnop"})
    assert body["ok"] is True

    written = env_file.read_text(encoding="utf-8")
    assert "GMAIL_ADDRESS=sandra@gmail.com" in written
    assert "GMAIL_APP_PASSWORD=abcdefghijklmnop" in written   # spaces stripped

    _, st = engine.get("/email/status")
    assert st["connected"] is True
    assert st["address"] == "sandra@gmail.com"


def test_reconnecting_replaces_the_old_credential_rather_than_stacking(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(email_client, "test_login", lambda a, p: (True, "ok"))
    env_file = tmp_path / ".env"
    env_file.write_text("GMAIL_ADDRESS=old@gmail.com\nGMAIL_APP_PASSWORD=oldpass\n",
                        encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)

    engine.post("/email/connect", {"address": "new@gmail.com", "app_password": "newpass"})

    written = env_file.read_text(encoding="utf-8")
    assert written.count("GMAIL_ADDRESS=") == 1
    assert "new@gmail.com" in written
    assert "oldpass" not in written


def test_the_status_endpoint_never_leaks_the_password(engine, monkeypatch):
    server.ENV["GMAIL_ADDRESS"] = "sandra@gmail.com"
    server.ENV["GMAIL_APP_PASSWORD"] = "supersecretpassword"

    status, body = engine.get("/email/status")
    assert status == 200
    assert "supersecretpassword" not in json.dumps(body)


# ── Gmail: reading the inbox ──────────────────────────────────────────

def test_the_inbox_says_so_plainly_when_email_is_not_connected():
    out = email_client.scan_inbox("", "")
    assert out["ok"] is False
    assert "not connected" in out["error"].lower()
    assert out["messages"] == []


def test_an_imap_failure_mid_scan_is_returned_not_raised(monkeypatch):
    class Blows:
        def __init__(self, host): pass
        def login(self, a, p): raise imaplib.IMAP4.error("server said no")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", Blows)

    out = email_client.scan_inbox("sandra@gmail.com", "abcdefghijklmnop")
    assert out["ok"] is False
    assert out["messages"] == []


@pytest.mark.parametrize("subject,expected", [
    ("We would like to schedule a call about your application", "interview"),
    ("Invitation to interview - Firefighter", "interview"),
    ("Unfortunately we are moving forward with other candidates", "rejection"),
    ("The position has been filled", "rejection"),
    ("We have received your application", "acknowledgement"),
    ("Thank you for applying to Onoway Fire", "acknowledgement"),
    ("A question about your availability next week", "interview"),
    ("Some unrelated note", "reply"),
])
def test_replies_are_sorted_into_the_right_kind(subject, expected):
    assert email_client._classify(subject, "") == expected


def test_an_interview_invitation_is_not_mistaken_for_a_rejection():
    """The costly confusion: these two must never cross over."""
    assert email_client._classify(
        "Interview invitation", "We would like to meet with you") == "interview"
    assert email_client._classify(
        "Your application", "Unfortunately, we have decided not to proceed") == "rejection"


# ── the assistant ─────────────────────────────────────────────────────

def test_the_assistant_being_unreachable_is_explained_not_swallowed(engine, monkeypatch):
    server.ENV["CHAT_PROVIDER"] = "claude-cli"
    monkeypatch.setattr(server, "_chat_claude_cli",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("claude not on PATH")))

    status, body = engine.post("/chat", {"message": "hello", "history": []})
    assert status == 200
    reply = body["reply"]
    assert "could not reach" in reply.lower()
    assert "claude not on PATH" in reply


def test_an_ollama_that_is_not_running_tells_her_how_to_start_it(engine, monkeypatch):
    server.ENV["CHAT_PROVIDER"] = "ollama"
    monkeypatch.setattr(server, "_chat_ollama",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.URLError("connection refused")))

    status, body = engine.post("/chat", {"message": "hello", "history": []})
    assert "ollama serve" in body["reply"]


def test_the_configured_provider_is_the_one_used(engine, monkeypatch):
    used = []
    server.ENV["CHAT_PROVIDER"] = "claude-cli"
    monkeypatch.setattr(server, "_chat_claude_cli",
                        lambda s, h, m: used.append("cli") or "from the cli")
    monkeypatch.setattr(server, "_chat_ollama",
                        lambda s, h, m: used.append("ollama") or "from ollama")

    _, body = engine.post("/chat", {"message": "hi", "history": []})
    assert used == ["cli"]
    assert body["reply"] == "from the cli"


def test_an_unknown_provider_falls_back_rather_than_breaking(engine, monkeypatch):
    server.ENV["CHAT_PROVIDER"] = "something-nobody-installed"
    monkeypatch.setattr(server, "_chat_ollama", lambda s, h, m: "fallback answered")

    _, body = engine.post("/chat", {"message": "hi", "history": []})
    assert body["reply"] == "fallback answered"


def test_the_conversation_is_kept_so_it_survives_a_reload(engine, monkeypatch):
    monkeypatch.setattr(server, "_chat_claude_cli", lambda s, h, m: "noted")
    server.ENV["CHAT_PROVIDER"] = "claude-cli"

    engine.post("/chat", {"message": "what should I do first?", "history": []})

    rows = server.db().execute("SELECT role, text FROM chat ORDER BY id").fetchall()
    kinds = [r["role"] for r in rows]
    assert "user" in kinds and "assistant" in kinds


def test_the_assistant_is_told_who_she_is(engine, monkeypatch):
    """Her profile and certificates have to reach the prompt, or advice is generic."""
    engine.post("/profile", {"first_name": "Sandra", "city": "Onoway"})
    engine.post("/certs", {"name": "NFPA 1001 Level I", "status": "Complete"})

    seen = {}
    monkeypatch.setattr(server, "_chat_claude_cli",
                        lambda s, h, m: seen.setdefault("system", s) and "" or "ok")
    server.ENV["CHAT_PROVIDER"] = "claude-cli"

    engine.post("/chat", {"message": "what am I missing?", "history": []})

    assert "Onoway" in seen["system"]
    assert "NFPA 1001" in seen["system"]


def test_connecting_with_no_password_is_refused_by_the_real_guard(engine, tmp_path, monkeypatch):
    """
    Found by a test of mine that mocked test_login into always succeeding and
    so let an EMPTY password be written to .env. The mock was wrong, not the
    app - but the case is worth pinning down with the real check in place.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("API_PORT=8770", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)

    status, body = engine.post("/email/connect",
                               {"address": "sandra@gmail.com", "app_password": ""})
    assert body["ok"] is False
    assert "GMAIL_APP_PASSWORD" not in env_file.read_text(encoding="utf-8")


def test_a_caller_using_the_wrong_field_name_cannot_save_a_blank_password(engine, tmp_path, monkeypatch):
    """The front-end sends app_password; anything else must not half-connect."""
    env_file = tmp_path / ".env"
    env_file.write_text("API_PORT=8770", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)

    status, body = engine.post("/email/connect",
                               {"address": "sandra@gmail.com", "password": "abcdefghijklmnop"})
    assert body["ok"] is False
    assert "GMAIL_APP_PASSWORD=abcdefghijklmnop" not in env_file.read_text(encoding="utf-8")
