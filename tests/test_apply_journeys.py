"""
Sandra applying for work - the whole journey, one file per way in.

These are use cases, not unit tests: each one walks a route she would
really take, from a posting appearing to a row she can act on. What ties
them together is a single rule that matters more than any of them
individually - EVERY attempt lands in the applications list with an
honest status, whether it worked, needed her, or fell over. An attempt
that silently vanishes is the worst outcome, because she has no way to
know it happened.

The browser is mocked at applier.fill_application. That seam is chosen
deliberately: everything the engine does with the result - the status it
picks, the reason it records, what it tells her - is real code under
test. Only Chromium itself is fake.
"""

import pytest

import server


def apply_returns(monkeypatch, report=None, raises=None):
    """Stand in for the real browser, and capture what it was asked to do."""
    import applier
    seen = {}

    def fake(url, profile, resume_path, channel="msedge", headless=False):
        seen.update(url=url, profile=profile, resume_path=resume_path,
                    channel=channel, headless=headless)
        if raises:
            raise raises
        return report

    monkeypatch.setattr(applier, "fill_application", fake)
    return seen


def apps(engine):
    status, rows = engine.get("/applications")
    assert status == 200
    return rows


# ── 1. the ordinary case: a real form, filled, ready to review ────────

def test_a_form_she_can_review_is_filled_and_queued(engine, monkeypatch):
    seen = apply_returns(monkeypatch, {
        "ok": True,
        "filled": ["first_name = Sandra", "email = sandra@example.com",
                   "phone = 780-555-0142", "résumé attached"],
        "skipped": [], "notes": [], "left_open": True,
    })

    status, rep = engine.post("/apply/start",
                              {"url": "https://onoway.example.ca/careers/firefighter"})
    assert status == 200
    assert rep["ok"] is True
    assert seen["url"] == "https://onoway.example.ca/careers/firefighter"

    row = apps(engine)[0]
    assert row["status"] == "review"
    assert "4 fields" in row["notes"]


def test_the_application_carries_her_real_profile_and_resume(engine, monkeypatch):
    """What the browser is handed has to be her actual details, not blanks."""
    engine.post("/profile", {"first_name": "Sandra", "last_name": "Thompson",
                             "email": "sandra@example.com", "city": "Onoway"})
    engine.post("/upload", {"kind": "resumes", "filename": "cv.txt",
                            "content_b64": "U2FuZHJh"})

    seen = apply_returns(monkeypatch, {"ok": True, "filled": [], "notes": []})
    engine.post("/apply/start", {"url": "https://example.ca/job"})

    assert seen["profile"]["first_name"] == "Sandra"
    assert seen["profile"]["city"] == "Onoway"
    assert seen["resume_path"] and seen["resume_path"].endswith("cv.txt")


def test_the_newest_resume_is_the_one_sent(engine, monkeypatch):
    engine.post("/upload", {"kind": "resumes", "filename": "old.txt",
                            "content_b64": "b2xk"})
    engine.post("/upload", {"kind": "resumes", "filename": "new.txt",
                            "content_b64": "bmV3"})

    seen = apply_returns(monkeypatch, {"ok": True, "filled": [], "notes": []})
    engine.post("/apply/start", {"url": "https://example.ca/job"})
    assert seen["resume_path"].endswith("new.txt")


# ── 2. LinkedIn and friends: a login wall, so it needs her ────────────

def test_linkedin_style_login_wall_is_flagged_for_her_not_swallowed(engine, monkeypatch):
    apply_returns(monkeypatch, {
        "ok": True, "filled": [], "skipped": [],
        "notes": ["This page has a sign-in or bot check, so it needs you "
                  "to finish it in person. The browser is open for you."],
        "left_open": True,
    })

    engine.post("/apply/start",
                {"url": "https://www.linkedin.com/jobs/view/1234567890"})

    row = apps(engine)[0]
    assert row["status"] == "needs_you"
    assert "sign-in" in row["notes"]
    assert "linkedin.com" in row["notes"]


