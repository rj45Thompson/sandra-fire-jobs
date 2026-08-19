"""
The conversation doing things, not just talking about them.

Muster is a front end to Claude running at home. If every answer ends in
"now go to the Jobs tab and press Find jobs", the app has failed at the
one thing it is for. So the assistant appends a small JSON instruction,
the engine carries it out, and Sandra reads plain English about what
happened.

Two rules run through all of it:

  - She never sees the JSON. It is machinery, and machinery on screen is
    a bug. (It was, once - the parser could not handle a nested object,
    so the action ran and the raw JSON was left on screen anyway.)
  - Nothing here submits an application. Filling stops before Send,
    always. The last word on anything reaching an employer is hers.
"""

import pytest

import server


def assistant_says(monkeypatch, text):
    """The assistant's reply, verbatim - including any action JSON."""
    server.ENV["CHAT_PROVIDER"] = "claude-cli"
    monkeypatch.setattr(server, "_chat_claude_cli", lambda *a, **k: text)


def say(engine, message="do the thing"):
    status, body = engine.post("/chat", {"message": message, "history": []})
    assert status == 200
    return body["reply"]


# ── the machinery never shows ─────────────────────────────────────────

def test_the_json_is_never_left_on_screen(engine, monkeypatch):
    assistant_says(monkeypatch,
                   'Noting that down.\n{"action": "remember", '
                   '"fields": {"city": "Onoway"}}')
    reply = say(engine)
    assert "{" not in reply and "action" not in reply
    assert "Noting that down." in reply


def test_a_nested_object_is_still_stripped(engine, monkeypatch):
    """
    The exact bug this was found by: a regex for a flat {...} matches the
    INNER object, so the action was missed and the JSON stayed visible.
    """
    assistant_says(monkeypatch,
                   'Got it.\n{"action": "remember", "fields": '
                   '{"work_auth": "Canadian citizen", "city": "Onoway"}}')
    reply = say(engine)
    assert "{" not in reply
    _, gaps = engine.get("/profile/gaps")
    have = {h["key"]: h["value"] for h in gaps["have"]}
    assert have["work_auth"] == "Canadian citizen"
    assert have["city"] == "Onoway"


def test_an_ordinary_answer_is_passed_through_untouched(engine, monkeypatch):
    assistant_says(monkeypatch, "Onoway is worth applying to first - they hire "
                                "paid-on-call and do not require live fire.")
    reply = say(engine, "where should I apply?")
    assert reply.startswith("Onoway is worth applying to first")


# ── remembering things about her ──────────────────────────────────────

def test_it_records_what_she_tells_it(engine, monkeypatch):
    assistant_says(monkeypatch,
                   'Saving that.\n{"action": "remember", "fields": '
                   '{"city": "Onoway", "province": "Alberta"}}')
    reply = say(engine)

    _, gaps = engine.get("/profile/gaps")
    have = {h["key"]: h["value"] for h in gaps["have"]}
    assert have["city"] == "Onoway"
    assert have["province"] == "Alberta"
    assert "city" in reply and "province" in reply


def test_a_field_name_it_invented_is_dropped_not_stored(engine, monkeypatch):
    """Only the fields an application actually asks for are real."""
    assistant_says(monkeypatch,
                   'Noted.\n{"action": "remember", "fields": '
                   '{"favourite_colour": "pink", "city": "Onoway"}}')
    say(engine)

    _, gaps = engine.get("/profile/gaps")
    have = {h["key"] for h in gaps["have"]}
    assert "city" in have
    assert "favourite_colour" not in have


def test_nothing_recognisable_says_so_rather_than_claiming_success(engine, monkeypatch):
    assistant_says(monkeypatch,
                   'Done.\n{"action": "remember", "fields": {"nonsense": "x"}}')
    reply = say(engine)
    assert "did not catch" in reply


# ── certifications ────────────────────────────────────────────────────

def test_a_certification_mentioned_in_chat_is_recorded(engine, monkeypatch):
    assistant_says(monkeypatch,
                   'Adding it.\n{"action": "cert", "name": "NFPA 1001 Level I", '
                   '"status": "Complete"}')
    reply = say(engine, "I finished my NFPA 1001 level one")

    _, certs = engine.get("/certs")
    assert len(certs) == 1
    assert certs[0]["name"] == "NFPA 1001 Level I"
    assert certs[0]["source"] == "chat"
    assert "NFPA 1001" in reply


def test_a_certification_from_chat_counts_toward_the_score(engine, monkeypatch):
    before = server.score_posting("Firefighter", "casual")
    assistant_says(monkeypatch,
                   'Adding it.\n{"action": "cert", "name": "NFPA 1001", '
                   '"status": "Complete"}')
    say(engine)
    assert server.score_posting("Firefighter", "casual") > before


def test_a_certification_with_no_name_is_refused(engine, monkeypatch):
    assistant_says(monkeypatch, 'Adding.\n{"action": "cert", "status": "Complete"}')
    reply = say(engine)
    assert "did not catch" in reply
    assert engine.get("/certs")[1] == []


# ── places to look ────────────────────────────────────────────────────

def test_it_adds_a_place_to_look(engine, monkeypatch):
    assistant_says(monkeypatch,
                   'Adding that.\n{"action": "watch", "places": [{"name": '
                   '"Job Bank - nursing, Alberta", "url": '
                   '"https://jobbank.gc.ca/x", "kind": "healthcare"}]}')
    reply = say(engine, "also look at job bank for nursing in alberta")

    _, srcs = engine.get("/sources")
    assert len(srcs) == 1
    assert srcs[0]["kind"] == "healthcare"
    assert "1 place" in reply