def test_a_consent_box_she_must_tick_herself_is_surfaced(engine, monkeypatch):
    """
    Muster never ticks "I certify this is accurate" - only she can say that.
    But it must not leave her to discover it when Submit silently fails.
    """
    apply_returns(monkeypatch, {
        "ok": True, "filled": ["first_name = Sandra"], "skipped": [],
        "notes": ["Unchecked boxes on this form that may need your attention: "
                  "I certify this information is accurate"],
        "left_open": True,
    })

    engine.post("/apply/start", {"url": "https://county.example.ca/apply"})

    row = apps(engine)[0]
    assert row["status"] == "needs_you"
    assert "certify" in row["notes"]


# ── 3. it went wrong: still her decision, still visible ───────────────

def test_a_dead_posting_is_recorded_as_failed_with_the_reason(engine, monkeypatch):
    apply_returns(monkeypatch, {
        "ok": False, "error": "Could not open the page: net::ERR_NAME_NOT_RESOLVED",
        "filled": [], "skipped": [],
    })

    engine.post("/apply/start", {"url": "https://gone.example.ca/job"})

    row = apps(engine)[0]
    assert row["status"] == "failed"
    assert "ERR_NAME_NOT_RESOLVED" in row["notes"]


def test_the_browser_failing_to_launch_is_reported_not_crashed(engine, monkeypatch):
    """Playwright missing, Edge missing - a setup problem, not a lost application."""
    apply_returns(monkeypatch, raises=RuntimeError("Executable doesn't exist"))

    status, rep = engine.post("/apply/start", {"url": "https://example.ca/job"})
    assert status == 200
    assert rep["ok"] is False
    assert "Could not run the browser" in rep["error"]

    row = apps(engine)[0]
    assert row["status"] == "failed"
    assert "Executable" in row["notes"]


def test_every_outcome_reaches_the_list(engine, monkeypatch):
    """The invariant: three attempts, three visible rows, no silent losses."""
    apply_returns(monkeypatch, {"ok": True, "filled": ["a"], "notes": []})
    engine.post("/apply/start", {"url": "https://a.example.ca/1"})

    apply_returns(monkeypatch, {"ok": True, "filled": [], "notes": ["needs a sign-in"]})
    engine.post("/apply/start", {"url": "https://b.example.ca/2"})

    apply_returns(monkeypatch, {"ok": False, "error": "boom", "filled": []})
    engine.post("/apply/start", {"url": "https://c.example.ca/3"})

    rows = apps(engine)
    assert len(rows) == 3
    assert {r["status"] for r in rows} == {"review", "needs_you", "failed"}
    assert all(r["notes"] for r in rows), "every row must carry a reason"


def test_a_rubbish_address_is_refused_before_a_browser_opens(engine, monkeypatch):
    import applier
    monkeypatch.setattr(applier, "fill_application", lambda *a, **k: pytest.fail(
        "should never have opened a browser for a non-URL"))

    status, rep = engine.post("/apply/start", {"url": "ask at the fire hall"})
    assert status == 200
    assert "error" in rep
    assert apps(engine) == []


# ── 4. applying twice to the same posting ─────────────────────────────

def test_re_applying_to_the_same_posting_does_not_duplicate_the_posting(engine, monkeypatch):
    apply_returns(monkeypatch, {"ok": True, "filled": [], "notes": []})
    url = "https://example.ca/same-job"
    engine.post("/apply/start", {"url": url})
    engine.post("/apply/start", {"url": url})

    _, postings = engine.get("/postings")
    matching = [p for p in postings if p["url"] == url]
    assert len(matching) <= 1

    # both attempts are still visible - she tried twice, and that is a fact
    assert len(apps(engine)) == 2


# ── 5. headless setting is honoured ───────────────────────────────────

def test_she_sees_the_browser_by_default_so_she_can_finish_it(engine, monkeypatch):
    """
    Headed is the point: the form is filled and left open for her to check
    and submit. Running it hidden would fill a form nobody ever sends.
    """
    server.ENV["HEADLESS"] = "false"
    seen = apply_returns(monkeypatch, {"ok": True, "filled": [], "notes": []})
    engine.post("/apply/start", {"url": "https://example.ca/job"})
    assert seen["headless"] is False


def test_headless_can_be_turned_on_for_unattended_runs(engine, monkeypatch):
    server.ENV["HEADLESS"] = "true"
    seen = apply_returns(monkeypatch, {"ok": True, "filled": [], "notes": []})
    engine.post("/apply/start", {"url": "https://example.ca/job"})
    assert seen["headless"] is True