def test_a_place_with_no_real_address_is_not_added(engine, monkeypatch):
    assistant_says(monkeypatch,
                   'Adding.\n{"action": "watch", "places": '
                   '[{"name": "somewhere", "url": "ask around"}]}')
    reply = say(engine)
    assert engine.get("/sources")[1] == []
    assert "could not" in reply


# ── the schedule ──────────────────────────────────────────────────────

def test_it_can_set_the_automatic_search(engine, monkeypatch):
    assistant_says(monkeypatch, 'Setting that.\n{"action": "schedule", "hours": 12}')
    reply = say(engine, "check for jobs twice a day")

    _, prof = engine.get("/profile")
    assert prof["scan_every_hours"] == "12"
    assert "12 hours" in reply


def test_it_can_turn_the_automatic_search_off(engine, monkeypatch):
    assistant_says(monkeypatch, 'Turning it off.\n{"action": "schedule", "hours": 0}')
    reply = say(engine, "stop checking automatically")
    assert "off" in reply.lower()


# ── applying ──────────────────────────────────────────────────────────

def test_it_can_fill_an_application_and_says_it_did_not_send_it(engine, monkeypatch):
    import applier
    monkeypatch.setattr(applier, "fill_application",
                        lambda url, prof, res, **k: {
                            "ok": True, "filled": ["first_name = Sandra",
                                                   "email = s@example.com"],
                            "skipped": [], "notes": [], "left_open": True})
    assistant_says(monkeypatch,
                   'Opening it now.\n{"action": "apply", "url": '
                   '"https://onoway.example.ca/apply"}')
    reply = say(engine, "apply to the onoway one")

    assert "2 fields" in reply
    assert "press submit yourself" in reply
    assert "never send one on your behalf" in reply

    _, apps = engine.get("/applications")
    assert apps[0]["status"] == "review"


def test_it_refuses_to_invent_a_posting_address(engine, monkeypatch):
    """Guessing a URL would open some unrelated page and fill her details in."""
    import applier
    monkeypatch.setattr(applier, "fill_application", lambda *a, **k: pytest.fail(
        "must not open a browser without a real address"))
    assistant_says(monkeypatch,
                   'Opening it.\n{"action": "apply", "url": "the onoway posting"}')
    reply = say(engine)
    assert "do not have the real address" in reply
    assert engine.get("/applications")[1] == []


def test_a_login_wall_is_reported_back_in_the_conversation(engine, monkeypatch):
    import applier
    monkeypatch.setattr(applier, "fill_application",
                        lambda url, prof, res, **k: {
                            "ok": True, "filled": [], "skipped": [],
                            "notes": ["This page has a sign-in or bot check."],
                            "left_open": True})
    assistant_says(monkeypatch,
                   'Trying it.\n{"action": "apply", "url": '
                   '"https://www.linkedin.com/jobs/view/1"}')
    reply = say(engine, "apply to that linkedin one")
    assert "sign-in" in reply


def test_a_failed_application_is_reported_honestly(engine, monkeypatch):
    import applier
    monkeypatch.setattr(applier, "fill_application",
                        lambda *a, **k: {"ok": False, "error": "page is gone",
                                         "filled": [], "notes": []})
    assistant_says(monkeypatch,
                   'Trying.\n{"action": "apply", "url": "https://dead.example.ca/x"}')
    reply = say(engine)
    assert "did not work" in reply
    assert "page is gone" in reply


# ── scanning ──────────────────────────────────────────────────────────

def test_it_can_run_a_scan_from_the_conversation(engine, monkeypatch):
    monkeypatch.setattr(server.Handler, "_scan",
                        lambda self: {"ok": True, "checked": 9, "found": 2,
                                      "skipped": 0})
    assistant_says(monkeypatch, 'Looking now.\n{"action": "scan"}')
    reply = say(engine, "any new jobs?")
    assert "9 places" in reply
    assert "2 new" in reply


# ── failure of the machinery itself ───────────────────────────────────

def test_an_action_that_throws_does_not_lose_the_conversation(engine, monkeypatch):
    def boom(self):
        raise RuntimeError("scan exploded")
    monkeypatch.setattr(server.Handler, "_scan", boom)
    assistant_says(monkeypatch, 'Looking now.\n{"action": "scan"}')

    reply = say(engine)
    assert "failed" in reply.lower()
    assert "Nothing was changed" in reply
    assert engine.get("/health")[1]["ok"] is True


def test_an_unknown_action_is_ignored_rather_than_guessed_at(engine, monkeypatch):
    assistant_says(monkeypatch,
                   'Sure.\n{"action": "delete_everything", "confirm": true}')
    reply = say(engine)
    assert "{" not in reply
    assert engine.get("/health")[1]["ok"] is True


def test_the_older_upgrade_form_still_works(engine, monkeypatch):
    """An earlier protocol shape; breaking it would break saved habits."""
    calls = []
    monkeypatch.setattr(server.Handler, "_upgrade",
                        lambda self, req: calls.append(req) or
                        {"ok": True, "message": "Updated styles.css."})
    assistant_says(monkeypatch, 'On it.\n{"upgrade": "make the accent teal"}')

    reply = say(engine, "make the accent teal")
    assert calls == ["make the accent teal"]
    assert "Updated styles.css." in reply
    assert "{" not in reply
